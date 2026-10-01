import hashlib
import json
from pathlib import Path

import pytest
from logistics_helpers import ScheduledSovereign, clear_message_id, treaty_world
from typer.testing import CliRunner

from sovereign_world.cli import app
from sovereign_world.commands import (
    CRISIS_GAP_DAYS,
    DirectOrder,
    DirectOrderKind,
    build_council_report,
    crisis_council_due,
    validate_envelope,
)
from sovereign_world.config import BudgetConfig, RunManifest, SovereignConfig, WorldConfig
from sovereign_world.engine import advance_day
from sovereign_world.gateway.factory import build_sovereigns, provider_for
from sovereign_world.gateway.provider import ScriptedProvider
from sovereign_world.gateway.records import CouncilOutcome, journal_councils, recorded_councils
from sovereign_world.gateway.sovereign import GatewaySovereign, RecordingSovereign
from sovereign_world.ids import EntityId
from sovereign_world.persistence import WorldStore
from sovereign_world.replay import rederive_run
from sovereign_world.rng import StableRng
from sovereign_world.scripted import BaselineSovereign
from sovereign_world.state import state_hash
from sovereign_world.war import War

runner = CliRunner()
QUIET = json.dumps({"commands": [], "rationale": "Wait and watch."})


def test_a_manifest_left_at_its_defaults_keeps_its_old_hash() -> None:
    manifest = RunManifest.new(config=WorldConfig(seed=21, width=48, height=48), engine_version="1")
    old = hashlib.sha256(
        json.dumps(
            {
                "run_id": str(manifest.run_id),
                "engine_version": "1",
                "config": manifest.config.model_dump(mode="json"),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    assert manifest.content_hash() == old
    changed = manifest.model_copy(
        update={"sovereigns": {"civilization:0000000001": SovereignConfig(provider="anthropic")}}
    )
    assert changed.content_hash() != old
    tighter = manifest.model_copy(update={"budgets": BudgetConfig(timeout_seconds=60)})
    assert tighter.content_hash() != old


def _at_war(state, home, rival, learned_day: int) -> None:
    state.wars = (
        War(
            war_id=EntityId("war:raid"),
            aggressor_id=rival,
            defender_id=home,
            declared=True,
            started_day=learned_day,
            defender_learned_day=learned_day,
        ),
    )


def test_a_crisis_calls_a_model_played_council_the_next_day_and_at_most_weekly() -> None:
    state, home, rival, _ = treaty_world(distance=4)
    state.day = 5
    _at_war(state, home, rival, learned_day=4)
    report = build_council_report(state, home)
    assert report.crisis == ("war",) and crisis_council_due(state, home)

    provider = ScriptedProvider([QUIET, QUIET])
    model = GatewaySovereign(provider)
    result = advance_day(state, StableRng(state.config.seed), sovereigns={home: model})
    [held] = [event for event in result.events.events if event.kind == "council_held"]
    assert held.payload["crisis"] is True and len(provider.requests) == 1
    assert result.state.civilizations[home].last_crisis_council == 5
    assert validate_envelope(model.drain_records()[0].envelope, state).errors == ()

    again = result.state
    _at_war(again, home, rival, learned_day=again.day - 1)
    assert not crisis_council_due(again, home), f"only one crisis council in {CRISIS_GAP_DAYS} days"


def test_scripted_sovereigns_keep_to_the_monthly_council() -> None:
    state, home, rival, _ = treaty_world(distance=4)
    state.day = 5
    _at_war(state, home, rival, learned_day=4)
    calls: list[int] = []

    class Counting(BaselineSovereign):
        def decide(self, report):
            calls.append(report.day)
            return super().decide(report)

    advance_day(state, StableRng(state.config.seed), sovereigns={home: Counting()})
    assert calls == []


def test_settings_build_each_civilizations_sovereign() -> None:
    state, home, rival, _ = treaty_world(distance=4)
    manifest = RunManifest.model_validate(
        {
            "run_id": state.run_id,
            "engine_version": "0.1.0",
            "config": state.config,
            "sovereigns": {
                str(home): SovereignConfig(provider="openai", model="named"),
                str(rival): SovereignConfig(
                    provider="compatible", base_url="http://127.0.0.1:1/v1", model="local"
                ),
            },
            "budgets": BudgetConfig(timeout_seconds=12.0),
        }
    )
    scripted = ScriptedProvider([QUIET])
    sovereigns = build_sovereigns(manifest, state.civilizations, providers={home: scripted})
    assert isinstance(sovereigns[home], GatewaySovereign)
    assert sovereigns[home].budgets.timeout_seconds == 12.0
    assert isinstance(sovereigns[rival], GatewaySovereign)
    others = [item for item in sorted(state.civilizations) if item not in {home, rival}]
    assert all(isinstance(sovereigns[item], RecordingSovereign) for item in others)

    with pytest.raises(ValueError):
        provider_for(home, SovereignConfig(provider="openai", model=""))
    stale = manifest.model_copy(
        update={
            "sovereigns": {
                str(home): SovereignConfig(provider="openai", model="m", prompt_version="council-0")
            }
        }
    )
    with pytest.raises(ValueError, match="fork the run"):
        build_sovereigns(stale, state.civilizations, providers={home: scripted})


def test_a_run_with_crisis_councils_rederives_from_its_records(tmp_path: Path) -> None:
    state, home, rival, route = treaty_world(distance=8)
    declare = DirectOrder(
        command_id="declare",
        kind=DirectOrderKind.DECLARE_WAR,
        message_id=EntityId(clear_message_id("war", days=12)),
        ambassador_id=state.civilizations[rival].population.living_ids[-1],
        recipient_civilization_id=home,
        message_text="Your fields are ours.",
        route=tuple(reversed(route)),
    )
    manifest = RunManifest.model_validate(
        {"run_id": state.run_id, "config": state.config, "engine_version": "0.1.0"}
    )
    store = WorldStore.create(tmp_path / "record", manifest, state)
    sovereigns = {
        home: GatewaySovereign(ScriptedProvider([QUIET] * 10)),
        rival: RecordingSovereign(ScheduledSovereign({0: (declare,)})),
    }
    rng = StableRng(state.config.seed)
    crises: list[tuple[int, tuple[str, ...]]] = []
    for _ in range(40):
        reasons = build_council_report(state, home).crisis
        transition = advance_day(state, rng, sovereigns=sovereigns)
        if any(
            event.kind == "council_held" and event.payload.get("crisis")
            for event in transition.events.events
        ):
            crises.append((state.day, reasons))
        state = transition.state
        store.append_transition(state, transition.events)
        journal_councils(store, sovereigns.values())
    days = [record.day for record in recorded_councils(store) if record.civilization_id == home]
    assert crises[0] == (1, ("first_contact",))
    assert any("war" in reasons for _, reasons in crises), crises
    assert days == sorted({0, 30, *(day for day, _ in crises)})
    assert rederive_run(store).state_hash == state_hash(state)


def test_the_command_line_runs_records_verifies_and_forks(tmp_path: Path) -> None:
    settings = tmp_path / "sovereigns.toml"
    settings.write_text(
        "[budgets]\ntimeout_seconds = 5\n\n"
        '[sovereigns."civilization:0000000001"]\n'
        'provider = "compatible"\nbase_url = "http://127.0.0.1:9/v1"\nmodel = "nobody-home"\n'
    )
    world = tmp_path / "world"
    assert (
        runner.invoke(
            app, ["init", str(world), "--seed", "21", "--sovereigns", str(settings)]
        ).exit_code
        == 0
    )
    advanced = runner.invoke(app, ["run", str(world), "--days", "31"])
    assert advanced.exit_code == 0, advanced.stdout
    councils = recorded_councils(WorldStore(world))
    unreachable = [record for record in councils if record.provider.startswith("compatible")]
    assert unreachable and all(
        record.outcome is CouncilOutcome.UNAVAILABLE for record in unreachable
    )
    assert {record.provider for record in councils} >= {"baseline"}
    verified = runner.invoke(app, ["verify", str(world)])
    assert verified.exit_code == 0 and "verified through day 31" in verified.stdout

    calm = tmp_path / "calm.toml"
    calm.write_text("[budgets]\ntimeout_seconds = 9\n")
    fork = tmp_path / "fork"
    forked = runner.invoke(
        app, ["fork", str(world), str(fork), "--day", "20", "--sovereigns", str(calm)]
    )
    assert forked.exit_code == 0, forked.stdout
    child = WorldStore(fork).manifest()
    parent = WorldStore(world).manifest()
    assert child.parent_run_id == parent.run_id and child.forked_at_day == 20
    assert child.run_id != parent.run_id and child.sovereigns == {}
    assert runner.invoke(app, ["run", str(fork), "--days", "12"]).exit_code == 0
    refork = runner.invoke(app, ["verify", str(fork)])
    assert refork.exit_code == 0 and "verified through day 32" in refork.stdout
