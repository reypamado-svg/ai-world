"""Rivers along tile borders: wading a stream or river costs time, a deep river blocks."""

from dataclasses import replace
from itertools import pairwise

from logistics_helpers import OneShotSovereign, envelope, treaty_world

from sovereign_world.commands import (
    DirectOrder,
    DirectOrderKind,
    build_council_report,
    validate_envelope,
)
from sovereign_world.diplomacy import TreatyKind
from sovereign_world.engine import advance_day
from sovereign_world.exploration import ExpeditionStatus
from sovereign_world.hexmap import HexCoord, RiverEdge, Terrain, corner_tiles, edge_key
from sovereign_world.ids import EntityId
from sovereign_world.logistics import JourneyKind, journey_days
from sovereign_world.rng import StableRng
from sovereign_world.scripted import BaselineSovereign
from sovereign_world.state import WorldState
from sovereign_world.travel import (
    CROSSING_COST,
    DEEP_FLOW,
    MAX_PROGRESS,
    STREAM_FLOW,
    entry_cost,
    passable,
    travel_days,
    way_to,
)


def _flatten(state: WorldState, around: tuple[HexCoord, ...], reach: int = 2) -> None:
    """Grassland within reach of the given tiles, and no rivers anywhere."""
    near = {
        tile.coord
        for tile in state.world_map.tiles
        if min(tile.coord.distance(spot) for spot in around) <= reach
    }
    state.world_map = replace(
        state.world_map,
        tiles=tuple(
            replace(tile, terrain=Terrain.GRASSLAND) if tile.coord in near else tile
            for tile in state.world_map.tiles
        ),
        rivers=(),
    )


def _river(state: WorldState, first: HexCoord, second: HexCoord, flow: int) -> None:
    a, b = edge_key(first, second)
    edge = RiverEdge(a=a, b=b, flow=flow, downstream=corner_tiles(a, b)[0])
    state.world_map = replace(
        state.world_map, rivers=tuple(sorted((*state.world_map.rivers, edge)))
    )


def test_crossing_a_border_river_costs_by_depth() -> None:
    state, _, _, route = treaty_world()
    _flatten(state, route)
    here, there = route[0], route[1]
    assert entry_cost(state.world_map, there, origin=here) == 10
    for flow, extra in ((1, CROSSING_COST["stream"]), (STREAM_FLOW, CROSSING_COST["river"])):
        _flatten(state, route)
        _river(state, here, there, flow)
        assert entry_cost(state.world_map, there, origin=here) == 10 + extra
        assert entry_cost(state.world_map, here, origin=there) == 10 + extra
    _flatten(state, route)
    _river(state, here, there, DEEP_FLOW)
    assert entry_cost(state.world_map, there, origin=here) is None
    # Without knowing where the traveller comes from, only the tile itself counts.
    assert entry_cost(state.world_map, there) == 10


def test_routes_and_their_days_count_every_river_crossed() -> None:
    state, _, _, route = treaty_world()
    _flatten(state, route)
    _river(state, route[1], route[2], STREAM_FLOW)
    assert travel_days(state.world_map, route[1:], start=route[0]) == 4
    assert journey_days(JourneyKind.MIGRATION, state.world_map, route) == 4
    assert journey_days(JourneyKind.SHIPMENT, state.world_map, route) == 8
    _river(state, route[2], route[3], DEEP_FLOW)
    assert not passable(state.world_map, route[1:], start=route[0])
    assert passable(state.world_map, route[1:3], start=route[0])


def test_unspent_walking_bound_covers_the_dearest_crossing() -> None:
    dearest = 50 + CROSSING_COST["river"]  # snow and a wadeable river
    assert dearest * 3 // 2 < MAX_PROGRESS


def test_the_shortest_way_goes_around_a_deep_river() -> None:
    state, _, _, route = treaty_world()
    _flatten(state, route)
    _river(state, route[0], route[1], DEEP_FLOW)
    way = way_to(state.world_map, route[0], frozenset({route[1]}))
    assert way is not None
    assert len(way) == 3, "one step round the end of the river border"
    assert all(
        state.world_map.river_between(first, second) is None for first, second in pairwise(way)
    )


def test_journeys_across_a_known_deep_river_are_refused() -> None:
    state, sender, recipient, route = treaty_world(TreatyKind.MIGRATION)
    _flatten(state, route)
    _river(state, route[1], route[2], DEEP_FLOW)
    order = DirectOrder(
        command_id="migrate:deep",
        kind=DirectOrderKind.DISPATCH_MIGRATION,
        journey_id=EntityId("journey:deep"),
        treaty_id=EntityId("treaty:migration"),
        recipient_civilization_id=recipient,
        traveller_ids=state.civilizations[sender].population.living_ids[-2:],
        route=route,
    )
    result = validate_envelope(envelope(state, sender, order), state)
    assert [error.code for error in result.errors] == ["invalid_route"]


