"""The spending cap in a real run (sealed trial): the run stops cleanly before a day whose
councils could pass it, keeps a checkpoint, refuses to go on, and still verifies."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sovereign_world import runner
from sovereign_world.cli import app
from sovereign_world.config import Price, RunManifest, SovereignConfig, SpendConfig, WorldConfig
from sovereign_world.gateway.factory import build_sovereigns
from sovereign_world.gateway.provider import ScriptedProvider
from sovereign_world.gateway.records import recorded_councils
from sovereign_world.ids import EntityId
from sovereign_world.persistence import WorldStore
from sovereign_world.replay import verify_run
from sovereign_world.spend import tally, worst_case_round
from sovereign_world.state import build_initial_state

QUIET = json.dumps({"commands": [], "rationale": "Wait and watch."})
PRICE = Price(input_per_million_usd=3, output_per_million_usd=15)
TOKENS_IN, TOKENS_OUT = 20_000, 500


def _store(root: Path, spend: SpendConfig | None) -> tuple[WorldStore, EntityId]:
    base = RunManifest.new(WorldConfig(seed=21, width=24, height=24), "0.2.0")
    home = sorted(build_initial_state(base).civilizations)[0]
    manifest = RunManifest.model_validate(
        {
            **base.model_dump(),
            "sovereigns": {
                str(home): SovereignConfig(
                    provider="compatible", base_url="http://127.0.0.1:1/v1", model="priced"
                ).model_dump()
            },
            "spend": spend.model_dump() if spend is not None else None,
        }
    )
    return WorldStore.create(root, manifest, build_initial_state(manifest)), home


@pytest.fixture
def scripted(monkeypatch: pytest.MonkeyPatch) -> list[ScriptedProvider]:
    """Every model-played council answers quietly, reporting fixed token counts."""
    made: list[ScriptedProvider] = []

    def build(manifest, civilization_ids, *, history=()):  # type: ignore[no-untyped-def]
        ids = sorted(civilization_ids)
        providers = {
            civ: ScriptedProvider(
                [QUIET] * 400, model="priced", input_tokens=TOKENS_IN, output_tokens=TOKENS_OUT
            )
            for civ in ids
            if str(civ) in manifest.sovereigns
        }
        made.extend(providers.values())
        return build_sovereigns(manifest, ids, history=history, providers=providers)

    monkeypatch.setattr(runner, "build_sovereigns", build)
    return made


def _one_call_usd() -> float:
    tokens_in = TOKENS_IN * PRICE.input_per_million_usd
    return (tokens_in + TOKENS_OUT * PRICE.output_per_million_usd) / 1e6


def test_the_run_stops_before_a_day_that_could_pass_the_cap(
    tmp_path: Path, scripted: list[ScriptedProvider]
) -> None:
    probe, _ = _store(tmp_path / "probe", SpendConfig(prices={"priced": PRICE}))
    worst = worst_case_round(probe.manifest()).cost_usd
    # Room for day 0's council and one worst-case round: day 30's council fits, and then the
    # next worst case no longer does.
    cap = _one_call_usd() + worst + _one_call_usd() / 2
    store, home = _store(tmp_path / "run", SpendConfig(max_cost_usd=cap, prices={"priced": PRICE}))

    with pytest.raises(runner.SpendCapReached) as stop:
        runner.run_days(store, 60)
    assert stop.value.state.day == 31
    assert "spending cap" in stop.value.reason
    councils = recorded_councils(store)
    assert [(record.civilization_id, record.day) for record in councils if record.usage] == [
        (home, 0),
        (home, 30),
    ]
    spent = tally(councils, store.manifest().spend)
    assert spent.cost_usd == pytest.approx(2 * _one_call_usd()) and spent.cost_usd <= cap
    assert store.load_checkpoint().day == 31
    assert verify_run(store).verified_through_day == 31

    asked = sum(len(provider.requests) for provider in scripted)
    with pytest.raises(runner.SpendCapReached) as again:
        runner.run_days(WorldStore(store.root), 5)
    assert again.value.state.day == 31
    assert sum(len(provider.requests) for provider in scripted) == asked
    assert len(recorded_councils(store)) == len(councils)


def test_the_command_line_stops_with_code_three_and_reports_the_spend(
    tmp_path: Path, scripted: list[ScriptedProvider]
) -> None:
    store, home = _store(tmp_path / "run", SpendConfig(prices={"priced": PRICE}))
    cli = CliRunner()
    # No cap in the manifest: the session's limit alone stops it before day 1's round.
    limit = _one_call_usd() + worst_case_round(store.manifest()).cost_usd - 0.000001
    result = cli.invoke(
        app, ["run", str(store.root), "--days", "40", "--spend-limit", f"{limit:.6f}"]
    )
    assert result.exit_code == 3, result.output
    assert "stopped before day 2" in result.output
    assert "spending cap" in result.output

    report = cli.invoke(app, ["spend", str(store.root), "--dry-run"])
    assert report.exit_code == 0, report.output
    assert f"civilization {home}:" in report.output
    assert "model priced:" in report.output
    assert "cost cap: none" in report.output
    assert "prompts of day 1" in report.output
    assert not [provider for provider in scripted if len(provider.requests) > 1]
