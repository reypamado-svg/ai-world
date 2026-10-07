"""Thousands of scripted histories for balance calibration, on every core.

    python -m sovereign_world.calibration run --out DIR [--seeds 0-249] [--size 32]
        [--days 365] [--rotations all] [--assignments builders,mixed] [--workers auto] [--quick]
    python -m sovereign_world.calibration report DIR

``run`` plays every seed, rotation and assignment and appends one row per civilization to
``DIR/histories.csv``; a history whose rows are already there is not played again, so a run
that was stopped carries on where it left off. ``DIR/run.json`` records what was asked, the
engine version, the rule hash and how long it took. ``report`` writes the fairness report
(``report.py``).
"""

from __future__ import annotations

import csv
import json
import os
import time
from collections.abc import Iterable, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from sovereign_world.calibration.histories import FIELDS, HistorySpec, Row, run_history
from sovereign_world.calibration.policies import POLICIES
from sovereign_world.config import ENGINE_VERSION
from sovereign_world.rulehash import rule_hash

ASSIGNMENTS: dict[str, tuple[str, ...]] = {
    "builders": ("builder",) * 4,
    "mixed": POLICIES,
}
"""Who plays which civilization. ``builders``: everyone the same, so only the start differs;
``mixed``: one of each policy, which rotation moves round every start."""
QUICK = {
    "seeds": "0-1",
    "size": 24,
    "days": 60,
    "rotations": "0,1",
    "assignments": "builders,mixed",
}
"""A small preset for tests: 8 histories of 60 days on 24 by 24 maps."""
CIVILIZATIONS = 4


def seeds_of(text: str) -> list[int]:
    """``0-249`` or ``1,5,9`` (or both, comma separated)."""
    seeds: list[int] = []
    for part in text.split(","):
        if "-" in part:
            low, high = part.split("-", 1)
            seeds.extend(range(int(low), int(high) + 1))
        elif part:
            seeds.append(int(part))
    return sorted(set(seeds))


def specs_of(
    seeds: Iterable[int], size: int, days: int, rotations: str, assignments: str
) -> list[HistorySpec]:
    turns = (
        list(range(CIVILIZATIONS))
        if rotations == "all"
        else sorted({int(item) for item in rotations.split(",") if item})
    )
    chosen = [item for item in assignments.split(",") if item]
    unknown = [item for item in chosen if item not in ASSIGNMENTS]
    if unknown:
        raise ValueError(f"unknown assignments {unknown}; choose from {', '.join(ASSIGNMENTS)}")
    return [
        HistorySpec(
            seed=seed,
            size=size,
            days=days,
            rotation=rotation,
            assignment=ASSIGNMENTS[label],
            label=label,
        )
        for label in chosen
        for seed in seeds
        for rotation in turns
    ]


def done_keys(path: Path) -> set[str]:
    """The histories whose rows are already in the file."""
    if not path.exists():
        return set()
    with path.open(newline="") as handle:
        return {
            f"{row['assignment']}|{row['seed']}|{row['size']}|{row['days']}|{row['rotation']}"
            for row in csv.DictReader(handle)
        }


def workers_of(text: str) -> int:
    if text == "auto":
        return max(1, (os.cpu_count() or 2) - 1)
    return max(1, int(text))


def run_batch(out: Path, specs: Sequence[HistorySpec], workers: int) -> dict[str, object]:
    """Play every spec not already in ``out/histories.csv``; return what ``run.json`` holds."""
    out.mkdir(parents=True, exist_ok=True)
    table = out / "histories.csv"
    done = done_keys(table)
    todo = [spec for spec in specs if spec.key not in done]
    started = time.monotonic()
    new_file = not table.exists()
    with table.open("a", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        if new_file:
            writer.writeheader()

        def write(rows: list[Row]) -> None:
            for row in rows:
                writer.writerow(row.values())
            handle.flush()

        if workers == 1:
            for spec in todo:
                write(run_history(spec))
        else:
            with ProcessPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(run_history, spec) for spec in todo]
                for future in as_completed(futures):
                    write(future.result())
    summary: dict[str, object] = {
        "histories": len(specs),
        "played_now": len(todo),
        "already_there": len(specs) - len(todo),
        "workers": workers,
        "elapsed_seconds": round(time.monotonic() - started, 1),
        "engine_version": ENGINE_VERSION,
        "rule_hash": rule_hash(),
        "specs": sorted({(spec.label, spec.size, spec.days) for spec in specs}),
        "seeds": sorted({spec.seed for spec in specs}),
        "rotations": sorted({spec.rotation for spec in specs}),
    }
    (out / "run.json").write_text(json.dumps(summary, indent=2, default=list) + "\n")
    return summary
