"""Spending on model calls (sealed trial): usage recorded with each council, priced from the
manifest's table, and the cap checked before every day."""

from __future__ import annotations

import json

import pytest

from sovereign_world.commands import build_council_report
from sovereign_world.config import (
    BudgetConfig,
    Price,
    RunManifest,
    SovereignConfig,
    SpendConfig,
    WorldConfig,
)
from sovereign_world.gateway.provider import ScriptedProvider
from sovereign_world.gateway.records import CouncilOutcome, CouncilRecord, ModelUsage
from sovereign_world.gateway.sovereign import GatewaySovereign
from sovereign_world.ids import EntityId
from sovereign_world.runner import _with_limit
from sovereign_world.spend import (
    CALLS_PER_COUNCIL,
    call_input_tokens,
    prompt_round,
    stop_reason,
    tally,
    worst_case_round,
)
from sovereign_world.state import build_initial_state

QUIET = json.dumps({"commands": [], "rationale": "Wait and watch."})
PRICES = {
    "cheap": Price(input_per_million_usd=1, output_per_million_usd=2),
    "dear": Price(input_per_million_usd=10, output_per_million_usd=40),
}


def _manifest(spend: SpendConfig | None = None, **sovereigns: SovereignConfig) -> RunManifest:
    base = RunManifest.new(WorldConfig(seed=21, width=24, height=24), "0.2.0")
    return RunManifest.model_validate(
        {
            **base.model_dump(),
            "sovereigns": {key: value.model_dump() for key, value in sovereigns.items()},
            "spend": spend.model_dump() if spend is not None else None,
        }
    )


def _model(name: str) -> SovereignConfig:
    return SovereignConfig(provider="compatible", base_url="http://127.0.0.1:1/v1", model=name)


def _record(civilization: str, *calls: tuple[str, int, int]) -> CouncilRecord:
    state = build_initial_state(_manifest())
    civ = EntityId(civilization)
    report = build_council_report(state, civ)
    return CouncilRecord(
        civilization_id=civ,
        day=0,
        report_id=report.report_id,
        provider="scripted",
        model="cheap",
        prompt_version="council-7",
        prompt_hash="",
        outcome=CouncilOutcome.ACCEPTED,
        envelope=GatewaySovereign(ScriptedProvider([QUIET])).decide(report),
        usage=tuple(
            ModelUsage(purpose="turn", model=model, input_tokens=tokens_in, output_tokens=out)
            for model, tokens_in, out in calls
        ),
    )


def _first_civilization() -> str:
    return str(sorted(build_initial_state(_manifest()).civilizations)[0])


def test_a_council_records_each_calls_answering_model_and_tokens() -> None:
    state = build_initial_state(_manifest())
    civ = sorted(state.civilizations)[0]
    report = build_council_report(state, civ)
    provider = ScriptedProvider(
        ["not json", QUIET], model="cheap", input_tokens=900, output_tokens=40
    )
    sovereign = GatewaySovereign(provider)
    sovereign.decide(report)
    (record,) = sovereign.drain_records()
    assert record.outcome is CouncilOutcome.REPAIRED
    assert [usage.purpose for usage in record.usage] == ["turn", "repair"]
    assert {(u.model, u.input_tokens, u.output_tokens) for u in record.usage} == {
        ("cheap", 900, 40)
    }
    fallback = ScriptedProvider([QUIET], model="cheap", answering_model="dear")
    sovereign = GatewaySovereign(fallback)
    sovereign.decide(report)
    (record,) = sovereign.drain_records()
    assert record.model == "cheap" and record.usage[0].model == "dear"


def test_recorded_usage_changes_no_prompt_and_no_command() -> None:
    state = build_initial_state(_manifest())
    report = build_council_report(state, sorted(state.civilizations)[0])
    records = []
    for tokens in (0, 5_000):
        sovereign = GatewaySovereign(
            ScriptedProvider([QUIET], input_tokens=tokens, output_tokens=tokens)
        )
        sovereign.decide(report)
        records.append(sovereign.drain_records()[0])
    first, second = records
    assert first.prompt_hash == second.prompt_hash
    assert first.envelope == second.envelope
    assert first.usage != second.usage


