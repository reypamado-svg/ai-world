"""Snapshot version 2: a whole world with its people in columns, read back exactly."""

from __future__ import annotations

import gzip
import json
from base64 import b64encode
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from parity import SAVED_DAYS, SCENARIOS, Scenario, hashes_path, hashes_v2_path, state_path
from property.test_people_store import _apply, operation
from test_journal_deltas import _cession_world, _manifest

from sovereign_world.ids import EntityId
from sovereign_world.journal import encode_snapshot, load_state
from sovereign_world.people_store import PeopleTable, PeopleView, PersonRecord
from sovereign_world.persistence import WorldStore
from sovereign_world.replay import replay_run, verify_run
from sovereign_world.state import WorldState, state_hash, state_hash_v2


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda item: item.name)
@pytest.mark.parametrize("day", SAVED_DAYS)
def test_a_saved_world_comes_back_byte_for_byte(scenario: Scenario, day: int) -> None:
    raw = gzip.decompress(state_path(scenario, day).read_bytes())
    state = WorldState.model_validate_json(raw)
    snapshot = encode_snapshot(state)
    assert snapshot.startswith(b'{"snapshot_version":2')
    again = load_state(snapshot)
    assert again.model_dump_json().encode() == raw
    assert state_hash(again) == json.loads(hashes_path(scenario).read_text())[day]
    assert state_hash_v2(again, fresh=True) == json.loads(hashes_v2_path(scenario).read_text())[day]
    # The old encoding still loads.
    assert load_state(raw).model_dump_json().encode() == raw


def test_captives_allegiances_and_ceded_people_come_back() -> None:
    state, step = _cession_world()
    for _ in range(150):
        state = step(state).state
    moved = [
        person
        for civilization in state.civilizations.values()
        for person in civilization.population.people.values()
        if person.allegiances
    ]
    assert moved, "the cession moved people"
    again = load_state(encode_snapshot(state))
    assert again.model_dump_json() == state.model_dump_json()


@settings(max_examples=150, deadline=None)
@given(st.lists(operation, max_size=40))
def test_people_columns_make_the_same_table(ops) -> None:
    view = PeopleView()
    oracle: dict[EntityId, PersonRecord] = {}
    for op in ops:
        if op[0] != "copy":
            _apply(view, oracle, op)
    columns = json.loads(json.dumps(view.table.columns()))
    table = PeopleTable.from_columns(columns)
    assert table.dump("json") == view.table.dump("json")
    assert table.dump("python") == view.table.dump("python")
    assert table.living_ids() == view.table.living_ids()
    assert list(table.loc_code[: table.size]) == list(view.table.loc_code[view.table.rows()])


def test_a_run_saved_with_old_snapshots_still_replays(tmp_path: Path) -> None:
    """Runs saved in S5 hold whole-world JSON in their snapshots and checkpoints."""
    state, step = _cession_world()
    store = WorldStore.create(tmp_path, _manifest(state), state)
    for day in range(1, 35):
        transition = step(state)
        state = transition.state
        if day in (5, 30):
            # Written as S5 wrote a snapshot.
            raw = state.model_dump_json().encode()
            store.append_record(
                "transition",
                {
                    "day": state.day,
                    "state_gzip_base64": b64encode(gzip.compress(raw, mtime=0)).decode(),
                    "state_hash": state_hash_v2(state),
                    "events": transition.events.canonical_json(),
                },
            )
            store._last = None
        else:
            store.append_transition(state, transition.events, previous=None)
    assert state_hash(replay_run(WorldStore(tmp_path))) == state_hash(state)
    assert verify_run(WorldStore(tmp_path)).verified_through_day == 34
