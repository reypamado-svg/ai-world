from dataclasses import replace

from logistics_helpers import OneShotSovereign, clear_journey_id, envelope, linked_world

from sovereign_world.commands import (
    DirectOrder,
    DirectOrderKind,
    build_council_report,
    validate_envelope,
)
from sovereign_world.engine import TransitionResult, advance_day
from sovereign_world.exploration import Expedition, Observation
from sovereign_world.hexmap import HexCoord, Terrain, Tile, WorldMap
from sovereign_world.ids import EntityId
from sovereign_world.logistics import (
    Journey,
    JourneyKind,
    JourneyOutcome,
    NoticeKind,
    advance_journeys_day,
)
from sovereign_world.resources import Resource
from sovereign_world.rng import StableRng
from sovereign_world.roads import GRADES, Road, RoadGrade, grades_of
from sovereign_world.state import WorldState, validate_world
from sovereign_world.territory import (
    HeldControl,
    Settlement,
    Territory,
    TileOwner,
    advance_territory,
    influence_field,
)
from sovereign_world.travel import entry_cost

CREW = 8


def _world(distance: int = 6):
    _, state, home, rival, route = linked_world(distance=distance)
    return state, home, rival, route


def _run(
    state: WorldState, days: int, sovereigns=None
) -> tuple[WorldState, list[TransitionResult]]:
    rng = StableRng(state.config.seed)
    results: list[TransitionResult] = []
    for _ in range(days):
        result = advance_day(state, rng, sovereigns=sovereigns)
        state = result.state
        results.append(result)
    return state, results


def _events(results: list[TransitionResult], kind: str):
    return [event for result in results for event in result.events.events if event.kind == kind]


def _road_order(
    state: WorldState,
    civilization_id: EntityId,
    route: tuple[HexCoord, ...],
    grade: RoadGrade,
    journey: str,
    *,
    crew: int = CREW,
) -> DirectOrder:
    return DirectOrder(
        command_id=f"road:{journey}",
        kind=DirectOrderKind.BUILD_ROAD,
        journey_id=EntityId(journey),
        traveller_ids=state.civilizations[civilization_id].population.living_ids[-crew:],
        route=route,
        road_grade=grade,
    )


def _grades(state: WorldState) -> dict[HexCoord, RoadGrade]:
    return grades_of(state.roads)


def _lay(state: WorldState, civilization_id: EntityId, grade: RoadGrade, *tiles: HexCoord) -> None:
    roads = {road.tile: road for road in state.roads}
    for tile in tiles:
        roads[tile] = Road(
            tile=tile, grade=grade, civilization_id=civilization_id, built_day=0, graded_day=0
        )
    state.roads = tuple(roads[tile] for tile in sorted(roads))


def _plain(width: int = 13, height: int = 5) -> WorldMap:
    tiles = [
        Tile(HexCoord(q, r), Terrain.GRASSLAND, 500, 500, 500, 500, 0, 0, 0)
        for r in range(height)
        for q in range(width)
    ]
    return WorldMap(width=width, height=height, tiles=tuple(tiles))


def test_each_grade_makes_a_tile_quicker_to_cross() -> None:
    state, _, _, route = _world()
    tile = route[1]
    expected = {
        Terrain.GRASSLAND: (10, 9, 8, 7, 6, 5, 4),
        Terrain.FOREST: (15, 13, 11, 9, 8, 6, 5),
        Terrain.MOUNTAIN: (30, 26, 22, 18, 15, 12, 10),
    }
    for terrain, costs in expected.items():
        state.world_map = replace(
            state.world_map,
            tiles=tuple(
                replace(item, terrain=terrain) if item.coord == tile else item
                for item in state.world_map.tiles
            ),
        )
        assert entry_cost(state.world_map, tile) == costs[0]
        for grade, cost in zip(GRADES, costs[1:], strict=True):
            assert entry_cost(state.world_map, tile, {tile: grade}) == cost
    water = next(item.coord for item in state.world_map.tiles if item.terrain is Terrain.WATER)
    assert entry_cost(state.world_map, water, {water: RoadGrade.HIGHWAY}) is None