def test_a_council_without_usage_is_saved_and_read_as_before() -> None:
    record = _record(_first_civilization())
    dumped = record.model_dump(mode="json")
    assert "usage" not in dumped
    assert CouncilRecord.model_validate(dumped) == record
    used = _record(_first_civilization(), ("cheap", 10, 2))
    dumped = used.model_dump(mode="json")
    assert dumped["usage"] == [
        {"purpose": "turn", "model": "cheap", "input_tokens": 10, "output_tokens": 2}
    ]
    assert CouncilRecord.model_validate(dumped) == used


def test_spending_is_priced_by_answering_model_and_unlisted_models_at_the_dearest_rate() -> None:
    spend = SpendConfig(prices=PRICES)
    civ = _first_civilization()
    total = tally(
        [
            _record(civ, ("cheap", 1_000_000, 1_000_000)),
            _record(civ, ("mystery", 1_000_000, 0)),
            _record(civ),
        ],
        spend,
    )
    assert total.councils == 2
    assert total.cost_usd == pytest.approx(1 + 2 + 10)
    assert total.unpriced == {"mystery"}
    assert total.by_model["mystery"][2] == pytest.approx(10)
    assert total.by_civilization[civ][:2] == [2_000_000, 1_000_000]
    merged = tally([], spend)
    merged.merge(total)
    merged.merge(total)
    assert merged.cost_usd == pytest.approx(26) and merged.councils == 4


def test_the_worst_case_round_counts_every_model_played_council_twice_at_full_size() -> None:
    civ = _first_civilization()
    manifest = _manifest(SpendConfig(prices=PRICES), **{civ: _model("dear")})
    worst = worst_case_round(manifest)
    budgets = BudgetConfig()
    assert worst.councils == 1
    assert worst.input_tokens == CALLS_PER_COUNCIL * call_input_tokens(budgets)
    assert worst.output_tokens == CALLS_PER_COUNCIL * budgets.max_output_tokens
    assert worst.cost_usd == pytest.approx(
        (worst.input_tokens * 10 + worst.output_tokens * 40) / 1_000_000
    )
    assert worst_case_round(_manifest(SpendConfig(prices=PRICES))).councils == 0


def test_the_real_prompts_fit_inside_the_worst_case() -> None:
    civ = _first_civilization()
    manifest = _manifest(SpendConfig(prices=PRICES), **{civ: _model("cheap")})
    sized = prompt_round(manifest, build_initial_state(manifest))
    worst = worst_case_round(manifest)
    assert sized.councils == 1
    assert 0 < sized.input_tokens <= worst.input_tokens // CALLS_PER_COUNCIL


def test_the_next_day_stops_when_a_worst_case_round_would_pass_any_cap() -> None:
    civ = _first_civilization()
    no_cap = _manifest(None, **{civ: _model("dear")})
    assert stop_reason(no_cap, tally([], None)) is None
    worst = worst_case_round(_manifest(SpendConfig(prices=PRICES), **{civ: _model("dear")}))
    spent = tally([_record(civ, ("dear", 100_000, 0))], SpendConfig(prices=PRICES))
    room = spent.cost_usd + worst.cost_usd

    def reason(**caps: object) -> str | None:
        capped = SpendConfig.model_validate({"prices": PRICES, **caps})
        return stop_reason(_manifest(capped, **{civ: _model("dear")}), spent)

    assert reason(max_cost_usd=room) is None
    stopped = reason(max_cost_usd=room - 0.01)
    assert stopped is not None and "cost" in stopped
    assert reason(max_cost_usd=room, reserve_councils=2) is not None
    assert reason(max_input_tokens=100_000 + worst.input_tokens) is None
    assert reason(max_input_tokens=100_000 + worst.input_tokens - 1) is not None
    assert reason(max_output_tokens=worst.output_tokens - 1) is not None


