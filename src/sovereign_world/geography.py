"""World generation, version 2: regions, climate, logical neighbours and rivers that flow.

Every tile is shaped by its surroundings. The map is first split into regions, each with one
dominant landscape (highland, plains, woodland, dry basin, cold upland or sea); elevation,
temperature and moisture are smooth fields over those regions; a neighbour pass then removes
pairings that cannot sit side by side (snow beside grassland, desert beside forest), so ranges
step down through hills and climates change gradually. Rivers start on high, wet ground and
run downhill along the borders between tiles, joining into larger rivers until they reach a
lake, the sea or the map edge.

All arithmetic is integer and every random draw comes from a named stream, so a seed always
produces the same world.
"""

from __future__ import annotations

import heapq
from collections import deque
from math import isqrt

import numpy as np

from sovereign_world.hexmap import HexCoord, RiverEdge, Terrain, Tile, WorldMap, corner_tiles
from sovereign_world.rng import StableRng

_OFFSETS = ((1, 0), (1, -1), (0, -1), (-1, 0), (-1, 1), (0, 1))

HIGHLAND, PLAINS, WOODLAND, DRY_BASIN, COLD_UPLAND, SEA = range(6)
_TARGET_ELEVATION = {
    HIGHLAND: 850,
    PLAINS: 350,
    WOODLAND: 400,
    DRY_BASIN: 300,
    COLD_UPLAND: 650,
}

WATER_BELOW = 150
HILLS_FROM = 650
MOUNTAIN_FROM = 800
SNOW_BELOW = 120
"""Mountains colder than this are snow-covered."""

ALLOWED: dict[Terrain, frozenset[Terrain]] = {
    Terrain.WATER: frozenset(Terrain),
    Terrain.SNOW: frozenset({Terrain.SNOW, Terrain.MOUNTAIN, Terrain.TUNDRA, Terrain.WATER}),
    Terrain.MOUNTAIN: frozenset(
        {
            Terrain.MOUNTAIN,
            Terrain.SNOW,
            Terrain.HILLS,
            Terrain.TUNDRA,
            Terrain.FOREST,
            Terrain.WATER,
        }
    ),
    Terrain.HILLS: frozenset(Terrain) - {Terrain.SNOW},
    Terrain.TUNDRA: frozenset(Terrain) - {Terrain.DESERT},
    Terrain.DESERT: frozenset({Terrain.DESERT, Terrain.GRASSLAND, Terrain.HILLS, Terrain.WATER}),
    Terrain.FOREST: frozenset(
        {
            Terrain.FOREST,
            Terrain.MOUNTAIN,
            Terrain.GRASSLAND,
            Terrain.HILLS,
            Terrain.TUNDRA,
            Terrain.WATER,
        }
    ),
    Terrain.GRASSLAND: frozenset(Terrain) - {Terrain.MOUNTAIN, Terrain.SNOW},
}
"""Which terrains may border each other; the relation is symmetric."""

_ELEVATION_BAND: dict[Terrain, tuple[int, int]] = {
    Terrain.WATER: (0, WATER_BELOW - 1),
    Terrain.GRASSLAND: (WATER_BELOW, HILLS_FROM - 1),
    Terrain.FOREST: (WATER_BELOW, MOUNTAIN_FROM - 1),
    Terrain.DESERT: (WATER_BELOW, HILLS_FROM - 1),
    Terrain.TUNDRA: (WATER_BELOW, MOUNTAIN_FROM - 1),
    Terrain.HILLS: (HILLS_FROM, MOUNTAIN_FROM - 1),
    Terrain.MOUNTAIN: (MOUNTAIN_FROM, 1_000),
    Terrain.SNOW: (MOUNTAIN_FROM, 1_000),
}

RIVER_SOURCE_ELEVATION = 550
RIVER_SOURCE_MOISTURE = 380
RIVER_SOURCE_SPACING = 3
LAKE_SHARE = 990
"""Lowland tiles whose lake noise ranks at or above this hold a lake."""

Vertex = tuple[HexCoord, HexCoord, HexCoord]
"""A corner shared by three tiles, as their sorted coordinates."""


