"""Checksummed journals and compressed SQLite checkpoints."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import sqlite3
from base64 import b64encode
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from sovereign_world.config import RunManifest
from sovereign_world.events import EventBatch
from sovereign_world.journal import (
    GZIP_LEVEL,
    SNAPSHOT_INTERVAL,
    Parts,
    Saved,
    compress,
    encode_delta,
    encode_snapshot,
    load_state,
    split_parts,
)
from sovereign_world.state import WorldState, state_hash, state_hash_v2


class JournalCorruption(RuntimeError):
    def __init__(self, offset: int, message: str) -> None:
        self.offset = offset
        super().__init__(f"journal corruption at byte {offset}: {message}")


class JournalRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    sequence: int
    type: str
    payload: dict[str, Any]
    previous_hash: str
    record_hash: str


def _record_hash(
    sequence: int,
    record_type: str,
    payload: dict[str, Any],
    previous_hash: str,
) -> str:
    encoded = json.dumps(
        {
            "sequence": sequence,
            "type": record_type,
            "payload": payload,
            "previous_hash": previous_hash,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


HEADER = "header"
SEAL = "seal"
"""The run's signed seal (sealed trial), saved once, right after the header, before day 1."""
"""The first record of a format-2 journal: its format and hash version."""


class WorldStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.database_path = root / "world.sqlite3"
        self.journal_path = root / "journal.jsonl"
        self._tail_sequence: int | None = None
        self._tail_hash: str | None = None
        self._verified_length: int | None = None
        self._journal_format: int | None = None
        self._last: Saved | None = None
        """The day this store last saved, to save the next one as the changes from it."""

    @classmethod
    def create(
        cls,
        root: Path,
        manifest: RunManifest,
        initial_state: WorldState,
    ) -> WorldStore:
        root.mkdir(parents=True, exist_ok=True)
        store = cls(root)
        if store.database_path.exists() or store.journal_path.exists():
            raise FileExistsError(f"world store already exists: {root}")
        with sqlite3.connect(store.database_path) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.executescript(
                """
                CREATE TABLE manifest (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    manifest_json TEXT NOT NULL,
                    manifest_hash TEXT NOT NULL
                );
                CREATE TABLE checkpoints (
                    day INTEGER PRIMARY KEY,
                    state_blob BLOB NOT NULL,
                    content_hash TEXT NOT NULL,
                    state_hash TEXT NOT NULL,
                    previous_checkpoint_hash TEXT NOT NULL
                );
                CREATE TABLE seal (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    seal_json TEXT NOT NULL,
                    seal_hash TEXT NOT NULL
                );
                """
            )
            connection.execute(
                "INSERT INTO manifest VALUES (1, ?, ?)",
                (manifest.model_dump_json(), manifest.content_hash()),
            )
            connection.commit()
        store.journal_path.write_bytes(b"")
        store._journal_format = manifest.journal_format
        if manifest.journal_format >= 2:
            store.append_record(
                HEADER,
                {
                    "journal_format": manifest.journal_format,
                    "hash_version": 2,
                    "run_id": str(manifest.run_id),
                    "manifest_hash": manifest.content_hash(),
                    "engine_version": manifest.engine_version,
                },
            )
        store.save_checkpoint(initial_state)
        if manifest.journal_format >= 2:
            # The first day is saved as its changes from this checkpoint.
            store._last = Saved(initial_state)
        return store

    @property
    def journal_format(self) -> int:
        """1 for journals without a header (whole world each day, hash v1), else the
        header's; it must agree with the manifest."""
        if self._journal_format is None:
            first = next(self.iter_records(), None)
            declared = self.manifest().journal_format
            found = (
                int(first.payload["journal_format"])
                if first is not None and first.type == HEADER
                else (declared if first is None else 1)
            )
            if found != declared:
                raise JournalCorruption(0, "journal format differs from the manifest")
            self._journal_format = found
        return self._journal_format

    @property
    def hash_version(self) -> int:
        return 1 if self.journal_format == 1 else 2

    def state_hash(self, state: WorldState, *, fresh: bool = False) -> str:
        """The run's hash of a state: version 1 or 2, as its journal format says."""
        if self.hash_version == 1:
            return state_hash(state)
        return state_hash_v2(state, fresh=fresh)

    def manifest(self) -> RunManifest:
        with sqlite3.connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT manifest_json, manifest_hash FROM manifest WHERE id = 1"
            ).fetchone()
        if row is None:
            raise RuntimeError("manifest is missing")
        manifest = RunManifest.model_validate_json(row[0])
        if manifest.content_hash() != row[1]:
            raise RuntimeError("manifest hash mismatch")
        return manifest

    def seal_document(self) -> dict[str, Any] | None:
        """The run's seal as saved, or None for a run that is not sealed (or predates seals)."""
        try:
            with sqlite3.connect(self.database_path) as connection:
                row = connection.execute(
                    "SELECT seal_json, seal_hash FROM seal WHERE id = 1"
                ).fetchone()
        except sqlite3.OperationalError:
            return None
        if row is None:
            return None
        if hashlib.sha256(row[0].encode("utf-8")).hexdigest() != row[1]:
            raise RuntimeError("seal hash mismatch")
        document: dict[str, Any] = json.loads(row[0])
        return document

    def write_seal(self, document: dict[str, Any]) -> None:
        """Save the seal once, in SQLite and as the journal's record after its header; only a
        run that has saved nothing but its header may be sealed."""
        records = self.read_records()
        if [record.type for record in records] != [HEADER]:
            raise RuntimeError("only a run that has saved nothing yet can be sealed")
        if self.seal_document() is not None:
            raise RuntimeError("the run is already sealed")
        text = json.dumps(document, sort_keys=True, separators=(",", ":"))
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS seal (id INTEGER PRIMARY KEY CHECK (id = 1),"
                " seal_json TEXT NOT NULL, seal_hash TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO seal VALUES (1, ?, ?)",
                (text, hashlib.sha256(text.encode("utf-8")).hexdigest()),
            )
            connection.commit()
        self.append_record(SEAL, document)

    def iter_records(self) -> Iterator[JournalRecord]:
        """Every complete record, checked against its sequence, chain and hash, read one
        line at a time; an incomplete last line is ignored, as a crash may leave one."""
        previous_hash = "0" * 64
        sequence = 0
        offset = 0
        with self.journal_path.open("rb") as journal:
            for line in journal:
                if not line.endswith(b"\n"):
                    break
                raw = line.rstrip(b"\r\n")
                try:
                    record = JournalRecord.model_validate_json(raw)
                except Exception as error:
                    raise JournalCorruption(offset, "invalid JSON record") from error
                expected_hash = _record_hash(
                    record.sequence,
                    record.type,
                    record.payload,
                    record.previous_hash,
                )
                if record.sequence != sequence + 1:
                    raise JournalCorruption(offset, "noncontiguous sequence")
                if record.previous_hash != previous_hash:
                    raise JournalCorruption(offset, "broken hash chain")
                if record.record_hash != expected_hash:
                    raise JournalCorruption(offset, "record hash mismatch")
                sequence = record.sequence
                previous_hash = record.record_hash
                offset += len(line)
                yield record
        self._tail_sequence = sequence
        self._tail_hash = previous_hash
        self._verified_length = offset

    def read_records(self) -> tuple[JournalRecord, ...]:
        return tuple(self.iter_records())

    def append_record(self, record_type: str, payload: dict[str, Any]) -> JournalRecord:
        if self._tail_sequence is None:
            self.read_records()
        if self._tail_sequence is None or self._tail_hash is None or self._verified_length is None:
            raise RuntimeError("journal tail was not initialized")
        if self.journal_path.stat().st_size != self._verified_length:
            with self.journal_path.open("r+b") as journal:
                journal.truncate(self._verified_length)
        sequence = self._tail_sequence + 1
        previous_hash = self._tail_hash
        digest = _record_hash(sequence, record_type, payload, previous_hash)
        record = JournalRecord(
            sequence=sequence,
            type=record_type,
            payload=payload,
            previous_hash=previous_hash,
            record_hash=digest,
        )
        encoded = record.model_dump_json().encode() + b"\n"
        with self.journal_path.open("ab") as journal:
            journal.write(encoded)
            journal.flush()
            os.fsync(journal.fileno())
        self._tail_sequence = sequence
        self._tail_hash = digest
        self._verified_length += len(encoded)
        return record

    def append_transition(
        self,
        state: WorldState,
        events: EventBatch,
        *,
        previous: WorldState | None = None,
        parts: Parts | None = None,
        hashed: str | None = None,
    ) -> JournalRecord:
        """Save a day. Format 2 saves only the changes from the day before when it has that
        day (the last it saved, or `previous`), and the whole world every 30 days. A caller
        that has already dumped the day (`parts`) and hashed it from that dump (`hashed`)
        passes them, so neither is done twice."""
        if self.journal_format == 1:
            encoded = b64encode(gzip.compress(state.model_dump_json().encode())).decode()
            return self.append_record(
                "transition",
                {
                    "day": state.day,
                    "state_gzip_base64": encoded,
                    "state_hash": state_hash(state),
                    "events": events.canonical_json(),
                },
            )
        if previous is not None and (self._last is None or self._last.day != previous.day):
            self._last = Saved(previous)
        if parts is None:
            parts = split_parts(state)
            hashed = None
        delta = None
        last = self._last
        if state.day % SNAPSHOT_INTERVAL and last is not None and last.day == state.day - 1:
            delta = encode_delta(last, state, parts)
        payload: dict[str, Any] = {
            "day": state.day,
            "state_hash": hashed or state_hash_v2(state, parts=parts),
            "events": events.canonical_json(),
        }
        if delta is None:
            # The same day always saves the same bytes.
            raw = encode_snapshot(state, parts)
            payload["state_gzip_base64"] = b64encode(
                gzip.compress(raw, compresslevel=GZIP_LEVEL, mtime=0)
            ).decode()
        else:
            payload["delta_gzip_base64"] = compress(delta)
        record = self.append_record("transition", payload)
        self._last = Saved(state, parts)
        return record

    def save_checkpoint(self, state: WorldState) -> None:
        raw = (
            encode_snapshot(state) if self.journal_format >= 2 else state.model_dump_json().encode()
        )
        digest = hashlib.sha256(raw).hexdigest()
        blob = gzip.compress(raw, compresslevel=GZIP_LEVEL if self.journal_format >= 2 else 9)
        with sqlite3.connect(self.database_path) as connection:
            previous = connection.execute(
                "SELECT content_hash FROM checkpoints WHERE day < ? ORDER BY day DESC LIMIT 1",
                (state.day,),
            ).fetchone()
            previous_hash = previous[0] if previous else "0" * 64
            connection.execute(
                """
                INSERT OR REPLACE INTO checkpoints
                (day, state_blob, content_hash, state_hash, previous_checkpoint_hash)
                VALUES (?, ?, ?, ?, ?)
                """,
                (state.day, blob, digest, self.state_hash(state), previous_hash),
            )
            connection.commit()

    def load_checkpoint(self, at_or_before: int | None = None) -> WorldState:
        query = "SELECT day, state_blob, content_hash, state_hash FROM checkpoints"
        parameters: tuple[int, ...] = ()
        if at_or_before is not None:
            query += " WHERE day <= ?"
            parameters = (at_or_before,)
        query += " ORDER BY day DESC"
        with sqlite3.connect(self.database_path) as connection:
            rows = connection.execute(query, parameters).fetchall()
        for _day, blob, expected_content, expected_state in rows:
            try:
                raw = gzip.decompress(blob)
                if hashlib.sha256(raw).hexdigest() != expected_content:
                    continue
                state = load_state(raw)
                if self.state_hash(state, fresh=True) != expected_state:
                    continue
                return state
            except Exception:
                continue
        raise RuntimeError("no verified checkpoint is available")
