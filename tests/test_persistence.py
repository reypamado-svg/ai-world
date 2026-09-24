from pathlib import Path

import pytest

from sovereign_world.config import RunManifest, WorldConfig
from sovereign_world.engine import advance_day
from sovereign_world.persistence import JournalCorruption, WorldStore
from sovereign_world.rng import StableRng
from sovereign_world.state import build_initial_state, state_hash


def _store_with_days(root: Path, days: int = 3) -> tuple[WorldStore, object]:
    config = WorldConfig(seed=30, width=48, height=48)
    manifest = RunManifest.new(config=config, engine_version="0.1.0")
    state = build_initial_state(manifest)
    store = WorldStore.create(root, manifest, state)
    rng = StableRng(config.seed)
    for _ in range(days):
        result = advance_day(state, rng)
        state = result.state
        store.append_transition(state, result.events)
    return store, state


def test_checkpoint_round_trip_preserves_state_hash(tmp_path: Path) -> None:
    store, state = _store_with_days(tmp_path)

    store.save_checkpoint(state)
    restored = store.load_checkpoint()

    assert state_hash(restored) == state_hash(state)


def test_truncated_journal_tail_is_ignored(tmp_path: Path) -> None:
    store, _ = _store_with_days(tmp_path, days=2)
    with store.journal_path.open("ab") as journal:
        journal.write(b'{"sequence":999,"type":"transition"')

    records = store.read_records()

    assert len(records) == 2


def test_corrupt_complete_record_reports_byte_offset(tmp_path: Path) -> None:
    store, _ = _store_with_days(tmp_path, days=2)
    data = store.journal_path.read_bytes().replace(
        b'"type":"transition"',
        b'"type":"transitioX"',
        1,
    )
    store.journal_path.write_bytes(data)

    with pytest.raises(JournalCorruption) as captured:
        store.read_records()

    assert captured.value.offset == 0
