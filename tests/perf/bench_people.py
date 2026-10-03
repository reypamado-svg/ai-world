"""How the engine's daily cost grows with population.

    PYTHONPATH=src:tests .venv/bin/python -B tests/perf/bench_people.py 4000 20000
        [--days 30] [--size 48] [--rules 1] [--profile] [--phases] [--memory] [--verify]

For each population size it grows a fresh world (tests/perf/synthetic.py) on a map of
`--size` tiles a side, runs scripted days, and saves each in a journal-format-2 store, as a
new run does. It reports milliseconds per simulated day for the day itself, hash v2 (cached
from day to day) and the journal record; the journal's size (each day's changes, the
whole-world snapshots, and megabytes per year at one snapshot every 30 days); and the
process's peak memory. With --verify it also times verifying the saved days from scratch.
`--rules 2` grows the world as a new run makes it (rules 2, generator 3); the default is
rules 1 on generator 2, as earlier measurements were taken. With --phases it prints the
milliseconds per day spent in the engine's costliest steps.
Not collected by pytest. Numbers depend on the machine; compare runs on the same one.

With --memory it also reports the people's own memory, per living person, on the grown
world before the days run: what one copy of every population takes (tracemalloc), and
what the people store holds in all (every object it reaches, counted once).
"""

from __future__ import annotations

import argparse
import contextlib
import cProfile
import functools
import io
import pstats
import resource
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

import numpy as np
from perf.synthetic import RUN_ID, grown_world

from sovereign_world import engine
from sovereign_world.config import RunManifest
from sovereign_world.engine import advance_day
from sovereign_world.journal import SNAPSHOT_INTERVAL, split_parts
from sovereign_world.persistence import WorldStore
from sovereign_world.replay import verify_run
from sovereign_world.rng import StableRng
from sovereign_world.scripted import BaselineSovereign
from sovereign_world.state import WorldState, state_hash_v2

SORT = "tottime"
PHASES = (
    "advance_population_day",
    "_advance_territory",
    "validate_world",
    "_working",
    "_settle_newcomers",
    "_learn_by_sight",
    "_assimilate",
    "_escapes",
    "_run_councils",
    "execute_work_day",
    "advance_knowledge_day",
)
"""Engine steps timed by --phases: module-level functions `advance_day` calls by name."""


class Phases:
    """Wraps the engine's named steps with timers for the life of a `with` block."""

    def __init__(self) -> None:
        self.seconds: dict[str, float] = dict.fromkeys(PHASES, 0.0)
        self._originals: dict[str, object] = {}

    def __enter__(self) -> Phases:
        for name in PHASES:
            original = getattr(engine, name)
            self._originals[name] = original

            @functools.wraps(original)
            def timed(*args, _name=name, _original=original, **kwargs):
                start = time.perf_counter()
                try:
                    return _original(*args, **kwargs)
                finally:
                    self.seconds[_name] += time.perf_counter() - start

            setattr(engine, name, timed)
        return self

    def __exit__(self, *_: object) -> None:
        for name, original in self._originals.items():
            setattr(engine, name, original)


def _deep_size(root: object) -> int:
    """Bytes held by an object and everything it reaches, each object counted once."""
    seen: set[int] = set()
    stack = [root]
    total = 0
    while stack:
        item = stack.pop()
        if id(item) in seen or item is None or isinstance(item, (bool, type)):
            continue
        seen.add(id(item))
        if isinstance(item, np.ndarray):
            total += item.nbytes + sys.getsizeof(item) - (item.nbytes if item.base is None else 0)
            continue
        total += sys.getsizeof(item)
        if isinstance(item, dict):
            stack.extend(item.keys())
            stack.extend(item.values())
        elif isinstance(item, (list, tuple, set, frozenset)):
            stack.extend(item)
        elif hasattr(item, "__slots__"):
            stack.extend(getattr(item, name) for name in item.__slots__ if hasattr(item, name))
        elif hasattr(item, "__dict__"):
            stack.append(vars(item))
    return total


def store_bytes(state) -> float:
    """Bytes the people of every civilization take, per living person."""
    living = sum(len(c.population.living_ids) for c in state.civilizations.values())
    tables = [c.population.people.table for c in state.civilizations.values()]
    return _deep_size(tables) / max(1, living)


def people_bytes(state) -> float:
    """Bytes one copy of every population takes, per living person."""
    living = sum(len(c.population.living_ids) for c in state.civilizations.values())
    tracemalloc.start()
    before = tracemalloc.get_traced_memory()[0]
    copies = [c.population.model_copy(deep=True) for c in state.civilizations.values()]
    after = tracemalloc.get_traced_memory()[0]
    tracemalloc.stop()
    del copies
    return (after - before) / max(1, living)


def _generator(rules: int) -> int:
    return 3 if rules >= 2 else 2


def _store(root: Path, state: WorldState) -> WorldStore:
    manifest = RunManifest(
        run_id=RUN_ID,
        engine_version="bench",
        config=state.config,
        generator_version=_generator(state.rules_version),
        rules_version=state.rules_version,
        journal_format=2,
    )
    return WorldStore.create(root, manifest, state)


