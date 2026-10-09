"""Land cover inside tiles (world generator version 3)."""

import time
from dataclasses import replace

import numpy as np
import pytest

from sovereign_world.config import RunManifest, WorldConfig
from sovereign_world.cover import FOREST_OPEN_FLOOR, GRASSLAND_WOOD_CAP, SNOWFIELD_BELOW
from sovereign_world.hexmap import COVER_CLASSES, COVER_TOTAL, CoverClass, Terrain, WorldMap
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState, build_initial_state, state_hash
from sovereign_world.worldgen import GeneratedWorld, generate_world

OPEN, WOOD, SCRUB, WETLAND, ROCK, SAND, SNOW = range(len(COVER_CLASSES))


def _world(seed: int, size: int, version: int = 3) -> GeneratedWorld:
    config = WorldConfig(seed=seed, width=size, height=size)
    return generate_world(config, StableRng(seed), generator_version=version)


@pytest.fixture(scope="module")
def big() -> GeneratedWorld:
    return _world(21, 100)


def _land(world: GeneratedWorld, terrain: Terrain | None = None):
    return [
        tile
        for tile in world.world_map.tiles
        if tile.terrain is not Terrain.WATER and (terrain is None or tile.terrain is terrain)
    ]


@pytest.mark.parametrize(("seed", "size"), [(9, 48), (21, 100), (3, 24)])
def test_every_land_tile_is_shared_out_and_water_has_none(seed: int, size: int) -> None:
    world = _world(seed, size)
    for tile in world.world_map.tiles:
        if tile.terrain is Terrain.WATER:
            assert tile.cover == ()
        else:
            assert len(tile.cover) == len(COVER_CLASSES)
            assert min(tile.cover) >= 0
            assert sum(tile.cover) == COVER_TOTAL


def test_version_two_maps_have_no_cover() -> None:
    assert all(tile.cover == () for tile in _world(9, 48, version=2).world_map.tiles)


def test_deserts_are_open_or_wet_only_at_an_oasis(big: GeneratedWorld) -> None:
    world_map = big.world_map
    oases = 0
    for tile in _land(big, Terrain.DESERT):
        beside_water = any(
            world_map.tile(n).terrain is Terrain.WATER for n in world_map.neighbors(tile.coord)
        )
        if not (tile.river or beside_water):
            assert tile.cover[OPEN] == 0 and tile.cover[WETLAND] == 0
        elif tile.cover[WETLAND] > 0:
            oases += 1
        assert tile.cover[WOOD] == 0
    assert oases > 0


def test_grassland_is_never_mostly_wood_and_forests_have_clearings(big: GeneratedWorld) -> None:
    assert all(tile.cover[WOOD] <= GRASSLAND_WOOD_CAP for tile in _land(big, Terrain.GRASSLAND))
    assert all(tile.cover[OPEN] >= FOREST_OPEN_FLOOR for tile in _land(big, Terrain.FOREST))


def test_grassland_beside_forest_carries_more_wood(big: GeneratedWorld) -> None:
    world_map = big.world_map

    def forests(coord) -> int:
        return sum(world_map.tile(n).terrain is Terrain.FOREST for n in world_map.neighbors(coord))

    grass = _land(big, Terrain.GRASSLAND)
    lone = [tile.cover[WOOD] for tile in grass if forests(tile.coord) == 0]
    edge = [tile.cover[WOOD] for tile in grass if forests(tile.coord) >= 2]
    assert np.mean(edge) > np.mean(lone) + 300


def test_snow_lies_only_on_cold_mountains_and_snowfields_show_only_rock(
    big: GeneratedWorld,
) -> None:
    mountains = _land(big, Terrain.MOUNTAIN)
    assert all(tile.cover[SNOW] == 0 for tile in mountains if tile.temperature >= SNOWFIELD_BELOW)
    assert any(tile.cover[SNOW] > 0 for tile in mountains if tile.temperature < SNOWFIELD_BELOW)
    for tile in _land(big, Terrain.SNOW):
        assert tile.cover[ROCK] + tile.cover[SNOW] == COVER_TOTAL
    for terrain in (Terrain.GRASSLAND, Terrain.FOREST, Terrain.HILLS, Terrain.TUNDRA):
        assert all(tile.cover[SNOW] == 0 and tile.cover[SAND] == 0 for tile in _land(big, terrain))


def test_rock_rises_from_lowland_to_hills_to_mountains(big: GeneratedWorld) -> None:
    def rock(terrain: Terrain) -> float:
        return float(np.mean([tile.cover[ROCK] for tile in _land(big, terrain)]))

    assert rock(Terrain.MOUNTAIN) > rock(Terrain.HILLS) > rock(Terrain.GRASSLAND)


def test_wetland_is_never_most_of_a_tile(big: GeneratedWorld) -> None:
    for tile in _land(big):
        cap = 3000 if tile.terrain is Terrain.TUNDRA else 2500
        assert tile.cover[WETLAND] <= cap


def test_cover_repeats_and_generation_stays_quick() -> None:
    config = WorldConfig(seed=21, width=100, height=100)
    started = time.perf_counter()
    first = generate_world(config, StableRng(21), generator_version=3)
    elapsed = time.perf_counter() - started
    second = generate_world(config, StableRng(21), generator_version=3)
    assert first.world_map.tiles == second.world_map.tiles
    assert elapsed < 5


@pytest.mark.parametrize(
    "cover",
    [(10_000, 0, 0, 0, 0, 0), (9_999, 0, 0, 0, 0, 0, 0), (10_001, -1, 0, 0, 0, 0, 0)],
)
def test_a_map_refuses_cover_that_does_not_share_out_the_tile(cover) -> None:
    world_map = _world(3, 24).world_map
    tiles = list(world_map.tiles)
    tiles[0] = replace(tiles[0], cover=cover)
    with pytest.raises(ValueError, match="cover"):
        WorldMap(world_map.width, world_map.height, tuple(tiles), rivers=world_map.rivers)


def test_saves_without_cover_have_no_cover_key_and_new_saves_round_trip() -> None:
    old = RunManifest(
        run_id="6f1f7c32-3f0b-4f1e-9b55-2a2f1d1c0e13",
        engine_version="0.1.0",
        config=WorldConfig(seed=5, width=24, height=24),
        generator_version=2,
    )
    dumped = build_initial_state(old).model_dump(mode="json")
    assert all("cover" not in tile for tile in dumped["world_map"]["tiles"])

    new = RunManifest.new(WorldConfig(seed=5, width=24, height=24), "test")
    state = build_initial_state(new)
    dumped = state.model_dump(mode="json")
    land = [tile for tile in dumped["world_map"]["tiles"] if tile["terrain"] != "water"]
    assert all(len(tile["cover"]) == len(COVER_CLASSES) for tile in land)
    assert all(
        "cover" not in tile for tile in dumped["world_map"]["tiles"] if tile["terrain"] == "water"
    )
    again = WorldState.model_validate_json(state.model_dump_json())
    assert state_hash(again) == state_hash(state)
    assert again.world_map.tiles == state.world_map.tiles


def test_cover_classes_are_in_their_fixed_order() -> None:
    assert [item.value for item in CoverClass] == [
        "open",
        "wood",
        "scrub",
        "wetland",
        "rock",
        "sand",
        "snowfield",
    ]
