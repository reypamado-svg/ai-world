"""Verified replay and integrity reporting."""

from __future__ import annotations

import gzip
from base64 import b64decode
from dataclasses import dataclass

from sovereign_world.persistence import WorldStore
from sovereign_world.state import WorldState, state_hash


@dataclass(frozen=True, slots=True)
class VerificationResult:
    verified_through_day: int
    state_hash: str
    records: int


def _recorded_state(payload: dict[str, object]) -> WorldState:
    compressed = payload.get("state_gzip_base64")
    if isinstance(compressed, str):
        raw = gzip.decompress(b64decode(compressed))
        return WorldState.model_validate_json(raw)
    legacy = payload.get("state_json")
    if isinstance(legacy, str):
        return WorldState.model_validate_json(legacy)
    raise RuntimeError("transition does not contain a recorded state")


def replay_run(store: WorldStore, target_day: int | None = None) -> WorldState:
    records = store.read_records()
    if target_day is None:
        target_day = max((int(record.payload["day"]) for record in records), default=0)
    if target_day == 0:
        return store.load_checkpoint(at_or_before=0)
    for record in reversed(records):
        if record.type != "transition" or int(record.payload["day"]) != target_day:
            continue
        state = _recorded_state(record.payload)
        if state_hash(state) != record.payload["state_hash"]:
            raise RuntimeError(f"state hash mismatch at day {target_day}")
        return state
    return store.load_checkpoint(at_or_before=target_day)


def verify_run(store: WorldStore) -> VerificationResult:
    records = store.read_records()
    last_day = 0
    last_hash = state_hash(store.load_checkpoint(at_or_before=0))
    for record in records:
        if record.type != "transition":
            continue
        state = _recorded_state(record.payload)
        actual_hash = state_hash(state)
        if actual_hash != record.payload["state_hash"]:
            raise RuntimeError(f"state hash mismatch at journal sequence {record.sequence}")
        last_day = state.day
        last_hash = actual_hash
    return VerificationResult(
        verified_through_day=last_day,
        state_hash=last_hash,
        records=len(records),
    )