def bench(
    people: int,
    days: int,
    profile: bool,
    memory: bool = False,
    size: int = 48,
    verify: bool = False,
    rules: int = 1,
) -> dict[str, float]:
    state = grown_world(people, size=size, generator=_generator(rules), rules=rules)
    living = sum(len(c.population.living_ids) for c in state.civilizations.values())
    per_person = people_bytes(state) if memory else 0.0
    stored = store_bytes(state) if memory else 0.0
    sovereigns = {civilization_id: BaselineSovereign() for civilization_id in state.civilizations}
    rng = StableRng(state.config.seed)
    profiler = cProfile.Profile() if profile else None
    day_s = hash_s = journal_s = verify_s = slowest_record = 0.0
    deltas: list[int] = []
    snapshots: list[int] = []
    with tempfile.TemporaryDirectory() as directory:
        store = _store(Path(directory) / "run", state)
        for _ in range(days):
            t0 = time.perf_counter()
            if profiler:
                profiler.enable()
            transition = advance_day(state, rng, sovereigns=sovereigns)
            state = transition.state
            if profiler:
                profiler.disable()
            t1 = time.perf_counter()
            # Dumped once and hashed from that dump, as a run's save does.
            parts = split_parts(state)
            hashed = state_hash_v2(state, parts=parts)
            t2 = time.perf_counter()
            before = store.journal_path.stat().st_size
            record = store.append_transition(state, transition.events, parts=parts, hashed=hashed)
            t3 = time.perf_counter()
            written = store.journal_path.stat().st_size - before
            (snapshots if "state_gzip_base64" in record.payload else deltas).append(written)
            day_s += t1 - t0
            hash_s += t2 - t1
            journal_s += t3 - t2
            slowest_record = max(slowest_record, t3 - t2)
        if verify:
            t4 = time.perf_counter()
            verify_run(WorldStore(store.root))
            verify_s = time.perf_counter() - t4
    # Peak resident memory of the whole process so far (kilobytes on Linux).
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    if profiler:
        out = io.StringIO()
        pstats.Stats(profiler, stream=out).sort_stats(SORT).print_stats(25)
        print(out.getvalue())
    delta = sum(deltas) / max(1, len(deltas))
    # The snapshot is the whole world once the people are grown; day 0 is the checkpoint.
    snapshot = max(snapshots, default=0)
    per_year = 365 * delta + (365 / SNAPSHOT_INTERVAL) * (snapshot - delta)
    return {
        "people": living,
        "day_ms": 1000 * day_s / days,
        "hash_ms": 1000 * hash_s / days,
        "journal_ms": 1000 * journal_s / days,
        "slowest_record_ms": 1000 * slowest_record,
        "verify_ms": 1000 * verify_s / days,
        "us_per_person_day": 1e6 * day_s / days / max(1, living),
        "delta_kb": delta / 1024,
        "snapshot_mb": snapshot / 1e6,
        "mb_per_year": per_year / 1e6,
        "peak_mb": peak / 1e6,
        "people_bytes": per_person,
        "store_bytes": stored,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("sizes", nargs="+", type=int)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--memory", action="store_true")
    parser.add_argument("--size", type=int, default=48, help="Map tiles a side.")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--rules", type=int, default=1, choices=(1, 2))
    parser.add_argument("--phases", action="store_true")
    args = parser.parse_args()
    print(
        "| people | map | rules | day ms | hash v2 ms | record ms | slowest record ms "
        "| verify ms/day | µs/person/day | delta KB/day | snapshot MB | MB/year | peak MB "
        "| copy bytes/person | stored bytes/person |"
    )
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    timings: list[tuple[int, Phases]] = []
    for people in args.sizes:
        phases = Phases() if args.phases else None
        with phases or contextlib.nullcontext():
            r = bench(
                people,
                args.days,
                args.profile,
                args.memory,
                args.size,
                args.verify,
                args.rules,
            )
        if phases is not None:
            timings.append((int(r["people"]), phases))
        print(
            f"| {r['people']:,} | {args.size}x{args.size} | {args.rules} | {r['day_ms']:.0f} "
            f"| {r['hash_ms']:.0f} | {r['journal_ms']:.0f} | {r['slowest_record_ms']:.0f} "
            f"| {r['verify_ms']:.0f} "
            f"| {r['us_per_person_day']:.1f} | {r['delta_kb']:.0f} | {r['snapshot_mb']:.1f} "
            f"| {r['mb_per_year']:.0f} | {r['peak_mb']:.0f} | {r['people_bytes']:.0f} "
            f"| {r['store_bytes']:.0f} |",
            flush=True,
        )
    for people, phases in timings:
        print(f"\nms per day by step, {people:,} people:")
        for name, seconds in sorted(phases.seconds.items(), key=lambda item: -item[1]):
            print(f"  {name:28s} {1000 * seconds / args.days:8.0f}")


if __name__ == "__main__":
    main()
