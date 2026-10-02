"""Land cover inside each tile (world generator version 3).

A 25 km tile is never one thing: grassland has groves, ponds and stony
patches; forest has clearings; hills are pasture with woods and scrub on the
slopes. Version 3 records, for every land tile, what share of it each cover
class takes, in basis points summing to 10,000, in `CoverClass` order. Water
tiles have no cover.

The shares start from a typical mix for the terrain and follow the tile's own
values and its neighbours: wetter tiles carry more wood and wetland, higher
ones more rock, drier ones more scrub, and cold mountains snowfields. Rules keep
them logical: a desert has open ground or wetland only at an oasis (a river or
water beside it), grassland is never more than 35% wood, a forest always has
clearings, snowfields show only rock, and wetland never covers most of a tile.
Integer arithmetic only, from its own random stream, so version 2 maps are
untouched.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from sovereign_world.geography import SNOW_BELOW, _Grid, _noise
from sovereign_world.hexmap import COVER_CLASSES, COVER_TOTAL, CoverClass, Terrain, WorldMap
from sovereign_world.rng import StableRng

OPEN, WOOD, SCRUB, WETLAND, ROCK, SAND, SNOWFIELD = range(len(COVER_CLASSES))

TERRAINS = tuple(Terrain)
"""Terrain codes used in the arrays below, in enum order."""

PRIOR: dict[Terrain, tuple[int, int, int, int, int, int, int]] = {
    # open, wood, scrub, wetland, rock, sand, snowfield (basis points)
    Terrain.GRASSLAND: (7000, 1500, 1000, 300, 200, 0, 0),
    Terrain.FOREST: (1500, 7000, 700, 500, 300, 0, 0),
    # Temperate hills are mostly pasture, with woods and scrub on the slopes.
    Terrain.HILLS: (3500, 2000, 2000, 200, 2300, 0, 0),
    Terrain.MOUNTAIN: (1000, 500, 1000, 100, 6400, 0, 1000),
    Terrain.DESERT: (300, 0, 1000, 0, 2200, 6500, 0),
    # Moss, lichen and sedge count as open; the wood is the edge of the taiga.
    Terrain.TUNDRA: (6000, 300, 1500, 1000, 1200, 0, 0),
    Terrain.SNOW: (0, 0, 0, 0, 1500, 0, 8500),
}

SNOWFIELD_BELOW = SNOW_BELOW + 150
"""Mountains colder than this carry snowfields; warmer ones have none."""
GRASSLAND_WOOD_CAP = 3500
FOREST_OPEN_FLOOR = 800
WETLAND_CAP = 2500
TUNDRA_WETLAND_CAP = 3000

SINK: dict[Terrain, int] = {
    Terrain.GRASSLAND: OPEN,
    Terrain.FOREST: WOOD,
    Terrain.HILLS: OPEN,
    Terrain.MOUNTAIN: ROCK,
    Terrain.DESERT: SAND,
    Terrain.TUNDRA: OPEN,
    Terrain.SNOW: ROCK,
}
"""Where shares go when a cap moves them off a class."""


def _normalise(values: np.ndarray) -> np.ndarray:
    """Scale each row to sum to COVER_TOTAL; leftover units go to the largest remainders,
    ties to the earlier class."""
    totals = values.sum(axis=1, keepdims=True)
    scaled = values * COVER_TOTAL
    shares = scaled // totals
    remainders = scaled % totals
    missing = COVER_TOTAL - shares.sum(axis=1)
    classes = values.shape[1]
    # Rank classes in each row by remainder (largest first), then by class order.
    order = np.lexsort((np.broadcast_to(np.arange(classes), values.shape), -remainders), axis=1)
    rank = np.empty_like(order)
    np.put_along_axis(rank, order, np.arange(classes)[None, :].repeat(len(values), 0), axis=1)
    result: np.ndarray = shares + (rank < missing[:, None])
    return result


def _cap(shares: np.ndarray, rows: np.ndarray, column: int, cap: int, sink: np.ndarray) -> None:
    """Move whatever exceeds `cap` in one class to each row's sink class."""
    excess = np.maximum(0, shares[rows, column] - cap)
    shares[rows, column] -= excess
    np.add.at(shares, (rows, sink[rows]), excess)