def test_a_crew_builds_tile_by_tile_and_a_part_built_road_already_helps() -> None:
    state, home, _, route = _world()
    order = _road_order(
        state, home, route[:4], RoadGrade.FOOTPATH, clear_journey_id("path", days=20)
    )
    state, results = _run(state, 3, {home: OneShotSovereign(order)})

    assert _events(results, "road_crew_dispatched")
    part_built = _grades(state)
    assert part_built and set(part_built) < set(route[:4]), "some tiles done, not all"
    assert entry_cost(state.world_map, route[0], part_built) == 9

    state, more = _run(state, 17)
    results += more
    assert _grades(state) == dict.fromkeys(route[:4], RoadGrade.FOOTPATH)
    assert [event.payload["grade"] for event in _events(results, "road_built")] == ["footpath"] * 4
    [journey] = state.journeys
    assert journey.outcome is JourneyOutcome.DELIVERED
    assert _events(results, "road_crew_returned")
    people = state.civilizations[home].population.people
    assert all(people[person_id].location == route[0] for person_id in journey.traveller_ids)
    validate_world(state)


def test_upgrades_pass_through_every_grade_and_spend_their_materials() -> None:
    state, home, rival, route = _world()
    _lay(state, rival, RoadGrade.TRACK, route[1])
    _lay(state, rival, RoadGrade.PAVED, route[2])
    before = dict(state.civilizations[home].inventory.quantities)
    order = _road_order(
        state, home, route[:3], RoadGrade.GRAVEL, clear_journey_id("gravel", days=30)
    )
    state, results = _run(state, 30, {home: OneShotSovereign(order)})

    built = [
        (HexCoord(int(event.payload["q"]), int(event.payload["r"])), event.payload["grade"])
        for event in _events(results, "road_built")
    ]
    assert [grade for tile, grade in built if tile == route[0]] == [
        "footpath",
        "track",
        "graded",
        "gravel",
    ]
    assert [grade for tile, grade in built if tile == route[1]] == ["graded", "gravel"]
    assert all(tile != route[2] for tile, _ in built), "a better road is walked over"
    assert _grades(state)[route[2]] is RoadGrade.PAVED
    road = next(item for item in state.roads if item.tile == route[1])
    assert road.civilization_id == home, "the last crew to work a road is recorded"
    assert road.built_day == 0 < road.graded_day

    [journey] = state.journeys
    assert journey.outcome is JourneyOutcome.DELIVERED and not journey.active
    after = state.civilizations[home].inventory.quantities
    assert before[Resource.STONE] - after[Resource.STONE] == 10
    assert before[Resource.TIMBER] - after[Resource.TIMBER] == 6
    validate_world(state)


def test_paving_needs_a_living_stoneworker_and_unused_stone_comes_home() -> None:
    state, home, _, route = _world()
    civilization = state.civilizations[home]
    crew = civilization.population.living_ids[-CREW:]
    mason = civilization.population.people[crew[0]]
    mason.skills = {**mason.skills, "stoneworking": 300}
    before = dict(civilization.inventory.quantities)

    without = _road_order(state, home, route[:2], RoadGrade.PAVED, "journey:none", crew=CREW - 1)
    errors = validate_envelope(envelope(state, home, without), state).errors
    assert [error.code for error in errors] == ["no_stoneworker"]

    order = _road_order(state, home, route[:2], RoadGrade.PAVED, clear_journey_id("pave", days=20))
    assert validate_envelope(envelope(state, home, order), state).errors == ()
    state, results = _run(state, 1, {home: OneShotSovereign(order)})
    [journey] = state.journeys
    assert journey.materials == {Resource.STONE: 2 * (5 + 15), Resource.TIMBER: 2 * 3}
    mason = state.civilizations[home].population.people[crew[0]]
    mason.alive = False
    mason.death_day = state.day

    state, more = _run(state, 20)
    results += more

    [halt] = _events(results, "road_work_stopped")
    assert halt.payload["reason"] == "no_stoneworker"
    assert _grades(state) == {route[0]: RoadGrade.GRAVEL}
    [journey] = state.journeys
    assert journey.outcome is JourneyOutcome.STOPPED and not journey.active
    after = state.civilizations[home].inventory.quantities
    assert before[Resource.STONE] - after[Resource.STONE] == 5, "only one gravel bed laid"
    assert before[Resource.TIMBER] - after[Resource.TIMBER] == 3
    returned = next(
        item
        for item in state.civilizations[home].logistics_notices
        if item.kind is NoticeKind.PARTY_RETURNED
    )
    assert returned.reported_outcome is JourneyOutcome.STOPPED
    assert returned.cargo[Resource.STONE] == 35


