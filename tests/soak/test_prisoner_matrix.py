"""Seeded raids that take prisoners on both sides, with releases and escapes."""

import pytest
from logistics_helpers import treaty_world

import sovereign_world.war as war_module
from sovereign_world.commands import CommandEnvelope, CouncilReport, DirectOrder, DirectOrderKind
from sovereign_world.engine import advance_day
from sovereign_world.hexmap import HexCoord
from sovereign_world.ids import EntityId
from sovereign_world.resources import Resource
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState, state_hash
from sovereign_world.war import WarObjective

DAYS = 100


class RaidingSovereign:
    """Raid with a fresh band at every council, and let half the captives go."""

    def __init__(self, enemy: EntityId, route: tuple[HexCoord, ...], *, merciful: bool) -> None:
        self.enemy = enemy
        self.route = route
        self.merciful = merciful

    def decide(self, report: CouncilReport) -> CommandEnvelope:
        council = report.day // 30
        free = [
            person_id for person_id in report.person_ids if person_id not in report.held_captive
        ]
        orders: list[DirectOrder] = [
            DirectOrder(
                command_id=f"raid:{report.day}",
                kind=DirectOrderKind.SEND_WAR_PARTY,
                journey_id=EntityId(f"journey:{report.civilization_id}:raid:{council}"),
                recipient_civilization_id=self.enemy,
                traveller_ids=tuple(free[council * 6 : council * 6 + 6]),
                route=self.route,
                war_objective=WarObjective.RAID,
            )
        ]
        held_home = [
            person_id for person_id in report.captives if council % 2 == 1 and self.merciful
        ]
        if held_home:
            orders.append(
                DirectOrder(
                    command_id=f"release:{report.day}",
                    kind=DirectOrderKind.RELEASE_PRISONERS,
                    captive_ids=tuple(held_home[: max(1, len(held_home) // 2)]),
                )
            )
        return CommandEnvelope(
            schema_version=1,
            civilization_id=report.civilization_id,
            council_day=report.day,
            correlation_id=report.report_id,
            commands=tuple(orders),
        )


def _simulate(initial: WorldState, first: EntityId, second: EntityId, route):
    state = initial.model_copy(deep=True)
    sovereigns = {
        first: RaidingSovereign(second, route, merciful=True),
        second: RaidingSovereign(first, tuple(reversed(route)), merciful=False),
    }
    rng = StableRng(state.config.seed)
    kinds: list[str] = []
    for _ in range(DAYS):
        transition = advance_day(state, rng, sovereigns=sovereigns)
        state = transition.state
        kinds.extend(event.kind for event in transition.events.events)
        captives = {
            person_id
            for civilization in state.civilizations.values()
            for person_id, person in civilization.population.people.items()
            if person.alive and person.captive_of is not None
        }
        for battle in state.battles:
            if battle.day == state.day - 1:
                assert not captives & set(battle.attackers) - set(battle.captured)
        for journey in state.journeys:
            if journey.active:
                assert not captives & set(journey.traveller_ids), "captives never travel free"
    return state, kinds


@pytest.mark.soak
@pytest.mark.parametrize("seed", range(8))
def test_seeded_prisoners_hold_their_invariants_and_replay_exactly(seed: int, monkeypatch) -> None:
    monkeypatch.setattr(war_module, "CAPTURE_BP", 10_000)
    initial, first, second, route = treaty_world(seed=seed, distance=2 + seed % 4)
    for civilization in initial.civilizations.values():
        civilization.inventory = civilization.inventory.model_copy(
            update={"quantities": {**civilization.inventory.quantities, Resource.AXE: 8}}
        )

    final, kinds = _simulate(initial, first, second, route)
    rerun, rerun_kinds = _simulate(initial, first, second, route)

    assert "captured" in kinds
    assert state_hash(rerun) == state_hash(final)
    assert rerun_kinds == kinds
