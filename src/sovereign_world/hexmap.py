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


@dataclass(frozen=True, slots=True)
class WorldMap:
    width: int
    height: int
    tiles: tuple[Tile, ...]

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("map dimensions must be positive")
        if len(self.tiles) != self.width * self.height:
            raise ValueError("tile count must equal width times height")
        for index, tile in enumerate(self.tiles):
            expected = HexCoord(index % self.width, index // self.width)
            if tile.coord != expected:
                raise ValueError(f"tile {index} has coordinate {tile.coord}, expected {expected}")

    def contains(self, coord: HexCoord) -> bool:
        return 0 <= coord.q < self.width and 0 <= coord.r < self.height

    def tile(self, coord: HexCoord) -> Tile:
        if not self.contains(coord):
            raise KeyError(coord)
        return self.tiles[coord.r * self.width + coord.q]

    def neighbors(self, coord: HexCoord) -> tuple[HexCoord, ...]:
        return tuple(neighbor for neighbor in coord.neighbors() if self.contains(neighbor))

    def content_hash(self) -> str:
        payload = {
            "width": self.width,
            "height": self.height,
            "tiles": [asdict(tile) for tile in self.tiles],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
        return hashlib.sha256(encoded).hexdigest()

