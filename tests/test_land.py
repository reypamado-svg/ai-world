"""What the land yields (rules version 2): fields, water, forage, irrigation and fishing from
the land cover, and water for new settlements."""

from dataclasses import replace

from logistics_helpers import envelope, linked_world

from sovereign_world.commands import (
    DirectOrder,
    DirectOrderKind,
    build_council_report,
    validate_envelope,
)
from sovereign_world.config import RunManifest, WorldConfig
from sovereign_world.cover import WET_FIELD, forage_bp, graze_bp
from sovereign_world.engine import advance_day
from sovereign_world.hexmap import HexCoord, Terrain, Tile, WorldMap
from sovereign_world.ids import EntityId
from sovereign_world.land import (
    FIELD_SCALE,
    food_capacity,
    stone_capacity,
    timber_capacity,
    water_near,
)
from sovereign_world.logistics import forage_chance_bp
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState, build_initial_state

GRASS = (7_000, 1_500, 1_000, 300, 200, 0, 0)
FOREST = (1_200, 7_400, 700, 400, 300, 0, 0)
MARSH = (5_000, 1_500, 1_000, 2_500, 0, 0, 0)


def _map(*tiles: Tile) -> WorldMap:
    """A small all-grassland map with some tiles repainted."""
    state = build_initial_state(RunManifest.new(WorldConfig(seed=9, width=24, height=24), "0.1.0"))
    painted = {tile.coord: tile for tile in tiles}
    return replace(
        state.world_map,
        tiles=tuple(painted.get(tile.coord, tile) for tile in state.world_map.tiles),
        rivers=(),
    )


def _tile(coord: HexCoord, cover, *, terrain=Terrain.GRASSLAND, soil=600, **extra) -> Tile:
    base = Tile(
        coord=coord,
        terrain=terrain,
        elevation=300,
        moisture=500,
        temperature=500,
        soil=soil,
        timber=500,
        stone=400,
        ore=100,
    )
    return replace(base, cover=cover, **extra)


A, B, C = HexCoord(5, 5), HexCoord(6, 5), HexCoord(7, 5)


def test_a_grassland_tile_yields_what_it_did_and_a_forest_yields_forage_instead() -> None:
    world = _map(_tile(A, GRASS), _tile(B, FOREST, terrain=Terrain.FOREST))
    grass = food_capacity(world, (A,), irrigation=False, fishing=False)
    # Fields: soil 600 // 200 = 3 over an open-and-scrub share of 7 500, as before; forage
    # adds wood and wetland over 5 000.
    assert grass == 3 * graze_bp(GRASS) // FIELD_SCALE + forage_bp(GRASS) // 5_000 == 3
    forest = food_capacity(world, (B,), irrigation=False, fishing=False)
    assert forest == 3 * graze_bp(FOREST) // FIELD_SCALE + forage_bp(FOREST) // 5_000 == 1
    # Shares are summed before dividing: two forest tiles give more than twice one.
    world = _map(_tile(B, FOREST, terrain=Terrain.FOREST), _tile(C, FOREST, terrain=Terrain.FOREST))
    assert food_capacity(world, (B, C), irrigation=False, fishing=False) == 1 + 3


def test_water_irrigation_and_fishing() -> None:
    lake = _tile(C, (), terrain=Terrain.WATER, soil=0)
    world = _map(_tile(A, MARSH), _tile(B, GRASS, river=True), lake)
    dry = food_capacity(world, (A, B, C), irrigation=False, fishing=False)
    # Fields 3·5 500 + 3·7 500 over 7 500 = 5; forage 4 000 + 1 800 over 5 000 = 1; both
    # land tiles are watered (+2 each) and the lake, without cover, keeps the old 0 + 2.
    assert dry == 5 + 1 + 2 + 2 + 2
    irrigated = food_capacity(world, (A, B, C), irrigation=True, fishing=False)
    assert irrigated == 7 + 1 + 2 + 2 + 2, "half again on watered fields"
    fished = food_capacity(world, (A, B, C), irrigation=False, fishing=True)
    assert fished == dry + 2, "one more for the river tile and one for the lake"


