"""Terrain-aware travel: what it costs to enter a tile, and daily progress toward it."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from math import ceil

from sovereign_world.hexmap import HexCoord, Terrain, WorldMap
from sovereign_world.roads import RoadGrade, road_cost

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

MAX_PROGRESS = -(-max(cost for cost in ENTRY_COST.values() if cost is not None) * 3 // 2) + DAY
"""Unspent walking never reaches this: the dearest tile, slowed by siege engines, plus a day."""


Roads = Mapping[HexCoord, RoadGrade]
"""The road grade of each tile that has a road."""


def entry_cost(world_map: WorldMap, coord: HexCoord, roads: Roads | None = None) -> int | None:
    """What entering a tile costs, or None if it cannot be entered; roads make it cheaper."""
    terrain = world_map.tile(coord).terrain
    base = ENTRY_COST[terrain]
    grade = roads.get(coord) if roads else None
    if base is None or grade is None:
        return base
    return road_cost(terrain, grade)


def passable(world_map: WorldMap, tiles: Iterable[HexCoord]) -> bool:
    return all(entry_cost(world_map, tile) is not None for tile in tiles)


def travel_days(
    world_map: WorldMap, entered: Iterable[HexCoord], roads: Roads | None = None
) -> int:
    """Whole days needed to enter each tile in turn, ignoring delays."""
    total = 0
    for tile in entered:
        cost = entry_cost(world_map, tile, roads)
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
