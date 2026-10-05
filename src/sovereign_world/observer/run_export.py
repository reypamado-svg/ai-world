"""A recorded run, exported for the observer to show (O2).

    python -m sovereign_world.observer.run_export RUN_DIR --out observer/data/runs/NAME
        [--stride N] [--days 0,30,60] [--replace]

reads a run with `RunReader` (which never writes to it) and writes, under `--out` only:

- `manifest.json`: what was exported, from which run, and how the files are laid out;
- `terrain/`: the run's own world, in the terrain export's format (its seed, size and
  generator, checked against the run's map);
- `ids.json`: every person id seen, in order of first appearance; a person's number in the
  day files is their place in this list;
- for each exported day, `days/dNNNNNN.json` (settlements with their houses, house work,
  institutions and residents; travellers by tile; tile owners; counts) and
  `days/dNNNNNN.people.bin.gz` (every living person as little-endian columns, one column
  after another, in the order `people_layout` gives).

The same run and options always write the same bytes. This is a stopgap until the O3 server
serves the same data over HTTP.
"""

from __future__ import annotations

import argparse
import gzip
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from sovereign_world.observer.projection import AWAY, DUTIES, DayProjection, project_day
from sovereign_world.observer.reader import RunReader
from sovereign_world.observer.terrain_export import export_terrain_for
from sovereign_world.state import build_initial_state

EXPORT_VERSION = 3
"""2: settlements of rules-3 runs carry their town `plan` and wall ring (`walls`).
3: walls also carry, where there are any, `tower_sections`, `gatehouses`, `ditch`, `stakes`
and `citadel`; settlements carry their standing `defence` order."""
PEOPLE_LAYOUT: tuple[tuple[str, str], ...] = (
    ("id", "<u4"),
    ("settlement", "<u2"),
    ("q", "<i2"),
    ("r", "<i2"),
    ("civilization", "u1"),
    ("sex", "u1"),
    ("age", "u1"),
    ("health", "u1"),
    ("duty", "u1"),
)
"""Column name and numpy dtype, in file order. `id` is the place in ids.json."""
GZIP_LEVEL = 6


@dataclass(frozen=True)
class RunExportSummary:
    out_dir: Path
    days: tuple[int, ...]
    people: int
    """Distinct people across the exported days."""
    bytes: int


def _dump(path: Path, data: Any) -> int:
    raw = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    path.write_bytes(raw)
    return len(raw)


def people_bytes(view: DayProjection, numbers: list[int]) -> bytes:
    """The day's people as columns in `PEOPLE_LAYOUT` order, gzipped reproducibly."""
    people = view.people
    columns = {
        "id": np.asarray(numbers, dtype=np.int64),
        "settlement": people.settlement,
        "q": people.q,
        "r": people.r,
        "civilization": people.civilization,
        "sex": people.sex,
        "age": people.age,
        "health": people.health,
        "duty": people.duty,
    }
    raw = b"".join(columns[name].astype(dtype).tobytes() for name, dtype in PEOPLE_LAYOUT)
    return gzip.compress(raw, compresslevel=GZIP_LEVEL, mtime=0)


def day_record(view: DayProjection) -> dict[str, Any]:
    return {
        "day": view.day,
        "counts": view.counts(),
        "settlements": [
            {
                "id": row.settlement_id,
                "civilization": row.civilization,
                "q": row.q,
                "r": row.r,
                "capital": row.capital,
                "founded_day": row.founded_day,
                "rank": row.rank,
                "houses": row.houses,
                "slots": row.slots,
                "residents": row.residents,
                "house_jobs": list(row.house_jobs),
                "institutions": list(row.institutions),
                **({"plan": row.plan} if row.plan is not None else {}),
                **({"walls": row.walls} if row.walls is not None else {}),
                **({"defence": row.defence} if row.defence is not None else {}),
            }
            for row in view.settlements
        ],
        "travellers": [list(item) for item in view.travellers],
        "owners": [list(item) for item in view.owners],
    }


def _inside(path: Path, root: Path) -> bool:
    return path.resolve() == root.resolve() or root.resolve() in path.resolve().parents


