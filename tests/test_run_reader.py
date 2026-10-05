"""The observer's run reader: every day, in any order, in either journal format."""

from __future__ import annotations

import random
import shutil
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from format_one import DAYS as OLD_DAYS
from format_one import FIXTURE
from parity import SCENARIOS, initial

from sovereign_world import persistence
from sovereign_world.config import RunManifest
from sovereign_world.engine import advance_day
from sovereign_world.gateway.records import journal_councils
from sovereign_world.gateway.sovereign import RecordingSovereign
from sovereign_world.observer.reader import RunReader
from sovereign_world.persistence import WorldStore
from sovereign_world.replay import replay_run
from sovereign_world.rng import StableRng
from sovereign_world.scripted import BaselineSovereign
from sovereign_world.state import WorldState, state_hash

DAYS = 64


def _record(
    root: Path, days: int, store: WorldStore | None = None, scenario: int = 1
) -> list[WorldState]:
    """A run (rules 2 unless told) saved in format 2 with recorded councils; every day's
    world."""
    start = initial(SCENARIOS[scenario])
    if store is None:
        manifest = RunManifest.model_validate(
            {
                "run_id": start.run_id,
                "config": start.config,
                "engine_version": "0.1.0",
                "rules_version": start.rules_version,
                "journal_format": 2,
            }
        )
        store = WorldStore.create(root, manifest, start)
    sovereigns = {
        civilization_id: RecordingSovereign(BaselineSovereign())
        for civilization_id in start.civilizations
    }
    rng = StableRng(start.config.seed)
    states = [start]
    for _ in range(days):
        transition = advance_day(states[-1], rng, sovereigns=sovereigns)
        states.append(transition.state)
        store.append_transition(transition.state, transition.events)
        journal_councils(store, sovereigns.values())
    return states


def test_every_day_of_a_format_two_run_in_order_and_at_random(tmp_path: Path) -> None:
    states = _record(tmp_path, DAYS)
    reader = RunReader(tmp_path)
    assert reader.journal_format == 2
    assert reader.days() == tuple(range(DAYS + 1))
    hashes = [state_hash(state) for state in states]
    assert [state_hash(state) for state in reader.iter_days()] == hashes
    order = list(range(DAYS + 1))
    random.Random(5).shuffle(order)
    for day in order:
        assert state_hash(reader.state_at(day)) == hashes[day], day
    # The same day twice, and a day just before the one last read.
    assert state_hash(reader.state_at(40)) == state_hash(reader.state_at(40)) == hashes[40]
    assert state_hash(reader.state_at(39)) == hashes[39]


def test_events_and_councils_as_saved(tmp_path: Path) -> None:
    _record(tmp_path, 35)
    reader = RunReader(tmp_path)
    store = WorldStore(tmp_path)
    for record in store.read_records():
        if record.type == "transition":
            day = int(record.payload["day"])
            events = reader.events_at(day)
            assert "\n".join(event.canonical_json() for event in events) == record.payload["events"]
            assert all(event.day == day - 1 or event.day == day for event in events)
    assert reader.events_at(0) == ()
    councils = reader.councils()
    assert {council.day for council in councils} == {0, 30}
    assert len({council.civilization_id for council in councils}) == len(
        reader.state_at(0).civilizations
    )


def test_refresh_finds_the_days_saved_since(tmp_path: Path) -> None:
    _record(tmp_path, 3)
    reader = RunReader(tmp_path)
    assert reader.days() == (0, 1, 2, 3)
    assert reader.refresh() == ()
    store = WorldStore(tmp_path)
    latest = replay_run(store)
    rng = StableRng(latest.config.seed)
    # Not the run's own continuation (the random stream starts again), but a saved day all
    # the same.
    transition = advance_day(latest, rng)
    store.append_transition(transition.state, transition.events, previous=latest)
    assert reader.refresh() == (4,)
    assert state_hash(reader.state_at(4)) == state_hash(transition.state)


def test_a_format_one_run_reads_the_same_way(tmp_path: Path) -> None:
    root = tmp_path / "old"
    shutil.copytree(FIXTURE, root)
    reader = RunReader(root)
    store = WorldStore(root)
    assert reader.journal_format == 1
    assert reader.days() == tuple(range(OLD_DAYS + 1))
    for day in reversed(reader.days()):
        assert state_hash(reader.state_at(day)) == state_hash(replay_run(store, target_day=day))
    assert reader.councils() and {council.day for council in reader.councils()} == {0}
    assert reader.events_at(1)


