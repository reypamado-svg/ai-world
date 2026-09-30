"""Seeded peace settlements that cede a colony with its people, with daily invariants."""

import pytest
from logistics_helpers import treaty_world

from sovereign_world.commands import CommandEnvelope, CouncilReport, DirectOrder, DirectOrderKind
from sovereign_world.diplomacy import PeaceTerms, TreatyKind
from sovereign_world.engine import advance_day
from sovereign_world.hexmap import HexCoord, Terrain
from sovereign_world.ids import EntityId
from sovereign_world.resources import Inventory, Resource
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState, state_hash
from sovereign_world.territory import Settlement
from sovereign_world.war import WarObjective

DAYS = 150
PEACE = EntityId("treaty:peace")


class Victor:
    """Raid at the first council, then accept whatever peace is offered."""

    def __init__(self, enemy: EntityId, route: tuple[HexCoord, ...]) -> None:
        self.enemy = enemy
        self.route = route

    def decide(self, report: CouncilReport) -> CommandEnvelope:
        orders: list[DirectOrder] = []
        if report.day == 0:
            orders.append(
                DirectOrder(
                    command_id="raid",
                    kind=DirectOrderKind.SEND_WAR_PARTY,
                    journey_id=EntityId(f"journey:{report.civilization_id}:raid"),
                    recipient_civilization_id=self.enemy,
                    traveller_ids=report.person_ids[:10],
                    route=self.route,
                    war_objective=WarObjective.RAID,
                )
            )
        offered = any(
            message.treaty_offer is not None and message.treaty_offer.offer_id == PEACE
            for message in report.received_messages
        )
        if offered and not any(item.treaty_id == PEACE for item in report.treaties):
            orders.append(
                DirectOrder(
                    command_id=f"accept:{report.day}",
                    kind=DirectOrderKind.ACCEPT_TREATY,
                    treaty_id=PEACE,
                    message_id=EntityId(f"message:{report.civilization_id}:accept:{report.day}"),
                    ambassador_id=report.person_ids[-1],
                    recipient_civilization_id=self.enemy,
                    message_text="Accepted.",
                    route=self.route,
                )
            )
        return _envelope(report, orders)


class Vanquished:
    """Sue for peace at the second council, giving up the colony."""

    def __init__(self, enemy: EntityId, route: tuple[HexCoord, ...], colony: EntityId) -> None:
        self.enemy = enemy
        self.route = route
        self.colony = colony

    def decide(self, report: CouncilReport) -> CommandEnvelope:
        orders = (
            [
                DirectOrder(
                    command_id="sue",
                    kind=DirectOrderKind.OFFER_TREATY,
                    treaty_id=PEACE,
                    treaty_kind=TreatyKind.PEACE,
                    peace_terms=PeaceTerms(truce_days=90, ceded_settlement=self.colony),
                    message_id=EntityId(f"message:{report.civilization_id}:peace"),
                    ambassador_id=report.person_ids[0],
                    recipient_civilization_id=self.enemy,
                    message_text="Take the colony, and leave us in peace.",
                    route=self.route,
                )
            ]
            if report.day == 30
            else []
        )
        return _envelope(report, orders)


def _envelope(report: CouncilReport, orders: list[DirectOrder]) -> CommandEnvelope:
    return CommandEnvelope(
        schema_version=1,
        civilization_id=report.civilization_id,
        council_day=report.day,
        correlation_id=report.report_id,
        commands=tuple(orders),
    )


def _prepare(seed: int) -> tuple[WorldState, EntityId, EntityId, tuple[HexCoord, ...], EntityId]:
    state, first, second, route = treaty_world(seed=seed, distance=4 + seed % 3)
    loser = state.civilizations[second]
    # A colony off to the side of the loser's capital, with a handful of settlers.
    tile = next(
        coord
        for coord in sorted(
            HexCoord(route[-1].q + dq, route[-1].r + dr)
            for dq in range(-4, 5)
            for dr in range(-4, 5)
        )
        if coord.distance(route[-1]) == 3
        and coord not in route
        and state.world_map.contains(coord)
        and state.world_map.tile(coord).terrain is not Terrain.WATER
    )
    colony = Settlement(
        settlement_id=EntityId(f"settlement:{second.rsplit(':', 1)[-1]}-0002"),
        civilization_id=second,
        tile=tile,
        founded_day=0,
    )
    loser.settlements = (*loser.settlements, colony)
    for person_id in loser.population.living_ids[-(4 + seed % 4) :]:
        loser.population.people[person_id].location = tile
    loser.stores = {
        colony.settlement_id: Inventory(capacity=2_000, quantities={Resource.FOOD: 400})
    }
    return state, first, second, route, colony.settlement_id


def _simulate(initial: WorldState, first, second, route, colony):
    state = initial.model_copy(deep=True)
    sovereigns = {
        first: Victor(second, route),
        second: Vanquished(first, tuple(reversed(route)), colony),
    }
    rng = StableRng(state.config.seed)
    kinds: list[str] = []
    for _ in range(DAYS):
        transition = advance_day(state, rng, sovereigns=sovereigns)
        state = transition.state
        kinds.extend(event.kind for event in transition.events.events)
        seen: set[EntityId] = set()
        for civilization_id, civilization in state.civilizations.items():
            for person_id, person in civilization.population.people.items():
                assert person_id not in seen, "a person belongs to one civilization"
                seen.add(person_id)
                assert person.civilization_id == civilization_id
                if person.allegiances:
                    assert person.allegiances[-1].to_civilization_id == civilization_id
    return state, kinds


@pytest.mark.soak
@pytest.mark.parametrize("seed", range(6))
def test_seeded_cessions_move_people_whole_and_replay_exactly(seed: int) -> None:
    initial, first, second, route, colony = _prepare(seed)

    final, kinds = _simulate(initial, first, second, route, colony)
    rerun, rerun_kinds = _simulate(initial, first, second, route, colony)

    assert "peace_made" in kinds and "settlement_ceded" in kinds
    assert any(item.settlement_id == colony for item in final.civilizations[first].settlements)
    assert not any(item.settlement_id == colony for item in final.civilizations[second].settlements)
    assert state_hash(rerun) == state_hash(final)
    assert rerun_kinds == kinds
