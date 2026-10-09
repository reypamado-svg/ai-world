"""Calibration policies (sealed trial): baseline play plus one trait, decided from the report
alone, deterministic, and keeping every world valid."""

from __future__ import annotations

import pytest
from logistics_helpers import linked_world

from sovereign_world.calibration.policies import POLICIES, CalibrationSovereign, plan
from sovereign_world.commands import DirectOrderKind, build_council_report
from sovereign_world.config import RunManifest, WorldConfig
from sovereign_world.diplomacy import TreatyKind
from sovereign_world.engine import advance_day
from sovereign_world.ids import EntityId
from sovereign_world.rng import StableRng
from sovereign_world.scripted import COUNCIL_ORDERS
from sovereign_world.state import WorldState, build_initial_state, state_hash, validate_world


def _run(
    state: WorldState, policies: dict[EntityId, str], days: int
) -> tuple[WorldState, list[str]]:
    rng = StableRng(state.config.seed)
    sovereigns = {civ: CalibrationSovereign(policy) for civ, policy in policies.items()}
    kinds: list[str] = []
    for _ in range(days):
        transition = advance_day(state, rng, sovereigns=sovereigns)
        state = transition.state
        kinds.extend(event.kind for event in transition.events.events)
        if state.day % 30 == 0:
            validate_world(state)
    return state, kinds


@pytest.mark.parametrize("policy", POLICIES)
def test_a_policy_gives_the_same_orders_for_the_same_report(policy: str) -> None:
    state = build_initial_state(RunManifest.new(WorldConfig(seed=21, width=24, height=24), "0.2.0"))
    civilization_id = sorted(state.civilizations)[0]
    report = build_council_report(state, civilization_id)
    first = plan(policy, report)
    assert first == plan(policy, report)
    assert len(first) <= COUNCIL_ORDERS
    with pytest.raises(ValueError):
        CalibrationSovereign("hermit")


def test_the_four_policies_keep_a_world_valid_and_replay_the_same() -> None:
    manifest = RunManifest.new(WorldConfig(seed=21, width=24, height=24), "0.2.0")
    state = build_initial_state(manifest)
    policies = {civ: POLICIES[index] for index, civ in enumerate(sorted(state.civilizations))}
    first, _ = _run(state.model_copy(deep=True), policies, 120)
    second, _ = _run(build_initial_state(manifest), policies, 120)
    assert state_hash(first) == state_hash(second)


def test_a_raider_declares_war_on_a_neighbour_it_can_walk_to_and_raids_it() -> None:
    _, state, raider, builder, _ = linked_world(rules_version=3)
    _, kinds = _run(state, {raider: "raider", builder: "builder"}, 70)
    assert "war_declared" in kinds
    assert "war_party_departed" in kinds or "battle_joined" in kinds, sorted(set(kinds))


def test_traders_agree_a_treaty_and_send_caravans() -> None:
    _, state, first, second, _ = linked_world(rules_version=3)
    final, kinds = _run(state, {first: "trader", second: "trader"}, 100)
    trades = [
        treaty
        for treaty in final.active_treaties
        if treaty.kind is TreatyKind.TRADE and treaty.ended_day is None
    ]
    assert trades, sorted(set(kinds))
    assert any(kind.startswith("shipment") for kind in kinds), sorted(set(kinds))


def test_a_trader_or_raider_with_no_neighbour_explores() -> None:
    state = build_initial_state(RunManifest.new(WorldConfig(seed=21, width=24, height=24), "0.2.0"))
    civilization_id = sorted(state.civilizations)[0]
    report = build_council_report(state, civilization_id)
    for policy in ("trader", "raider"):
        kinds = [command.kind for command in plan(policy, report) if hasattr(command, "kind")]
        assert DirectOrderKind.START_EXPEDITION in kinds, (policy, kinds)
