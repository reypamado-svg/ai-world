"""Reference runs whose daily state hashes must never change.

Engine work that is meant to change nothing (speedups, refactors of how
people are stored) is checked against these hashes. New rules and new
generators must leave them alone too: they apply only to runs that ask for
them.

Regenerate the fixture only when a change is meant to alter old runs, which
should never happen:

    PYTHONPATH=src .venv/bin/python tests/golden.py
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from sovereign_world.config import RunManifest, WorldConfig
from sovereign_world.engine import advance_day
from sovereign_world.rng import StableRng
from sovereign_world.scripted import BaselineSovereign
from sovereign_world.state import build_initial_state, state_hash

FIXTURE = Path(__file__).parent / "fixtures" / "golden_hashes.json"
RUN_ID = UUID("00000000-0000-4000-8000-000000000021")


@dataclass(frozen=True, slots=True)
class GoldenRun:
    name: str
    seed: int
    size: int
    generator: int
    days: int


GOLDEN_RUNS = (
    GoldenRun("seed21-48-gen1", seed=21, size=48, generator=1, days=365),
    GoldenRun("seed21-48-gen2", seed=21, size=48, generator=2, days=365),
    *(
        GoldenRun(f"seed{seed}-24-gen2", seed=seed, size=24, generator=2, days=120)
        for seed in range(12)
    ),
)


def daily_hashes(run: GoldenRun) -> Iterator[str]:
    """The state hash after each day of a scripted run (baseline sovereigns)."""
    config = WorldConfig(seed=run.seed, width=run.size, height=run.size)
    manifest = RunManifest(
        run_id=RUN_ID, engine_version="golden", config=config, generator_version=run.generator
    )
    state = build_initial_state(manifest)
    sovereigns = {civilization_id: BaselineSovereign() for civilization_id in state.civilizations}
    rng = StableRng(config.seed)
    yield state_hash(state)
    for _ in range(run.days):
        state = advance_day(state, rng, sovereigns=sovereigns).state
        yield state_hash(state)


def record() -> None:
    hashes = {run.name: list(daily_hashes(run)) for run in GOLDEN_RUNS}
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(hashes, indent=0, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    record()
