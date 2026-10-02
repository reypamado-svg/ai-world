"""The observer's terrain export reproduces engine data exactly and touches nothing else."""

from __future__ import annotations

import json
import os
from pathlib import Path

from sovereign_world.config import CURRENT_GENERATOR, WorldConfig
from sovereign_world.hexmap import COVER_CLASSES, HexCoord, Terrain
from sovereign_world.observer.terrain_export import TILE_FIELDS, export_terrain
from sovereign_world.rng import StableRng
from sovereign_world.travel import (
    CROSSING_COST,
    DAY,
    DEEP_FLOW,
    ENTRY_COST,
    STREAM_FLOW,
    TILE_SPACING_M,
)
from sovereign_world.worldgen import generate_world

SRC = Path(__file__).resolve().parents[2] / "src" / "sovereign_world"


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_export_is_byte_identical_across_runs(tmp_path: Path) -> None:
    export_terrain(21, 24, 24, tmp_path / "a")
    export_terrain(21, 24, 24, tmp_path / "b")
    assert _snapshot(tmp_path / "a") == _snapshot(tmp_path / "b")


def test_every_tile_round_trips_in_exactly_one_chunk(tmp_path: Path) -> None:
    summary = export_terrain(21, 30, 26, tmp_path, chunk_tiles=8)
    world = generate_world(WorldConfig(seed=21, width=30, height=26), StableRng(21)).world_map
    seen: dict[tuple[int, int], dict[str, object]] = {}
    for path in sorted((tmp_path / "chunks").glob("*.json")):
        chunk = json.loads(path.read_text())
        assert chunk["fields"] == list(TILE_FIELDS)
        for row in chunk["tiles"]:
            record = dict(zip(TILE_FIELDS, row, strict=True))
            key = (int(row[0]), int(row[1]))
            assert key not in seen, f"tile {key} appears in two chunks"
            assert (key[0] // 8, key[1] // 8) == (chunk["cq"], chunk["cr"])
            seen[key] = record
    assert summary.tiles == len(world.tiles) == len(seen)
    for tile in world.tiles:
        record = seen[(tile.coord.q, tile.coord.r)]
        assert record == {
            "q": tile.coord.q,
            "r": tile.coord.r,
            "terrain": tile.terrain.value,
            "elevation": tile.elevation,
            "moisture": tile.moisture,
            "temperature": tile.temperature,
            "soil": tile.soil,
            "timber": tile.timber,
            "stone": tile.stone,
            "ore": tile.ore,
            "river": tile.river,
            "cover": list(tile.cover),
        }
        if tile.terrain is not Terrain.WATER:
            assert sum(record["cover"]) == 10_000


def test_day0_matches_the_engine_starts(tmp_path: Path) -> None:
    export_terrain(21, 24, 24, tmp_path)
    generated = generate_world(WorldConfig(seed=21, width=24, height=24), StableRng(21))
    day0 = json.loads((tmp_path / "day0.json").read_text())
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert day0["advanced_days"] == 0
    tiles = sorted(tuple(c["capital"]["tile"]) for c in day0["civilizations"])
    assert tiles == sorted((s.center.q, s.center.r) for s in generated.starts)
    assert all(c["founders"] == 32 for c in day0["civilizations"])
    assert all(c["capital"]["founded_day"] == 0 for c in day0["civilizations"])
    assert "hex_radius_m" not in manifest["presentation"], "the scale is an engine rule now"
    assert manifest["export_version"] == 4
    assert manifest["engine"]["cover_classes"] == [item.value for item in COVER_CLASSES]
    assert manifest["engine"]["start_spacing"] == generated.start_spacing >= 12
    assert manifest["engine"]["travel"] == {
        "note": manifest["engine"]["travel"]["note"],
        "tile_spacing_m": TILE_SPACING_M,
        "day_tenths": DAY,
        "entry_cost_tenths": {terrain.value: cost for terrain, cost in ENTRY_COST.items()},
        "crossing_cost_tenths": dict(CROSSING_COST),
        "stream_flow": STREAM_FLOW,
        "deep_flow": DEEP_FLOW,
    }
    assert "no simulation meaning" in manifest["presentation"]["note"]
    assert set(manifest) >= {"engine", "engine_day0", "presentation"}


def test_only_the_output_directory_is_written(tmp_path: Path) -> None:
    before = _snapshot(tmp_path)
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        export_terrain(21, 24, 24, tmp_path / "out")
    finally:
        os.chdir(cwd)
    after = _snapshot(tmp_path)
    created = set(after) - set(before)
    assert created and all(name.startswith("out") for name in created)
    assert {k: v for k, v in after.items() if not k.startswith("out")} == before


def test_engine_never_imports_the_observer_and_the_exporter_avoids_persistence() -> None:
    for path in SRC.rglob("*.py"):
        if "observer" in path.relative_to(SRC).parts:
            continue
        assert "sovereign_world.observer" not in path.read_text(), path
    exporter = (SRC / "observer" / "terrain_export.py").read_text()
    assert "persistence" not in exporter


def test_hydrology_matches_the_engine_rivers(tmp_path: Path) -> None:
    export_terrain(9, 48, 48, tmp_path)
    world = generate_world(WorldConfig(seed=9, width=48, height=48), StableRng(9)).world_map
    hydrology = json.loads((tmp_path / "hydrology.json").read_text())
    manifest = json.loads((tmp_path / "manifest.json").read_text())

    rows = [dict(zip(hydrology["fields"], row, strict=True)) for row in hydrology["edges"]]
    assert world.rivers
    assert len(rows) == len(world.rivers)
    for row, edge in zip(rows, world.rivers, strict=True):
        assert (row["aq"], row["ar"], row["bq"], row["br"]) == (
            edge.a.q,
            edge.a.r,
            edge.b.q,
            edge.b.r,
        )
        assert row["flow"] == edge.flow
        down = None if edge.downstream is None else (edge.downstream.q, edge.downstream.r)
        assert (None if row["down_q"] is None else (row["down_q"], row["down_r"])) == down
    lakes = {tuple(coord) for coord in hydrology["lakes"]}
    assert all(world.tile(HexCoord(q, r)).terrain is Terrain.WATER for q, r in lakes)
    assert manifest["engine"]["generator_version"] == CURRENT_GENERATOR
    assert manifest["files"]["hydrology"] == "hydrology.json"


def test_sites_match_the_engine(tmp_path: Path) -> None:
    export_terrain(9, 48, 48, tmp_path)
    generated = generate_world(WorldConfig(seed=9, width=48, height=48), StableRng(9))
    exported = json.loads((tmp_path / "sites.json").read_text())
    manifest = json.loads((tmp_path / "manifest.json").read_text())

    assert manifest["files"]["sites"] == "sites.json"
    rows = [dict(zip(exported["fields"], row, strict=True)) for row in exported["sites"]]
    assert generated.sites
    assert rows == [
        {
            "site_id": site.site_id,
            "q": site.tile.q,
            "r": site.tile.r,
            "kind": site.kind.value,
            "richness": site.richness,
            "remaining": site.remaining,
        }
        for site in generated.sites
    ]
