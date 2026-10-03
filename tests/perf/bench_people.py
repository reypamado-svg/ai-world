"""How the engine's daily cost grows with population.

    PYTHONPATH=src:tests .venv/bin/python -B tests/perf/bench_people.py 4000 20000
        [--days 30] [--profile] [--memory]

For each population size it grows a fresh world (tests/perf/synthetic.py),
runs scripted days, and reports milliseconds per simulated day for the day
itself, the state hash and the journal record (gzipped full state, as the
journal writes it today), plus the process's peak memory and journal bytes per day. Not
collected by pytest. Numbers depend on the machine; compare runs on the same
one.

With --memory it also reports the people's own memory, per living person, on the grown
world before the days run: what one copy of every population takes (tracemalloc), and
what the people store holds in all (every object it reaches, counted once).
"""

from __future__ import annotations

import argparse
import cProfile
import gzip
import io
import pstats
import resource
import sys
import time
import tracemalloc

import numpy as np
from perf.synthetic import grown_world

from sovereign_world.engine import advance_day
from sovereign_world.rng import StableRng
from sovereign_world.scripted import BaselineSovereign
from sovereign_world.state import state_hash

SORT = "tottime"


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


def bench(people: int, days: int, profile: bool, memory: bool = False) -> dict[str, float]:
    state = grown_world(people)
    living = sum(len(c.population.living_ids) for c in state.civilizations.values())
    per_person = people_bytes(state) if memory else 0.0
    stored = store_bytes(state) if memory else 0.0
    sovereigns = {civilization_id: BaselineSovereign() for civilization_id in state.civilizations}
    rng = StableRng(state.config.seed)
    profiler = cProfile.Profile() if profile else None
    day_s = hash_s = journal_s = 0.0
    journal_bytes = 0
    for _ in range(days):
        t0 = time.perf_counter()
        if profiler:
            profiler.enable()
        state = advance_day(state, rng, sovereigns=sovereigns).state
        if profiler:
            profiler.disable()
        t1 = time.perf_counter()
        state_hash(state)
        t2 = time.perf_counter()
        record = gzip.compress(state.model_dump_json().encode())
        t3 = time.perf_counter()
        day_s += t1 - t0
        hash_s += t2 - t1
        journal_s += t3 - t2
        journal_bytes += len(record)
    # Peak resident memory of the whole process so far (kilobytes on Linux).
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    if profiler:
        out = io.StringIO()
        pstats.Stats(profiler, stream=out).sort_stats(SORT).print_stats(25)
        print(out.getvalue())
    return {
        "people": living,
        "day_ms": 1000 * day_s / days,
        "hash_ms": 1000 * hash_s / days,
        "journal_ms": 1000 * journal_s / days,
        "us_per_person_day": 1e6 * day_s / days / max(1, living),
        "journal_kb_per_day": journal_bytes / days / 1024,
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
    args = parser.parse_args()
    print(
        "| people | day ms | hash ms | journal ms | µs/person/day | journal KB/day | peak MB "
        "| copy bytes/person | stored bytes/person |"
    )
    print("|---|---|---|---|---|---|---|---|---|")
    for size in args.sizes:
        r = bench(size, args.days, args.profile, args.memory)
        print(
            f"| {r['people']:,} | {r['day_ms']:.0f} | {r['hash_ms']:.0f} | {r['journal_ms']:.0f} | "
            f"{r['us_per_person_day']:.1f} | {r['journal_kb_per_day']:.0f} | {r['peak_mb']:.0f} "
            f"| {r['people_bytes']:.0f} | {r['store_bytes']:.0f} |",
            flush=True,
        )


if __name__ == "__main__":
    main()
