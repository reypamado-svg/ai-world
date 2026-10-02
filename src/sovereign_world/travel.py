"""Terrain-aware travel: what it costs to enter a tile, and daily progress toward it."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from collections.abc import Set as AbstractSet
from math import ceil
from typing import Literal

from sovereign_world.hexmap import HexCoord, Terrain, WorldMap, edge_key
from sovereign_world.roads import RoadGrade, road_cost

DAY = 10
"""One day of travel, in tenths of a day."""

ENTRY_COST: dict[Terrain, int | None] = {
    Terrain.GRASSLAND: 10,
    Terrain.FOREST: 15,
    Terrain.DESERT: 15,
    Terrain.TUNDRA: 15,
    Terrain.MOUNTAIN: 30,
    Terrain.HILLS: 20,
    Terrain.SNOW: 50,
    Terrain.WATER: None,
}
"""Cost of entering a tile, in tenths of a day; None means impassable."""


STREAM_FLOW = 4
"""Rivers below this flow are streams."""
DEEP_FLOW = 10
"""Rivers at or above this flow are too deep to wade."""

Depth = Literal["stream", "river", "deep"]

CROSSING_COST: dict[Depth, int | None] = {"stream": 5, "river": 10, "deep": None}
"""Extra cost of wading across a river border, in tenths of a day; None means it cannot be
waded."""

MAX_PROGRESS = (
    -(
        -(
            max(cost for cost in ENTRY_COST.values() if cost is not None)
            + max(cost for cost in CROSSING_COST.values() if cost is not None)
        )
        * 3
        // 2
    )
    + DAY
)
"""Unspent walking never reaches this: the dearest tile and river crossing, slowed by siege
engines, plus a day."""

Roads = Mapping[HexCoord, RoadGrade]
"""The road grade of each tile that has a road."""

Bridges = AbstractSet[tuple[HexCoord, HexCoord]]
"""River borders that are bridged, as edge keys (see ``hexmap.edge_key``)."""

NO_BRIDGES: frozenset[tuple[HexCoord, HexCoord]] = frozenset()


def river_depth(flow: int) -> Depth:
    """How deep a river of this flow is: stream, river or deep."""
    if flow < STREAM_FLOW:
        return "stream"
    if flow < DEEP_FLOW:
        return "river"
    return "deep"


def crossing(
    world_map: WorldMap, origin: HexCoord, coord: HexCoord, bridges: Bridges = NO_BRIDGES
) -> int | None:
    """The extra cost of crossing the border from origin into coord: 0 where no river runs
    or a bridge stands, a wading cost for a stream or river, and None where a deep river
    cannot be crossed."""
    river = world_map.river_between(origin, coord)
    if river is None or edge_key(origin, coord) in bridges:
        return 0
    return CROSSING_COST[river_depth(river.flow)]


def entry_cost(
    world_map: WorldMap,
    coord: HexCoord,
    roads: Roads | None = None,
    *,
    origin: HexCoord | None = None,
    bridges: Bridges = NO_BRIDGES,
) -> int | None:
    """What entering a tile costs, or None if it cannot be entered; roads make it cheaper.

    Given the tile the traveller comes from, a river along the border between them is
    crossed too: wading adds to the cost, and a deep river cannot be crossed at all.
    """
    terrain = world_map.tile(coord).terrain
    base = ENTRY_COST[terrain]
    if base is None:
        return None
    grade = roads.get(coord) if roads else None
    cost = base if grade is None else road_cost(terrain, grade)
    if origin is None:
        return cost
    extra = crossing(world_map, origin, coord, bridges)
    return None if extra is None else cost + extra


def _steps(
    entered: Iterable[HexCoord], start: HexCoord | None
) -> Iterable[tuple[HexCoord | None, HexCoord]]:
    """Each tile entered, with the tile it is entered from when that is known."""
    previous = start
    for tile in entered:
        yield previous, tile
        previous = tile if start is not None else None


def passable(
    world_map: WorldMap,
    tiles: Iterable[HexCoord],
    *,
    start: HexCoord | None = None,
    bridges: Bridges = NO_BRIDGES,
) -> bool:
    """Whether every tile can be entered; given the start, also every river border crossed."""
    return all(
        entry_cost(world_map, tile, origin=origin, bridges=bridges) is not None
        for origin, tile in _steps(tiles, start)
    )


def travel_days(
    world_map: WorldMap,
    entered: Iterable[HexCoord],
    roads: Roads | None = None,
    *,
    start: HexCoord | None = None,
    bridges: Bridges = NO_BRIDGES,
) -> int:
    """Whole days needed to enter each tile in turn, ignoring delays; given the start, river
    crossings on the way are counted too."""
    total = 0
    for origin, tile in _steps(entered, start):
        cost = entry_cost(world_map, tile, roads, origin=origin, bridges=bridges)
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


def way_to(
    world_map: WorldMap,
    start: HexCoord,
    goals: frozenset[HexCoord],
    *,
    bridges: Bridges = NO_BRIDGES,
) -> tuple[HexCoord, ...] | None:
    """The shortest way over passable land from start to the nearest goal, by tiles.

    Ties between equally short ways go to neighbours in a fixed order. Returns None when
    no goal can be reached.
    """
    if start in goals:
        return (start,)
    previous: dict[HexCoord, HexCoord | None] = {start: None}
    frontier = [start]
    while frontier:
        following: list[HexCoord] = []
        for tile in frontier:
            for neighbor in sorted(tile.neighbors()):
                if neighbor in previous or not world_map.contains(neighbor):
                    continue
                if entry_cost(world_map, neighbor, origin=tile, bridges=bridges) is None:
                    continue
                previous[neighbor] = tile
                if neighbor in goals:
                    path = [neighbor]
                    while (back := previous[path[-1]]) is not None:
                        path.append(back)
                    return tuple(reversed(path))
                following.append(neighbor)
        frontier = following
    return None
