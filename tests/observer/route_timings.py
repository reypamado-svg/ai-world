"""Time the observer server's answers on a run (not a test; O6's performance report).

    .venv/bin/python tests/observer/route_timings.py RUN_DIR
    .venv/bin/python tests/observer/route_timings.py --baseline [--days 365] [--keep DIR]
    .venv/bin/python tests/observer/route_timings.py --people 100000 [--days 10] [--keep DIR]

With a run directory it times the server on that run, which it only reads. `--baseline` first
makes a fresh scripted run (seed 21, 48x48, the current rules) in a temporary directory, and
`--people N` a world of about N people run for a few days with the scripted baseline; `--keep`
leaves either where `sovereign-world observe` can open it. Provider credentials are removed from
the environment, so no model is ever called.

It prints a table: the walk (projecting each day in order, as the server does once on start),
then each route, first ask and the median of the cached repeats, in milliseconds, through
FastAPI's test client (no network). A run of one day (just initialised) has no "changes from the
day before" row.
"""

from __future__ import annotations

import argparse
import os
import re
import statistics
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

PROVIDER_ENV = re.compile(r"(API_KEY|ANTHROPIC|OPENAI|SOVEREIGN_.*KEY)", re.IGNORECASE)
TOKEN = "route-timings-" + "t" * 32
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _clean_environment() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if not PROVIDER_ENV.search(key)}


def _baseline(root: Path, days: int) -> None:
    env = _clean_environment()
    for args in (
        ["init", str(root), "--seed", "21", "--width", "48", "--height", "48"],
        ["run", str(root), "--days", str(days)],
    ):
        command = [sys.executable, "-c", "from sovereign_world.cli import app; app()", *args]
        subprocess.run(command, env=env, check=True, capture_output=True, text=True)


def _people(root: Path, people: int, days: int) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from perf.synthetic import RUN_ID, grown_world

    from sovereign_world.config import RunManifest
    from sovereign_world.engine import advance_day
    from sovereign_world.persistence import WorldStore
    from sovereign_world.rng import StableRng
    from sovereign_world.scripted import BaselineSovereign

    for key in [k for k in os.environ if PROVIDER_ENV.search(k)]:
        del os.environ[key]
    state = grown_world(people, generator=3, rules=2)
    manifest = RunManifest(
        run_id=RUN_ID,
        engine_version="route-timings",
        config=state.config,
        generator_version=3,
        rules_version=2,
        journal_format=2,
    )
    store = WorldStore.create(root, manifest, state)
    sovereigns = {civilization_id: BaselineSovereign() for civilization_id in state.civilizations}
    rng = StableRng(state.config.seed)
    for _ in range(days):
        transition = advance_day(state, rng, sovereigns=sovereigns)
        store.append_transition(transition.state, transition.events, previous=state)
        state = transition.state
    store.save_checkpoint(state)


def _timed(ask: Callable[[], object], repeats: int) -> tuple[float, float | None]:
    times = []
    for _ in range(1 + repeats):
        start = time.perf_counter()
        ask()
        times.append((time.perf_counter() - start) * 1000)
    return times[0], (statistics.median(times[1:]) if repeats else None)


def timings(root: Path, repeats: int = 4) -> list[tuple[str, float, float | None]]:
    """Each measure's name, first time and cached median (milliseconds)."""
    from fastapi.testclient import TestClient

    from sovereign_world.observer.server import build_app
    from sovereign_world.observer.service import RunService

    service = RunService(root)
    start = time.perf_counter()
    service.walk_all()
    walk = (time.perf_counter() - start) * 1000
    client = TestClient(build_app(service, TOKEN))

    def get(path: str) -> Callable[[], object]:
        def ask() -> object:
            answer = client.get(path, headers=AUTH)
            if answer.status_code != 200:
                raise RuntimeError(f"{path}: HTTP {answer.status_code}")
            return answer

        return ask

    days = client.get("/api/run/days", headers=AUTH).json()
    last, middle = days[-1], days[len(days) // 2]
    rows: list[tuple[str, float, float | None]] = [
        ("walk, per day", walk / len(days), None),
    ]
    measures = [
        ("status", "/api/status"),
        ("manifest", "/api/run/manifest"),
        ("id table", "/api/run/ids"),
        ("a day's record", f"/api/run/days/{last}"),
        ("a day's people", f"/api/run/days/{last}/people"),
    ]
    if len(days) > 1:
        measures.append(
            ("changes from the day before", f"/api/run/days/{last}/changes?from={days[-2]}")
        )
    measures += [
        ("routes", f"/api/run/days/{middle}/routes"),
        ("chronicle", f"/api/run/chronicle?day={middle}"),
        ("perspective (civilization 0)", f"/api/run/days/{last}/perspective/0"),
        ("terrain manifest", "/api/run/terrain/manifest.json"),
    ]
    for name, path in measures:
        first, cached = _timed(get(path), repeats)
        rows.append((name, first, cached))
    # A day no longer in the cache, asked again.
    service._cache.clear()
    dropped = days[1] if len(days) > 1 else days[0]
    first, _ = _timed(get(f"/api/run/days/{dropped}"), 0)
    rows.append(("a day dropped from the cache", first, None))
    return rows


def table(root: Path, rows: list[tuple[str, float, float | None]]) -> str:
    lines = [
        f"Observer route timings for {root} (ms; first ask, then the median of cached repeats)",
        "",
        "| Measure | First | Cached |",
        "|---|---|---|",
    ]
    for name, first, cached in rows:
        lines.append(f"| {name} | {first:.1f} | {'' if cached is None else f'{cached:.1f}'} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> str:
    parser = argparse.ArgumentParser(description="Time the observer server's answers on a run.")
    parser.add_argument("run", nargs="?", type=Path)
    parser.add_argument("--baseline", action="store_true", help="make a fresh scripted run first")
    parser.add_argument("--people", type=int, default=0, help="make a world of about N people")
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--keep", type=Path, default=None, help="where to leave a made run")
    parser.add_argument("--repeats", type=int, default=4)
    args = parser.parse_args(argv)
    if (args.run is None) == (not args.baseline and not args.people):
        parser.error("give a run directory, or --baseline, or --people N")
    with tempfile.TemporaryDirectory(prefix="route-timings-") as scratch:
        root = args.run
        if root is None:
            root = args.keep if args.keep is not None else Path(scratch) / "run"
            if args.baseline:
                _baseline(root, args.days or 365)
            else:
                _people(root, args.people, args.days or 10)
        text = table(root, timings(root, args.repeats))
    print(text)
    return text


if __name__ == "__main__":
    main()
