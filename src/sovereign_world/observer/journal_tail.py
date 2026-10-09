"""Following a run's journal without ever writing to it (O2).

`JournalTail` reads a journal by byte offset. It takes only complete lines (a line still being
written is left until its newline arrives) and checks each record against the one before: its
sequence, its link in the hash chain and its own hash. It keeps no payloads in memory, only
where each record lies, and re-reads one when asked.

When a record fails its checks the tail stops at the last good one and stays stopped: it
never repairs. When the file shrinks below what was read, or its first line changes (the run
was replaced), it starts again from the beginning and counts a new `history_epoch`, so a
reader knows the days it had may no longer be the run's.

It opens the journal read-only and never constructs `WorldStore`, whose append path may
truncate the file.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sovereign_world.persistence import _record_hash

GENESIS = "0" * 64


@dataclass(frozen=True)
class TailRecord:
    """Where a verified record lies in the journal, and what kind it is."""

    sequence: int
    type: str
    day: int | None
    offset: int
    length: int
    record_hash: str
    has_snapshot: bool
    has_delta: bool


class JournalTail:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.history_epoch = 0
        self._reset()

    def _reset(self) -> None:
        self.offset = 0
        self.sequence = 0
        self.previous_hash = GENESIS
        self.fingerprint: str | None = None
        self.stopped_at: int | None = None
        """Byte offset of the first record that failed its checks; None while reading."""
        self.stop_reason: str | None = None

    def _first_line_hash(self) -> str | None:
        with self.path.open("rb") as journal:
            line = journal.readline()
        if not line.endswith(b"\n"):
            return None
        return hashlib.sha256(line).hexdigest()

    def _history_changed(self) -> bool:
        if not self.path.exists():
            return self.offset > 0
        if self.path.stat().st_size < self.offset:
            return True
        return self.fingerprint is not None and self._first_line_hash() != self.fingerprint

    def poll(self) -> tuple[TailRecord, ...]:
        """Records completed since the last poll (all of them after a restart)."""
        if self._history_changed():
            self._reset()
            self.history_epoch += 1
        if self.stopped_at is not None or not self.path.exists():
            return ()
        found: list[TailRecord] = []
        with self.path.open("rb") as journal:
            journal.seek(self.offset)
            while True:
                line = journal.readline()
                if not line.endswith(b"\n"):
                    break
                record = self._verify(line)
                if record is None:
                    break
                found.append(record)
        return tuple(found)

    def _verify(self, line: bytes) -> TailRecord | None:
        offset = self.offset
        try:
            data = json.loads(line)
            sequence = int(data["sequence"])
            record_type = str(data["type"])
            payload: dict[str, Any] = data["payload"]
            previous_hash = str(data["previous_hash"])
            record_hash = str(data["record_hash"])
        except (ValueError, KeyError, TypeError):
            self._stop(offset, "invalid JSON record")
            return None
        if sequence != self.sequence + 1:
            self._stop(offset, "noncontiguous sequence")
            return None
        if previous_hash != self.previous_hash:
            self._stop(offset, "broken hash chain")
            return None
        if _record_hash(sequence, record_type, payload, previous_hash) != record_hash:
            self._stop(offset, "record hash mismatch")
            return None
        if offset == 0:
            self.fingerprint = hashlib.sha256(line).hexdigest()
        self.sequence = sequence
        self.previous_hash = record_hash
        self.offset += len(line)
        day = payload.get("day")
        return TailRecord(
            sequence=sequence,
            type=record_type,
            day=int(day) if record_type == "transition" and day is not None else None,
            offset=offset,
            length=len(line),
            record_hash=record_hash,
            has_snapshot="state_gzip_base64" in payload,
            has_delta="delta_gzip_base64" in payload,
        )

    def _stop(self, offset: int, reason: str) -> None:
        self.stopped_at = offset
        self.stop_reason = reason

    def payload(self, record: TailRecord) -> dict[str, Any]:
        """A verified record's payload, read again from the journal and checked against its
        hash (the file may have changed underneath)."""
        with self.path.open("rb") as journal:
            journal.seek(record.offset)
            line = journal.read(record.length)
        data = json.loads(line)
        payload: dict[str, Any] = data["payload"]
        if (
            int(data["sequence"]) != record.sequence
            or _record_hash(record.sequence, str(data["type"]), payload, str(data["previous_hash"]))
            != record.record_hash
        ):
            raise RuntimeError(f"journal record {record.sequence} changed since it was read")
        return payload
