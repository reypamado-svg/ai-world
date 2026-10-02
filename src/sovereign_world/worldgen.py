"""Seeded terrain generation and balanced starting-region selection."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from math import isqrt

import numpy as np

from sovereign_world import geography
from sovereign_world.config import CURRENT_GENERATOR, WorldConfig
from sovereign_world.cover import generate_cover
from sovereign_world.hexmap import HexCoord, Terrain, Tile, WorldMap
from sovereign_world.rng import StableRng
from sovereign_world.travel import DEEP_FLOW

LOWLAND_STARTS = frozenset({Terrain.GRASSLAND, Terrain.FOREST})
"""Where a version-2 world may place a capital."""

_REGION_RADIUS = 4
_REGION_OFFSETS = tuple(
    (dq, dr)
    for dq in range(-_REGION_RADIUS, _REGION_RADIUS + 1)
    for dr in range(-_REGION_RADIUS, _REGION_RADIUS + 1)
    if (abs(dq) + abs(dr) + abs(dq + dr)) // 2 <= _REGION_RADIUS
)


@dataclass(frozen=True, slots=True)
class StartViability:
    has_water: bool
    food_units_per_day: int
    construction_units: int
    strength: str
    vulnerability: str
    score: int


@dataclass(frozen=True, slots=True)
class StartingRegion:
    civilization_index: int
    center: HexCoord
    viability: StartViability


@dataclass(frozen=True, slots=True)
class GeneratedWorld:
    world_map: WorldMap
    starts: tuple[StartingRegion, ...]
    start_spacing: int = 12
    """The least distance between starts the generator held to."""


class WorldGenerationError(RuntimeError):
    def __init__(self, seed: int, attempts: int, failures: dict[str, int], count: int = 4) -> None:
        self.seed = seed
        self.attempts = attempts
        self.failures = failures
        super().__init__(
            f"could not place {count} viable starts for seed {seed} after {attempts} attempts: "
            f"{failures}"
        )


def _values(rng: np.random.Generator, size: int) -> np.ndarray:
    return rng.integers(0, 1_001, size=size, dtype=np.int64)


def _terrain(elevation: int, moisture: int, temperature: int) -> Terrain:
    if elevation < 120:
        return Terrain.WATER
    if elevation > 820:
        return Terrain.MOUNTAIN
    if temperature < 180:
        return Terrain.TUNDRA
    if moisture < 190 and temperature > 620:
        return Terrain.DESERT
    if moisture > 610:
        return Terrain.FOREST
    return Terrain.GRASSLAND


def _generate_map(config: WorldConfig, rng: StableRng, attempt: int) -> WorldMap:
    size = config.width * config.height
    elevation = _values(rng.stream(f"world:{attempt}:elevation"), size)
    moisture = _values(rng.stream(f"world:{attempt}:moisture"), size)
    temperature = _values(rng.stream(f"world:{attempt}:temperature"), size)
    soil = _values(rng.stream(f"world:{attempt}:soil"), size)
    timber = _values(rng.stream(f"world:{attempt}:timber"), size)
    stone = _values(rng.stream(f"world:{attempt}:stone"), size)
    ore = _values(rng.stream(f"world:{attempt}:ore"), size)
    river_columns = {config.width * fraction // 5 for fraction in range(1, 5)}

    tiles: list[Tile] = []
    for index in range(size):
        coord = HexCoord(index % config.width, index // config.width)
        terrain = _terrain(int(elevation[index]), int(moisture[index]), int(temperature[index]))
        river = coord.q in river_columns and terrain is not Terrain.MOUNTAIN
        tiles.append(
            Tile(
                coord=coord,
                terrain=terrain,
                elevation=int(elevation[index]),
                moisture=int(moisture[index]),
                temperature=int(temperature[index]),
                soil=int(soil[index]),
                timber=int(timber[index]),
                stone=int(stone[index]),
                ore=int(ore[index]),
                river=river,
            )
        )
    return WorldMap(width=config.width, height=config.height, tiles=tuple(tiles))


def _region_tiles(world_map: WorldMap, center: HexCoord) -> tuple[Tile, ...]:
    """Tiles within four steps of a centre, in map order."""
    coords = sorted(
        (HexCoord(center.q + dq, center.r + dr) for dq, dr in _REGION_OFFSETS),
        key=lambda coord: (coord.r, coord.q),
    )
    return tuple(world_map.tile(coord) for coord in coords if world_map.contains(coord))


def _viability(world_map: WorldMap, center: HexCoord, generator_version: int = 1) -> StartViability:
    tiles = _region_tiles(world_map, center)
    has_water = any(tile.has_water for tile in tiles)
    food = sum((tile.soil // 200) + (2 if tile.has_water else 0) for tile in tiles)
    construction = sum((tile.timber + tile.stone) // 250 for tile in tiles)
    resources = {
        "fertile soil": sum(tile.soil for tile in tiles),
        "timber": sum(tile.timber for tile in tiles),
        "stone": sum(tile.stone for tile in tiles),
        "ore": sum(tile.ore for tile in tiles),
    }
    strength = max(resources, key=lambda name: (resources[name], name))
    vulnerability = min(resources, key=lambda name: (resources[name], name))
    harsh_terrain = {Terrain.DESERT, Terrain.TUNDRA, Terrain.MOUNTAIN, Terrain.SNOW}
    hazard = sum(tile.terrain in harsh_terrain for tile in tiles)
    score = food * 4 + construction * 2 + (200 if has_water else 0) - hazard * 3
    if generator_version >= 2 and any(
        tile.river for tile in tiles if tile.coord.distance(center) <= 1
    ):
        score += 150
    return StartViability(
        has_water=has_water,
        food_units_per_day=food,
        construction_units=construction,
        strength=strength,
        vulnerability=vulnerability,
        score=score,
    )


def _largest_landmass(world_map: WorldMap) -> frozenset[HexCoord]:
    """The biggest set of land tiles connected without crossing water."""
    unvisited = {tile.coord for tile in world_map.tiles if tile.terrain is not Terrain.WATER}
    largest: set[HexCoord] = set()
    for origin in sorted(unvisited):
        if origin not in unvisited:
            continue
        component = {origin}
        frontier = [origin]
        unvisited.discard(origin)
        while frontier:
            coord = frontier.pop()
            for neighbor in world_map.neighbors(coord):
                if neighbor in unvisited:
                    unvisited.discard(neighbor)
                    component.add(neighbor)
                    frontier.append(neighbor)
        if len(component) > len(largest):
            largest = component
    return frozenset(largest)


def _candidates(
    world_map: WorldMap, generator_version: int
) -> tuple[list[tuple[StartViability, HexCoord]], frozenset[HexCoord]]:
    """Viable start sites, best first, and the land they must all lie on."""
    candidates: list[tuple[StartViability, HexCoord]] = []
    # Settle only on land that every other start can reach on foot.
    if generator_version >= 2:
        landmass = geography.foot_component(world_map, DEEP_FLOW)
    else:
        landmass = _largest_landmass(world_map)
    for r in range(4, world_map.height - 4):
        for q in range(4, world_map.width - 4):
            center = HexCoord(q, r)
            if center not in landmass:
                continue
            if generator_version >= 2 and world_map.tile(center).terrain not in LOWLAND_STARTS:
                continue
            viability = _viability(world_map, center, generator_version)
            if (
                viability.has_water
                and viability.food_units_per_day >= 64
                and viability.construction_units >= 64
            ):
                candidates.append((viability, center))
    candidates.sort(key=lambda item: (-item[0].score, item[1].q, item[1].r))
    return candidates, landmass


def _select_starts(
    world_map: WorldMap,
    count: int,
    min_distance: int,
    generator_version: int = 1,
) -> tuple[list[StartingRegion], list[str]]:
    candidates, _ = _candidates(world_map, generator_version)
    # Version 1 picks greedily from the best site; version 2 also tries each next-best site as
    # the first pick, since regional terrain clusters good sites together.
    firsts = range(len(candidates)) if generator_version >= 2 else range(min(1, len(candidates)))
    selected: list[StartingRegion] = []
    for first in firsts:
        selected = []
        for viability, center in (candidates[first], *candidates):
            if all(center.distance(existing.center) >= min_distance for existing in selected):
                selected.append(
                    StartingRegion(
                        civilization_index=len(selected),
                        center=center,
                        viability=viability,
                    )
                )
                if len(selected) == count:
                    return selected, []

    reasons = ["separation"] if candidates else ["viability"]
    return selected, reasons


def start_spacing_target(land_tiles: int, count: int, width: int, height: int, floor: int) -> int:
    """How far apart version-3 starts aim to be: wider with more land and fewer civilizations."""
    return max(floor, min(isqrt(land_tiles // (3 * count)), min(width, height) // 2))


def _spaced_starts(
    world_map: WorldMap,
    count: int,
    floor: int,
) -> tuple[list[StartingRegion], int, list[str]]:
    """Version 3: starts as far apart as the land allows, among good sites.

    It aims for the spacing the land supports (`start_spacing_target`) and steps down by two
    tiles to `floor` until a set fits. At each spacing it first draws from the sites scoring
    at least 80% of the best, then 60%, then any viable site: spacing comes before quality.
    Each set starts from one of the best sites in its pool (any of them at the floor) and
    adds, each time, the site farthest from those already chosen (ties by position).
    """
    candidates, landmass = _candidates(world_map, 3)
    if not candidates:
        return [], floor, ["viability"]
    q = np.array([center.q for _, center in candidates], dtype=np.int64)
    r = np.array([center.r for _, center in candidates], dtype=np.int64)
    score = np.array([viability.score for viability, _ in candidates], dtype=np.int64)
    best = int(score[0])
    target = start_spacing_target(len(landmass), count, world_map.width, world_map.height, floor)
    for spacing in range(target, floor - 1, -2):
        for share in (80, 60, 0):
            pool = (
                np.flatnonzero(score * 100 >= best * share) if share else np.arange(len(candidates))
            )
            firsts = pool if spacing == floor and share == 0 else pool[:8]
            for first in firsts:
                chosen = [int(first)]
                nearest = np.full(len(pool), 10**9, dtype=np.int64)
                while len(chosen) < count:
                    last = chosen[-1]
                    dq = q[pool] - q[last]
                    dr = r[pool] - r[last]
                    nearest = np.minimum(nearest, (abs(dq) + abs(dr) + abs(dq + dr)) // 2)
                    fits = nearest >= spacing
                    if not fits.any():
                        break
                    # Farthest from those chosen first; ties by (q, r).
                    order = np.lexsort((r[pool], q[pool], -nearest))
                    pick = next(int(index) for index in order if fits[index])
                    chosen.append(int(pool[pick]))
                if len(chosen) == count:
                    starts = [
                        StartingRegion(
                            civilization_index=index,
                            center=candidates[item][1],
                            viability=candidates[item][0],
                        )
                        for index, item in enumerate(chosen)
                    ]
                    return starts, spacing, []
    return [], floor, ["separation"]


def generate_world(
    config: WorldConfig,
    rng: StableRng,
    max_attempts: int = 100,
    min_start_distance: int = 12,
    generator_version: int = CURRENT_GENERATOR,
) -> GeneratedWorld:
    """Make a world and its starting regions.

    Version 1 is the original generator, kept so a run made with it can be rebuilt from its
    manifest; version 2 makes regional terrain with logical neighbours and flowing rivers;
    version 3 keeps version 2's terrain, adds land cover inside each tile, and places starts as
    far apart as the land allows.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")
    if not 1 <= generator_version <= CURRENT_GENERATOR:
        raise ValueError(f"unknown world generator version {generator_version}")
    failures: Counter[str] = Counter()
    for attempt in range(max_attempts):
        if generator_version == 1:
            world_map = _generate_map(config, rng, attempt)
        else:
            world_map = geography.generate_map(config.width, config.height, rng, attempt)
        if generator_version >= 3:
            world_map = generate_cover(world_map, rng, attempt)
        spacing = min_start_distance
        if generator_version >= 3:
            starts, spacing, reasons = _spaced_starts(
                world_map, count=config.civilizations, floor=min_start_distance
            )
        else:
            starts, reasons = _select_starts(
                world_map,
                count=config.civilizations,
                min_distance=min_start_distance,
                generator_version=generator_version,
            )
        if len(starts) == config.civilizations:
            return GeneratedWorld(world_map=world_map, starts=tuple(starts), start_spacing=spacing)
        failures.update(reasons)
    raise WorldGenerationError(
        config.seed, max_attempts, dict(failures), count=config.civilizations
    )
