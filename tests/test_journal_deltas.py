"""Journal format 2 saves the changes between days, and every day rebuilds exactly."""

from __future__ import annotations

import gzip
import json
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from logistics_helpers import treaty_world
from parity import (
    SAVED_DAYS,
    SCENARIOS,
    Scenario,
    hashes_path,
    hashes_v2_path,
    initial,
    state_path,
)
from property.test_people_store import IDS, _apply, operation
from scenario_helpers import Vanquished, Victor

from sovereign_world.config import RunManifest
from sovereign_world.engine import TransitionResult, advance_day
from sovereign_world.gateway.records import journal_councils
from sovereign_world.gateway.sovereign import RecordingSovereign
from sovereign_world.hexmap import HexCoord, Terrain
from sovereign_world.ids import EntityId
from sovereign_world.journal import (
    SNAPSHOT_INTERVAL,
    _people_delta,
    apply_people_delta,
    compress,
    decompress,
    people_delta,
)
from sovereign_world.people_store import PeopleView, PersonRecord, people_hash
from sovereign_world.persistence import JournalCorruption, WorldStore, _record_hash
from sovereign_world.replay import recorded_states, rederive_run, replay_run, verify_run
from sovereign_world.resources import Inventory, Resource
from sovereign_world.rng import StableRng
from sovereign_world.scripted import BaselineSovereign
from sovereign_world.state import WorldState, state_hash, state_hash_v2
from sovereign_world.territory import Settlement

Step = Callable[[WorldState], TransitionResult]


def _manifest(state: WorldState) -> RunManifest:
    return RunManifest.model_validate(
        {
            "run_id": state.run_id,
            "config": state.config,
            "engine_version": "0.1.0",
            "rules_version": state.rules_version,
            "journal_format": 2,
        }
    )


def _baseline(state: WorldState) -> Step:
    sovereigns = {civilization_id: BaselineSovereign() for civilization_id in state.civilizations}
    rng = StableRng(state.config.seed)
    return lambda current: advance_day(current, rng, sovereigns=sovereigns)


def _record(root: Path, start: WorldState, step: Step, days: int) -> list[str]:
    """Save `days` days in a new format-2 store; returns each day's hash v1, day 0 first."""
    store = WorldStore.create(root, _manifest(start), start)
    hashes = [state_hash(start)]
    state = start
    for _ in range(days):
        transition = step(state)
        state = transition.state
        store.append_transition(state, transition.events)
        hashes.append(state_hash(state))
    return hashes


def _kinds(store: WorldStore) -> dict[int, str]:
    """Each saved day: a whole world (`snapshot`) or its changes (`delta`)."""
    return {
        int(record.payload["day"]): "delta" if "delta_gzip_base64" in record.payload else "snapshot"
        for record in store.read_records()
        if record.type == "transition"
    }


def _rebuilt(store: WorldStore) -> list[WorldState]:
    start = store.load_checkpoint(at_or_before=0)
    return [state for _, state in recorded_states(store, store.read_records(), start=start)]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda item: item.name)
def test_every_day_rebuilds_as_recorded(tmp_path: Path, scenario: Scenario) -> None:
    start = initial(scenario)
    expected = json.loads(hashes_path(scenario).read_text())
    expected_v2 = json.loads(hashes_v2_path(scenario).read_text())
    days = len(expected) - 1
    assert _record(tmp_path, start, _baseline(start), days) == expected
    store = WorldStore(tmp_path)
    kinds = _kinds(store)
    assert [day for day, kind in kinds.items() if kind == "snapshot"] == list(
        range(SNAPSHOT_INTERVAL, days + 1, SNAPSHOT_INTERVAL)
    )
    rebuilt = _rebuilt(store)
    assert [state.day for state in rebuilt] == list(range(1, days + 1))
    for state in rebuilt:
        assert state_hash(state) == expected[state.day], state.day
        assert state_hash_v2(state, fresh=True) == expected_v2[state.day], state.day
    # Byte for byte, on the days the parity fixtures keep whole.
    for day in SAVED_DAYS[1:]:
        saved = gzip.decompress(state_path(scenario, day).read_bytes())
        assert replay_run(store, target_day=day).model_dump_json().encode() == saved
    assert verify_run(store).verified_through_day == days