class _Grid:
    """Index arithmetic for one map size."""

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.size = width * height
        index = np.arange(self.size, dtype=np.int64)
        self.q = index % width
        self.r = index // width
        self.x = 16 * self.q + 8 * self.r
        self.y = 14 * self.r
        neighbours = np.full((self.size, 6), -1, dtype=np.int64)
        for slot, (dq, dr) in enumerate(_OFFSETS):
            nq = self.q + dq
            nr = self.r + dr
            inside = (nq >= 0) & (nq < width) & (nr >= 0) & (nr < height)
            neighbours[inside, slot] = nr[inside] * width + nq[inside]
        self.neighbours = neighbours
        self.lists: list[list[int]] = [[int(n) for n in row if n >= 0] for row in neighbours]

    def index(self, coord: HexCoord) -> int:
        return coord.r * self.width + coord.q

    def contains(self, coord: HexCoord) -> bool:
        return 0 <= coord.q < self.width and 0 <= coord.r < self.height

    def mean_with_neighbours(self, values: np.ndarray) -> np.ndarray:
        valid = self.neighbours >= 0
        gathered = np.where(valid, values[np.maximum(self.neighbours, 0)], 0)
        total: np.ndarray = values + gathered.sum(axis=1)
        mean: np.ndarray = total // (1 + valid.sum(axis=1))
        return mean


def _rank(values: np.ndarray) -> np.ndarray:
    """Spread values evenly over 0..1000 by rank, so thresholds mean the same on every seed."""
    order = np.argsort(values, kind="stable")
    ranks = np.empty_like(order)
    ranks[order] = np.arange(values.size, dtype=np.int64)
    return ranks * 1_000 // max(1, values.size - 1)


def _noise(rng: np.random.Generator, grid: _Grid, period: int, octaves: int = 4) -> np.ndarray:
    """Smooth integer value noise over the map, features about ``period`` tiles across."""
    total = np.zeros(grid.size, dtype=np.int64)
    for octave in range(octaves):
        spacing = max(16, (period * 16) >> octave)
        columns = int(grid.x.max()) // spacing + 2
        rows = int(grid.y.max()) // spacing + 2
        lattice = rng.integers(0, 1_001, size=(rows, columns), dtype=np.int64)
        gx = grid.x // spacing
        gy = grid.y // spacing
        tx = (grid.x % spacing) * 1_024 // spacing
        ty = (grid.y % spacing) * 1_024 // spacing
        sx = (tx * tx * (3_072 - 2 * tx)) >> 20
        sy = (ty * ty * (3_072 - 2 * ty)) >> 20
        top = (lattice[gy, gx] * (1_024 - sx) + lattice[gy, gx + 1] * sx) >> 10
        bottom = (lattice[gy + 1, gx] * (1_024 - sx) + lattice[gy + 1, gx + 1] * sx) >> 10
        total += ((top * (1_024 - sy) + bottom * sy) >> 10) * (1_000 >> octave)
    return _rank(total)


