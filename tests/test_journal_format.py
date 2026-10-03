"""Journal formats: old runs stay format 1 (hash v1); new runs are format 2 (hash v2)."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from sovereign_world.cli import app
from sovereign_world.config import CURRENT_JOURNAL_FORMAT, RunManifest, WorldConfig
from sovereign_world.engine import advance_day
from sovereign_world.persistence import HEADER, JournalCorruption, WorldStore
from sovereign_world.replay import replay_run, verify_run
from sovereign_world.rng import StableRng
from sovereign_world.state import build_initial_state, state_hash, state_hash_v2

CONFIG = WorldConfig(seed=9, width=24, height=24)
RUN_ID = "6f1f7c32-3f0b-4f1e-9b55-2a2f1d1c0e14"


def _manifest(**update: object) -> RunManifest:
    return RunManifest.model_validate(
        {"run_id": RUN_ID, "engine_version": "0.1.0", "config": CONFIG, **update}
    )


def _store(root: Path, manifest: RunManifest, days: int = 3) -> WorldStore:
    state = build_initial_state(manifest)
    store = WorldStore.create(root, manifest, state)
    rng = StableRng(CONFIG.seed)
    for _ in range(days):
        result = advance_day(state, rng)
        state = result.state
        store.append_transition(state, result.events)
    return store


def test_new_runs_use_format_two_and_old_manifests_keep_their_hash() -> None:
    assert RunManifest.new(CONFIG, "0.1.0").journal_format == CURRENT_JOURNAL_FORMAT == 2
    old = _manifest()
    assert old.journal_format == 1
    assert old.content_hash() == _manifest(journal_format=1).content_hash()
    assert _manifest(journal_format=2).content_hash() != old.content_hash()


def test_a_format_one_store_has_no_header_and_hashes_whole(tmp_path: Path) -> None:
    store = _store(tmp_path, _manifest())
    records = store.read_records()
    assert [record.type for record in records] == ["transition"] * 3
    assert store.journal_format == 1 and store.hash_version == 1
    state = replay_run(store)
    assert records[-1].payload["state_hash"] == state_hash(state)
    assert verify_run(store).state_hash == state_hash(state)


def test_a_format_two_store_opens_with_its_header_and_hashes_in_parts(tmp_path: Path) -> None:
    store = _store(tmp_path, _manifest(journal_format=2))
    records = WorldStore(tmp_path).read_records()
    assert [record.type for record in records] == [HEADER, *["transition"] * 3]
    assert records[0].payload["journal_format"] == 2
    assert records[0].payload["hash_version"] == 2
    reopened = WorldStore(tmp_path)
    assert reopened.journal_format == 2
    state = replay_run(reopened)
    assert records[-1].payload["state_hash"] == state_hash_v2(state, fresh=True)
    assert verify_run(reopened).state_hash == state_hash_v2(state)
    assert store.load_checkpoint().day == 0


def test_a_header_that_disagrees_with_the_manifest_is_refused(tmp_path: Path) -> None:
    store = _store(tmp_path, _manifest(), days=0)
    store.append_record(HEADER, {"journal_format": 2, "hash_version": 2})
    with pytest.raises(JournalCorruption, match="format"):
        _ = WorldStore(tmp_path).journal_format


def test_a_fork_of_an_old_run_is_saved_in_the_current_format(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    _store(parent, _manifest(), days=3)
    sovereigns = tmp_path / "sovereigns.toml"
    sovereigns.write_text("")
    child = tmp_path / "child"
    result = CliRunner().invoke(
        app, ["fork", str(parent), str(child), "--day", "2", "--sovereigns", str(sovereigns)]
    )
    assert result.exit_code == 0, result.stdout
    store = WorldStore(child)
    assert store.journal_format == 2
    forked = store.load_checkpoint()
    assert forked.day == 2
    parent_day = replay_run(WorldStore(parent), target_day=2)
    assert state_hash_v2(forked) == state_hash_v2(
        parent_day.model_copy(
            update={"run_id": forked.run_id, "manifest_hash": forked.manifest_hash}
        )
    )
    inspected = CliRunner().invoke(app, ["inspect", str(child)])
    assert "journal format: 2" in inspected.stdout