def _cession_world() -> tuple[WorldState, Step]:
    """A war that ends with the loser ceding a colony and its people (seed 0 of the soak)."""
    state, first, second, route = treaty_world(seed=0, distance=4)
    loser = state.civilizations[second]
    tile = next(
        coord
        for coord in sorted(
            HexCoord(route[-1].q + dq, route[-1].r + dr)
            for dq in range(-4, 5)
            for dr in range(-4, 5)
        )
        if coord.distance(route[-1]) == 3
        and coord not in route
        and state.world_map.contains(coord)
        and state.world_map.tile(coord).terrain is not Terrain.WATER
    )
    colony = Settlement(
        settlement_id=EntityId(f"settlement:{second.rsplit(':', 1)[-1]}-0002"),
        civilization_id=second,
        tile=tile,
        founded_day=0,
    )
    loser.settlements = (*loser.settlements, colony)
    for person_id in loser.population.living_ids[-4:]:
        loser.population.people[person_id].location = tile
    loser.stores = {
        colony.settlement_id: Inventory(capacity=2_000, quantities={Resource.FOOD: 400})
    }
    sovereigns = {
        first: Victor(second, route),
        second: Vanquished(first, tuple(reversed(route)), colony.settlement_id),
    }
    rng = StableRng(state.config.seed)
    return state, lambda current: advance_day(current, rng, sovereigns=sovereigns)


def test_war_and_a_ceded_colony_rebuild_day_for_day(tmp_path: Path) -> None:
    start, step = _cession_world()
    hashes = _record(tmp_path, start, step, 150)
    store = WorldStore(tmp_path)
    events = [
        event["kind"]
        for record in store.read_records()
        if record.type == "transition"
        for event in map(json.loads, record.payload["events"].splitlines())
    ]
    assert "peace_made" in events and "settlement_ceded" in events
    # The cession moved people between civilizations inside a day's changes.
    moved = [
        decompress(record.payload["delta_gzip_base64"])["people"]
        for record in store.read_records()
        if "delta_gzip_base64" in record.payload
    ]
    assert any(
        any("removed" in change for change in people.values())
        and any("added" in change for change in people.values())
        for people in moved
    )
    for state in _rebuilt(store):
        assert state_hash(state) == hashes[state.day], state.day
    assert verify_run(store).verified_through_day == 150


def test_snapshots_come_every_30_days_after_a_gap_and_on_a_fresh_store(tmp_path: Path) -> None:
    start = initial(SCENARIOS[1])
    step = _baseline(start)
    states = [start]
    events = []
    for _ in range(64):
        transition = step(states[-1])
        states.append(transition.state)
        events.append(transition.events)
    store = WorldStore.create(tmp_path, _manifest(start), start)
    for day in range(1, 35):
        store.append_transition(states[day], events[day - 1])
    # A store opened afresh has not the day before at hand: a snapshot, unless given it.
    reopened = WorldStore(tmp_path)
    reopened.append_transition(states[35], events[34])
    reopened.append_transition(states[36], events[35])
    again = WorldStore(tmp_path)
    again.append_transition(states[37], events[36], previous=states[36])
    # Day 39 follows no saved day 38: a snapshot.
    again.append_transition(states[39], events[38])
    for day in range(40, 65):
        again.append_transition(states[day], events[day - 1])
    kinds = _kinds(again)
    assert [day for day, kind in kinds.items() if kind == "snapshot"] == [30, 35, 39, 60]
    assert kinds[1] == kinds[36] == kinds[37] == kinds[40] == "delta"
    by_day = {state.day: state for state in _rebuilt(again)}
    for day, state in by_day.items():
        assert state_hash(state) == state_hash(states[day])
    assert state_hash(replay_run(again, target_day=59)) == state_hash(states[59])


def test_the_same_run_saves_the_same_bytes(tmp_path: Path) -> None:
    for name in ("one", "two"):
        start = initial(SCENARIOS[0])
        _record(tmp_path / name, start, _baseline(start), 40)
    first = (tmp_path / "one" / "journal.jsonl").read_bytes()
    assert first == (tmp_path / "two" / "journal.jsonl").read_bytes()
    assert b"delta_gzip_base64" in first


def _rewrite(store: WorldStore, edit: Callable[[list[dict]], list[dict]]) -> None:
    """Rewrite the journal's records, re-chaining them so only the content is wrong."""
    lines = [json.loads(line) for line in store.journal_path.read_text().splitlines()]
    previous = "0" * 64
    out = []
    for sequence, record in enumerate(edit(lines), start=1):
        record["sequence"] = sequence
        record["previous_hash"] = previous
        record["record_hash"] = previous = _record_hash(
            sequence, record["type"], record["payload"], previous
        )
        out.append(json.dumps(record))
    store.journal_path.write_text("\n".join(out) + "\n")


def _small_run(root: Path, days: int = 12) -> WorldStore:
    start = initial(SCENARIOS[1])
    _record(root, start, _baseline(start), days)
    return WorldStore(root)


def test_a_flipped_byte_in_a_saved_change_is_caught(tmp_path: Path) -> None:
    store = _small_run(tmp_path)
    raw = bytearray(store.journal_path.read_bytes())
    at = raw.index(b'"delta_gzip_base64":"') + len(b'"delta_gzip_base64":"') + 40
    raw[at] = ord("A") if raw[at] != ord("A") else ord("B")
    store.journal_path.write_bytes(bytes(raw))
    with pytest.raises(JournalCorruption, match="record hash mismatch"):
        WorldStore(tmp_path).read_records()