def generate_cover(world_map: WorldMap, rng: StableRng, attempt: int) -> WorldMap:
    """The same map with every land tile's cover shares filled in."""
    grid = _Grid(world_map.width, world_map.height)
    size = grid.size
    terrain = np.zeros(size, dtype=np.int64)
    elevation = np.zeros(size, dtype=np.int64)
    moisture = np.zeros(size, dtype=np.int64)
    temperature = np.zeros(size, dtype=np.int64)
    river = np.zeros(size, dtype=np.int64)
    for tile in world_map.tiles:
        index = grid.index(tile.coord)
        terrain[index] = TERRAINS.index(tile.terrain)
        elevation[index] = tile.elevation
        moisture[index] = tile.moisture
        temperature[index] = tile.temperature
        river[index] = int(tile.river)

    code = {item: TERRAINS.index(item) for item in TERRAINS}
    valid = grid.neighbours >= 0
    around = np.where(valid, terrain[np.maximum(grid.neighbours, 0)], -1)
    forest_nb = (around == code[Terrain.FOREST]).sum(axis=1)
    water_nb = np.minimum(2, (around == code[Terrain.WATER]).sum(axis=1))
    high_nb = ((around == code[Terrain.MOUNTAIN]) | (around == code[Terrain.HILLS])).sum(axis=1)

    prior = np.zeros((len(TERRAINS), len(COVER_CLASSES)), dtype=np.int64)
    for item, mix in PRIOR.items():
        prior[code[item]] = mix
    values = prior[terrain].copy()
    is_ = {item: terrain == code[item] for item in TERRAINS}

    values[:, WOOD] += np.clip((moisture - 500) // 5, -1500, 1500) + 400 * forest_nb
    values[:, WETLAND] += 800 * river + 400 * water_nb + np.maximum(0, moisture - 600) // 4
    values[:, ROCK] += np.maximum(0, elevation - 500) // 4 + 300 * high_nb
    values[:, SCRUB] += np.maximum(0, 450 - moisture) // 5
    values[:, SAND] += np.where(is_[Terrain.DESERT], 2 * np.maximum(0, 300 - moisture), 0)
    values[:, SNOWFIELD] += np.where(
        is_[Terrain.MOUNTAIN], 8 * np.maximum(0, SNOWFIELD_BELOW - temperature), 0
    )

    # A little variety between neighbours, only on classes the tile already has.
    stream = rng.stream(f"world:{attempt}:v3:cover")
    for column in range(len(COVER_CLASSES)):
        jitter = (_noise(stream, grid, period=3, octaves=2) - 500) * 300 // 500
        values[:, column] = np.where(values[:, column] > 0, values[:, column] + jitter, 0)
    values = np.maximum(0, values)

    # Logic before scaling.
    oasis = (river > 0) | (water_nb > 0)
    dry_desert = is_[Terrain.DESERT] & ~oasis
    values[dry_desert, OPEN] = 0
    values[dry_desert, WETLAND] = 0
    values[is_[Terrain.MOUNTAIN] & (temperature >= SNOWFIELD_BELOW), SNOWFIELD] = 0
    # Above the snow line nothing grows: only rock shows through the snow.
    snow = is_[Terrain.SNOW]
    for column in (OPEN, WOOD, SCRUB, WETLAND, SAND):
        values[snow, column] = 0

    land = ~is_[Terrain.WATER]
    shares = np.zeros_like(values)
    shares[land] = _normalise(values[land])

    # Caps and floors, after scaling, through each terrain's sink class.
    sink = np.array([SINK.get(item, OPEN) for item in TERRAINS], dtype=np.int64)[terrain]
    rows = np.flatnonzero(is_[Terrain.GRASSLAND])
    _cap(shares, rows, WOOD, GRASSLAND_WOOD_CAP, sink)
    rows = np.flatnonzero(land & ~is_[Terrain.TUNDRA])
    _cap(shares, rows, WETLAND, WETLAND_CAP, sink)
    rows = np.flatnonzero(is_[Terrain.TUNDRA])
    _cap(shares, rows, WETLAND, TUNDRA_WETLAND_CAP, sink)
    forest = np.flatnonzero(is_[Terrain.FOREST])
    deficit = np.maximum(0, FOREST_OPEN_FLOOR - shares[forest, OPEN])
    shares[forest, OPEN] += deficit
    largest = shares[forest].argmax(axis=1)
    shares[forest, largest] -= deficit

    tiles = tuple(
        replace(tile, cover=tuple(int(value) for value in shares[grid.index(tile.coord)]))
        if tile.terrain is not Terrain.WATER
        else tile
        for tile in world_map.tiles
    )
    return WorldMap(world_map.width, world_map.height, tiles, rivers=world_map.rivers)


def share(cover: tuple[int, ...], item: CoverClass) -> int:
    """A tile's share of one cover class, in basis points (0 without cover)."""
    return cover[COVER_CLASSES.index(item)] if cover else 0
