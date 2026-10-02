"""World generator version 2: regional terrain, logical neighbours and rivers that flow."""

import json
import time
from collections import Counter
from functools import cache
from itertools import combinations

import pytest

from sovereign_world.config import CURRENT_GENERATOR, RunManifest, WorldConfig
from sovereign_world.geography import ALLOWED, SNOW_BELOW, foot_component
from sovereign_world.hexmap import HexCoord, RiverEdge, Terrain, Tile, WorldMap, corner_tiles
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState, build_initial_state, state_hash
from sovereign_world.travel import DEEP_FLOW
from sovereign_world.worldgen import LOWLAND_STARTS, GeneratedWorld, generate_world

Corner = tuple[HexCoord, ...]


@cache
def _world(seed: int, size: int) -> GeneratedWorld:
    return generate_world(WorldConfig(seed=seed, width=size, height=size), StableRng(seed))


def _corner(*coords: HexCoord) -> Corner:
    return tuple(sorted(coords))


def _ends(world_map: WorldMap, edge: RiverEdge) -> tuple[Corner, Corner]:
    """The corners an edge runs from and to."""
    first, second = corner_tiles(edge.a, edge.b)
    if edge.downstream is None:
        down = first if not world_map.contains(first) else second
    else:
        down = edge.downstream
    up = second if down == first else first
    return _corner(edge.a, edge.b, up), _corner(edge.a, edge.b, down)


def _terminal(world_map: WorldMap, corner: Corner) -> bool:
    return any(
        not world_map.contains(coord) or world_map.tile(coord).terrain is Terrain.WATER
        for coord in corner
    )


def _height(world_map: WorldMap, corner: Corner) -> int:
    inside = [world_map.tile(coord).elevation for coord in corner if world_map.contains(coord)]
    return sum(inside) // len(inside)


@pytest.mark.parametrize(("seed", "size"), [(9, 48), (21, 100)])
def test_tiles_mostly_share_terrain_with_their_neighbours(seed: int, size: int) -> None:
    world_map = _world(seed, size).world_map
    land = [tile for tile in world_map.tiles if tile.terrain is not Terrain.WATER]

    alike = sum(
        sum(world_map.tile(n).terrain is tile.terrain for n in world_map.neighbors(tile.coord)) >= 3
        for tile in land
    )

    assert alike * 100 >= 70 * len(land)


@pytest.mark.parametrize(("seed", "size"), [(9, 48), (21, 100), (3, 24)])
def test_no_terrain_borders_one_it_cannot(seed: int, size: int) -> None:
    world_map = _world(seed, size).world_map

    clashes = [
        (tile.coord, neighbour)
        for tile in world_map.tiles
        for neighbour in world_map.neighbors(tile.coord)
        if world_map.tile(neighbour).terrain not in ALLOWED[tile.terrain]
    ]

    assert clashes == []


def test_neighbour_table_is_symmetric() -> None:
    for terrain, allowed in ALLOWED.items():
        for other in allowed:
            assert terrain in ALLOWED[other]


def test_mountains_form_ranges() -> None:
    world_map = _world(21, 100).world_map
    high = {
        tile.coord for tile in world_map.tiles if tile.terrain in (Terrain.MOUNTAIN, Terrain.SNOW)
    }
    sizes: list[int] = []
    unvisited = set(high)
    while unvisited:
        frontier = [min(unvisited)]
        unvisited.discard(frontier[0])
        size = 0
        while frontier:
            coord = frontier.pop()
            size += 1
            for neighbour in world_map.neighbors(coord):
                if neighbour in unvisited:
                    unvisited.discard(neighbour)
                    frontier.append(neighbour)
        sizes.append(size)

    assert sum(sizes) >= 4 * len(sizes)
    assert max(sizes) >= 12


