"""Seeded wars between neighbours: raids, counter-raids and drill, with daily invariants."""

from pathlib import Path

import pytest
from logistics_helpers import treaty_world

from sovereign_world.commands import CommandEnvelope, CouncilReport, DirectOrder, DirectOrderKind
from sovereign_world.config import RunManifest
from sovereign_world.engine import advance_day
from sovereign_world.hexmap import HexCoord
from sovereign_world.ids import EntityId
from sovereign_world.logistics import JourneyKind, JourneyOutcome
from sovereign_world.persistence import WorldStore
from sovereign_world.replay import replay_run, verify_run
from sovereign_world.resources import Resource
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState, state_hash
from sovereign_world.war import WarObjective

DAYS = 75
CONSERVED = (Resource.STONE, Resource.TIMBER)


class WarlikeSovereign:
    """Declare war (or not), then raid at every council with a fresh band of eight."""

    def __init__(self, enemy: EntityId, route: tuple[HexCoord, ...], *, declare: bool) -> None:
        self.enemy = enemy
        self.route = route
        self.declare = declare

    def decide(self, report: CouncilReport) -> CommandEnvelope:
        council = report.day // 30
        people = report.person_ids
        orders: list[DirectOrder] = []
        if council == 0 and self.declare:
            orders.append(
                DirectOrder(
                    command_id="declare",
                    kind=DirectOrderKind.DECLARE_WAR,
                    message_id=EntityId(f"message:{report.civilization_id}:war"),
                    ambassador_id=people[-1],
                    recipient_civilization_id=self.enemy,
                    message_text="War.",
                    route=self.route,
                )
            )
        orders.append(
            DirectOrder(
                command_id=f"raid:{report.day}",
                kind=DirectOrderKind.SEND_WAR_PARTY,
                journey_id=EntityId(f"journey:{report.civilization_id}:raid:{council}"),
                recipient_civilization_id=self.enemy,
                traveller_ids=people[council * 8 : council * 8 + 8],
                route=self.route,
                cargo={Resource.AXE: 4},
                war_objective=WarObjective.RAID,
            )
        )
        return _envelope(report, orders)


class DefendingSovereign:
    """Drill part of the village, then answer with a counter-raid."""

    def __init__(self, enemy: EntityId, route: tuple[HexCoord, ...]) -> None:
        self.enemy = enemy
        self.route = route

    def decide(self, report: CouncilReport) -> CommandEnvelope:
        people = report.person_ids
        if report.day == 0:
            orders = [
                DirectOrder(
                    command_id="drill",
                    kind=DirectOrderKind.DRILL,
                    worker_ids=people[:6],
                    drill_days=25,
                )
            ]
        else:
            orders = [
                DirectOrder(
                    command_id=f"counter:{report.day}",
                    kind=DirectOrderKind.SEND_WAR_PARTY,
                    journey_id=EntityId(f"journey:{report.civilization_id}:counter:{report.day}"),
                    recipient_civilization_id=self.enemy,
                    traveller_ids=people[:6],
                    route=self.route,
                    war_objective=WarObjective.ATTACK,
                )
            ]
        return _envelope(report, orders)


def _envelope(report: CouncilReport, orders: list[DirectOrder]) -> CommandEnvelope:
    return CommandEnvelope(
        schema_version=1,
        civilization_id=report.civilization_id,
        council_day=report.day,
        correlation_id=report.report_id,
        commands=tuple(orders),
    )


def _held(state: WorldState, resource: Resource) -> int:
    """Stores, plus plunder on the road or lost with a party that perished."""
    stored = sum(
        civilization.inventory.quantities.get(resource, 0)
        for civilization in state.civilizations.values()
    )
    carried = sum(
        journey.plunder.get(resource, 0)
        for journey in state.journeys
        if journey.kind is JourneyKind.CAMPAIGN
        and (journey.active or journey.outcome is JourneyOutcome.PERISHED)
    )
    return stored + carried


def _simulate(initial: WorldState, first: EntityId, second: EntityId, route, store=None):
    state = initial.model_copy(deep=True)
    sovereigns = {
        first: WarlikeSovereign(second, route, declare=state.config.seed % 2 == 0),
        second: DefendingSovereign(first, tuple(reversed(route))),
    }
    rng = StableRng(state.config.seed)
    totals = {resource: _held(state, resource) for resource in CONSERVED}
    dead: set[EntityId] = set()
    kinds: list[str] = []
    for _ in range(DAYS):
        before = {
            person_id: person.alive
            for civilization in state.civilizations.values()
            for person_id, person in civilization.population.people.items()
        }
        transition = advance_day(state, rng, sovereigns=sovereigns)
        state = transition.state
        if store is not None:
            store.append_transition(state, transition.events)
        kinds.extend(event.kind for event in transition.events.events)
        for resource in CONSERVED:
            assert _held(state, resource) == totals[resource], f"{resource} appeared or vanished"
        people = {
            person_id: person
            for civilization in state.civilizations.values()
            for person_id, person in civilization.population.people.items()
        }
        assert not any(people[person_id].alive for person_id in dead), "the dead returned"
        dead |= {person_id for person_id, person in people.items() if not person.alive}
        for battle in state.battles:
            if battle.day != state.day - 1:
                continue
            war = next(
                (
                    item
                    for item in state.wars
                    if item.involves(battle.attacker_id, battle.defender_id)
                    and item.started_day <= battle.day
                ),
                None,
            )
            assert war is not None, "a battle was fought outside a war"
            assert all(before[person_id] for person_id in (*battle.attackers, *battle.defenders)), (
                "the dead fought"
            )
        for civilization_id, civilization in state.civilizations.items():
            fought = {
                battle.battle_id
                for battle in state.battles
                if civilization_id in {battle.attacker_id, battle.defender_id}
            }
            assert {report.battle_id for report in civilization.war_reports} <= fought
    return state, kinds


@pytest.mark.soak
@pytest.mark.parametrize("seed", range(12))
def test_seeded_wars_kill_only_in_war_conserve_goods_and_replay(seed: int, tmp_path: Path) -> None:
    initial, first, second, route = treaty_world(seed=seed, distance=2 + seed % 4)
    manifest = RunManifest.model_validate(
        {"run_id": initial.run_id, "config": initial.config, "engine_version": "0.1.0"}
    )
    store = WorldStore.create(tmp_path / "record", manifest, initial)

    final, kinds = _simulate(initial, first, second, route, store)
    rerun, rerun_kinds = _simulate(initial, first, second, route)

    assert "battle_joined" in kinds, "the raiders met the enemy"
    assert ("war_declared" if seed % 2 == 0 else "undeclared_attack") in kinds
    assert "treaty_breached" in kinds
    assert "drill_completed" in kinds
    assert state_hash(rerun) == state_hash(final)
    assert rerun_kinds == kinds
    assert state_hash(replay_run(store)) == state_hash(final)
    assert verify_run(store).state_hash == state_hash(final)
