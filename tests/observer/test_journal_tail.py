"""The observer follows a journal by byte offset and never writes to it (O2).

Each test snapshots the run directory (every file's bytes and modification time, and the
listing) before reading and checks nothing changed afterwards.
"""

from __future__ import annotations

import json
from pathlib import Path

from parity import SCENARIOS, initial
from test_run_reader import _record

from sovereign_world.config import RunManifest
from sovereign_world.engine import advance_day
from sovereign_world.observer.journal_tail import JournalTail
from sovereign_world.persistence import WorldStore
from sovereign_world.replay import replay_run
from sovereign_world.rng import StableRng


def _snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(root.iterdir())
        if path.is_file()
    }


def _untouched(root: Path, before: dict[str, tuple[bytes, int]]) -> None:
    assert _snapshot(root) == before


def _transitions(records: tuple) -> list[int]:
    return [record.day for record in records if record.type == "transition"]


def _line_of_day(lines: list[bytes], day: int) -> int:
    """Index of the line saving `day` (council records sit between the days)."""
    for index, line in enumerate(lines):
        record = json.loads(line)
        if record["type"] == "transition" and record["payload"]["day"] == day:
            return index
    raise LookupError(day)


def test_every_record_once_in_order_with_payloads_read_back(tmp_path: Path) -> None:
    _record(tmp_path, 5)
    before = _snapshot(tmp_path)
    tail = JournalTail(tmp_path / "journal.jsonl")
    records = tail.poll()
    assert [record.sequence for record in records] == list(range(1, len(records) + 1))
    assert records[0].type == "header"
    assert _transitions(records) == [1, 2, 3, 4, 5]
    assert tail.poll() == ()
    stored = WorldStore(tmp_path).read_records()
    assert [tail.payload(record) for record in records] == [record.payload for record in stored]
    assert tail.stopped_at is None and tail.history_epoch == 0
    _untouched(tmp_path, before)


def test_a_line_still_being_written_is_left_until_it_is_complete(tmp_path: Path) -> None:
    _record(tmp_path, 3)
    journal = tmp_path / "journal.jsonl"
    whole = journal.read_bytes()
    last_start = whole.rstrip(b"\n").rfind(b"\n") + 1
    journal.write_bytes(whole[: last_start + 40])  # the last record half written
    tail = JournalTail(journal)
    first = tail.poll()
    assert _transitions(first) == [1, 2]
    before = _snapshot(tmp_path)
    assert tail.poll() == ()
    _untouched(tmp_path, before)
    journal.write_bytes(whole)  # the writer finishes the line
    assert _transitions(tail.poll()) == [3]
    assert tail.history_epoch == 0 and tail.stopped_at is None


def test_a_flipped_byte_stops_the_tail_at_the_last_good_record(tmp_path: Path) -> None:
    _record(tmp_path, 4)
    journal = tmp_path / "journal.jsonl"
    lines = journal.read_bytes().splitlines(keepends=True)
    # Change one digit inside day 3's state hash.
    target = _line_of_day(lines, 3)
    raw = lines[target]
    at = raw.index(b'"state_hash":"') + len(b'"state_hash":"')
    flipped = raw[:at] + (b"1" if raw[at : at + 1] != b"1" else b"2") + raw[at + 1 :]
    lines[target] = flipped
    journal.write_bytes(b"".join(lines))
    before = _snapshot(tmp_path)
    tail = JournalTail(journal)
    records = tail.poll()
    assert _transitions(records) == [1, 2]
    assert tail.stopped_at == sum(len(line) for line in lines[:target])
    assert tail.stop_reason == "record hash mismatch"
    # It stays stopped, and never repairs.
    assert tail.poll() == ()
    _untouched(tmp_path, before)


def test_truncation_and_replacement_start_a_new_history(tmp_path: Path) -> None:
    _record(tmp_path, 4)
    journal = tmp_path / "journal.jsonl"
    tail = JournalTail(journal)
    assert _transitions(tail.poll()) == [1, 2, 3, 4]
    lines = journal.read_bytes().splitlines(keepends=True)
    journal.write_bytes(b"".join(lines[: _line_of_day(lines, 2) + 1]))  # cut back to day 2
    before = _snapshot(tmp_path)
    assert _transitions(tail.poll()) == [1, 2]
    assert tail.history_epoch == 1
    _untouched(tmp_path, before)
    # Replaced by another, longer run: its first line (the header) differs.
    other = tmp_path / "other"
    start = initial(SCENARIOS[1])
    manifest = RunManifest.model_validate(
        {
            "run_id": start.run_id,
            "config": start.config,
            "engine_version": "0.1.1",
            "rules_version": start.rules_version,
            "journal_format": 2,
        }
    )
    _record(other, 6, WorldStore.create(other, manifest, start))
    replacement = (other / "journal.jsonl").read_bytes()
    assert replacement.splitlines()[0] != lines[0].rstrip(b"\n")
    journal.write_bytes(replacement)
    before = _snapshot(tmp_path)
    assert _transitions(tail.poll()) == [1, 2, 3, 4, 5, 6]
    assert tail.history_epoch == 2
    _untouched(tmp_path, before)


def test_a_day_appended_while_reading_is_picked_up(tmp_path: Path) -> None:
    _record(tmp_path, 2)
    tail = JournalTail(tmp_path / "journal.jsonl")
    assert _transitions(tail.poll()) == [1, 2]
    store = WorldStore(tmp_path)
    latest = replay_run(store)
    transition = advance_day(latest, StableRng(latest.config.seed))
    store.append_transition(transition.state, transition.events, previous=latest)
    before = _snapshot(tmp_path)
    assert _transitions(tail.poll()) == [3]
    assert tail.history_epoch == 0
    _untouched(tmp_path, before)


def test_the_tail_never_imports_the_store_class() -> None:
    observer = Path(__file__).resolve().parents[2] / "src" / "sovereign_world" / "observer"
    source = (observer / "journal_tail.py").read_text()
    assert "WorldStore" not in source.split('"""', 2)[2]
