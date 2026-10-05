"""Reading a saved run for the observer, in either journal format, without writing (O2).

`RunReader` answers what the observer asks of a run: which days are saved, the world on any
of them, that day's events, and the councils held. A format-2 journal is rebuilt from the
whole-world snapshot at or before the day asked for, through the days' changes; reading days
in order rebuilds each from the one before.

It never writes to the run. The journal is followed by `JournalTail` (byte offsets, complete
lines only, every record checked, never repaired); the SQLite file is opened read-only and
immutable, so not even SQLite's side files appear; `WorldStore`, whose append path may
truncate the journal, is never constructed. When the journal is cut back or replaced the
reader starts again and its `history_epoch` goes up. Payloads are read from the journal when
needed rather than kept in memory.

Immutable opening means a checkpoint still sitting in a live writer's WAL file is not seen;
the reader needs only the verified checkpoint the journal starts from.
"""

from __future__ import annotations

import gzip
import hashlib
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from sovereign_world.config import RunManifest
from sovereign_world.events import DomainEvent
from sovereign_world.gateway.records import COUNCIL_RECORD, CouncilRecord
from sovereign_world.journal import StateCursor, decompress, load_state
from sovereign_world.observer.journal_tail import JournalTail, TailRecord
from sovereign_world.persistence import HEADER
from sovereign_world.replay import recorded_state
from sovereign_world.state import WorldState, state_hash, state_hash_v2


def _readonly(path: Path) -> sqlite3.Connection:
    """A connection that cannot write, and leaves no journal, WAL or shared-memory file.

    While a writer has the database open, what it saved may still be only in its write-ahead
    log; a reader then opens read-only through the writer's own log and shared-memory files,
    which already exist, so nothing new is made. With no writer, every saved page is in the
    database file, which is read as unchanging.
    """
    if not path.exists():
        raise FileNotFoundError(path)
    live = all(path.with_name(path.name + suffix).exists() for suffix in ("-wal", "-shm"))
    mode = "mode=ro" if live else "mode=ro&immutable=1"
    return sqlite3.connect(f"{path.resolve().as_uri()}?{mode}", uri=True)


