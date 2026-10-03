"""The population day's array steps (Phase 5 S6) give exactly what the per-person steps do."""

from __future__ import annotations

import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st

from sovereign_world.hexmap import HexCoord
from sovereign_world.ids import EntityId
from sovereign_world.people import (
    _eligible_pair_ids,
    _eligible_pairs,
    _mortality_threshold,
    _mortality_thresholds,
)
from sovereign_world.people_store import PeopleView, Person, Sex
from sovereign_world.rng import StableRng


def test_a_batch_of_draws_is_the_same_as_drawing_one_by_one() -> None:
    one = StableRng(5).stream("people")
    other = StableRng(5).stream("people")
    for size in (0, 1, 7, 10_000, 0, 3):
        batch = other.integers(0, 1_000_000, size=size).tolist()
        assert batch == [int(one.integers(0, 1_000_000)) for _ in range(size)]
    # Both streams carry on from the same place.
    assert int(one.integers(0, 1_000_000)) == int(other.integers(0, 1_000_000))


person = st.fixed_dictionaries(
    {
        "sex": st.sampled_from([Sex.FEMALE, Sex.MALE]),
        "age_days": st.integers(0, 110 * 365),
        "health_bp": st.integers(0, 10_000),
        "nutrition_debt": st.integers(0, 2_000),
        "disease_load": st.integers(0, 10_000),
        "alive": st.booleans(),
        "parents": st.sampled_from([(), (0, 1), (1, 2), (2, 3)]),
    }
)


def _view(people: list[dict]) -> PeopleView:
    view = PeopleView()
    # Ids out of order, so id order and row order differ.
    for number, values in reversed(list(enumerate(people))):
        person_id = EntityId(f"person:0000000001-{number:010d}")
        view[person_id] = Person(
            person_id=person_id,
            civilization_id=EntityId("civilization:0000000001"),
            sex=values["sex"],
            birth_day=-values["age_days"],
            age_days=values["age_days"],
            location=HexCoord(0, 0),
            parent_ids=tuple(EntityId(f"person:0000000009-{p:010d}") for p in values["parents"]),
            health_bp=values["health_bp"],
            nutrition_debt=values["nutrition_debt"],
            disease_load=values["disease_load"],
            alive=values["alive"],
        )
    return view


@settings(max_examples=200, deadline=None)
@given(st.lists(person, max_size=30))
def test_thresholds_and_couples_match_the_per_person_rules(people: list[dict]) -> None:
    view = _view(people)
    table = view.table
    rows = table.rows()
    expected = [_mortality_threshold(table.person(row))[0] for row in rows.tolist()]
    assert _mortality_thresholds(table, rows).tolist() == expected
    assert _eligible_pair_ids(table) == [
        (woman.person_id, man.person_id) for woman, man in _eligible_pairs(view)
    ]
    assert np.array_equal(table.living_rows(), table.ordered(table.rows()[table.alive[rows]]))
