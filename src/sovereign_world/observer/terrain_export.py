"""Export engine terrain and day-0 settlements for the observer prototype.

The export runs the engine's own world generation for a seed and writes what
it produces, chunk by chunk, so the browser never needs the whole world at
once. It advances no days, opens no store and writes nothing outside the
output directory.

Two kinds of numbers are kept apart in the manifest:

- ``engine`` and ``engine_day0`` are authoritative engine data, including the
  world rule for tile spacing and travel costs (``engine.travel``): axial hex
  tiles, 0-1000 attributes, and the settlements and founders that exist on
  day 0 before any day is simulated.
- ``presentation`` holds display constants (projection, chunking). They
  have no simulation meaning.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from sovereign_world.config import CURRENT_GENERATOR, ENGINE_VERSION, RunManifest, WorldConfig
from sovereign_world.hexmap import COVER_CLASSES, HexCoord, Terrain, Tile, WorldMap
from sovereign_world.rng import StableRng
from sovereign_world.state import build_initial_state
from sovereign_world.travel import (
    CROSSING_COST,
    DAY,
    DEEP_FLOW,
    ENTRY_COST,
    STREAM_FLOW,
    TILE_SPACING_M,
)
from sovereign_world.worldgen import generate_world

TERRAIN_CODES: tuple[Terrain, ...] = tuple(Terrain)
TILE_FIELDS: tuple[str, ...] = (
    "q",
    "r",
    "terrain",
    "elevation",
    "moisture",
    "temperature",
    "soil",
    "timber",
    "stone",
    "ore",
    "river",
    "cover",
)
OVERVIEW_MAX = 256
EXPORT_VERSION = 4


@dataclass(frozen=True, slots=True)
class ExportSummary:
    out_dir: Path
    tiles: int
    chunks: int
    files: tuple[str, ...]


def encode(payload: Any) -> bytes:
    """A terrain file's bytes: canonical JSON and a newline."""
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return (text + "\n").encode("ascii")


@dataclass(frozen=True, slots=True)
class TerrainBundle:
    """Every file of a terrain export, by its path under the export directory, in write
    order (the O3 server serves these bytes; the export writes them)."""

    files: dict[str, bytes]
    tiles: int
    chunks: int

    def write(self, out_dir: Path) -> ExportSummary:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "chunks").mkdir(exist_ok=True)
        for name, raw in self.files.items():
            (out_dir / name).write_bytes(raw)
        return ExportSummary(
            out_dir=out_dir, tiles=self.tiles, chunks=self.chunks, files=tuple(self.files)
        )


def _tile_row(tile: Tile) -> list[int | str | bool | list[int]]:
    return [
        tile.coord.q,
        tile.coord.r,
        tile.terrain.value,
        tile.elevation,
        tile.moisture,
        tile.temperature,
        tile.soil,
        tile.timber,
        tile.stone,
        tile.ore,
        tile.river,
        list(tile.cover),
    ]