class RunReader:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.database_path = root / "world.sqlite3"
        self._tail = JournalTail(root / "journal.jsonl")
        self._manifest: RunManifest | None = None
        self._epoch = self._tail.history_epoch
        self._clear()
        self.refresh()

    def _clear(self) -> None:
        self._records: list[TailRecord] = []
        self._transitions: dict[int, TailRecord] = {}
        self._start: WorldState | None = None
        self._cursor = StateCursor()
        self._journal_format: int | None = None

    # ------------------------------------------------------------ run facts
    def manifest(self) -> RunManifest:
        if self._manifest is None:
            connection = _readonly(self.database_path)
            try:
                row = connection.execute(
                    "SELECT manifest_json, manifest_hash FROM manifest WHERE id = 1"
                ).fetchone()
            finally:
                connection.close()
            if row is None:
                raise RuntimeError("manifest is missing")
            manifest = RunManifest.model_validate_json(row[0])
            if manifest.content_hash() != row[1]:
                raise RuntimeError("manifest hash mismatch")
            self._manifest = manifest
        return self._manifest

    @property
    def journal_format(self) -> int:
        """1 for journals without a header, else the header's; it must agree with the
        manifest."""
        if self._journal_format is None:
            declared = self.manifest().journal_format
            first = self._records[0] if self._records else None
            if first is None:
                found = declared
            elif first.type == HEADER:
                found = int(self._tail.payload(first)["journal_format"])
            else:
                found = 1
            if found != declared:
                raise RuntimeError("journal format differs from the manifest")
            self._journal_format = found
        return self._journal_format

    @property
    def history_epoch(self) -> int:
        """Goes up each time the journal was cut back or replaced and reading began again."""
        return self._tail.history_epoch

    @property
    def stopped_at(self) -> int | None:
        """Byte offset of a record that failed its checks, after which nothing is read."""
        return self._tail.stopped_at

    def refresh(self) -> tuple[int, ...]:
        """Read records saved since the last look; returns the days they added (after the
        journal was cut back or replaced, every day it now holds)."""
        found = self._tail.poll()
        if self._tail.history_epoch != self._epoch:
            self._epoch = self._tail.history_epoch
            self._clear()
        added: list[int] = []
        for record in found:
            self._records.append(record)
            if record.type == "transition" and record.day is not None:
                self._transitions[record.day] = record
                added.append(record.day)
        return tuple(added)

    def _payload(self, record: TailRecord) -> dict[str, Any]:
        return self._tail.payload(record)

    def _state_hash(self, state: WorldState) -> str:
        return state_hash(state) if self.journal_format == 1 else state_hash_v2(state, fresh=True)

    def _load_checkpoint(self, at_or_before: int | None = None) -> WorldState:
        """The latest verified checkpoint at or before a day, as `WorldStore.load_checkpoint`
        checks it, read without writing."""
        query = "SELECT day, state_blob, content_hash, state_hash FROM checkpoints"
        parameters: tuple[int, ...] = ()
        if at_or_before is not None:
            query += " WHERE day <= ?"
            parameters = (at_or_before,)
        query += " ORDER BY day DESC"
        connection = _readonly(self.database_path)
        try:
            rows = connection.execute(query, parameters).fetchall()
        finally:
            connection.close()
        for _day, blob, expected_content, expected_state in rows:
            try:
                raw = gzip.decompress(blob)
                if hashlib.sha256(raw).hexdigest() != expected_content:
                    continue
                state = load_state(raw)
                if self._state_hash(state) != expected_state:
                    continue
                return state
            except Exception:
                continue
        raise RuntimeError("no verified checkpoint is available")

    def _first_checkpoint(self) -> WorldState:
        """The day the journal follows: day 0, or the day a fork began."""
        if self._start is None:
            first = min(self._transitions, default=None)
            self._start = (
                self._load_checkpoint()
                if first is None
                else self._load_checkpoint(at_or_before=first - 1)
            )
        return self._start

    # ------------------------------------------------------------ days
    def days(self) -> tuple[int, ...]:
        return (self._first_checkpoint().day, *sorted(self._transitions))

    def state_at(self, day: int) -> WorldState:
        if day == self._first_checkpoint().day:
            return self._first_checkpoint()
        if day not in self._transitions:
            raise KeyError(f"day {day} is not saved in this run")
        if self.journal_format == 1:
            return recorded_state(self._payload(self._transitions[day]))
        cursor = self._cursor
        if (
            cursor.state is None
            or not cursor.state.day < day
            or self._snapshot_between(cursor.state.day, day)
        ):
            # Start again from the last whole world at or before the day.
            base = max(
                (
                    saved
                    for saved, item in self._transitions.items()
                    if saved <= day and item.has_snapshot
                ),
                default=None,
            )
            if base is None:
                cursor.load_snapshot(self._first_checkpoint())
            else:
                cursor.load_snapshot(recorded_state(self._payload(self._transitions[base])))
        assert cursor.state is not None
        while cursor.state.day < day:
            following = self._transitions.get(cursor.state.day + 1)
            if following is None or not following.has_delta:
                raise RuntimeError(f"day {cursor.state.day + 1} cannot be rebuilt in order")
            cursor.apply_delta(decompress(self._payload(following)["delta_gzip_base64"]))
        return cursor.state

    def _snapshot_between(self, after: int, through: int) -> bool:
        """Whether a whole world is saved after one day and by another: quicker to load."""
        return any(
            self._transitions[day].has_snapshot
            for day in range(after + 1, through + 1)
            if day in self._transitions
        )

    def iter_days(self) -> Iterator[WorldState]:
        for day in self.days():
            yield self.state_at(day)

    def events_at(self, day: int) -> tuple[DomainEvent, ...]:
        record = self._transitions.get(day)
        if record is None:
            return ()
        text = str(self._payload(record)["events"])
        return tuple(DomainEvent.model_validate_json(line) for line in text.splitlines())

    def councils(self) -> tuple[CouncilRecord, ...]:
        return tuple(
            CouncilRecord.model_validate(self._payload(record))
            for record in self._records
            if record.type == COUNCIL_RECORD
        )
