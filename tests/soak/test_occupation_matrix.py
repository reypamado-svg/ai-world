"""Seeded occupations: road-wrecking war parties take a settlement, live off it, burn a
storehouse and go home, with daily invariants."""

import pytest
from logistics_helpers import treaty_world

from sovereign_world.commands import CommandEnvelope, CouncilReport, DirectOrder, DirectOrderKind
from sovereign_world.engine import advance_day
from sovereign_world.hexmap import HexCoord
from sovereign_world.ids import EntityId
from sovereign_world.resources import Resource
from sovereign_world.rng import StableRng
from sovereign_world.roads import Road, RoadGrade
from sovereign_world.state import WorldState, state_hash, validate_world
from sovereign_world.war import WarObjective

DAYS = 110


class OccupyingSovereign:
    """Occupy once the land has settled, burn a storehouse a council later, then withdraw."""

    def __init__(self, enemy: EntityId, route: tuple[HexCoord, ...]) -> None:
        self.enemy = enemy
        self.route = route

    def decide(self, report: CouncilReport) -> CommandEnvelope:
        journey_id = EntityId(f"journey:{report.civilization_id}:occupy")
        held = [item for item in report.occupations if item.active]
        orders: list[DirectOrder] = []
        if report.day == 30:
            orders.append(
                DirectOrder(
                    command_id="occupy",
                    kind=DirectOrderKind.SEND_WAR_PARTY,
                    journey_id=journey_id,
                    recipient_civilization_id=self.enemy,
                    traveller_ids=report.person_ids[:12],
                    route=self.route,
                    cargo={Resource.AXE: 8},
                    war_objective=WarObjective.OCCUPY,
                    wreck_roads=True,
                    extra_provisions=60,
                )
            )
        elif held and report.day == 60:
            orders.append(
                DirectOrder(
                    command_id="burn",
                    kind=DirectOrderKind.BURN_STOREHOUSE,
                    journey_id=journey_id,
                    storehouse_id=EntityId(f"storehouse:{held[0].settlement_id}:01"),
                )
            )
        elif held:
            orders.append(
                DirectOrder(
                    command_id="home", kind=DirectOrderKind.LIFT_SIEGE, journey_id=journey_id
                )
            )
        return CommandEnvelope(
            schema_version=1,
            civilization_id=report.civilization_id,
            council_day=report.day,
            correlation_id=report.report_id,
            commands=tuple(orders),
        )


def _prepare(seed: int) -> tuple[WorldState, EntityId, EntityId, tuple[HexCoord, ...]]:
    state, first, second, route = treaty_world(seed=seed, distance=3 + seed % 3)
    attacker = state.civilizations[first]
    attacker.inventory = attacker.inventory.model_copy(
        update={"quantities": {**attacker.inventory.quantities, Resource.AXE: 8}}
    )
    # Most of the defenders are out in the fields; a few hold the settlement.
    people = state.civilizations[second].population.people
    for person_id in sorted(people)[3 + seed % 4 :]:
        people[person_id].location = HexCoord(route[-1].q, route[-1].r - 3)
    state.roads = tuple(
        Road(tile=tile, grade=RoadGrade.TRACK, civilization_id=second, built_day=0, graded_day=0)
        for tile in sorted(route[1:-1])
    )
    return state, first, second, route


def _simulate(initial: WorldState, first: EntityId, second: EntityId, route):
    state = initial.model_copy(deep=True)
    sovereigns = {first: OccupyingSovereign(second, route)}
    rng = StableRng(state.config.seed)
    kinds: list[str] = []
    for _ in range(DAYS):
        held = {
            occupation.tile
            for occupation in state.occupations
            if occupation.active and occupation.owner_id == second
        }
        transition = advance_day(state, rng, sovereigns=sovereigns)
        state = transition.state
        kinds.extend(event.kind for event in transition.events.events)
        validate_world(state)
        for journey in state.journeys:
            if journey.sender_civilization_id == second and journey.departed_day == state.day - 1:
                assert journey.route[0] not in held, "nobody leaves an occupied settlement"
            if journey.encamped:
                assert journey.route_index == len(journey.route) - 1, "occupiers stay put"
        for civilization in state.civilizations.values():
            for inventory in (civilization.inventory, *civilization.stores.values()):
                assert inventory.total_units <= inventory.capacity
    return state, kinds


@pytest.mark.soak
@pytest.mark.parametrize("seed", range(8))
def test_seeded_occupations_hold_their_invariants_and_replay_exactly(seed: int) -> None:
    initial, first, second, route = _prepare(seed)

    final, kinds = _simulate(initial, first, second, route)
    rerun, rerun_kinds = _simulate(initial, first, second, route)

    assert kinds.count("settlement_occupied") == 1
    assert kinds.count("storehouse_burned") == 1
    [occupation] = final.occupations
    assert occupation.end is not None, "the occupiers withdrew"
    wrecked = {tile for journey in final.journeys for tile in journey.wrecked}
    assert kinds.count("road_wrecked") == len(wrecked), "each road tile is wrecked once"
    assert state_hash(rerun) == state_hash(final)
    assert rerun_kinds == kinds
