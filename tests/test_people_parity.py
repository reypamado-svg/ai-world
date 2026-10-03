"""The people store changes nothing: recorded worlds replay, load and save byte for byte."""

import gzip
import json

import pytest
from parity import SAVED_DAYS, SCENARIOS, Scenario, hashes_path, run, state_path

from sovereign_world.state import WorldState, state_hash


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda item: item.name)
def test_every_day_hashes_as_recorded(scenario: Scenario) -> None:
    expected = json.loads(hashes_path(scenario).read_text())
    actual = [state_hash(state) for state in run(scenario)]
    assert actual == expected


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda item: item.name)
@pytest.mark.parametrize("day", SAVED_DAYS)
def test_saved_worlds_load_and_save_byte_for_byte(scenario: Scenario, day: int) -> None:
    raw = gzip.decompress(state_path(scenario, day).read_bytes())
    state = WorldState.model_validate_json(raw)
    assert state.model_dump_json().encode() == raw
    expected = json.loads(hashes_path(scenario).read_text())
    assert state_hash(state) == expected[day]
