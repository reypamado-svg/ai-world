"""Finite axial hex-grid primitives."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import StrEnum


@dataclass(frozen=True, order=True, slots=True)
class HexCoord:
    q: int
    r: int

    def neighbors(self) -> tuple[HexCoord, ...]:
        offsets = ((1, 0), (1, -1), (0, -1), (-1, 0), (-1, 1), (0, 1))
        return tuple(HexCoord(self.q + dq, self.r + dr) for dq, dr in offsets)

    def distance(self, other: HexCoord) -> int:
        dq = self.q - other.q
        dr = self.r - other.r
        return (abs(dq) + abs(dr) + abs(dq + dr)) // 2


class Terrain(StrEnum):
    WATER = "water"
    GRASSLAND = "grassland"
    FOREST = "forest"
    MOUNTAIN = "mountain"
    DESERT = "desert"
    TUNDRA = "tundra"
    HILLS = "hills"
    SNOW = "snow"


@dataclass(frozen=True, slots=True)
class Tile:
    coord: HexCoord
    terrain: Terrain
    elevation: int
    moisture: int
    temperature: int
    soil: int
    timber: int
    stone: int
    ore: int
    river: bool = False

    @property
    def has_water(self) -> bool:
        return self.terrain is Terrain.WATER or self.river


@dataclass(frozen=True, order=True, slots=True)
class RiverEdge:
    """One stretch of river along the border between two neighbouring land tiles.

    Water runs toward the corner this border shares with ``downstream``, the third tile there;
    None means the river leaves the map at that corner. ``flow`` grows downstream as tributaries
    join, and sets how deep the river is.
    """

    a: HexCoord
    b: HexCoord
    flow: int
    downstream: HexCoord | None


def edge_key(first: HexCoord, second: HexCoord) -> tuple[HexCoord, HexCoord]:
    """The two tiles of a border, in the order a river edge stores them."""
    return (first, second) if first < second else (second, first)


@dataclass(frozen=True)
class WorldMap:
    width: int
    height: int
    tiles: tuple[Tile, ...]
    rivers: tuple[RiverEdge, ...] = ()
    """River borders, sorted; maps made before rivers had courses have none."""

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("map dimensions must be positive")
        if len(self.tiles) != self.width * self.height:
            raise ValueError("tile count must equal width times height")
        for index, tile in enumerate(self.tiles):
            expected = HexCoord(index % self.width, index // self.width)
            if tile.coord != expected:
                raise ValueError(f"tile {index} has coordinate {tile.coord}, expected {expected}")
        index_by_edge: dict[tuple[HexCoord, HexCoord], RiverEdge] = {}
        for edge in self.rivers:
            if not edge.a < edge.b or edge.b not in edge.a.neighbors():
                raise ValueError(f"river edge {edge.a}-{edge.b} is not an ordered border")
            if not (self.contains(edge.a) and self.contains(edge.b)):
                raise ValueError(f"river edge {edge.a}-{edge.b} leaves the map")
            if edge.flow < 1:
                raise ValueError("river flow must be positive")
            if edge.downstream is not None and edge.downstream not in corner_tiles(edge.a, edge.b):
                raise ValueError(f"river edge {edge.a}-{edge.b} flows to a tile beside neither")
            if (edge.a, edge.b) in index_by_edge:
                raise ValueError(f"river edge {edge.a}-{edge.b} is listed twice")
            index_by_edge[(edge.a, edge.b)] = edge
        if list(self.rivers) != sorted(self.rivers):
            raise ValueError("river edges must be sorted")
        object.__setattr__(self, "_river_index", index_by_edge)

    def contains(self, coord: HexCoord) -> bool:
        return 0 <= coord.q < self.width and 0 <= coord.r < self.height

    def tile(self, coord: HexCoord) -> Tile:
        if not self.contains(coord):
            raise KeyError(coord)
        return self.tiles[coord.r * self.width + coord.q]

    def neighbors(self, coord: HexCoord) -> tuple[HexCoord, ...]:
        return tuple(neighbor for neighbor in coord.neighbors() if self.contains(neighbor))

    def __deepcopy__(self, memo: dict[int, object]) -> WorldMap:
        # Frozen all the way down (tiles, rivers, coordinates): a copy can share it.
        return self

    def river_between(self, first: HexCoord, second: HexCoord) -> RiverEdge | None:
        """The river running along the border of two tiles, if any."""
        index: dict[tuple[HexCoord, HexCoord], RiverEdge] = self.__dict__["_river_index"]
        return index.get(edge_key(first, second))

    def content_hash(self) -> str:
        payload: dict[str, object] = {
            "width": self.width,
            "height": self.height,
            "tiles": [asdict(tile) for tile in self.tiles],
        }
        if self.rivers:
            payload["rivers"] = [asdict(edge) for edge in self.rivers]
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
        return hashlib.sha256(encoded).hexdigest()


def corner_tiles(first: HexCoord, second: HexCoord) -> tuple[HexCoord, ...]:
    """The two tiles beside both ends of a border: one at each of its corners."""
    around = set(second.neighbors())
    return tuple(coord for coord in first.neighbors() if coord in around)
