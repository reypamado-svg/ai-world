from dataclasses import replace

from logistics_helpers import OneShotSovereign, clear_journey_id, envelope, treaty_world

from sovereign_world.commands import DirectOrder, DirectOrderKind, validate_envelope
from sovereign_world.config import RunManifest, WorldConfig
from sovereign_world.diplomacy import TreatyKind
from sovereign_world.engine import advance_day
from sovereign_world.exploration import ExpeditionStatus
from sovereign_world.hexmap import HexCoord, Terrain
from sovereign_world.ids import EntityId
from sovereign_world.logistics import JourneyKind, journey_days
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState, build_initial_state
from sovereign_world.travel import DAY, entry_cost, step, travel_days


def _paint(state: WorldState, terrain: Terrain, *tiles: HexCoord) -> None:
    painted = {tile: replace(state.world_map.tile(tile), terrain=terrain) for tile in tiles}
    state.world_map = replace(
        state.world_map,
        tiles=tuple(painted.get(tile.coord, tile) for tile in state.world_map.tiles),
    )


def _migration(state, sender, recipient, route, journey_id: str) -> DirectOrder:
    return DirectOrder(
        command_id=f"migrate:{journey_id}",
        kind=DirectOrderKind.DISPATCH_MIGRATION,
        journey_id=EntityId(journey_id),
        treaty_id=EntityId("treaty:migration"),
        recipient_civilization_id=recipient,
        traveller_ids=state.civilizations[sender].population.living_ids[-2:],
        route=route,
    )


def test_each_terrain_has_its_entry_cost() -> None:
    state, _, _, route = treaty_world()
    tile = route[1]
    for terrain, cost in (
        (Terrain.GRASSLAND, 10),
        (Terrain.FOREST, 15),
        (Terrain.DESERT, 15),
        (Terrain.TUNDRA, 15),
        (Terrain.MOUNTAIN, 30),
        (Terrain.WATER, None),
    ):
        _paint(state, terrain, tile)
        assert entry_cost(state.world_map, tile) == cost


def test_rough_ground_takes_longer_and_progress_carries_over() -> None:
    def days_to_cross(cost: int, tiles: int) -> list[bool]:
        progress, moves = 0, []
        for _ in range(tiles * cost // DAY):
            moved, progress = step(progress, cost)
            moves.append(moved)
        return moves

    assert days_to_cross(10, 3) == [True, True, True]
    assert days_to_cross(15, 2) == [False, True, True], "forest averages a day and a half"
    assert days_to_cross(30, 1) == [False, False, True], "a mountain takes three days"


def test_travel_days_and_provisions_follow_the_terrain() -> None:
    state, _, _, route = treaty_world()
    _paint(state, Terrain.MOUNTAIN, route[1])
    _paint(state, Terrain.FOREST, route[2])

    assert travel_days(state.world_map, route[1:]) == 6, "3 + 1.5 + 1, rounded up"
    assert journey_days(JourneyKind.MIGRATION, state.world_map, route) == 6
    outbound_and_back = 6 + travel_days(state.world_map, (route[2], route[1], route[0]))
    assert journey_days(JourneyKind.SHIPMENT, state.world_map, route) == outbound_and_back


def test_migrants_take_three_days_to_cross_a_mountain() -> None:
    state, sender, recipient, route = treaty_world(TreatyKind.MIGRATION)
    _paint(state, Terrain.MOUNTAIN, route[1])
    order = _migration(state, sender, recipient, route, clear_journey_id("pass"))
    sovereigns = {sender: OneShotSovereign(order)}
    rng = StableRng(state.config.seed)
    arrival = None
    for day in range(10):
        result = advance_day(state, rng, sovereigns=sovereigns)
        state = result.state
        if any(event.kind == "migrants_received" for event in result.events.events):
            arrival = day
            break

    # 3 days to climb the mountain, then a day for each of the two grassland tiles.
    assert arrival == 4


def test_routes_over_known_water_are_refused() -> None:
    state, sender, recipient, route = treaty_world(TreatyKind.MIGRATION)
    _paint(state, Terrain.WATER, route[1])
    people = state.civilizations[sender].population.living_ids
    orders = (
        _migration(state, sender, recipient, route, "journey:wet"),
        DirectOrder(
            command_id="envoy",
            kind=DirectOrderKind.SEND_MESSAGE,
            message_id=EntityId("message:wet"),
            ambassador_id=people[0],
            recipient_civilization_id=recipient,
            message_text="Greetings.",
            route=route,
        ),
        DirectOrder(
            command_id="explore",
            kind=DirectOrderKind.START_EXPEDITION,
            expedition_id=EntityId("expedition:wet"),
            explorer_ids=(people[1],),
            route=route[:2],
        ),
    )

    for order in orders:
        result = validate_envelope(envelope(state, sender, order), state)
        assert [error.code for error in result.errors] == ["invalid_route"], order.command_id


def test_explorers_meet_unknown_water_and_stop_there() -> None:
    state, sender, _, _ = treaty_world()
    civilization = state.civilizations[sender]
    home = civilization.start_center
    # Find a straight line of tiles leading out of the known area.
    beyond = [HexCoord(home.q, home.r + offset) for offset in range(0, 7)]
    if not all(state.world_map.contains(tile) for tile in beyond):
        beyond = [HexCoord(home.q, home.r - offset) for offset in range(0, 7)]
    first_unknown = next(tile for tile in beyond if tile not in civilization.known_tiles)
    _paint(state, Terrain.GRASSLAND, *beyond[1 : beyond.index(first_unknown)])
    _paint(state, Terrain.WATER, first_unknown)
    route = tuple(beyond[: beyond.index(first_unknown) + 1])
    explorer = civilization.population.living_ids[0]
    order = DirectOrder(
        command_id="explore",
        kind=DirectOrderKind.START_EXPEDITION,
        expedition_id=EntityId("expedition:shore"),
        explorer_ids=(explorer,),
        route=route,
    )
    assert validate_envelope(envelope(state, sender, order), state).errors == (), (
        "unknown water is not revealed by refusing the route"
    )

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
    assert first_unknown in civilization.known_tiles, "they saw the water that stopped them"
    assert civilization.population.people[explorer].location == route[-2]


def test_every_start_is_on_land_all_can_reach_on_foot() -> None:
    for width in (24, 48):
        for seed in range(12):
            state = build_initial_state(
                RunManifest.new(
                    config=WorldConfig(seed=seed, width=width, height=width),
                    engine_version="0.1.0",
                )
            )
            starts = [civilization.start_center for civilization in state.civilizations.values()]
            assert all(entry_cost(state.world_map, start) is not None for start in starts)
            reachable = {starts[0]}
            frontier = [starts[0]]
            while frontier:
                for neighbor in state.world_map.neighbors(frontier.pop()):
                    if neighbor not in reachable and entry_cost(state.world_map, neighbor):
                        reachable.add(neighbor)
                        frontier.append(neighbor)
            assert set(starts) <= reachable, (width, seed)