def test_a_crew_stops_at_land_that_turned_foreign_and_walks_home() -> None:
    state, home, rival, route = _world()
    state.territory = Territory(
        held=(HeldControl(tile=route[2], civilization_id=rival, value=60),),
        owners=(TileOwner(tile=route[2], civilization_id=rival, since_day=0),),
    )
    order = _road_order(state, home, route[:4], RoadGrade.FOOTPATH, clear_journey_id("x", days=12))
    assert validate_envelope(envelope(state, home, order), state).errors == (), (
        "the civilization has not seen who owns the land"
    )
    state, results = _run(state, 12, {home: OneShotSovereign(order)})

    [halt] = _events(results, "road_work_stopped")
    assert halt.payload["reason"] == "foreign_land"
    assert set(_grades(state)) == {route[0], route[1]}
    [journey] = state.journeys
    assert journey.outcome is JourneyOutcome.STOPPED and not journey.active
    people = state.civilizations[home].population.people
    assert all(people[person_id].location == route[0] for person_id in journey.traveller_ids)


def test_a_crew_turns_home_while_it_still_has_food_for_the_walk() -> None:
    state, home, _, route = _world()
    order = _road_order(
        state, home, route, RoadGrade.GRAVEL, clear_journey_id("long", days=60), crew=1
    )
    state, results = _run(state, 60, {home: OneShotSovereign(order)})

    [journey] = state.journeys
    assert journey.provisions_packed == 50, "one bearer carries at most 50 days of food"
    assert journey.outcome is JourneyOutcome.STOPPED and not journey.active
    assert [event.payload["reason"] for event in _events(results, "road_work_stopped")] == [
        "provisions"
    ]
    [worker] = journey.traveller_ids
    person = state.civilizations[home].population.people[worker]
    assert person.alive and person.location == route[0]
    assert _grades(state), "the crew built something before it turned back"


def test_road_orders_are_checked_against_what_the_civilization_knows() -> None:
    state, home, rival, route = _world()

    def codes(order: DirectOrder) -> list[str]:
        errors = validate_envelope(envelope(state, home, order), state).errors
        return [error.code for error in errors]

    assert codes(_road_order(state, home, route[:3], RoadGrade.TRACK, "j:fine")) == []
    no_grade = _road_order(state, home, route[:3], RoadGrade.TRACK, "j:grade").model_copy(
        update={"road_grade": None}
    )
    assert codes(no_grade) == ["invalid_road"]
    assert codes(_road_order(state, home, route[1:4], RoadGrade.TRACK, "j:away")) == [
        "invalid_route"
    ], "a crew sets out from one of its own settlements"

    civilization = state.civilizations[home]
    civilization.inventory = civilization.inventory.model_copy(
        update={"quantities": {**civilization.inventory.quantities, Resource.STONE: 9}}
    )
    assert codes(_road_order(state, home, route[:2], RoadGrade.GRAVEL, "j:stone")) == [
        "insufficient_materials"
    ]

    civilization.observations = tuple(
        observation.model_copy(update={"observed_owner": rival})
        if observation.tile == route[2]
        else observation
        for observation in civilization.observations
    )
    assert codes(_road_order(state, home, route[:3], RoadGrade.TRACK, "j:foreign")) == [
        "foreign_land"
    ]


def test_every_traveller_moves_faster_on_any_road() -> None:
    def arrival_day(roads: bool) -> int:
        state, home, rival, route = _world()
        if roads:
            _lay(state, rival, RoadGrade.PAVED, *route[1:])
        colony = Settlement(
            settlement_id=EntityId(f"settlement:{home.rsplit(':', 1)[-1]}-0002"),
            civilization_id=home,
            tile=route[5],
            founded_day=0,
        )
        state.civilizations[home].settlements = (*state.civilizations[home].settlements, colony)
        order = DirectOrder(
            command_id="move",
            kind=DirectOrderKind.RELOCATE_GROUP,
            journey_id=EntityId(clear_journey_id("move", days=8)),
            traveller_ids=state.civilizations[home].population.living_ids[-2:],
            route=route[:6],
        )
        state, results = _run(state, 8, {home: OneShotSovereign(order)})
        [event] = _events(results, "group_relocated")
        return event.day

    assert arrival_day(roads=False) == 4
    assert arrival_day(roads=True) < 4, "another civilization's road speeds this party"


