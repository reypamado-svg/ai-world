"""Runs saved before journal format 2 still read, replay, verify, rederive and fork."""

from __future__ import annotations

import shutil
from pathlib import Path

from format_one import DAYS, FIXTURE
from typer.testing import CliRunner

from sovereign_world.cli import app
from sovereign_world.gateway.records import recorded_councils
from sovereign_world.persistence import HEADER, WorldStore
from sovereign_world.replay import rederive_run, replay_run, verify_run
from sovereign_world.state import state_hash, state_hash_v2


def _copy(tmp_path: Path) -> WorldStore:
    """A copy, so nothing here can touch the committed run."""
    root = tmp_path / "old"
    shutil.copytree(FIXTURE, root)
    return WorldStore(root)


def test_an_old_run_reads_as_format_one(tmp_path: Path) -> None:
    store = _copy(tmp_path)
    records = store.read_records()
    assert HEADER not in {record.type for record in records}
    assert store.journal_format == 1 and store.hash_version == 1
    assert store.manifest().journal_format == 1
    transitions = [record for record in records if record.type == "transition"]
    assert [record.payload["day"] for record in transitions] == list(range(1, DAYS + 1))
    assert all("state_gzip_base64" in record.payload for record in transitions)
    assert recorded_councils(store), "the day-0 councils were recorded"


def test_an_old_run_replays_verifies_and_rederives_under_hash_one(tmp_path: Path) -> None:
    store = _copy(tmp_path)
    latest = replay_run(store)
    assert latest.day == DAYS
    for record in store.read_records():
        if record.type == "transition":
            day = replay_run(store, target_day=int(record.payload["day"]))
            assert state_hash(day) == record.payload["state_hash"]
    assert verify_run(store).state_hash == state_hash(latest)
    assert rederive_run(store).state_hash == state_hash(latest)
    assert state_hash(store.load_checkpoint()) == state_hash(latest)


def test_an_old_run_carries_on_in_its_own_format(tmp_path: Path) -> None:
    store = _copy(tmp_path)
    runner = CliRunner()
    assert runner.invoke(app, ["run", str(store.root), "--days", "2"]).exit_code == 0
    reopened = WorldStore(store.root)
    assert reopened.journal_format == 1
    transitions = [record for record in reopened.read_records() if record.type == "transition"]
    assert all("state_gzip_base64" in record.payload for record in transitions)
    verified = runner.invoke(app, ["verify", str(store.root)])
    assert verified.exit_code == 0, verified.stdout
    assert f"verified through day {DAYS + 2}" in verified.stdout
    assert "journal format 1" in verified.stdout


def test_a_fork_of_an_old_run_saves_its_changes_in_format_two(tmp_path: Path) -> None:
    store = _copy(tmp_path)
    sovereigns = tmp_path / "sovereigns.toml"
    sovereigns.write_text("")
    child = tmp_path / "child"
    runner = CliRunner()
    forked = runner.invoke(
        app, ["fork", str(store.root), str(child), "--day", "3", "--sovereigns", str(sovereigns)]
    )
    assert forked.exit_code == 0, forked.stdout
    assert runner.invoke(app, ["run", str(child), "--days", "3"]).exit_code == 0
    fork = WorldStore(child)
    assert fork.journal_format == 2
    payloads = [record.payload for record in fork.read_records() if record.type == "transition"]
    assert [payload["day"] for payload in payloads] == [4, 5, 6]
    assert all("delta_gzip_base64" in payload for payload in payloads)
    latest = replay_run(fork)
    assert verify_run(fork).state_hash == state_hash_v2(latest, fresh=True)
