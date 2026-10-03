"""Measure simulation throughput on a disposable scripted world (not a test).

Run: .venv/bin/python tests/observer/throughput.py [--days 365]

It creates a world in a temporary directory with the default scripted
(baseline) sovereigns, removes provider credentials from the environment so
no model can be called, times `run`, reports days per second and the
population, and deletes the directory. Browser rendering performance is
measured separately (observer/tests/measure.mjs).
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROVIDER_ENV = re.compile(r"(API_KEY|ANTHROPIC|OPENAI|SOVEREIGN_.*KEY)", re.IGNORECASE)


def _cli(args: list[str], env: dict[str, str]) -> str:
    command = [sys.executable, "-c", "from sovereign_world.cli import app; app()", *args]
    done = subprocess.run(command, env=env, check=True, capture_output=True, text=True)
    return done.stdout


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--seed", type=int, default=21)
    parser.add_argument("--size", type=int, default=48)
    args = parser.parse_args()
    env = {k: v for k, v in os.environ.items() if not PROVIDER_ENV.search(k)}
    with tempfile.TemporaryDirectory(prefix="throughput-") as tmp:
        world = Path(tmp) / "world"
        size = str(args.size)
        _cli(["init", str(world), "--seed", str(args.seed), "--width", size, "--height", size], env)
        start = time.perf_counter()
        _cli(["run", str(world), "--days", str(args.days)], env)
        seconds = time.perf_counter() - start
        summary = _cli(["inspect", str(world)], env)
    living = sum(int(m) for m in re.findall(r"living=(\d+)", summary))
    print(f"world: seed {args.seed}, {args.size}x{args.size}, scripted sovereigns, no providers")
    print(f"simulated {args.days} days in {seconds:.1f} s = {args.days / seconds:.1f} days/s")
    print(f"population after {args.days} days: {living} living")
    print("(includes process start-up and journal writes; the temporary world was deleted)")


if __name__ == "__main__":
    main()
