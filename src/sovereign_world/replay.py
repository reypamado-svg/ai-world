"""Verified replay and integrity reporting."""

from __future__ import annotations

import gzip
from base64 import b64decode
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from sovereign_world.engine import advance_day
from sovereign_world.gateway.records import RecordedSovereign, recorded_councils
from sovereign_world.ids import EntityId
from sovereign_world.journal import StateCursor, decompress, load_state
from sovereign_world.persistence import JournalRecord, WorldStore
from sovereign_world.rng import StableRng
from sovereign_world.scripted import Sovereign
from sovereign_world.state import WorldState


@dataclass(frozen=True, slots=True)
class VerificationResult:
    verified_through_day: int
    state_hash: str
    records: int


def recorded_state(payload: dict[str, object]) -> WorldState:
    compressed = payload.get("state_gzip_base64")
    if isinstance(compressed, str):
        return load_state(gzip.decompress(b64decode(compressed)))
    legacy = payload.get("state_json")
    if isinstance(legacy, str):
        return WorldState.model_validate_json(legacy)
    raise RuntimeError("transition does not contain a recorded state")


def recorded_states(
    store: WorldStore, records: Iterable[JournalRecord], *, start: WorldState | None = None
) -> Iterator[tuple[JournalRecord, WorldState]]:
    """Each day's world as the journal holds it, in order: whole for snapshots, rebuilt from
    the day before for format-2 changes. `start` is the saved day the records follow (the
    run's first checkpoint), which the first day's changes may build on."""
    cursor = StateCursor()
    if start is not None and store.journal_format >= 2:
        cursor.load_snapshot(start)
    for record in records:
        if record.type != "transition":
            continue
        payload = record.payload
        compressed = payload.get("delta_gzip_base64")
        if isinstance(compressed, str):
            if cursor.state is None:
                raise RuntimeError(
                    f"journal record {record.sequence} holds changes with no day before it"
                )
            yield record, cursor.apply_delta(decompress(compressed))
        elif store.journal_format == 1:
            yield record, recorded_state(payload)
        else:
            yield record, cursor.load_snapshot(recorded_state(payload))


def replay_run(store: WorldStore, target_day: int | None = None) -> WorldState:
    records = store.read_records()
    if target_day is None:
        latest = max(
            (int(record.payload["day"]) for record in records if record.type == "transition"),
            default=None,
        )
        if latest is None:
            # Nothing journaled yet: the run starts at its first checkpoint, day 0 or its fork.
            return store.load_checkpoint()
        target_day = latest
    if target_day == 0:
        return store.load_checkpoint(at_or_before=0)
    transitions = [record for record in records if record.type == "transition"]
    found = [
        index for index, record in enumerate(transitions) if record.payload["day"] == target_day
    ]
    if not found:
        return store.load_checkpoint(at_or_before=target_day)
    end = found[-1]
    # Start from the last whole world at or before the day, and rebuild forward to it; with
    # none in the journal, from the checkpoint the journal begins after.
    snapshots = [
        index for index in range(end + 1) if "delta_gzip_base64" not in transitions[index].payload
    ]
    first = snapshots[-1] if snapshots else 0
    start = (
        None
        if snapshots
        else store.load_checkpoint(at_or_before=int(transitions[0].payload["day"]) - 1)
    )
    state: WorldState | None = None
    for _, rebuilt in recorded_states(store, transitions[first : end + 1], start=start):
        state = rebuilt
    assert state is not None
    if store.state_hash(state, fresh=True) != transitions[end].payload["state_hash"]:
        raise RuntimeError(f"state hash mismatch at day {target_day}")
    return state


def verify_run(store: WorldStore) -> VerificationResult:
    records = store.read_records()
    manifest_hash = store.manifest().content_hash()
    header = records[0] if records and records[0].type == "header" else None
    if header is not None and header.payload.get("manifest_hash") != manifest_hash:
        raise RuntimeError("manifest hash mismatch in the journal's header")
    days = [int(record.payload["day"]) for record in records if record.type == "transition"]
    # A forked run begins at its fork, not at day zero.
    start = store.load_checkpoint(at_or_before=days[0] - 1) if days else store.load_checkpoint()
    last_day = start.day
    last_hash = store.state_hash(start, fresh=True)
    for record, state in recorded_states(store, records, start=start):
        actual_hash = store.state_hash(state, fresh=True)
        if actual_hash != record.payload["state_hash"]:
            raise RuntimeError(f"state hash mismatch at journal sequence {record.sequence}")
        # Every day names the manifest the run started under (a fork restamps its own start).
        if state.manifest_hash != start.manifest_hash:
            raise RuntimeError(f"manifest hash mismatch at day {state.day}")
        last_day = state.day
        last_hash = actual_hash
    return VerificationResult(
        verified_through_day=last_day,
        state_hash=last_hash,
        records=len(records),
    )


def rederive_run(store: WorldStore) -> VerificationResult:
    """Run the world again from its first day with the recorded councils, calling no model.

    Every day's state must hash the same as the journal's: the recorded replies, the seed
    and the configuration alone reproduce the run.
    """
    records = store.read_records()
    councils = recorded_councils(store)
    days = [int(record.payload["day"]) for record in records if record.type == "transition"]
    # A forked run begins at its fork, not at day zero.
    state = store.load_checkpoint(at_or_before=min(days, default=1) - 1)
    sovereigns: dict[EntityId, Sovereign] = {}
    for civilization_id in sorted({council.civilization_id for council in councils}):
        own = [council for council in councils if council.civilization_id == civilization_id]
        sovereigns[civilization_id] = RecordedSovereign(
            own, crisis_councils=any(council.crisis_councils for council in own)
        )
    rng = StableRng(state.config.seed)
    for record in records:
        if record.type != "transition":
            continue
        state = advance_day(state, rng, sovereigns=sovereigns).state
        if (
            int(record.payload["day"]) != state.day
            or store.state_hash(state) != record.payload["state_hash"]
        ):
            raise RuntimeError(f"rederived state differs from the journal at day {state.day}")
    return VerificationResult(
        verified_through_day=state.day,
        state_hash=store.state_hash(state),
        records=len(records),
    )