def test_an_unpriced_reply_stops_the_run_when_the_table_says_refuse() -> None:
    civ = _first_civilization()
    spent = tally([_record(civ, ("mystery", 1, 1))], SpendConfig(prices=PRICES))
    refuse = _manifest(SpendConfig(prices=PRICES, unknown_model="refuse"), **{civ: _model("cheap")})
    assert "mystery" in (stop_reason(refuse, spent) or "")
    highest = _manifest(SpendConfig(prices=PRICES), **{civ: _model("cheap")})
    assert stop_reason(highest, spent) is None


def test_a_session_limit_lowers_the_cap_and_never_raises_it() -> None:
    capped = _manifest(SpendConfig(max_cost_usd=5, prices=PRICES))
    assert _with_limit(capped, None) is capped
    assert _with_limit(capped, 2).spend.max_cost_usd == 2  # type: ignore[union-attr]
    assert _with_limit(capped, 9).spend.max_cost_usd == 5  # type: ignore[union-attr]
    assert _with_limit(_manifest(), 3).spend.max_cost_usd == 3  # type: ignore[union-attr]


def test_the_spending_cap_enters_the_manifest_hash_only_when_set() -> None:
    plain = _manifest()
    same = RunManifest.model_validate({**plain.model_dump(), "spend": None})
    assert same.content_hash() == plain.content_hash()
    capped = RunManifest.model_validate(
        {**plain.model_dump(), "spend": SpendConfig(max_cost_usd=5).model_dump()}
    )
    assert capped.content_hash() != plain.content_hash()


def test_each_cap_counts_the_worst_case_rounds_it_covers() -> None:
    from sovereign_world.spend import Tally, caps_of, rounds_covered, shown_cap

    free = SpendConfig(
        max_input_tokens=6_000_000,
        max_output_tokens=1_500_000,
        prices={"m": Price(input_per_million_usd=0, output_per_million_usd=0)},
    )
    manifest = _manifest(free, **{"civilization:0000000001": _model("m")})
    worst = worst_case_round(manifest)
    assert worst.cost_usd == 0 and worst.input_tokens == 62_668
    assert caps_of(free) == [("input tokens", 6e6), ("output tokens", 1.5e6)]
    assert rounds_covered(manifest, Tally()) == {"input tokens": 95, "output tokens": 93}
    used = Tally(input_tokens=6_000_000 - 62_667)
    assert rounds_covered(manifest, used)["input tokens"] == 0
    assert shown_cap("cost", 50) == "$50.00"
    assert shown_cap("input tokens", 6e6) == "6,000,000 input tokens"
    paid = SpendConfig(
        max_cost_usd=10, prices={"m": Price(input_per_million_usd=3, output_per_million_usd=15)}
    )
    covered = rounds_covered(_manifest(paid, **{"civilization:0000000001": _model("m")}), Tally())
    assert list(covered) == ["cost"] and covered["cost"] > 0
    assert caps_of(None) == [] and caps_of(SpendConfig()) == []


def test_the_worst_case_round_is_priced_at_the_dearest_rate_because_a_fallback_may_answer() -> None:
    from sovereign_world.spend import Tally, dearest, prompt_round, stop_reason

    table = SpendConfig(prices=PRICES)
    manifest = _manifest(table, **{"civilization:0000000001": _model("cheap")})
    worst = worst_case_round(manifest)
    top = dearest(table)
    expected = (
        worst.input_tokens * top.input_per_million_usd
        + worst.output_tokens * top.output_per_million_usd
    ) / 1_000_000
    assert worst.cost_usd == pytest.approx(expected)
    assert worst.by_model["cheap"][2] == pytest.approx(expected)
    cheap = PRICES["cheap"]
    at_cheap = (
        worst.input_tokens * cheap.input_per_million_usd
        + worst.output_tokens * cheap.output_per_million_usd
    ) / 1_000_000
    assert at_cheap < expected
    sized = prompt_round(manifest, build_initial_state(manifest))
    assert sized.cost_usd < expected / 2, "the dry run still prices the real prompts as configured"
    capped = _manifest(
        SpendConfig(prices=PRICES, max_cost_usd=at_cheap + 0.001),
        **{"civilization:0000000001": _model("cheap")},
    )
    assert stop_reason(capped, Tally()) is not None
