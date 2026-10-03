"""A run saved in journal format 1, as runs were before Phase 5 S5, kept to prove old runs
still read, replay, verify and rederive.

    PYTHONPATH=src:tests .venv/bin/python -B tests/format_one.py

rewrites the fixture; do that only on purpose.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from uuid import UUID

from sovereign_world.config import RunManifest, WorldConfig
from sovereign_world.engine import advance_day
from sovereign_world.gateway.records import journal_councils
from sovereign_world.gateway.sovereign import RecordingSovereign
from sovereign_world.persistence import WorldStore
from sovereign_world.rng import StableRng
from sovereign_world.scripted import BaselineSovereign
from sovereign_world.state import build_initial_state

FIXTURE = Path(__file__).parent / "fixtures" / "format-one"
DAYS = 5


def record(root: Path = FIXTURE) -> WorldStore:
    manifest = RunManifest(
        run_id=UUID(int=11),
        engine_version="0.2.0",
        config=WorldConfig(seed=11, width=24, height=24),
        generator_version=2,
    )
    assert manifest.journal_format == 1
    state = build_initial_state(manifest)
    shutil.rmtree(root, ignore_errors=True)
    store = WorldStore.create(root, manifest, state)
    sovereigns = {
        civilization_id: RecordingSovereign(BaselineSovereign())
        for civilization_id in state.civilizations
    }
    rng = StableRng(manifest.config.seed)
    for _ in range(DAYS):
        transition = advance_day(state, rng, sovereigns=sovereigns)
        state = transition.state
        store.append_transition(state, transition.events)
        journal_councils(store, sovereigns.values())
    store.save_checkpoint(state)
    return store


if __name__ == "__main__":
    record()
