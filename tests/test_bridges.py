"""Road crews bridge the rivers on their way; any traveller then crosses at plain cost."""

import json
from math import ceil

import pytest
from logistics_helpers import (
    OneShotSovereign,
    clear_journey_id,
    envelope,
    flatten,
    linked_world,
    river,
    treaty_world,
)

from sovereign_world.bridges import BRIDGE_LABOUR, Bridge, bridge_materials, bridged_edges
from sovereign_world.commands import (
    DirectOrder,
    DirectOrderKind,
    build_council_report,
    validate_envelope,
)
from sovereign_world.diplomacy import TreatyKind
from sovereign_world.engine import TransitionResult, advance_day
from sovereign_world.hexmap import HexCoord, edge_key
from sovereign_world.ids import EntityId
from sovereign_world.logistics import JourneyOutcome, JourneyPhase, roadwork_days
from sovereign_world.resources import Resource
from sovereign_world.rng import StableRng
from sovereign_world.roads import RoadGrade
from sovereign_world.state import WorldState, state_hash, validate_world
from sovereign_world.travel import DEEP_FLOW, STREAM_FLOW, entry_cost, passable, travel_days, way_to

CREW = 8


def _world() -> tuple[WorldState, EntityId, EntityId, tuple[HexCoord, ...]]:
    _, state, home, rival, route = linked_world(distance=4)
    flatten(state, route)
    return state, home, rival, route


def _road_order(
    state: WorldState,
    home: EntityId,
    route: tuple[HexCoord, ...],
    grade: RoadGrade,
    journey: str,
    crew: int = CREW,
) -> DirectOrder:
    return DirectOrder(
        command_id=f"road:{journey}",
        kind=DirectOrderKind.BUILD_ROAD,
        journey_id=EntityId(journey),
        traveller_ids=state.civilizations[home].population.living_ids[-crew:],
        route=route,
        road_grade=grade,
    )


def _run(state: WorldState, days: int, sovereigns=None):
    rng = StableRng(state.config.seed)
    results: list[TransitionResult] = []
    for _ in range(days):
        result = advance_day(state, rng, sovereigns=sovereigns)
        state = result.state
        results.append(result)
    return state, results


def _events(results, kind: str | None = None):
    return [
        event
        for result in results
        for event in result.events.events
        if kind is None or event.kind == kind
    ]


def test_a_bridge_crosses_a_border_at_plain_cost() -> None:
    state, _, _, route = _world()
    edge = edge_key(route[0], route[1])
    for flow in (1, STREAM_FLOW, DEEP_FLOW):
        flatten(state, route)
        river(state, route[0], route[1], flow)
        assert entry_cost(state.world_map, route[1], origin=route[0], bridges={edge}) == 10
        assert passable(state.world_map, route[1:], start=route[0], bridges={edge})
        assert travel_days(state.world_map, route[1:], start=route[0], bridges={edge}) == 4
    way = way_to(state.world_map, route[0], frozenset({route[1]}), bridges={edge})
    assert way == route[:2], "straight over the bridged deep river"


def test_a_graded_crew_bridges_a_stream_before_crossing() -> None:
    state, home, _, route = _world()
    river(state, route[0], route[1], 1)
    before = dict(state.civilizations[home].inventory.quantities)
    order = _road_order(state, home, route[:2], RoadGrade.GRADED, clear_journey_id("span", days=40))
    assert validate_envelope(envelope(state, home, order), state).errors == ()

    state, results = _run(state, 40, {home: OneShotSovereign(order)})

    # The crew grades its first tile, bridges the stream from that bank, then grades the next.
    order_of = [
        (event.kind, HexCoord(event.payload["q"], event.payload["r"]))
        for event in _events(results)
        if event.kind in {"road_built", "bridge_built"}
    ]
    a, b = edge_key(route[0], route[1])
    bridged = order_of.index(("bridge_built", a))
    assert {tile for kind, tile in order_of[:bridged]} == {route[0]}
    assert {tile for kind, tile in order_of[bridged + 1 :]} == {route[1]}
    [bridge_event] = _events(results, "bridge_built")
    assert bridge_event.payload["depth"] == "stream"
    assert (bridge_event.payload["across_q"], bridge_event.payload["across_r"]) == (b.q, b.r)
    assert state.bridges == (
        Bridge(
            a=a,
            b=b,
            civilization_id=home,
            built_day=state.bridges[0].built_day,
        ),
    )
    [journey] = state.journeys
    assert journey.outcome is JourneyOutcome.DELIVERED
    after = state.civilizations[home].inventory.quantities
    assert before[Resource.TIMBER] - after[Resource.TIMBER] == 2 * 3 + 20
    validate_world(state)