def export_run(
    root: Path,
    out_dir: Path,
    *,
    stride: int = 1,
    days: tuple[int, ...] | None = None,
    replace: bool = False,
    chunk_tiles: int = 8,
) -> RunExportSummary:
    """Export a recorded run's days for the observer; writes only under `out_dir`."""
    if stride < 1:
        raise ValueError("stride must be positive")
    if _inside(out_dir, root):
        raise ValueError("the export cannot be written inside the run")
    if out_dir.exists() and any(out_dir.iterdir()):
        if not replace:
            raise FileExistsError(f"{out_dir} is not empty (pass replace to overwrite it)")
        if not (out_dir / "manifest.json").exists():
            raise FileExistsError(f"{out_dir} does not hold a run export; not replacing it")
        shutil.rmtree(out_dir)
    reader = RunReader(root)
    manifest = reader.manifest()
    saved = reader.days()
    chosen = (
        tuple(day for day in days if day in saved)
        if days is not None
        else tuple(day for index, day in enumerate(saved) if index % stride == 0)
    )
    if days is not None and len(chosen) != len(days):
        missing = sorted(set(days) - set(saved))
        raise KeyError(f"days not saved in this run: {missing}")
    start = reader.state_at(saved[0])
    if start.world_map.content_hash() != build_initial_state(manifest).world_map.content_hash():
        raise RuntimeError("the run's map is not the one its seed generates; cannot export terrain")

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "days").mkdir()
    total = 0
    export_terrain_for(manifest, out_dir / "terrain", chunk_tiles)
    numbers: dict[str, int] = {}
    civilizations: tuple[str, ...] = ()
    for day in chosen:
        view = project_day(reader.state_at(day))
        civilizations = civilizations or view.civilizations
        ids = [numbers.setdefault(person_id, len(numbers)) for person_id in view.people.ids]
        total += _dump(out_dir / "days" / f"d{day:06d}.json", day_record(view))
        blob = people_bytes(view, ids)
        (out_dir / "days" / f"d{day:06d}.people.bin.gz").write_bytes(blob)
        total += len(blob)
    ordered = sorted(numbers, key=numbers.__getitem__)
    total += _dump(out_dir / "ids.json", ordered)
    total += _dump(
        out_dir / "manifest.json",
        {
            "export_version": EXPORT_VERSION,
            "kind": "recorded run",
            "run_id": str(manifest.run_id),
            "engine_version": manifest.engine_version,
            "journal_format": reader.journal_format,
            "history_epoch": reader.history_epoch,
            "rules_version": manifest.rules_version,
            "generator_version": manifest.generator_version,
            "seed": manifest.config.seed,
            "width": manifest.config.width,
            "height": manifest.config.height,
            "civilizations": list(civilizations),
            "days": list(chosen),
            "saved_days": [saved[0], saved[-1]],
            "duties": list(DUTIES),
            "away": AWAY,
            "people_layout": [list(item) for item in PEOPLE_LAYOUT],
            "files": {
                "terrain": "terrain/manifest.json",
                "ids": "ids.json",
                "day": "days/d{day:06d}.json",
                "people": "days/d{day:06d}.people.bin.gz",
            },
            "note": (
                "Recorded engine data: who lives where, houses, duties, owners. Where people"
                " stand inside a settlement, and their movement, are presentation."
            ),
        },
    )
    return RunExportSummary(out_dir=out_dir, days=chosen, people=len(ordered), bytes=total)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Export a recorded run for the observer.")
    parser.add_argument("run", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--stride", type=int, default=1)
    parser.add_argument("--days", type=str, default=None, help="comma-separated days")
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args(argv)
    days = tuple(int(item) for item in args.days.split(",")) if args.days else None
    summary = export_run(args.run, args.out, stride=args.stride, days=days, replace=args.replace)
    print(
        f"wrote {len(summary.days)} days, {summary.people} people,"
        f" {summary.bytes / 1e6:.1f} MB to {summary.out_dir}"
    )


if __name__ == "__main__":
    main()
