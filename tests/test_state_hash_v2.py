"""Hash version 2: built from parts, cached from day to day, and still a function of the state."""

from sovereign_world.config import RunManifest, WorldConfig
from sovereign_world.engine import advance_day
from sovereign_world.hexmap import HexCoord
from sovereign_world.resources import Resource
from sovereign_world.rng import StableRng
from sovereign_world.state import (
    WorldState,
    build_initial_state,
    hash_parts,
    state_hash,
    state_hash_v2,
)


def _state() -> WorldState:
    state = build_initial_state(RunManifest.new(WorldConfig(seed=9, width=24, height=24), "0.1.0"))
    return advance_day(state, StableRng(9)).state


def test_the_map_hash_is_worked_out_once_and_unchanged() -> None:
    state = _state()
    first = state.world_map.content_hash()
    assert first == state.world_map._compute_hash()
    assert state.world_map.content_hash() is first


def test_any_change_anywhere_changes_hash_v2() -> None:
    state = _state()
    before = state_hash_v2(state)
    home = sorted(state.civilizations)[0]
    person_id = state.civilizations[home].population.living_ids[0]

    def changed(edit) -> str:
        copy = state.model_copy(deep=True)
        edit(copy)
        cached = state_hash_v2(copy)
        assert cached == state_hash_v2(copy, fresh=True)
        assert state_hash(copy) != state_hash(state)
        return cached

    edits = (
        lambda s: setattr(s.civilizations[home].population.people[person_id], "health_bp", 1),
        lambda s: setattr(
            s.civilizations[home].population.people[person_id], "location", HexCoord(1, 1)
        ),
        lambda s: s.civilizations[home].population.people[person_id].skills.update(lore=3),
        lambda s: s.civilizations[home].population.people[person_id].languages.update(x=3),
        lambda s: setattr(
            s.civilizations[home],
            "inventory",
            s.civilizations[home].inventory.model_copy(
                update={
                    "quantities": {**s.civilizations[home].inventory.quantities, Resource.ORE: 7}
                }
            ),
        ),
        lambda s: setattr(s, "day", s.day + 1),
    )
    seen = {before}
    for edit in edits:
        after = changed(edit)
        assert after not in seen
        seen.add(after)
    assert state_hash_v2(state) == before, "the original is untouched by its copies"


def test_hash_v2_survives_saving_and_loading() -> None:
    state = _state()
    again = WorldState.model_validate_json(state.model_dump_json())
    assert state_hash_v2(again) == state_hash_v2(state)
    parts = hash_parts(state)
    assert parts["version"] == 2 and set(parts["civilizations"]) == set(state.civilizations)
