"""Parity fixtures for the people store (Phase 5 S4).

Recorded before people moved into columns: for each scenario, every day's state hash, and
the full saved state on a few days. The engine must reproduce every hash, and each saved
state must load and save again byte for byte.

    PYTHONPATH=src:tests .venv/bin/python -B tests/parity.py

rewrites the fixtures; do that only on purpose.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from sovereign_world.config import RunManifest, WorldConfig
from sovereign_world.engine import advance_day
from sovereign_world.rng import StableRng
from sovereign_world.scripted import BaselineSovereign
from sovereign_world.state import WorldState, build_initial_state, state_hash

FIXTURES = Path(__file__).parent / "fixtures" / "parity"
DAYS = 320
"""Long enough for councils, deaths and the first births (280 days after conception)."""
SAVED_DAYS = (0, 31, DAYS)


@dataclass(frozen=True)
class Scenario:
    name: str
    seed: int
    generator_version: int
    rules_version: int


SCENARIOS = (
    Scenario("rules1-seed0", seed=0, generator_version=2, rules_version=1),
    Scenario("rules2-seed7", seed=7, generator_version=3, rules_version=2),
)


def initial(scenario: Scenario) -> WorldState:
    manifest = RunManifest(
        run_id=UUID(int=scenario.seed + 1),
        engine_version="0.2.0",
        config=WorldConfig(seed=scenario.seed, width=24, height=24),
        generator_version=scenario.generator_version,
        rules_version=scenario.rules_version,
    )
    return build_initial_state(manifest)


def run(scenario: Scenario) -> Iterator[WorldState]:
    """The scenario's world on day 0 and after each of its days, with baseline sovereigns."""
    state = initial(scenario)
    sovereigns = {civilization_id: BaselineSovereign() for civilization_id in state.civilizations}
    rng = StableRng(scenario.seed)
    yield state
    for _ in range(DAYS):
        state = advance_day(state, rng, sovereigns=sovereigns).state
        yield state


def hashes_path(scenario: Scenario) -> Path:
    return FIXTURES / f"{scenario.name}-hashes.json"


def state_path(scenario: Scenario, day: int) -> Path:
    return FIXTURES / f"{scenario.name}-day{day:03d}.json.gz"


def record() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for scenario in SCENARIOS:
        hashes: list[str] = []
        for state in run(scenario):
            hashes.append(state_hash(state))
            if state.day in SAVED_DAYS:
                state_path(scenario, state.day).write_bytes(
                    gzip.compress(state.model_dump_json().encode(), mtime=0)
                )
        hashes_path(scenario).write_text(json.dumps(hashes, indent=0) + "\n")


if __name__ == "__main__":
    record()
