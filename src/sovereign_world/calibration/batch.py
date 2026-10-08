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
in ``DIR/failures.txt`` and ``run.json`` and the others go on, but the batch is not finished: it
is an engine bug to fix, and since the fix changes the engine hash, the whole batch is then
played again in a new folder.
``DIR/run.json`` records what was asked, the engine version, the engine and rule hashes, how
long it took and whether the batch finished. ``report`` writes the fairness report
(``report.py``).
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import time
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

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


_INTEGERS = (
    "seed",
    "size",
    "days",
    "rotation",
    "civilization",
    "position",
    "living",
    "peak",
    "births",
    "deaths",
    "settlements",
    "tiles",
    "wars_declared",
    "battles",
    "rejected_orders",
)
SHARE_TOLERANCE = 1e-9


def row_of(raw: Mapping[Any, object]) -> Row | None:
    """A history's row as read back from the table, or None when a field is missing, extra or
    does not parse (a line cut off part-way)."""
    if None in raw or set(raw) != set(FIELDS):
        return None
    text = {name: raw[name] for name in FIELDS}
    if not all(isinstance(value, str) for value in text.values()):
        return None
    values: dict[str, str] = {name: str(value) for name, value in text.items()}
    try:
        numbers = {name: int(values[name]) for name in _INTEGERS}
        eliminated = None if values["eliminated_day"] == "" else int(values["eliminated_day"])
        homeless = {"True": True, "False": False}[values["homeless"]]
        share = float(values["winner_share"])
    except (KeyError, ValueError):
        return None
    if (
        values["assignment"] not in ASSIGNMENTS
        or values["policy"] not in POLICIES
        or not values["realm_rank"]
        or not math.isfinite(share)
    ):
        return None
    return Row(
        assignment=values["assignment"],
        policy=values["policy"],
        realm_rank=values["realm_rank"],
        eliminated_day=eliminated,
        homeless=homeless,
        winner_share=share,
        **numbers,
    )


def history_is_whole(rows: Sequence[Row], civilizations: int) -> bool:
    """Whether a history's rows are all there and agree with one another: one per civilization,
    and each winner's share what the living counts give (a share cut short shows here)."""
    if len(rows) != civilizations:
        return False
    if sorted(row.civilization for row in rows) != list(range(civilizations)):
        return False
    most = max(row.living for row in rows)
    leaders = sum(row.living == most for row in rows)
    return all(
        abs(row.winner_share - (1 / leaders if row.living == most and most > 0 else 0.0))
        <= SHARE_TOLERANCE
        for row in rows
    )


def _read_table(path: Path) -> list[tuple[dict[str | None, object], Row | None]]:
    """The table's rows, raw and parsed; a last line without its line end is taken as torn."""
    data = path.read_bytes()
    raws: list[dict[str | None, object]] = list(
        csv.DictReader(io.StringIO(data.decode("utf-8", errors="replace"), newline=""))
    )
    parsed = [row_of(raw) for raw in raws]
    if raws and not data.endswith(b"\n"):
        parsed[-1] = None
    return list(zip(raws, parsed, strict=True))


def _raw_key(raw: dict[str | None, object]) -> str | None:
    try:
        return _key_of({str(name): str(value) for name, value in raw.items() if name is not None})
    except KeyError:
        return None


def drop_partial_histories(path: Path, rows_per_history: int) -> int:
    """Remove the rows of any history cut off part-way (fewer rows than civilizations, a torn
    line, or rows that do not agree with one another), so it is played again; return how many
    rows were removed."""
    if not path.exists():
        return 0
    rows = _read_table(path)
    broken = {_raw_key(raw) for raw, row in rows if row is None}
    histories: dict[str, list[Row]] = {}
    for raw, row in rows:
        key = _raw_key(raw)
        if row is not None and key is not None and key not in broken:
            histories.setdefault(key, []).append(row)
    whole = {key for key, held in histories.items() if history_is_whole(held, rows_per_history)}
    kept = [
        {str(name): value for name, value in raw.items()}
        for raw, row in rows
        if row is not None and (key := _raw_key(raw)) is not None and key in whole
    ]
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
        # A history the engine failed on is missing from the table: the batch is not whole.
        finished=not failures,
    )
    if failures:
        (out / "failures.txt").write_text("".join(f"{line}\n" for line in sorted(failures)))
    _write_run(out, summary)
    return summary