def test_terrain_mix_is_plausible() -> None:
    world_map = _world(21, 100).world_map
    counts = Counter(tile.terrain for tile in world_map.tiles)
    share = {terrain: counts[terrain] * 100 // len(world_map.tiles) for terrain in Terrain}

    assert 8 <= share[Terrain.WATER] <= 30
    assert 5 <= share[Terrain.MOUNTAIN] + share[Terrain.SNOW] <= 25
    assert share[Terrain.DESERT] <= 15
    assert share[Terrain.GRASSLAND] >= 10
    assert share[Terrain.FOREST] >= 10
    assert share[Terrain.HILLS] >= 3


def test_snow_lies_only_on_high_cold_ground() -> None:
    world_map = _world(21, 100).world_map
    snow = [tile for tile in world_map.tiles if tile.terrain is Terrain.SNOW]
    mountains = [tile for tile in world_map.tiles if tile.terrain is Terrain.MOUNTAIN]
    lowland = [tile for tile in world_map.tiles if tile.terrain in LOWLAND_STARTS]

    assert snow
    assert all(tile.elevation >= 800 and tile.temperature < SNOW_BELOW for tile in snow)
    mean = lambda tiles: sum(tile.temperature for tile in tiles) // len(tiles)  # noqa: E731
    assert mean(mountains) < mean(lowland)


def test_resources_follow_the_land() -> None:
    world_map = _world(21, 100).world_map

    def mean(terrain: Terrain, field: str) -> int:
        tiles = [tile for tile in world_map.tiles if tile.terrain is terrain]
        return sum(getattr(tile, field) for tile in tiles) // len(tiles)

    assert mean(Terrain.FOREST, "timber") > mean(Terrain.GRASSLAND, "timber")
    assert mean(Terrain.MOUNTAIN, "stone") > mean(Terrain.GRASSLAND, "stone")
    assert mean(Terrain.GRASSLAND, "soil") > mean(Terrain.MOUNTAIN, "soil")


@pytest.mark.parametrize(("seed", "size"), [(9, 48), (21, 100)])
def test_rivers_run_continuously_to_water_or_the_edge(seed: int, size: int) -> None:
    world_map = _world(seed, size).world_map
    assert world_map.rivers
    leaving: dict[Corner, list[RiverEdge]] = {}
    arriving: dict[Corner, list[RiverEdge]] = {}
    for edge in world_map.rivers:
        up, down = _ends(world_map, edge)
        leaving.setdefault(up, []).append(edge)
        arriving.setdefault(down, []).append(edge)
        assert world_map.tile(edge.a).terrain is not Terrain.WATER
        assert world_map.tile(edge.b).terrain is not Terrain.WATER

    downhill = 0
    for edge in world_map.rivers:
        up, down = _ends(world_map, edge)
        downhill += _height(world_map, down) <= _height(world_map, up)
        if _terminal(world_map, down):
            assert down not in leaving
            continue
        # A river never just stops on dry land: one edge carries it on, with all it gathered.
        (onward,) = leaving[down]
        assert onward.flow >= sum(incoming.flow for incoming in arriving[down])
    # Water runs downhill, except where it spills out of a filled hollow.
    assert downhill * 100 >= 85 * len(world_map.rivers)


def test_river_courses_are_long_and_never_loop() -> None:
    world_map = _world(21, 100).world_map
    onward: dict[Corner, RiverEdge] = {}
    for edge in world_map.rivers:
        onward[_ends(world_map, edge)[0]] = edge

    longest = 0
    for edge in world_map.rivers:
        steps = 1
        corner = _ends(world_map, edge)[1]
        while corner in onward:
            steps += 1
            assert steps <= len(world_map.rivers)
            corner = _ends(world_map, onward[corner])[1]
        longest = max(longest, steps)

    assert longest >= 8
    assert any(edge.flow >= DEEP_FLOW for edge in world_map.rivers)


def test_riverside_tiles_are_exactly_those_beside_a_river() -> None:
    world_map = _world(21, 100).world_map
    beside = {coord for edge in world_map.rivers for coord in (edge.a, edge.b)}

    assert {tile.coord for tile in world_map.tiles if tile.river} == beside


def test_river_lookup_finds_a_border_from_either_side() -> None:
    world_map = _world(9, 48).world_map
    edge = world_map.rivers[0]

    assert world_map.river_between(edge.a, edge.b) == edge
    assert world_map.river_between(edge.b, edge.a) == edge
    assert world_map.river_between(edge.a, edge.a) is None


@pytest.mark.parametrize("size", [24, 48])
@pytest.mark.parametrize("seed", range(12))
def test_starts_are_lowland_and_reachable_on_foot(seed: int, size: int) -> None:
    generated = _world(seed, size)
    reachable = foot_component(generated.world_map, DEEP_FLOW)

    assert len(generated.starts) == 4
    for start in generated.starts:
        assert generated.world_map.tile(start.center).terrain in LOWLAND_STARTS
        assert start.center in reachable
        assert start.viability.has_water
    for left, right in combinations(generated.starts, 2):
        assert left.center.distance(right.center) >= 12


def test_generation_repeats_and_is_quick() -> None:
    config = WorldConfig(seed=21, width=100, height=100)
    started = time.perf_counter()
    first = generate_world(config, StableRng(config.seed))
    elapsed = time.perf_counter() - started
    second = generate_world(config, StableRng(config.seed))

    assert first.world_map.content_hash() == second.world_map.content_hash()
    assert first.world_map.rivers == second.world_map.rivers
    assert first.starts == second.starts
    assert elapsed < 5


def test_version_one_still_builds_the_original_world() -> None:
    config = WorldConfig(seed=21, width=48, height=48)

    generated = generate_world(config, StableRng(config.seed), generator_version=1)

    assert [(start.center.q, start.center.r) for start in generated.starts] == [
        (21, 28),
        (40, 25),
        (8, 16),
        (22, 5),
    ]
    assert generated.world_map.rivers == ()
    assert {tile.terrain for tile in generated.world_map.tiles} <= {
        Terrain.WATER,
        Terrain.GRASSLAND,
        Terrain.FOREST,
        Terrain.MOUNTAIN,
        Terrain.DESERT,
        Terrain.TUNDRA,
    }


def test_unknown_generator_version_is_refused() -> None:
    with pytest.raises(ValueError, match="generator version"):
        generate_world(
            WorldConfig(seed=1, width=24, height=24),
            StableRng(1),
            generator_version=CURRENT_GENERATOR + 1,
        )


def test_new_runs_record_the_current_generator() -> None:
    manifest = RunManifest.new(WorldConfig(seed=4, width=24, height=24), "0.2.0")

    state = build_initial_state(manifest)

    assert manifest.generator_version == CURRENT_GENERATOR
    assert state.world_map.rivers


def test_manifests_from_before_versions_keep_their_hash() -> None:
    old = {
        "run_id": "6f1f7c32-3f0b-4f1e-9b55-2a2f1d1c0e11",
        "engine_version": "0.1.0",
        "config": {"seed": 3, "width": 24, "height": 24},
    }
    manifest = RunManifest.model_validate(old)
    dumped = manifest.model_dump(mode="json")
    for key in ("sovereigns", "budgets", "parent_run_id", "forked_at_day", "generator_version"):
        dumped.pop(key)

    assert manifest.generator_version == 1
    expected = RunManifest.model_validate(dumped).content_hash()
    assert manifest.content_hash() == expected
    upgraded = manifest.model_copy(update={"generator_version": 2})
    assert upgraded.content_hash() != manifest.content_hash()


def test_maps_without_rivers_save_exactly_as_before() -> None:
    manifest = RunManifest(
        run_id="6f1f7c32-3f0b-4f1e-9b55-2a2f1d1c0e12",
        engine_version="0.1.0",
        config=WorldConfig(seed=5, width=24, height=24),
    )
    state = build_initial_state(manifest)
    dumped = state.model_dump(mode="json")

    assert "rivers" not in dumped["world_map"]
    reloaded = WorldState.model_validate_json(json.dumps(dumped))
    assert state_hash(reloaded) == state_hash(state)


def test_maps_with_rivers_round_trip() -> None:
    state = build_initial_state(RunManifest.new(WorldConfig(seed=6, width=24, height=24), "0.2.0"))

    reloaded = WorldState.model_validate_json(state.model_dump_json())

    assert reloaded.world_map.rivers == state.world_map.rivers
    assert state_hash(reloaded) == state_hash(state)
    edge = reloaded.world_map.rivers[0]
    assert reloaded.world_map.river_between(edge.b, edge.a) == edge


def _pair_map() -> tuple[Tile, ...]:
    return tuple(
        Tile(
            coord=HexCoord(q, r),
            terrain=Terrain.GRASSLAND,
            elevation=500,
            moisture=500,
            temperature=500,
            soil=500,
            timber=500,
            stone=500,
            ore=500,
        )
        for r in range(3)
        for q in range(3)
    )


@pytest.mark.parametrize(
    ("edge", "message"),
    [
        (RiverEdge(HexCoord(1, 0), HexCoord(0, 0), 1, None), "ordered border"),
        (RiverEdge(HexCoord(0, 0), HexCoord(2, 0), 1, None), "ordered border"),
        (RiverEdge(HexCoord(0, 0), HexCoord(1, 0), 0, None), "flow"),
        (RiverEdge(HexCoord(0, 0), HexCoord(1, 0), 1, HexCoord(2, 2)), "beside neither"),
    ],
)
def test_world_map_rejects_malformed_rivers(edge: RiverEdge, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        WorldMap(width=3, height=3, tiles=_pair_map(), rivers=(edge,))


def test_copying_a_state_shares_its_frozen_map() -> None:
    state = build_initial_state(RunManifest.new(WorldConfig(seed=7, width=24, height=24), "0.2.0"))

    copy = state.model_copy(deep=True)

    assert copy.world_map is state.world_map, "the map never changes, so copies share it"
    assert state_hash(copy) == state_hash(state)
    with pytest.raises(AttributeError):
        copy.world_map.width = 1  # type: ignore[misc]
