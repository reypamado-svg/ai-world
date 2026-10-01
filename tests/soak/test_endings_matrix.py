"""Seeded ends: a homeless civilization drifts into its neighbour until it is eliminated,
leaving ruins, and the last civilization standing is recorded."""

import pytest
from logistics_helpers import treaty_world
from scenario_helpers import Welcoming

from sovereign_world.endings import EndingKind
from sovereign_world.engine import advance_day
from sovereign_world.ids import EntityId
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState, state_hash

DAYS = 330


def _prepare(seed: int) -> tuple[WorldState, EntityId, EntityId]:
    state, home, rival, route = treaty_world(seed=seed, distance=3 + seed % 3)
    # Every other civilization has already died out.
    for civilization_id, civilization in state.civilizations.items():
        if civilization_id in {home, rival}:
            continue
        for person in civilization.population.people.values():
            person.alive, person.death_day, person.health_bp = False, 0, 0
        civilization.population = civilization.population.model_copy(
            update={"scheduled_births": ()}
        )
    # The rival is down to a few people, all out in the fields and away from home.
    rival_people = state.civilizations[rival].population.people
    survivors = sorted(rival_people)[: 4 + seed % 3]
    field = route[-2]
    for person_id, person in rival_people.items():
        if person_id in survivors:
            person.location = field
        else:
            person.alive, person.death_day, person.health_bp = False, 0, 0
    state.civilizations[rival].population = state.civilizations[rival].population.model_copy(
        update={"scheduled_births": ()}
    )
    return state, home, rival


def _simulate(initial: WorldState, home: EntityId, rival: EntityId):
    state = initial.model_copy(deep=True)
    sovereigns = {home: Welcoming()}
    rng = StableRng(state.config.seed)
    kinds: list[str] = []
    for _ in range(DAYS):
        transition = advance_day(state, rng, sovereigns=sovereigns)
        state = transition.state
        kinds.extend(event.kind for event in transition.events.events)
        people = [
            person_id
            for civilization in state.civilizations.values()
            for person_id in civilization.population.people
        ]
        assert len(people) == len(set(people)), "a person belongs to one civilization"
    return state, kinds


@pytest.mark.soak
@pytest.mark.parametrize("seed", range(6))
def test_seeded_breakups_end_in_ruins_and_a_recorded_last_civilization(seed: int) -> None:
    initial, home, rival = _prepare(seed)

    final, kinds = _simulate(initial, home, rival)
    rerun, rerun_kinds = _simulate(initial, home, rival)

    assert "civilization_homeless" in kinds and "people_drifted" in kinds
    assert final.civilizations[rival].eliminated_day is not None
    assert any(ruin.former_civilization_id == rival for ruin in final.ruins)
    last = [ending for ending in final.endings if ending.kind is EndingKind.LAST_CIVILIZATION]
    assert [ending.survivor_id for ending in last] == [home]
    admitted_ids = {
        person_id
        for person_id, person in final.civilizations[home].population.people.items()
        if any(change.from_civilization_id == rival for change in person.allegiances)
    }
    assert admitted_ids, "the rival's people found a new home"
    assert state_hash(rerun) == state_hash(final)
    assert rerun_kinds == kinds
