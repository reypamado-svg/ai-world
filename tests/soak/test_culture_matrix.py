"""Seeded blended societies: newcomers assimilate month by month, keep their ancestry for
life, and every run replays exactly."""

import pytest
from logistics_helpers import treaty_world

from sovereign_world.culture import ancestry, culture
from sovereign_world.engine import _change_allegiance, advance_day
from sovereign_world.ids import EntityId
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState, state_hash

DAYS = 150


def _prepare(seed: int) -> tuple[WorldState, EntityId, EntityId, tuple[EntityId, ...]]:
    state, home, rival, _ = treaty_world(seed=seed, distance=3 + seed % 3)
    people = state.civilizations[rival].population
    moving = tuple(
        person_id
        for person_id in people.living_ids
        if not any(birth.parent_ids[0] == person_id for birth in people.scheduled_births)
    )[: 4 + seed % 4]
    _change_allegiance(state, moving, rival, home, "release")
    newcomers = state.civilizations[home].population.people
    for index, person_id in enumerate(moving):
        newcomers[person_id].assimilation = 40 + 10 * index
        if index % 2:
            newcomers[person_id].languages = {home: 60}
    return state, home, rival, moving


def _simulate(initial: WorldState):
    state = initial.model_copy(deep=True)
    rng = StableRng(state.config.seed)
    kinds: list[str] = []
    for _ in range(DAYS):
        transition = advance_day(state, rng)
        state = transition.state
        kinds.extend(event.kind for event in transition.events.events)
    return state, kinds


@pytest.mark.soak
@pytest.mark.parametrize("seed", range(6))
def test_seeded_newcomers_assimilate_and_replay_exactly(seed: int) -> None:
    initial, home, rival, moving = _prepare(seed)

    final, kinds = _simulate(initial)
    rerun, rerun_kinds = _simulate(initial)

    assert "person_assimilated" in kinds
    people = final.civilizations[home].population.people
    for person_id in moving:
        person = people[person_id]
        assert ancestry(person) == (rival,), "ancestry is kept for life"
        assert culture(person) in {home, rival}
        assert person.culture is None or 0 <= person.assimilation < 100
    for person in people.values():
        if person.birth_day > 0 and set(person.parent_ids) & set(moving):
            assert culture(person) == home and rival in ancestry(person)
    assert state_hash(rerun) == state_hash(final)
    assert rerun_kinds == kinds