def test_a_deep_river_needs_a_stoneworker_and_stone() -> None:
    state, home, _, route = _world()
    river(state, route[0], route[1], DEEP_FLOW)
    civilization = state.civilizations[home]
    crew = civilization.population.living_ids[-CREW:]
    for person_id in crew:
        person = civilization.population.people[person_id]
        person.skills = {
            key: value for key, value in person.skills.items() if key != "stoneworking"
        }

    without = _road_order(state, home, route[:2], RoadGrade.GRADED, "journey:nomason")
    errors = validate_envelope(envelope(state, home, without), state).errors
    assert [error.code for error in errors] == ["no_stoneworker"]

    mason = civilization.population.people[crew[0]]
    mason.skills = {**mason.skills, "stoneworking": 300}
    order = _road_order(state, home, route[:2], RoadGrade.GRADED, clear_journey_id("deep", days=60))
    assert validate_envelope(envelope(state, home, order), state).errors == ()
    state, results = _run(state, 1, {home: OneShotSovereign(order)})
    [journey] = state.journeys
    assert journey.materials == {Resource.STONE: 20, Resource.TIMBER: 2 * 3 + 40}

    state, more = _run(state, 59)
    results += more
    [bridge_event] = _events(results, "bridge_built")
    assert bridge_event.payload["depth"] == "deep"
    validate_world(state)

    # Anyone may now cross where no one could wade.
    assert passable(
        state.world_map, route[1:], start=route[0], bridges=bridged_edges(state.bridges)
    )
    assert not passable(state.world_map, route[1:], start=route[0])


def test_a_track_crew_wades_streams_and_is_refused_at_deep_rivers() -> None:
    state, home, _, route = _world()
    river(state, route[0], route[1], 1)
    order = _road_order(state, home, route[:2], RoadGrade.TRACK, clear_journey_id("wade", days=20))
    assert validate_envelope(envelope(state, home, order), state).errors == ()
    state, results = _run(state, 20, {home: OneShotSovereign(order)})
    assert _events(results, "bridge_built") == []
    assert state.bridges == ()

    state, home, _, route = _world()
    river(state, route[0], route[1], DEEP_FLOW)
    order = _road_order(state, home, route[:2], RoadGrade.TRACK, "journey:track-deep")
    errors = validate_envelope(envelope(state, home, order), state).errors
    assert [error.code for error in errors] == ["invalid_route"]


def test_estimates_count_bridge_labour_and_materials() -> None:
    state, _, _, route = _world()
    plain = roadwork_days(state.world_map, route[:3], RoadGrade.GRADED, {}, CREW)
    river(state, route[1], route[2], DEEP_FLOW)
    spanned = roadwork_days(state.world_map, route[:3], RoadGrade.GRADED, {}, CREW)
    assert spanned == plain + ceil(BRIDGE_LABOUR["deep"] / CREW)
    assert bridge_materials(state.world_map, route[:3], RoadGrade.GRADED, frozenset()) == {
        Resource.STONE: 20,
        Resource.TIMBER: 40,
    }
    assert bridge_materials(state.world_map, route[:3], RoadGrade.TRACK, frozenset()) == {}


def test_bridges_are_left_out_of_saves_until_one_stands() -> None:
    state, home, _, route = _world()
    dumped = state.model_dump(mode="json")
    assert "bridges" not in dumped
    assert state_hash(WorldState.model_validate_json(json.dumps(dumped))) == state_hash(state)

    river(state, route[0], route[1], 1)
    state, _ = _run(state, 1)  # a day settles the fixture's hand-made observations
    a, b = edge_key(route[0], route[1])
    state.bridges = (Bridge(a=a, b=b, civilization_id=home, built_day=0),)
    validate_world(state)
    reloaded = WorldState.model_validate_json(state.model_dump_json())
    assert reloaded.bridges == state.bridges
    assert state_hash(reloaded) == state_hash(state)

    c, d = edge_key(route[1], route[2])
    state.bridges = (*state.bridges, Bridge(a=c, b=d, civilization_id=home, built_day=0))
    with pytest.raises(ValueError, match="spans a river"):
        validate_world(state)