def test_tiles_without_cover_yield_as_before() -> None:
    world = _map(_tile(A, (), soil=800), _tile(B, (), soil=400, river=True))
    assert food_capacity(world, (A, B), irrigation=True, fishing=False) == 4 + 2 + 2


def test_timber_and_stone_come_from_wood_and_rock() -> None:
    hills = (3_000, 2_000, 2_000, 0, 3_000, 0, 0)
    world = _map(_tile(A, FOREST), _tile(B, hills))
    assert timber_capacity(world, (A,)) == 7_400 * 5 // 10_000 == 3
    assert timber_capacity(world, (A, B)) == (7_400 * 5 + 2_000 * 5) // 10_000
    assert stone_capacity(world, (B,)) == 3_000 * 4 // 20_000 == 0
    assert stone_capacity(world, (A, B)) == (300 * 4 + 3_000 * 4) // 20_000


def test_water_near_a_tile() -> None:
    wet = (6_000, 1_000, 1_000, WET_FIELD, 1_000, 0, 0)
    dry = (8_000, 1_000, 1_000, 0, 0, 0, 0)
    world = _map(_tile(A, dry), _tile(B, wet), _tile(C, dry))
    assert water_near(world, B)
    assert not water_near(world, A), "wetland beside a tile does not count; open water does"
    lake = _map(_tile(A, dry), _tile(B, (), terrain=Terrain.WATER, soil=0))
    assert water_near(lake, A)


def test_foraging_travellers_find_more_in_woods_under_rules_two_only() -> None:
    world = _map(_tile(A, FOREST, terrain=Terrain.FOREST))
    plain = forage_chance_bp(world, A)
    assert forage_chance_bp(world, A, cover=True) == plain + forage_bp(FOREST) // 10


def _settle(state: WorldState, home, tile: HexCoord) -> DirectOrder:
    return DirectOrder(
        command_id="settle",
        kind=DirectOrderKind.FOUND_SETTLEMENT,
        journey_id=EntityId("journey:settle"),
        traveller_ids=state.civilizations[home].population.living_ids[-4:],
        route=(state.civilizations[home].start_center, tile),
    )


def test_a_new_settlement_needs_water_under_rules_two() -> None:
    for rules_version in (1, 2):
        _, state, home, _, route = linked_world(distance=6, rules_version=rules_version)
        start = state.civilizations[home].start_center
        site = route[3]
        dry = (8_000, 1_000, 1_000, 0, 0, 0, 0)
        around = {site, *site.neighbors()}
        state.world_map = replace(
            state.world_map,
            tiles=tuple(
                replace(tile, cover=dry, terrain=Terrain.GRASSLAND, river=False)
                if tile.coord in around
                else tile
                for tile in state.world_map.tiles
            ),
        )
        order = DirectOrder(
            command_id="settle",
            kind=DirectOrderKind.FOUND_SETTLEMENT,
            journey_id=EntityId("journey:settle"),
            traveller_ids=state.civilizations[home].population.living_ids[-4:],
            route=route[:4],
        )
        assert route[0] == start
        codes = [
            error.code for error in validate_envelope(envelope(state, home, order), state).errors
        ]
        assert codes == (["invalid_destination"] if rules_version == 2 else [])


def test_reports_show_the_land_under_rules_two_only() -> None:
    for rules_version in (1, 2):
        state = build_initial_state(
            RunManifest.new(
                WorldConfig(seed=9, width=24, height=24), "0.1.0", rules_version=rules_version
            )
        )
        home = sorted(state.civilizations)[0]
        report = build_council_report(state, home)
        if rules_version == 1:
            assert report.land == {} and "land" not in report.model_dump(mode="json")
            continue
        [(_, view)] = report.land.items()
        assert view.food_per_day > 0 and view.timber_per_day >= 0
        state = advance_day(state, StableRng(9)).state
        assert build_council_report(state, home).land
