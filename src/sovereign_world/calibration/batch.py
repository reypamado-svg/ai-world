"""Thousands of scripted histories for balance calibration, on every core.

    python -m sovereign_world.calibration run --out DIR [--seeds 0-249] [--size 32]
        [--days 365] [--rotations all] [--assignments builders,mixed] [--workers auto] [--quick]
    python -m sovereign_world.calibration report DIR

``run`` plays every seed, rotation and assignment and appends one row per civilization to
``DIR/histories.csv``; a history whose rows are already there is not played again, so a run
that was stopped carries on where it left off. A history the engine fails on is named in
``DIR/failures.txt`` and ``run.json`` and the others go on; it is an engine bug to fix, after
which running again plays it. ``DIR/run.json`` records what was asked, the engine version, the
engine and rule hashes and how long it took. ``report`` writes the fairness report
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
from sovereign_world.rulehash import engine_hash, rule_hash

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
    seeds: Iterable[int],
    size: int,
    days: int,
    rotations: str,
    assignments: str,
    interval: int = 30,
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
            interval=interval,
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


def _one(name: str, values: set[int]) -> dict[str, int]:
    """A setting every history of the batch shares, for the launch gate to match against."""
    return {name: next(iter(values))} if len(values) == 1 else {}


def _play(spec: HistorySpec) -> tuple[str, list[Row] | str]:
    """One history's rows, or what stopped it (so one engine bug does not stop the batch)."""
    try:
        return spec.key, run_history(spec)
    except Exception as error:
        return spec.key, f"{type(error).__name__}: {str(error).splitlines()[0]}"


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

        failures: list[str] = []

        def write(played: tuple[str, list[Row] | str]) -> None:
            key, rows = played
            if isinstance(rows, str):
                failures.append(f"{key}: {rows}")
                return
            for row in rows:
                writer.writerow(row.values())
            handle.flush()

        if workers == 1:
            for spec in todo:
                write(_play(spec))
        else:
            with ProcessPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(_play, spec) for spec in todo]
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
        "engine_hash": engine_hash(),
        "specs": sorted({(spec.label, spec.size, spec.days) for spec in specs}),
        "seeds": sorted({spec.seed for spec in specs}),
        "rotations": sorted({spec.rotation for spec in specs}),
        **_one("council_interval_days", {spec.interval for spec in specs}),
        **_one("size", {spec.size for spec in specs}),
        **_one("days", {spec.days for spec in specs}),
        **_one("civilizations", {len(spec.assignment) for spec in specs}),
        "failed": sorted(failures),
    }
    if failures:
        (out / "failures.txt").write_text("".join(f"{line}\n" for line in sorted(failures)))
    (out / "run.json").write_text(json.dumps(summary, indent=2, default=list) + "\n")
    return summary