def _regions(rng: StableRng, grid: _Grid, prefix: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Assign each tile to a region and each region a landscape; returns tile regions,
    region biomes and the latitude temperature of every tile."""
    count = max(6, grid.size // 700)
    columns = max(1, isqrt(count * grid.width // grid.height))
    rows = -(-count // columns)
    jitter = rng.stream(f"{prefix}:region-seeds")
    seed_q = np.empty(count, dtype=np.int64)
    seed_r = np.empty(count, dtype=np.int64)
    for number in range(count):
        cell_q, cell_r = number % columns, number // columns
        span_q = max(1, grid.width // columns)
        span_r = max(1, grid.height // rows)
        offset_q = int(jitter.integers(span_q))
        offset_r = int(jitter.integers(span_r))
        seed_q[number] = min(grid.width - 1, cell_q * grid.width // columns + offset_q)
        seed_r[number] = min(grid.height - 1, cell_r * grid.height // rows + offset_r)

    warp_period = max(3, min(grid.width, grid.height) // 6)
    warp_q = (_noise(rng.stream(f"{prefix}:warp-q"), grid, warp_period, 2) - 500) * 3 // 500
    warp_r = (_noise(rng.stream(f"{prefix}:warp-r"), grid, warp_period, 2) - 500) * 3 // 500
    dq = (grid.q + warp_q)[:, None] - seed_q[None, :]
    dr = (grid.r + warp_r)[:, None] - seed_r[None, :]
    distance = (np.abs(dq) + np.abs(dr) + np.abs(dq + dr)) // 2
    region = np.argmin(distance, axis=1)

    latitude = 250 + 650 * grid.r // max(1, grid.height - 1)
    rolls = rng.stream(f"{prefix}:biomes")
    biome_roll = rolls.integers(0, 100, size=count)
    sea_roll = rolls.integers(0, 100, size=count)
    border = max(2, min(grid.width, grid.height) // 20)
    biomes = np.empty(count, dtype=np.int64)
    seas = 0
    for number in range(count):
        near_border = (
            seed_q[number] < border
            or seed_q[number] >= grid.width - border
            or seed_r[number] < border
            or seed_r[number] >= grid.height - border
        )
        if near_border and sea_roll[number] < 60 and seas < count // 3:
            biomes[number] = SEA
            seas += 1
            continue
        roll = int(biome_roll[number])
        members = region == number
        warm = int(latitude[members].mean()) >= 550 if members.any() else False
        if roll < 20:
            biomes[number] = HIGHLAND
        elif roll < 50:
            biomes[number] = PLAINS
        elif roll < 75:
            biomes[number] = WOODLAND
        elif roll < 90:
            biomes[number] = DRY_BASIN if warm else PLAINS
        else:
            biomes[number] = COLD_UPLAND
    return region, biomes, latitude


def _distance_to(grid: _Grid, sources: np.ndarray, limit: int) -> np.ndarray:
    """Hex steps from every tile to the nearest marked tile, capped at ``limit``."""
    distance = np.full(grid.size, limit, dtype=np.int64)
    frontier: deque[int] = deque()
    for marked in np.flatnonzero(sources):
        distance[marked] = 0
        frontier.append(int(marked))
    while frontier:
        index = frontier.popleft()
        step = int(distance[index]) + 1
        if step >= limit:
            continue
        for neighbour in grid.lists[index]:
            if distance[neighbour] > step:
                distance[neighbour] = step
                frontier.append(neighbour)
    return distance


def _classify(elevation: int, moisture: int, temperature: int) -> Terrain:
    if elevation < WATER_BELOW:
        return Terrain.WATER
    if elevation >= MOUNTAIN_FROM:
        return Terrain.SNOW if temperature < SNOW_BELOW else Terrain.MOUNTAIN
    if elevation >= HILLS_FROM:
        return Terrain.TUNDRA if temperature < 150 else Terrain.HILLS
    if temperature < 180:
        return Terrain.TUNDRA
    if moisture < 230 and temperature > 600:
        return Terrain.DESERT
    if moisture >= 500:
        return Terrain.FOREST
    return Terrain.GRASSLAND


def _bridge(loser: Terrain, other: Terrain) -> Terrain:
    """What a tile becomes when it cannot sit beside its neighbour."""
    if loser is Terrain.SNOW:
        return Terrain.MOUNTAIN
    if loser is Terrain.MOUNTAIN:
        return Terrain.HILLS
    if other in (Terrain.MOUNTAIN, Terrain.SNOW):
        return Terrain.TUNDRA if loser is Terrain.HILLS else Terrain.HILLS
    return Terrain.GRASSLAND


def _same_count(terrain: list[Terrain], neighbours: list[int], value: Terrain) -> int:
    return sum(terrain[n] is value for n in neighbours)


def _harmonise(grid: _Grid, terrain: list[Terrain]) -> None:
    """Remove lone speckles, then every pairing the neighbour table forbids."""
    for index in range(grid.size):
        here = terrain[index]
        if here is Terrain.WATER:
            continue
        around = [terrain[n] for n in grid.lists[index]]
        if here in around:
            continue
        land = [value for value in around if value is not Terrain.WATER]
        for value in sorted(set(land)):
            if land.count(value) >= 4:
                terrain[index] = value
                break
    for _sweep in range(64):
        changed = False
        for index in range(grid.size):
            for neighbour in grid.lists[index]:
                if neighbour < index:
                    continue
                first, second = terrain[index], terrain[neighbour]
                if second in ALLOWED[first]:
                    continue
                first_same = _same_count(terrain, grid.lists[index], first)
                second_same = _same_count(terrain, grid.lists[neighbour], second)
                if first_same < second_same:
                    terrain[index] = _bridge(first, second)
                else:
                    terrain[neighbour] = _bridge(second, first)
                changed = True
                if terrain[index] is not first:
                    break
        if not changed:
            return
    raise RuntimeError("terrain neighbours did not settle")


def _vertices_of(coord: HexCoord) -> list[Vertex]:
    around = coord.neighbors()
    corners: list[Vertex] = []
    for slot in range(6):
        trio = sorted((coord, around[slot], around[(slot + 1) % 6]))
        corners.append((trio[0], trio[1], trio[2]))
    return corners


def _next_corners(vertex: Vertex) -> list[tuple[tuple[HexCoord, HexCoord], Vertex]]:
    """The three borders leaving a corner, each with the corner at its other end."""
    result: list[tuple[tuple[HexCoord, HexCoord], Vertex]] = []
    for skip in range(3):
        first, second = (coord for position, coord in enumerate(vertex) if position != skip)
        third = vertex[skip]
        other = next(coord for coord in corner_tiles(first, second) if coord != third)
        trio = sorted((first, second, other))
        result.append(((first, second), (trio[0], trio[1], trio[2])))
    return result


class _Rivers:
    """Rivers that drain every corner of the land toward water or the map edge.

    Corner heights are flood-filled from the sea, lakes and the map edge, lowest first
    (priority flood), so each dry corner learns which neighbouring corner its water runs to;
    hollows fill up and spill over their lowest rim instead of trapping the river. A river
    then follows those links from its source, and flow adds up wherever rivers meet.
    """

    def __init__(
        self,
        grid: _Grid,
        terrain: list[Terrain],
        elevation: np.ndarray,
        moisture: np.ndarray,
    ) -> None:
        self.grid = grid
        self.terrain = terrain
        self.elevation = elevation
        self.moisture = moisture
        self.flow: dict[tuple[HexCoord, HexCoord], int] = {}
        self.downstream: dict[tuple[HexCoord, HexCoord], HexCoord | None] = {}
        self.touched: set[HexCoord] = set()
        self.filled: dict[Vertex, int] = {}
        self.outlet: dict[Vertex, tuple[tuple[HexCoord, HexCoord], Vertex]] = {}
        self._drain()

    def _dry(self, vertex: Vertex) -> bool:
        return all(
            self.grid.contains(coord) and self.terrain[self.grid.index(coord)] is not Terrain.WATER
            for coord in vertex
        )

    def height(self, vertex: Vertex) -> int:
        inside = [self.grid.index(coord) for coord in vertex if self.grid.contains(coord)]
        return int(sum(int(self.elevation[i]) for i in inside) // len(inside))

    def wetness(self, vertex: Vertex) -> int:
        return int(sum(int(self.moisture[self.grid.index(coord)]) for coord in vertex) // 3)

    def _corners(self) -> list[Vertex]:
        seen: set[Vertex] = set()
        corners: list[Vertex] = []
        for tile_index in range(self.grid.size):
            coord = HexCoord(int(self.grid.q[tile_index]), int(self.grid.r[tile_index]))
            for vertex in _vertices_of(coord):
                if vertex not in seen:
                    seen.add(vertex)
                    corners.append(vertex)
        return corners

    def _drain(self) -> None:
        heap: list[tuple[int, Vertex]] = []
        for vertex in self._corners():
            if not self._dry(vertex):
                self.filled[vertex] = self.height(vertex)
                heapq.heappush(heap, (self.filled[vertex], vertex))
        while heap:
            level, vertex = heapq.heappop(heap)
            for border, after in _next_corners(vertex):
                if after in self.filled or not self._dry(after):
                    continue
                self.filled[after] = max(level, self.height(after))
                self.outlet[after] = (border, vertex)
                heapq.heappush(heap, (self.filled[after], after))

    def sources(self, limit: int) -> list[Vertex]:
        """Wet high ground: the highest quarter of the land, or anything above the source line."""
        heights = sorted(self.height(vertex) for vertex in self.outlet)
        if not heights:
            return []
        threshold = min(RIVER_SOURCE_ELEVATION, heights[len(heights) * 3 // 4])
        candidates = sorted(
            (-self.height(vertex), vertex)
            for vertex in self.outlet
            if self.height(vertex) >= threshold and self.wetness(vertex) >= RIVER_SOURCE_MOISTURE
        )
        chosen: list[Vertex] = []
        for _height, vertex in candidates:
            if all(vertex[0].distance(other[0]) >= RIVER_SOURCE_SPACING for other in chosen):
                chosen.append(vertex)
                if len(chosen) == limit:
                    break
        return chosen

    def run(self, source: Vertex) -> None:
        weight = 1 + self.wetness(source) // 250
        vertex = source
        while vertex in self.outlet:
            border, after = self.outlet[vertex]
            third = next(coord for coord in after if coord not in border)
            self.flow[border] = self.flow.get(border, 0) + weight
            self.downstream[border] = third if self.grid.contains(third) else None
            self.touched.update(border)
            vertex = after

    def edges(self) -> tuple[RiverEdge, ...]:
        return tuple(
            sorted(
                RiverEdge(a=a, b=b, flow=self.flow[(a, b)], downstream=self.downstream[(a, b)])
                for a, b in self.flow
            )
        )


def generate_map(config_width: int, config_height: int, rng: StableRng, attempt: int) -> WorldMap:
    grid = _Grid(config_width, config_height)
    prefix = f"world:{attempt}:v2"
    region, biomes, latitude = _regions(rng, grid, prefix)
    tile_biome = biomes[region]
    span = max(4, min(grid.width, grid.height) // 4)

    base = _noise(rng.stream(f"{prefix}:elevation"), grid, span)
    targets = np.array([_TARGET_ELEVATION.get(b, 0) for b in range(6)], dtype=np.int64)
    elevation = (4 * base + 6 * targets[tile_biome]) // 10
    elevation = np.where(tile_biome == SEA, base // 8, elevation)
    ridges = _noise(rng.stream(f"{prefix}:ridges"), grid, max(4, grid.width // 4), 3)
    ridged = 1_000 - np.abs(2 * ridges - 1_000)
    elevation = elevation + np.where((tile_biome == HIGHLAND) & (ridged >= 600), 150, 0)
    for _pass in range(2):
        elevation = grid.mean_with_neighbours(elevation)
    elevation = np.clip(elevation, 0, 1_000)

    lakes = _noise(rng.stream(f"{prefix}:lakes"), grid, 4, 2)
    lowland = (tile_biome == PLAINS) | (tile_biome == WOODLAND) | (tile_biome == COLD_UPLAND)
    elevation = np.where(lowland & (lakes >= LAKE_SHARE), WATER_BELOW - 10, elevation)

    warmth = _noise(rng.stream(f"{prefix}:temperature"), grid, max(3, span // 2), 3)
    temperature = latitude + (warmth - 500) // 5 - np.maximum(0, elevation - 450) * 6 // 10
    temperature = np.clip(temperature, 0, 1_000)

    near_water = _distance_to(grid, elevation < WATER_BELOW, 6)
    rows = elevation.reshape(grid.height, grid.width)
    upwind = np.zeros_like(rows)
    for step in range(1, 6):
        upwind[:, step:] = np.maximum(upwind[:, step:], rows[:, :-step])
    shadow = (np.maximum(0, upwind - rows) * 4 // 10).reshape(grid.size)
    damp = _noise(rng.stream(f"{prefix}:moisture"), grid, max(3, span // 2))
    moisture = (
        damp * 6 // 10
        + np.maximum(0, 300 - 60 * near_water)
        - shadow
        + np.where(tile_biome == WOODLAND, 200, 0)
        - np.where(tile_biome == DRY_BASIN, 200, 0)
    )
    moisture = np.clip(moisture, 0, 1_000)

    terrain = [
        _classify(int(elevation[i]), int(moisture[i]), int(temperature[i]))
        for i in range(grid.size)
    ]
    for index in range(grid.size):
        around = [terrain[n] for n in grid.lists[index]]
        water_around = sum(value is Terrain.WATER for value in around)
        if terrain[index] is not Terrain.WATER and water_around >= 4:
            terrain[index] = Terrain.WATER
    _harmonise(grid, terrain)
    for index in range(grid.size):
        low, high = _ELEVATION_BAND[terrain[index]]
        elevation[index] = min(high, max(low, int(elevation[index])))

    rivers = _Rivers(grid, terrain, elevation, moisture)
    for source in rivers.sources(max(4, grid.size // 150)):
        rivers.run(source)
    edges = rivers.edges()
    riverside = rivers.touched

    soil_noise = _noise(rng.stream(f"{prefix}:soil"), grid, 3, 2)
    timber_noise = _noise(rng.stream(f"{prefix}:timber"), grid, 3, 2)
    stone_noise = _noise(rng.stream(f"{prefix}:stone"), grid, 3, 2)
    ore_noise = _noise(rng.stream(f"{prefix}:ore"), grid, 3, 2)
    tiles: list[Tile] = []
    for index in range(grid.size):
        coord = HexCoord(int(grid.q[index]), int(grid.r[index]))
        kind = terrain[index]
        height = int(elevation[index])
        river = coord in riverside
        soil = (
            (4 * int(soil_noise[index]) + 3 * (1_000 - height)) // 10
            + (150 if river else 0)
            + _SOIL_BONUS[kind]
        )
        timber = int(timber_noise[index]) // 3 + _TIMBER_BONUS[kind]
        stone = int(stone_noise[index]) // 3 + _STONE_BONUS[kind] + max(0, height - 600) // 4
        ore = int(ore_noise[index]) // 2 + _ORE_BONUS[kind]
        tiles.append(
            Tile(
                coord=coord,
                terrain=kind,
                elevation=height,
                moisture=int(moisture[index]),
                temperature=int(temperature[index]),
                soil=_clamp(soil),
                timber=_clamp(timber),
                stone=_clamp(stone),
                ore=_clamp(ore),
                river=river,
            )
        )
    return WorldMap(width=grid.width, height=grid.height, tiles=tuple(tiles), rivers=edges)


def _clamp(value: int) -> int:
    return min(1_000, max(0, value))


_SOIL_BONUS = {
    Terrain.WATER: 0,
    Terrain.GRASSLAND: 100,
    Terrain.FOREST: 50,
    Terrain.HILLS: -100,
    Terrain.TUNDRA: -150,
    Terrain.DESERT: -200,
    Terrain.MOUNTAIN: -300,
    Terrain.SNOW: -300,
}
_TIMBER_BONUS = {
    Terrain.WATER: 0,
    Terrain.GRASSLAND: 150,
    Terrain.FOREST: 550,
    Terrain.HILLS: 200,
    Terrain.TUNDRA: 100,
    Terrain.DESERT: 0,
    Terrain.MOUNTAIN: 50,
    Terrain.SNOW: 0,
}
_STONE_BONUS = {
    Terrain.WATER: 0,
    Terrain.GRASSLAND: 50,
    Terrain.FOREST: 50,
    Terrain.HILLS: 400,
    Terrain.TUNDRA: 50,
    Terrain.DESERT: 200,
    Terrain.MOUNTAIN: 550,
    Terrain.SNOW: 450,
}
_ORE_BONUS = {
    Terrain.WATER: 0,
    Terrain.GRASSLAND: 0,
    Terrain.FOREST: 0,
    Terrain.HILLS: 250,
    Terrain.TUNDRA: 0,
    Terrain.DESERT: 0,
    Terrain.MOUNTAIN: 300,
    Terrain.SNOW: 300,
}


def foot_component(world_map: WorldMap, deep_flow: int) -> frozenset[HexCoord]:
    """The largest set of land tiles people can walk between: water and deep rivers block."""
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
            for neighbour in world_map.neighbors(coord):
                if neighbour not in unvisited:
                    continue
                river = world_map.river_between(coord, neighbour)
                if river is not None and river.flow >= deep_flow:
                    continue
                unvisited.discard(neighbour)
                component.add(neighbour)
                frontier.append(neighbour)
        if len(component) > len(largest):
            largest = component
    return frozenset(largest)
