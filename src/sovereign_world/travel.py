"""Terrain-aware travel: what it costs to enter a tile, and daily progress toward it."""

from __future__ import annotations

from collections.abc import Iterable
from math import ceil

from sovereign_world.hexmap import HexCoord, Terrain, WorldMap

DAY = 10
"""One day of travel, in tenths of a day."""

ENTRY_COST: dict[Terrain, int | None] = {
    Terrain.GRASSLAND: 10,
    Terrain.FOREST: 15,
    Terrain.DESERT: 15,
    Terrain.TUNDRA: 15,
    Terrain.MOUNTAIN: 30,
    Terrain.WATER: None,
}
"""Cost of entering a tile, in tenths of a day; None means impassable."""

MAX_PROGRESS = max(cost for cost in ENTRY_COST.values() if cost is not None) + DAY


def entry_cost(world_map: WorldMap, coord: HexCoord, harbours: frozenset[HexCoord]) -> int | None:
    """What entering a tile costs; a settlement's own tile is always enterable."""
    if coord in harbours:
        return DAY
    return ENTRY_COST[world_map.tile(coord).terrain]


def passable(
    world_map: WorldMap, tiles: Iterable[HexCoord], harbours: frozenset[HexCoord]
) -> bool:
    return all(entry_cost(world_map, tile, harbours) is not None for tile in tiles)


def travel_days(
    world_map: WorldMap, entered: Iterable[HexCoord], harbours: frozenset[HexCoord]
) -> int:
    """Whole days needed to enter each tile in turn, ignoring delays."""
    total = 0
    for tile in entered:
        cost = entry_cost(world_map, tile, harbours)
        if cost is None:
            raise ValueError(f"tile {tile} is impassable")
        total += cost
    return ceil(total / DAY)


def step(progress: int, cost: int) -> tuple[bool, int]:
    """Spend one day of travel toward a tile; return whether the party enters it."""
    progress += DAY
    if progress >= cost:
        return True, progress - cost
    return False, progress