def _overview(tiles: tuple[Tile, ...], width: int, height: int) -> dict[str, Any]:
    """Terrain codes for the minimap, majority-downsampled to at most 256 a side."""
    step = max(1, -(-max(width, height) // OVERVIEW_MAX))
    out_w = -(-width // step)
    out_h = -(-height // step)
    codes = []
    for oy in range(out_h):
        for ox in range(out_w):
            counts: Counter[int] = Counter()
            for r in range(oy * step, min(height, (oy + 1) * step)):
                for q in range(ox * step, min(width, (ox + 1) * step)):
                    counts[TERRAIN_CODES.index(tiles[r * width + q].terrain)] += 1
            codes.append(max(sorted(counts), key=lambda code: counts[code]))
    return {
        "width": out_w,
        "height": out_h,
        "step": step,
        "terrain_codes": [terrain.value for terrain in TERRAIN_CODES],
        "codes": "".join(str(code) for code in codes),
    }


def _lakes(world_map: WorldMap) -> list[list[int]]:
    """Water tiles in bodies that do not reach the map edge, as [q, r]."""
    water = {tile.coord for tile in world_map.tiles if tile.terrain is Terrain.WATER}
    lakes: list[HexCoord] = []
    unvisited = set(water)
    for origin in sorted(water):
        if origin not in unvisited:
            continue
        body = [origin]
        unvisited.discard(origin)
        frontier = [origin]
        while frontier:
            coord = frontier.pop()
            for neighbour in world_map.neighbors(coord):
                if neighbour in unvisited:
                    unvisited.discard(neighbour)
                    body.append(neighbour)
                    frontier.append(neighbour)
        edge = any(len(world_map.neighbors(coord)) < 6 for coord in body)
        if not edge:
            lakes.extend(body)
    return [[coord.q, coord.r] for coord in sorted(lakes)]


def _hydrology(world_map: WorldMap) -> dict[str, Any]:
    return {
        "fields": ["aq", "ar", "bq", "br", "flow", "down_q", "down_r"],
        "edges": [
            [
                edge.a.q,
                edge.a.r,
                edge.b.q,
                edge.b.r,
                edge.flow,
                None if edge.downstream is None else edge.downstream.q,
                None if edge.downstream is None else edge.downstream.r,
            ]
            for edge in world_map.rivers
        ],
        "deep_flow": DEEP_FLOW,
        "lakes": _lakes(world_map),
    }


def export_terrain(
    seed: int, width: int, height: int, out_dir: Path, chunk_tiles: int = 8
) -> ExportSummary:
    """Write manifest, day-0 settlements, overview and terrain chunks to ``out_dir``."""
    config = WorldConfig(seed=seed, width=width, height=height)
    manifest = RunManifest(
        run_id=uuid5(NAMESPACE_URL, f"ai-world/observer-export/{seed}/{width}x{height}"),
        engine_version=ENGINE_VERSION,
        config=config,
        generator_version=CURRENT_GENERATOR,
    )
    return export_terrain_for(manifest, out_dir, chunk_tiles)


def export_terrain_for(manifest: RunManifest, out_dir: Path, chunk_tiles: int = 8) -> ExportSummary:
    """The terrain export for a run's own world: its seed, size, civilizations and generator
    (O2 run exports use it; `export_terrain` is the same for a fresh seed)."""
    return terrain_bundle(manifest, chunk_tiles).write(out_dir)


def terrain_bundle(
    manifest: RunManifest, chunk_tiles: int = 8, recorded: WorldMap | None = None
) -> TerrainBundle:
    """The terrain export's files for a run's own world, in memory.

    `recorded` is the run's own map when it is not the one its seed generates (a scenario
    that edited its land): its tiles and rivers are shown instead, and the manifest says so.
    """
    if chunk_tiles < 1:
        raise ValueError("chunk_tiles must be positive")
    config = manifest.config
    seed, width, height = config.seed, config.width, config.height
    generated = generate_world(
        config, StableRng(seed), generator_version=manifest.generator_version
    )
    state = build_initial_state(manifest)
    tiles = generated.world_map.tiles
    if state.world_map.tiles != tiles:
        raise RuntimeError("day-0 state and world generation disagree about the map")
    shown = generated.world_map if recorded is None else recorded
    tiles = shown.tiles

    out: dict[str, bytes] = {}
    chunks: dict[tuple[int, int], list[list[int | str | bool | list[int]]]] = {}
    for tile in tiles:
        key = (tile.coord.q // chunk_tiles, tile.coord.r // chunk_tiles)
        chunks.setdefault(key, []).append(_tile_row(tile))
    for (cq, cr), rows in sorted(chunks.items()):
        out[f"chunks/c{cq}_{cr}.json"] = encode(
            {"cq": cq, "cr": cr, "fields": list(TILE_FIELDS), "tiles": rows}
        )

    starts = {start.center: start for start in generated.starts}
    civilizations = []
    for civilization in state.civilizations.values():
        capital = next(s for s in civilization.settlements if s.capital)
        start = starts[civilization.start_center]
        radius = max(t.distance(civilization.start_center) for t in civilization.known_tiles)
        civilizations.append(
            {
                "civilization_id": civilization.civilization_id,
                "capital": {
                    "settlement_id": capital.settlement_id,
                    "tile": [capital.tile.q, capital.tile.r],
                    "founded_day": capital.founded_day,
                    "capital": capital.capital,
                },
                "founders": len(civilization.population.people),
                "known_tiles_radius": radius,
                "start_strength": start.viability.strength,
                "start_vulnerability": start.viability.vulnerability,
            }
        )
    day0 = {"advanced_days": 0, "source": "build_initial_state", "civilizations": civilizations}
    out["day0.json"] = encode(day0)
    out["overview.json"] = encode(_overview(tiles, width, height))
    out["hydrology.json"] = encode(_hydrology(shown))
    out["sites.json"] = encode(
        {
            "source": "engine worldgen, day 0",
            "fields": ["site_id", "q", "r", "kind", "richness", "remaining"],
            "sites": [
                [
                    site.site_id,
                    site.tile.q,
                    site.tile.r,
                    site.kind.value,
                    site.richness,
                    site.remaining,
                ]
                for site in state.sites
            ],
        },
    )

    chunk_counts = (-(-width // chunk_tiles), -(-height // chunk_tiles))
    out["manifest.json"] = encode(
        {
            "export_version": EXPORT_VERSION,
            "source": "engine worldgen",
            **(
                {"map": "the run's own recorded map, edited from its seed's"}
                if recorded is not None
                else {}
            ),
            "engine": {
                "seed": seed,
                "width": width,
                "height": height,
                "coordinates": "axial hex (q, r); q in [0, width), r in [0, height)",
                "tile_index": "r * width + q",
                "attribute_range": [0, 1000],
                "terrains": [terrain.value for terrain in TERRAIN_CODES],
                "generator_version": manifest.generator_version,
                "river": (
                    "a tile's river flag means a river runs along at least one of its borders;"
                    " hydrology.json lists each river border (between tiles a and b), its flow,"
                    " and the tile at the corner it flows toward; flow at or above deep_flow"
                    " is a deep river"
                ),
                "time_step": "one day",
                "start_centres": [[s.center.q, s.center.r] for s in generated.starts],
                "start_spacing": generated.start_spacing,
                "cover_classes": [item.value for item in COVER_CLASSES],
                "cover": (
                    "each land tile's share under each cover class, in cover_classes order, in"
                    " basis points summing to 10000; empty for water"
                ),
                "sites": (
                    "sites.json lists ore deposits, quarries, ancient ruins and troves placed"
                    " when the world was made; the same kit for each civilization"
                ),
                "travel": {
                    "note": (
                        "world rule: tile centres lie a day's walk apart; costs are in tenths"
                        " of a day, null where it cannot be done on foot"
                    ),
                    "tile_spacing_m": TILE_SPACING_M,
                    "day_tenths": DAY,
                    "entry_cost_tenths": {
                        terrain.value: ENTRY_COST[terrain] for terrain in TERRAIN_CODES
                    },
                    "crossing_cost_tenths": dict(CROSSING_COST),
                    "stream_flow": STREAM_FLOW,
                    "deep_flow": DEEP_FLOW,
                },
            },
            "engine_day0": {
                "file": "day0.json",
                "advanced_days": 0,
                "source": "build_initial_state",
                "note": "Settlements and founder counts that exist before any day is simulated.",
            },
            "presentation": {
                "note": "Display constants only. They have no simulation meaning.",
                "frame": "rotated-45",
                "K": 16,
                "KZ": 14,
                "chunk_tiles": chunk_tiles,
                "chunks": list(chunk_counts),
            },
            "files": {
                "chunks": "chunks/c{cq}_{cr}.json",
                "overview": "overview.json",
                "hydrology": "hydrology.json",
                "sites": "sites.json",
            },
        },
    )
    return TerrainBundle(files=out, tiles=len(tiles), chunks=len(chunks))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Export engine terrain for the observer.")
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--width", type=int, default=100)
    parser.add_argument("--height", type=int, default=100)
    parser.add_argument("--chunk-tiles", type=int, default=8)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    summary = export_terrain(args.seed, args.width, args.height, args.out, args.chunk_tiles)
    print(f"wrote {summary.tiles} tiles in {summary.chunks} chunks to {summary.out_dir}")


if __name__ == "__main__":
    main()