def test_influence_reaches_further_along_a_road() -> None:
    world = _plain()
    source = [(HexCoord(1, 2), 80)]
    road = {HexCoord(q, 2): RoadGrade.PAVED for q in range(2, 13)}

    bare = influence_field(world, source)
    paved = influence_field(world, source, road)
    assert bare[HexCoord(5, 2)] == 40 and bare.get(HexCoord(6, 2), 0) < 40
    assert paved[HexCoord(9, 2)] == 40, "eight paved tiles cost as much as four bare ones"

    town = Settlement(
        settlement_id=EntityId("settlement:1-1"),
        civilization_id=EntityId("civilization:0000000001"),
        tile=HexCoord(1, 2),
        founded_day=0,
        capital=True,
    )
    territory = Territory()
    for day in range(30):
        territory = advance_territory(
            territory, world, [town], {town.settlement_id: 32}, day, roads=road
        ).territory
    owned = territory.owner_of()
    assert HexCoord(9, 2) in owned, "territory follows the road"
    assert max(tile.q for tile in owned if tile.r == 0) < 9, "and thins away from it"


def test_reports_show_known_roads_at_the_grade_last_seen() -> None:
    state, home, rival, route = _world()
    far = route[-2]
    _lay(state, rival, RoadGrade.PAVED, far, route[1])

    known = {view.tile: view for view in build_council_report(state, home).known_roads}
    assert far not in known, "an unseen road is unknown"
    assert known[route[1]].grade is RoadGrade.PAVED, "a road in sight of a settlement is seen"

    civilization = state.civilizations[home]
    observations = {item.tile: item for item in civilization.observations}
    observations[far] = Observation(
        tile=far,
        observed_day=0,
        observer_id=civilization.population.living_ids[0],
        observed_road=RoadGrade.TRACK,
    )
    civilization.observations = tuple(observations[tile] for tile in sorted(observations))
    known = {view.tile: view for view in build_council_report(state, home).known_roads}
    assert known[far].grade is RoadGrade.TRACK, "a later upgrade stays unknown until seen"
    assert known[far].as_of_day == 0


def test_explorers_cover_more_ground_on_roads_and_note_their_grade() -> None:
    state, home, rival, route = _world()
    _lay(state, rival, RoadGrade.PAVED, *route[1:4])
    explorer = state.civilizations[home].population.living_ids[-1]
    state.civilizations[home].expeditions = (
        Expedition(
            expedition_id=EntityId("expedition:road"),
            explorer_ids=(explorer,),
            route=route[:5],
        ),
    )

    state, _ = _run(state, 1)

    assert state.civilizations[home].population.people[explorer].location == route[2], (
        "two paved tiles in one day"
    )
    seen = {item.tile: item for item in state.civilizations[home].observations}
    assert seen[route[2]].observed_road is RoadGrade.PAVED
    assert seen[route[2]].observed_day == 0
    known = {view.tile: view.grade for view in build_council_report(state, home).known_roads}
    assert known[route[2]] is RoadGrade.PAVED


def test_labour_for_a_grade_another_crew_finished_is_not_carried_over() -> None:
    state, home, _, route = _world()
    people = state.civilizations[home].population.living_ids

    def crew(prefix: str, members: tuple[EntityId, ...], work_done: int) -> Journey:
        return Journey(
            journey_id=EntityId(clear_journey_id(prefix, days=1)),
            kind=JourneyKind.ROADWORK,
            sender_civilization_id=home,
            recipient_civilization_id=home,
            traveller_ids=tuple(sorted(members)),
            route=route[:2],
            provisions_packed=40 * len(members),
            provisions=40 * len(members),
            departed_day=0,
            road_grade=RoadGrade.TRACK,
            work_done=work_done,
            work_grade=RoadGrade.FOOTPATH,
        )

    first = crew("a", people[-8:], 9)
    second = crew("b", people[:1], 9)
    result = advance_journeys_day(
        (first, second),
        {home: state.civilizations[home].population.people},
        day=0,
        rng=StableRng(state.config.seed),
        treaties_in_force=frozenset(),
        world_map=state.world_map,
    )

    assert [(built.journey_id, built.grade) for built in result.roads_built] == [
        (first.journey_id, RoadGrade.FOOTPATH)
    ]
    after = {journey.journey_id: journey for journey in result.journeys}
    assert after[first.journey_id].work_grade is RoadGrade.TRACK
    assert after[first.journey_id].work_done == 7, "the finishing crew's overflow counts on"
    assert after[second.journey_id].work_grade is RoadGrade.TRACK
    assert after[second.journey_id].work_done == 1, "work on the finished footpath is spent"
