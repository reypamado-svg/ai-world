import sqlite3
from pathlib import Path

from sovereign_world.config import RunManifest, WorldConfig
from sovereign_world.engine import advance_day
from sovereign_world.persistence import WorldStore
from sovereign_world.rng import StableRng
from sovereign_world.state import build_initial_state


def test_corrupt_newest_checkpoint_falls_back_to_previous(tmp_path: Path) -> None:
    config = WorldConfig(seed=51, width=48, height=48)
    manifest = RunManifest.new(config=config, engine_version="0.1.0")
    state = build_initial_state(manifest)
    store = WorldStore.create(tmp_path, manifest, state)
    rng = StableRng(config.seed)
    for _ in range(2):
        result = advance_day(state, rng)
        state = result.state
        store.append_transition(state, result.events)
        store.save_checkpoint(state)

    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE checkpoints SET state_blob = ? WHERE day = ?",
            (b"corrupt", 2),
        )
        connection.commit()

    recovered = store.load_checkpoint()

    assert recovered.day == 1