def _survey(state: WorldState, sender: EntityId, route: tuple[HexCoord, ...]) -> DirectOrder:
    return DirectOrder(
        command_id="explore",
        kind=DirectOrderKind.START_EXPEDITION,
        expedition_id=EntityId("expedition:river"),
        explorer_ids=(state.civilizations[sender].population.living_ids[0],),
        route=route,
    )


def _line_out(state: WorldState, sender: EntityId) -> tuple[list[HexCoord], HexCoord]:
    """A straight line from home out of the known area, and its first unknown tile."""
    civilization = state.civilizations[sender]
    home = civilization.start_center
    beyond = [HexCoord(home.q, home.r + offset) for offset in range(0, 7)]
    if not all(state.world_map.contains(tile) for tile in beyond):
        beyond = [HexCoord(home.q, home.r - offset) for offset in range(0, 7)]
    first_unknown = next(tile for tile in beyond if tile not in civilization.known_tiles)
    return beyond, first_unknown


def test_a_known_deep_river_refuses_an_expedition_but_an_unknown_one_does_not() -> None:
    state, sender, _, _ = treaty_world()
    beyond, first_unknown = _line_out(state, sender)
    route = tuple(beyond[: beyond.index(first_unknown) + 1])
    _flatten(state, route)
    # Between two known tiles: the civilization has seen this river.
    _river(state, route[0], route[1], DEEP_FLOW)
    result = validate_envelope(envelope(state, sender, _survey(state, sender, route)), state)
    assert [error.code for error in result.errors] == ["invalid_route"]

    _flatten(state, route)
    # Between two unknown tiles: refusing would reveal it.
    further = HexCoord(first_unknown.q, first_unknown.r + (route[1].r - route[0].r))
    unknown_route = (*route, further)
    _flatten(state, unknown_route)
    _river(state, first_unknown, further, DEEP_FLOW)
    assert first_unknown not in state.civilizations[sender].known_tiles
    result = validate_envelope(
        envelope(state, sender, _survey(state, sender, unknown_route)), state
    )
    assert result.errors == ()


def test_explorers_stop_at_an_unknown_deep_river() -> None:
    state, sender, _, _ = treaty_world()
    beyond, first_unknown = _line_out(state, sender)
    # The river runs between two tiles nobody has seen, so the route is accepted.
    route = tuple(beyond[: beyond.index(first_unknown) + 2])
    _flatten(state, route)
    _river(state, route[-2], route[-1], DEEP_FLOW)
    order = _survey(state, sender, route)
    assert validate_envelope(envelope(state, sender, order), state).errors == ()

    rng = StableRng(state.config.seed)
    kinds: list[str] = []
    sovereigns = {sender: OneShotSovereign(order)}
    for _ in range(len(route) + 2):
        result = advance_day(state, rng, sovereigns=sovereigns)
        state = result.state
        kinds.extend(event.kind for event in result.events.events)

    civilization = state.civilizations[sender]
    [expedition] = civilization.expeditions
    assert expedition.status is ExpeditionStatus.BLOCKED
    assert "expedition_blocked" in kinds
    assert route[-1] in civilization.known_tiles, "they saw the far bank"
    assert civilization.population.people[order.explorer_ids[0]].location == route[-2]


def test_council_reports_list_known_rivers_and_their_depth() -> None:
    state, sender, _, route = treaty_world()
    _flatten(state, route)
    _river(state, route[0], route[1], DEEP_FLOW)
    _river(state, route[1], route[2], 1)

    report = build_council_report(state, sender)

    depths = {frozenset((view.tile, view.across)): view.depth for view in report.known_rivers}
    assert depths == {
        frozenset((route[0], route[1])): "deep",
        frozenset((route[1], route[2])): "stream",
    }


def test_the_baseline_survey_stops_short_of_a_known_deep_river() -> None:
    state, sender, _, _ = treaty_world()
    home = state.civilizations[sender].start_center
    direction = -1 if home.q >= 4 else 1
    ahead = tuple(HexCoord(home.q + step * direction, home.r) for step in range(6))
    _flatten(state, ahead)
    _river(state, ahead[2], ahead[3], DEEP_FLOW)
    state.day = 0

    envelope_ = BaselineSovereign().decide(build_council_report(state, sender))

    [survey] = [c for c in envelope_.commands if c.kind is DirectOrderKind.START_EXPEDITION]
    assert survey.route == ahead[:3]