def test_a_tampered_change_fails_verification(tmp_path: Path) -> None:
    store = _small_run(tmp_path)

    def tamper(records: list[dict]) -> list[dict]:
        target = next(record for record in records if "delta_gzip_base64" in record["payload"])
        delta = decompress(target["payload"]["delta_gzip_base64"])
        delta["world"].setdefault("set", {})["day"] = 999
        target["payload"]["delta_gzip_base64"] = compress(delta)
        return records

    _rewrite(store, tamper)
    with pytest.raises((RuntimeError, ValueError)):
        verify_run(WorldStore(tmp_path))


def test_changes_with_no_day_before_them_are_refused(tmp_path: Path) -> None:
    store = _small_run(tmp_path)
    records = store.read_records()
    with pytest.raises(RuntimeError, match="no day before"):
        list(recorded_states(store, records))
    # Nor may a change skip a day.
    _rewrite(
        store,
        lambda lines: [
            line for line in lines if line["type"] != "transition" or line["payload"]["day"] != 3
        ],
    )
    with pytest.raises(RuntimeError, match="day before"):
        verify_run(WorldStore(tmp_path))


def test_a_cut_off_last_line_is_ignored_and_the_run_carries_on(tmp_path: Path) -> None:
    store = _small_run(tmp_path)
    raw = store.journal_path.read_bytes()
    store.journal_path.write_bytes(raw[: -len(raw.splitlines()[-1]) // 2])
    reopened = WorldStore(tmp_path)
    latest = replay_run(reopened)
    assert latest.day == 11
    start = initial(SCENARIOS[1])
    step = _baseline(start)
    state = start
    for _ in range(11):
        state = step(state).state
    transition = step(state)
    reopened.append_transition(transition.state, transition.events, previous=latest)
    assert _kinds(reopened)[12] == "delta"
    assert verify_run(WorldStore(tmp_path)).state_hash == state_hash_v2(transition.state)


def test_recorded_councils_rederive_a_format_two_run(tmp_path: Path) -> None:
    start = initial(SCENARIOS[1])
    store = WorldStore.create(tmp_path, _manifest(start), start)
    sovereigns = {
        civilization_id: RecordingSovereign(BaselineSovereign())
        for civilization_id in start.civilizations
    }
    rng = StableRng(start.config.seed)
    state = start
    for _ in range(45):
        transition = advance_day(state, rng, sovereigns=sovereigns)
        store.append_transition(transition.state, transition.events, previous=state)
        state = transition.state
        journal_councils(store, sovereigns.values())
    assert "delta" in _kinds(store).values()
    result = rederive_run(WorldStore(tmp_path))
    assert result.state_hash == state_hash_v2(state) and result.verified_through_day == 45
    assert verify_run(WorldStore(tmp_path)).state_hash == result.state_hash


@settings(max_examples=150, deadline=None)
@given(st.lists(operation, max_size=20), st.lists(operation, max_size=20))
def test_a_days_people_changes_rebuild_the_next_day(before_ops, after_ops) -> None:
    view = PeopleView()
    oracle: dict[EntityId, PersonRecord] = {}
    for op in before_ops:
        if op[0] != "copy":
            _apply(view, oracle, op)
    before = deepcopy(view)
    for op in after_ops:
        if op[0] != "copy":
            _apply(view, oracle, op)
    delta = people_delta(before.table, view.table)
    if delta is None:
        # Only when someone left and came back, or the order otherwise cannot be kept.
        return
    table = before.table.copy()
    apply_people_delta(table, json.loads(json.dumps(delta)))
    assert list(PeopleView(table)) == list(view)
    assert table.dump("json") == view.table.dump("json")
    assert people_hash(table, fresh=True) == people_hash(view.table, fresh=True)
    assert set(IDS) >= set(view)


@settings(max_examples=150, deadline=None)
@given(st.lists(operation, max_size=20), st.lists(operation, max_size=20))
def test_changes_between_copies_of_one_table_are_found_the_quick_way(before_ops, after_ops) -> None:
    view = PeopleView()
    oracle: dict[EntityId, PersonRecord] = {}
    for op in before_ops:
        if op[0] != "copy":
            _apply(view, oracle, op)
    # As the journal sees a day: its saved copy, and the next day's copy, changed.
    saved = view.table.copy()
    day = PeopleView(view.table.copy())
    for op in after_ops:
        if op[0] != "copy":
            _apply(day, oracle, op)
    quick = people_delta(saved, day.table)
    general = _people_delta(saved, day.table)
    # Where the general way can keep the order, both agree; where it cannot (someone left
    # and came back), the quick way still records the day, as their leaving and arrival.
    assert quick is not None
    if general is not None:
        assert quick == general
    table = saved.copy()
    apply_people_delta(table, json.loads(json.dumps(quick)))
    assert table.dump("json") == day.table.dump("json")
