"""council-6: rules-3 councils are told how to design their towns, the baseline designs its
capital, and a model's design is recorded and rederived exactly."""

import json
from pathlib import Path

from sovereign_world.commands import DirectOrderKind, build_council_report
from sovereign_world.config import RunManifest, WorldConfig
from sovereign_world.engine import advance_day
from sovereign_world.gateway.prompt import PROMPT_VERSION, charter, town_plan_rule
from sovereign_world.gateway.provider import ScriptedProvider
from sovereign_world.gateway.records import journal_councils
from sovereign_world.gateway.sovereign import GatewaySovereign
from sovereign_world.persistence import WorldStore
from sovereign_world.replay import rederive_run
from sovereign_world.rings import SALVAGE_SHARE
from sovereign_world.rng import StableRng
from sovereign_world.scripted import BaselineSovereign, plan_baseline_commands
from sovereign_world.state import WorldState, build_initial_state, state_hash, validate_world
from sovereign_world.townplan import (
    KEEP_DEFENCE_BP,
    MARKET_ROOM,
    SHELTERED_HOUSES,
    Place,
    PlanStyle,
    is_default,
    sections_of,
)
from sovereign_world.walls import WallGrade, section_materials, section_person_days

CONFIG = WorldConfig(seed=21, width=24, height=24)


def _state(rules_version: int = 3) -> WorldState:
    return build_initial_state(RunManifest.new(CONFIG, "0.2.0", rules_version=rules_version))


def _report(rules_version: int = 3):  # type: ignore[no-untyped-def]
    state = _state(rules_version)
    return build_council_report(state, sorted(state.civilizations)[0])


def test_rules_three_councils_are_told_how_towns_and_walls_are_laid_out() -> None:
    assert PROMPT_VERSION == "council-6"
    rule = town_plan_rule()
    assert rule in charter(_report(3))
    assert rule not in charter(_report(2))
    assert "plan_settlement" not in charter(_report(2)).split("The reply schema")[0]
    for ring in range(1, 6):
        assert (
            f"{sections_of(ring)} sections at ring {ring} ({SHELTERED_HOUSES[ring]} houses" in rule
        )
    for style in PlanStyle:
        assert style.value in rule
    for place in Place:
        assert place.value in rule
    palisade = section_materials(WallGrade.EARTHWORK, WallGrade.PALISADE)
    assert f"palisade {palisade[next(iter(palisade))]} timber" in rule
    assert f"{section_person_days(None, WallGrade.EARTHWORK)} person-days" in rule
    assert f"+{(KEEP_DEFENCE_BP - 10_000) / 100:g}%" in rule
    assert f"{MARKET_ROOM} to the store's room" in rule
    assert SALVAGE_SHARE == 2 and "half of what it cost comes back" in rule
    assert "2 tools and 30 person-days" in rule
    assert "shrine is drawn, and does nothing yet" in rule


def test_the_baseline_designs_its_capital_at_the_first_council_with_room() -> None:
    # Day 0's council is full, so the design waits for day 30.
    first = plan_baseline_commands(_report(3))
    assert first[-1].kind is DirectOrderKind.PLAN_SETTLEMENT
    assert DirectOrderKind.PLAN_SETTLEMENT not in {
        getattr(item, "kind", None) for item in first[:8]
    }
    older = plan_baseline_commands(_report(2))
    assert all(getattr(item, "kind", None) is not DirectOrderKind.PLAN_SETTLEMENT for item in older)

    state = _state()
    sovereigns = {key: BaselineSovereign() for key in state.civilizations}
    rng = StableRng(state.config.seed)
    rooms = {key: item.inventory.capacity for key, item in state.civilizations.items()}
    for _ in range(31):
        state = advance_day(state, rng, sovereigns=sovereigns).state
    validate_world(state)
    for key, civilization in state.civilizations.items():
        [capital] = [item for item in civilization.settlements if item.capital]
        plan = civilization.town_plans[capital.settlement_id]
        assert not is_default(plan) and plan.planned_day == 30
        assert plan.style is PlanStyle.RINGED and plan.keep is Place.CENTRE
        assert plan.market is Place.BY_STORE and plan.gates == (0, 3)
        assert civilization.inventory.capacity >= rooms[key] + MARKET_ROOM
        report = build_council_report(state, key)
        assert all(
            getattr(item, "kind", None) is not DirectOrderKind.PLAN_SETTLEMENT
            for item in plan_baseline_commands(report)
        ), "a designed capital is not designed again"


def test_a_models_design_is_recorded_and_rederived(tmp_path: Path) -> None:
    state = _state()
    civilization_id = sorted(state.civilizations)[0]
    [capital] = state.civilizations[civilization_id].settlements
    workers = state.civilizations[civilization_id].population.living_ids[:2]
    reply = json.dumps(
        {
            "commands": [
                {
                    "command_id": "design",
                    "kind": "plan_settlement",
                    "settlement_id": capital.settlement_id,
                    "town_plan": {
                        "style": "grid",
                        "keep": "centre",
                        "shrine": "edge",
                        "wall_ring": 1,
                        "gates": [2, 5],
                    },
                },
                {
                    "command_id": "earthworks",
                    "kind": "build_walls",
                    "worker_ids": list(workers),
                    "wall_grade": "earthwork",
                    "wall_sections": 2,
                },
            ],
            "rationale": "A tight grid behind a small ring, two stretches first.",
        }
    )
    manifest = RunManifest.model_validate(
        {
            "run_id": state.run_id,
            "config": state.config,
            "engine_version": "0.2.0",
            "rules_version": 3,
        }
    )
    store = WorldStore.create(tmp_path / "record", manifest, state)
    sovereigns = {civilization_id: GatewaySovereign(ScriptedProvider([reply]))}
    rng = StableRng(state.config.seed)
    for _ in range(4):
        transition = advance_day(state, rng, sovereigns=sovereigns)
        state = transition.state
        store.append_transition(state, transition.events)
        journal_councils(store, sovereigns.values())
    civilization = state.civilizations[civilization_id]
    assert civilization.town_plans[capital.settlement_id].wall_ring == 1
    ring = civilization.wall_rings[capital.settlement_id]
    assert len(ring.sections) == 6 and ring.built == 2
    assert rederive_run(store).state_hash == state_hash(state)
