"""A footpath from the capital is later upgraded to gravel; travel quickens and land follows."""

from pathlib import Path

from logistics_helpers import ScheduledSovereign, linked_world

from sovereign_world.commands import DirectOrder, DirectOrderKind, build_council_report
from sovereign_world.engine import advance_day
from sovereign_world.events import DomainEvent
from sovereign_world.ids import EntityId
from sovereign_world.persistence import WorldStore
from sovereign_world.replay import replay_run, verify_run
from sovereign_world.rng import StableRng
from sovereign_world.roads import RoadGrade, grades_of
from sovereign_world.state import WorldState, state_hash, validate_world
from sovereign_world.travel import entry_cost

DAYS = 120


def _crew_order(state: WorldState, civilization_id, route, grade: RoadGrade, day: int):
    return DirectOrder(
        command_id=f"road:{day}",
        kind=DirectOrderKind.BUILD_ROAD,
        journey_id=EntityId(f"journey:road:{day}"),
        traveller_ids=state.civilizations[civilization_id].population.living_ids[-16:],
        route=route,
        road_grade=grade,
    )


def test_a_road_built_then_upgraded_speeds_travel_and_carries_land(tmp_path: Path) -> None:
    manifest, initial, home, _, route = linked_world(distance=10)
    road = route[:5]

    def run(with_roads: bool, store: WorldStore | None = None):
        state = initial.model_copy(deep=True)
        orders = (
            {
                0: (_crew_order(state, home, road, RoadGrade.FOOTPATH, 0),),
                30: (_crew_order(state, home, road, RoadGrade.GRAVEL, 30),),
            }
            if with_roads
            else {}
        )
        sovereigns = {home: ScheduledSovereign(orders)}
        rng = StableRng(manifest.config.seed)
        events: list[DomainEvent] = []
        costs: dict[int, int] = {}
        for _ in range(DAYS):
            transition = advance_day(state, rng, sovereigns=sovereigns)
            state = transition.state
            if store is not None:
                store.append_transition(state, transition.events)
            events.extend(transition.events.events)
            grades = grades_of(state.roads)
            costs[state.day] = sum(
                entry_cost(state.world_map, tile, grades) or 0 for tile in road[1:]
            )
        return state, events, costs

    store = WorldStore.create(tmp_path / "record", manifest, initial)
    state, events, costs = run(with_roads=True, store=store)
    bare, _, _ = run(with_roads=False)

    built = [event.payload["grade"] for event in events if event.kind == "road_built"]
    assert built.count("footpath") == len(road)
    assert built.count("gravel") == len(road)
    assert grades_of(state.roads) == dict.fromkeys(road, RoadGrade.GRAVEL)
    assert [event.kind for event in events].count("road_crew_returned") == 2

    assert costs[1] == 40, "four grassland tiles, no road yet"
    assert 36 in costs.values(), "the footpath saves a little"
    assert costs[DAYS] == 24, "the gravel road saves much more"
    assert sorted(costs.values(), reverse=True) == [costs[day] for day in sorted(costs)], (
        "travel only ever gets quicker"
    )

    beyond = route[5]
    assert state.territory.owner_of().get(beyond) == home, "land follows the road"
    assert bare.territory.owner_of().get(beyond) != home, "and is not held without it"

    known = {view.tile: view.grade for view in build_council_report(state, home).known_roads}
    assert known == dict.fromkeys(road, RoadGrade.GRAVEL), "the builders know their road"
    validate_world(state)
    assert state_hash(replay_run(store)) == state_hash(state)
    assert verify_run(store).state_hash == state_hash(state)
    rerun, _, _ = run(with_roads=True)
    assert state_hash(rerun) == state_hash(state)
