"""Checksummed journals and compressed SQLite checkpoints."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from sovereign_world.config import RunManifest
from sovereign_world.events import EventBatch
from sovereign_world.state import WorldState, state_hash


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


class WorldStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.database_path = root / "world.sqlite3"
        self.journal_path = root / "journal.jsonl"

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
                """
            )
            connection.execute(
                "INSERT INTO manifest VALUES (1, ?, ?)",
                (manifest.model_dump_json(), manifest.content_hash()),
            )
            connection.commit()
        store.journal_path.write_bytes(b"")
        store.save_checkpoint(initial_state)
        return store

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

    def read_records(self) -> tuple[JournalRecord, ...]:
        data = self.journal_path.read_bytes()
        complete_length = data.rfind(b"\n") + 1
        if complete_length == 0:
            return ()
        records: list[JournalRecord] = []
        previous_hash = "0" * 64
        offset = 0
        for line in data[:complete_length].splitlines(keepends=True):
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
            if record.sequence != len(records) + 1:
                raise JournalCorruption(offset, "noncontiguous sequence")
            if record.previous_hash != previous_hash:
                raise JournalCorruption(offset, "broken hash chain")
            if record.record_hash != expected_hash:
                raise JournalCorruption(offset, "record hash mismatch")
            records.append(record)
            previous_hash = record.record_hash
            offset += len(line)
        return tuple(records)

    def append_record(self, record_type: str, payload: dict[str, Any]) -> JournalRecord:
        records = self.read_records()
        sequence = len(records) + 1
        previous_hash = records[-1].record_hash if records else "0" * 64
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
        return record

    def append_transition(self, state: WorldState, events: EventBatch) -> JournalRecord:
        return self.append_record(
            "transition",
            {
                "day": state.day,
                "state_json": state.model_dump_json(),
                "state_hash": state_hash(state),
                "events": events.canonical_json(),
            },
        )

    def save_checkpoint(self, state: WorldState) -> None:
        raw = state.model_dump_json().encode()
        digest = hashlib.sha256(raw).hexdigest()
        blob = gzip.compress(raw)
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
                (state.day, blob, digest, state_hash(state), previous_hash),
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
                state = WorldState.model_validate_json(raw)
                if state_hash(state) != expected_state:
                    continue
                return state
            except Exception:
                continue
        raise RuntimeError("no verified checkpoint is available")

