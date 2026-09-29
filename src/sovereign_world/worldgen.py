"""Seeded terrain generation and balanced starting-region selection."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

import numpy as np

from sovereign_world.config import WorldConfig
from sovereign_world.hexmap import HexCoord, Terrain, Tile, WorldMap
from sovereign_world.rng import StableRng


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


class WorldGenerationError(RuntimeError):
    def __init__(self, seed: int, attempts: int, failures: dict[str, int]) -> None:
        self.seed = seed
        self.attempts = attempts
        self.failures = failures
        super().__init__(
            f"could not place four viable starts for seed {seed} after {attempts} attempts: "
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


def _region_tiles(world_map: WorldMap, center: HexCoord, radius: int = 4) -> tuple[Tile, ...]:
    return tuple(tile for tile in world_map.tiles if center.distance(tile.coord) <= radius)


def _viability(world_map: WorldMap, center: HexCoord) -> StartViability:
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
    harsh_terrain = {Terrain.DESERT, Terrain.TUNDRA, Terrain.MOUNTAIN}
    hazard = sum(tile.terrain in harsh_terrain for tile in tiles)
    score = food * 4 + construction * 2 + (200 if has_water else 0) - hazard * 3
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


def _select_starts(
    world_map: WorldMap,
    count: int,
    min_distance: int,
) -> tuple[list[StartingRegion], list[str]]:
    candidates: list[tuple[StartViability, HexCoord]] = []
    # Settle only on land that every other start can reach on foot.
    landmass = _largest_landmass(world_map)
    for r in range(4, world_map.height - 4):
        for q in range(4, world_map.width - 4):
            center = HexCoord(q, r)
            if center not in landmass:
                continue
            viability = _viability(world_map, center)
            if (
                viability.has_water
                and viability.food_units_per_day >= 64
                and viability.construction_units >= 64
            ):
                candidates.append((viability, center))

    candidates.sort(key=lambda item: (-item[0].score, item[1].q, item[1].r))
    selected: list[StartingRegion] = []
    for viability, center in candidates:
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


def generate_world(
    config: WorldConfig,
    rng: StableRng,
    max_attempts: int = 100,
    min_start_distance: int = 12,
) -> GeneratedWorld:
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")
    failures: Counter[str] = Counter()
    for attempt in range(max_attempts):
        world_map = _generate_map(config, rng, attempt)
        starts, reasons = _select_starts(
            world_map,
            count=config.civilizations,
            min_distance=min_start_distance,
        )
        if len(starts) == config.civilizations:
            return GeneratedWorld(world_map=world_map, starts=tuple(starts))
        failures.update(reasons)
    raise WorldGenerationError(config.seed, max_attempts, dict(failures))