def test_reports_list_own_and_seen_bridges_only() -> None:
    state, home, rival, route = _world()
    river(state, route[0], route[1], 1)
    a, b = edge_key(route[0], route[1])
    far = max(state.world_map.tiles, key=lambda tile: tile.coord.distance(route[0])).coord
    neighbour = state.world_map.neighbors(far)[0]
    river(state, far, neighbour, 1)
    c, d = edge_key(far, neighbour)
    state.bridges = tuple(
        sorted(
            (
                Bridge(a=a, b=b, civilization_id=home, built_day=0),
                Bridge(a=c, b=d, civilization_id=rival, built_day=0),
            ),
            key=lambda bridge: (bridge.a, bridge.b),
        )
    )

    own = build_council_report(state, home)
    assert [(bridge.a, bridge.b) for bridge in own.known_bridges] == [(a, b)]
    seen_by_rival = build_council_report(state, rival)
    assert (c, d) in {(bridge.a, bridge.b) for bridge in seen_by_rival.known_bridges}


def test_reported_bridges_show_only_where_they_stand() -> None:
    state, home, rival, route = _world()
    river(state, route[0], route[1], 1)
    a, b = edge_key(route[0], route[1])
    state.bridges = (Bridge(a=a, b=b, civilization_id=rival, built_day=3),)

    [view] = build_council_report(state, home).known_bridges

    assert (view.a, view.b) == (a, b)
    assert set(type(view).model_fields) == {"a", "b"}, "no builder or build day is revealed"


def _migration(state: WorldState, home: EntityId, rival: EntityId, route) -> DirectOrder:
    return DirectOrder(
        command_id="migrate:old",
        kind=DirectOrderKind.DISPATCH_MIGRATION,
        journey_id=EntityId(clear_journey_id("old", days=12)),
        treaty_id=EntityId("treaty:migration"),
        recipient_civilization_id=rival,
        traveller_ids=state.civilizations[home].population.living_ids[-2:],
        route=route,
    )


def test_a_party_from_an_old_save_meeting_a_deep_river_turns_home_without_a_crash() -> None:
    state, home, rival, route = treaty_world(TreatyKind.MIGRATION, distance=4)
    flatten(state, route)
    order = _migration(state, home, rival, route)
    assert validate_envelope(envelope(state, home, order), state).errors == ()
    state, _ = _run(state, 1, {home: OneShotSovereign(order)})
    # As if the save were made before deep rivers blocked travel: one now lies ahead.
    river(state, route[2], route[3], DEEP_FLOW)

    state, _ = _run(state, 10)

    [journey] = state.journeys
    assert journey.outcome is JourneyOutcome.TURNED_BACK
    people = state.civilizations[home].population.people
    assert all(people[person].location == route[0] for person in journey.traveller_ids)
    validate_world(state)


def test_an_old_crew_meeting_a_deep_river_stops() -> None:
    state, home, _, route = _world()
    order = _road_order(state, home, route[:4], RoadGrade.FOOTPATH, clear_journey_id("x", days=20))
    assert validate_envelope(envelope(state, home, order), state).errors == ()
    state, _ = _run(state, 1, {home: OneShotSovereign(order)})
    river(state, route[2], route[3], DEEP_FLOW)

    state, results = _run(state, 20)

    reasons = [event.payload["reason"] for event in _events(results, "road_work_stopped")]
    assert reasons == ["river"]


def test_a_party_from_an_old_save_fords_home_across_a_river_it_already_crossed() -> None:
    state, home, rival, route = treaty_world(TreatyKind.TRADE, distance=3)
    flatten(state, route)
    order = DirectOrder(
        command_id="ship:old",
        kind=DirectOrderKind.DISPATCH_SHIPMENT,
        journey_id=EntityId(clear_journey_id("ship", days=16)),
        treaty_id=EntityId("treaty:trade"),
        recipient_civilization_id=rival,
        traveller_ids=state.civilizations[home].population.living_ids[:2],
        route=route,
        cargo={Resource.TIMBER: 10},
    )
    assert validate_envelope(envelope(state, home, order), state).errors == ()
    state, _ = _run(state, 1, {home: OneShotSovereign(order)})
    for _ in range(12):
        [journey] = state.journeys
        if journey.phase is JourneyPhase.RETURNING and journey.route_index >= 2:
            break
        state, _ = _run(state, 1)
    assert journey.phase is JourneyPhase.RETURNING, "the goods are delivered beyond the river"
    # As if the save were made before deep rivers blocked travel: one lies on the way home.
    river(state, route[0], route[1], DEEP_FLOW)

    state, _ = _run(state, 12)

    [journey] = state.journeys
    assert not journey.active
    people = state.civilizations[home].population.people
    assert all(people[person].location == route[0] for person in journey.traveller_ids)
