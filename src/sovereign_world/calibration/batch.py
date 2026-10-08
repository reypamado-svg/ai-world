"""Thousands of scripted histories for balance calibration, on every core.

    python -m sovereign_world.calibration run --out DIR [--seeds 0-249] [--size 32]
        [--days 365] [--rotations all] [--assignments builders,mixed] [--civilizations 4]
        [--council-interval 28] [--workers auto] [--quick]
    python -m sovereign_world.calibration report DIR

``run`` plays every seed, rotation and assignment and appends one row per civilization to
``DIR/histories.csv``; a history whose rows are already there is not played again, so a run
that was stopped carries on where it left off (a history whose rows were cut off part-way is
dropped and played again). One folder holds one engine, one council interval and one number of
civilizations: ``run.json`` names them before the first row is written, and a run asking for
others in a folder made with different ones is refused. A history the engine fails on is named
in ``DIR/failures.txt`` and ``run.json`` and the others go on; it is an engine bug to fix, and
since the fix changes the engine hash, the whole batch is then played again in a new folder.
``DIR/run.json`` records what was asked, the engine version, the engine and rule hashes, how
long it took and whether the batch finished. ``report`` writes the fairness report
(``report.py``).
"""

from __future__ import annotations

import csv
import json
import os
import time
from collections import Counter
from collections.abc import Iterable, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from sovereign_world.calibration.histories import FIELDS, HistorySpec, Row, run_history
from sovereign_world.calibration.policies import POLICIES
from sovereign_world.config import ENGINE_VERSION
from sovereign_world.rulehash import engine_hash, rule_hash

ASSIGNMENTS = ("builders", "mixed")
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


def assignment_of(label: str, civilizations: int) -> tuple[str, ...]:
    """The policies of civilization 0, 1, ... for an assignment. With fewer than four
    civilizations, ``mixed`` keeps the last policies (the builder is what ``builders`` plays)."""
    if label == "builders":
        return ("builder",) * civilizations
    if label == "mixed":
        return POLICIES[-civilizations:]
    raise ValueError(f"unknown assignment {label!r}; choose from {', '.join(ASSIGNMENTS)}")


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
    civilizations: int = 4,
) -> list[HistorySpec]:
    if not 2 <= civilizations <= len(POLICIES):
        raise ValueError(f"a world holds 2 to {len(POLICIES)} civilizations")
    turns = (
        list(range(civilizations))
        if rotations == "all"
        else sorted({int(item) for item in rotations.split(",") if item})
    )
    if any(not 0 <= turn < civilizations for turn in turns):
        raise ValueError(f"rotations must be 0 to {civilizations - 1}")
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
            assignment=assignment_of(label, civilizations),
            label=label,
            interval=interval,
        )
        for label in chosen
        for seed in seeds
        for rotation in turns
    ]


def base_key(spec: HistorySpec) -> str:
    """A history's name as its rows can show it (the folder's interval and number of
    civilizations are its own; see ``_check_folder``)."""
    return f"{spec.label}|{spec.seed}|{spec.size}|{spec.days}|{spec.rotation}"


def _key_of(row: dict[str, str]) -> str:
    return f"{row['assignment']}|{row['seed']}|{row['size']}|{row['days']}|{row['rotation']}"


def done_keys(path: Path, rows_per_history: int | None = None) -> set[str]:
    """The histories whose rows are already in the file, named as ``base_key`` names them; with
    ``rows_per_history``, only those with all their rows (one per civilization)."""
    if not path.exists():
        return set()
    with path.open(newline="") as handle:
        counts = Counter(_key_of(row) for row in csv.DictReader(handle))
    return {key for key, count in counts.items() if rows_per_history in (None, count)}


def drop_partial_histories(path: Path, rows_per_history: int) -> int:
    """Remove the rows of any history cut off part-way (fewer rows than civilizations, or a
    torn last line), so it is played again; return how many rows were removed."""
    if not path.exists():
        return 0
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    whole = [row for row in rows if None not in row.values() and None not in row]
    counts = Counter(_key_of(row) for row in whole)
    kept = [row for row in whole if counts[_key_of(row)] == rows_per_history]
    if len(kept) == len(rows):
        return 0
    spare = path.with_name(f"{path.name}.tmp")
    with spare.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(kept)
    os.replace(spare, path)
    return len(rows) - len(kept)


def workers_of(text: str) -> int:
    if text == "auto":
        return max(1, (os.cpu_count() or 2) - 1)
    return max(1, int(text))


def _one(name: str, values: set[int]) -> dict[str, int]:
    """A setting every history of the batch shares, for the launch gate to match against."""
    return {name: next(iter(values))} if len(values) == 1 else {}


def _check_folder(out: Path, specs: Sequence[HistorySpec]) -> None:
    """Refuse to add histories of another engine, council interval or number of civilizations
    to a folder that already holds some (their rows would be taken for these)."""
    recorded = out / "run.json"
    if not specs:
        return
    if not recorded.exists():
        if (out / "histories.csv").exists():
            raise ValueError(
                f"{out} holds histories but no run.json saying which engine played them;"
                " use a new folder"
            )
        return
    before = json.loads(recorded.read_text())
    old_engine, new_engine = str(before.get("engine_hash", "")), engine_hash()
    if old_engine != new_engine:
        raise ValueError(
            f"{out} holds histories played under engine {old_engine[:12] or '(unknown)'}…, and"
            f" this engine is {new_engine[:12]}…; use a new folder so one report measures one"
            " engine"
        )
    for name, values in (
        ("council_interval_days", {spec.interval for spec in specs}),
        ("civilizations", {len(spec.assignment) for spec in specs}),
    ):
        old = before.get(name, 30 if name == "council_interval_days" else 4)
        if values != {old}:
            raise ValueError(
                f"{out} holds histories with {name} {old}; use another folder for"
                f" {', '.join(str(value) for value in sorted(values))}"
            )


def _summary(specs: Sequence[HistorySpec], **extra: object) -> dict[str, object]:
    """What ``run.json`` records about the batch and the engine that plays it."""
    return {
        "histories": len(specs),
        **extra,
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
    }


def _write_run(out: Path, summary: dict[str, object]) -> None:
    (out / "run.json").write_text(json.dumps(summary, indent=2, default=list) + "\n")


def _play(spec: HistorySpec) -> tuple[str, list[Row] | str]:
    """One history's rows, or what stopped it (so one engine bug does not stop the batch)."""
    try:
        return spec.key, run_history(spec)
    except Exception as error:
        return spec.key, f"{type(error).__name__}: {str(error).splitlines()[0]}"


def run_batch(out: Path, specs: Sequence[HistorySpec], workers: int) -> dict[str, object]:
    """Play every spec not already in ``out/histories.csv``; return what ``run.json`` holds."""
    out.mkdir(parents=True, exist_ok=True)
    _check_folder(out, specs)
    table = out / "histories.csv"
    rows_per_history = len(specs[0].assignment) if specs else None
    if rows_per_history is not None:
        drop_partial_histories(table, rows_per_history)
    done = done_keys(table, rows_per_history)
    todo = [spec for spec in specs if base_key(spec) not in done]
    # The engine is named before the first row, so a stopped batch says what played it.
    _write_run(out, _summary(specs, finished=False))
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
    summary = _summary(
        specs,
        played_now=len(todo),
        already_there=len(specs) - len(todo),
        workers=workers,
        elapsed_seconds=round(time.monotonic() - started, 1),
        failed=sorted(failures),
        finished=True,
    )
    if failures:
        (out / "failures.txt").write_text("".join(f"{line}\n" for line in sorted(failures)))
    _write_run(out, summary)
    return summary