def _files(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(root.iterdir())
        if path.is_file()
    }


def test_reading_a_run_writes_nothing(tmp_path: Path) -> None:
    """Every day, event and council of a format-2 run and of the format-1 fixture is read;
    no file changes (bytes or modification time) and SQLite leaves no side file."""
    new = tmp_path / "new"
    _record(new, 35)
    old = tmp_path / "old"
    shutil.copytree(FIXTURE, old)
    for root in (new, old):
        before = _files(root)
        reader = RunReader(root)
        for day in reader.days():
            reader.state_at(day)
            reader.events_at(day)
        reader.councils()
        reader.refresh()
        assert reader.history_epoch == 0 and reader.stopped_at is None
        assert _files(root) == before, root.name
        assert not [p for p in root.iterdir() if p.name.endswith(("-wal", "-shm", "-journal"))]


def test_the_reader_never_builds_a_store() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "src" / "sovereign_world" / "observer" / "reader.py"
    ).read_text()
    assert "WorldStore(" not in source
    assert "sqlite3.connect(" in source and "mode=ro&immutable=1" in source


def test_a_journal_cut_back_starts_a_new_history(tmp_path: Path) -> None:
    states = _record(tmp_path, 6)
    reader = RunReader(tmp_path)
    assert reader.days() == tuple(range(7))
    journal = tmp_path / "journal.jsonl"
    lines = journal.read_bytes().splitlines(keepends=True)
    keep = next(
        index
        for index, line in enumerate(lines)
        if b'"type":"transition"' in line and b'"day":3,' in line
    )
    journal.write_bytes(b"".join(lines[: keep + 1]))
    assert reader.refresh() == (1, 2, 3)
    assert reader.history_epoch == 1
    assert reader.days() == (0, 1, 2, 3)
    assert state_hash(reader.state_at(3)) == state_hash(states[3])


def test_a_replaced_run_is_read_with_its_own_manifest(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _record(run, 3)
    reader = RunReader(run)
    before = reader.manifest()
    other = tmp_path / "other"
    states = _record(other, 2, scenario=0)
    # The whole run is replaced: its database with its own write-ahead files, if any.
    for name in ("world.sqlite3", "world.sqlite3-wal", "world.sqlite3-shm", "journal.jsonl"):
        if (other / name).exists():
            shutil.copyfile(other / name, run / name)
        else:
            (run / name).unlink(missing_ok=True)
    reader.refresh()
    assert reader.history_epoch == 1
    after = reader.manifest()
    assert after.run_id == states[0].run_id != before.run_id
    assert reader.days() == (0, 1, 2)
    assert state_hash(reader.state_at(2)) == state_hash(states[2])


def test_a_run_whose_writer_is_still_open_reads_from_its_write_ahead_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A live run's writer keeps its connections open, so what it saved can sit in the
    write-ahead log rather than the database file. The reader still sees it, and makes no
    file of its own."""
    held: list[sqlite3.Connection] = []
    connect = sqlite3.connect

    def keep_open(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        connection = connect(*args, **kwargs)
        held.append(connection)
        return connection

    monkeypatch.setattr(persistence.sqlite3, "connect", keep_open)
    states = _record(tmp_path, 3)
    monkeypatch.undo()
    names = {path.name for path in tmp_path.iterdir()}
    assert {"world.sqlite3-wal", "world.sqlite3-shm"} <= names
    # Nothing has reached the database file itself yet: a reader that skipped the log would
    # find no tables at all.
    with sqlite3.connect(
        f"{(tmp_path / 'world.sqlite3').resolve().as_uri()}?mode=ro&immutable=1", uri=True
    ) as blind:
        assert blind.execute("SELECT name FROM sqlite_master").fetchall() == []
    reader = RunReader(tmp_path)
    assert reader.days() == (0, 1, 2, 3)
    assert [state_hash(reader.state_at(day)) for day in reader.days()] == [
        state_hash(state) for state in states
    ]
    assert {path.name for path in tmp_path.iterdir()} == names
    for connection in held:
        connection.close()
