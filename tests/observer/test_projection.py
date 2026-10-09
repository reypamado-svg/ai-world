"""The observer's projection of a day counts people exactly as the engine does (O2)."""

from __future__ import annotations

import numpy as np
from perf.synthetic import grown_world
from test_council_report import _mixed_world

from sovereign_world.commands import _duties
from sovereign_world.engine import advance_day
from sovereign_world.housing import residents_by_settlement
from sovereign_world.observer.projection import AWAY, DUTIES, DUTY, project_day
from sovereign_world.rng import StableRng
from sovereign_world.scripted import BaselineSovereign
from sovereign_world.state import WorldState


def _baseline_days(state: WorldState, days: int) -> WorldState:
    sovereigns = {civilization_id: BaselineSovereign() for civilization_id in state.civilizations}
    rng = StableRng(state.config.seed)
    for _ in range(days):
        state = advance_day(state, rng, sovereigns=sovereigns).state
    return state


def test_residents_and_travellers_add_up_as_the_engine_counts() -> None:
    state = _mixed_world()
    view = project_day(state)
    people = view.people
    living = sum(
        len(civilization.population.living_ids) for civilization in state.civilizations.values()
    )
    assert len(people) == living
    assert len(set(people.ids)) == living
    index = 0
    for civilization_id in sorted(state.civilizations):
        residents = residents_by_settlement(state, civilization_id)
        for item in state.civilizations[civilization_id].settlements:
            row = view.settlements[index]
            assert row.settlement_id == item.settlement_id
            assert row.residents == len(residents.get(item.settlement_id, []))
            assert np.count_nonzero(people.settlement == index) == row.residents
            at = [people.ids[k] for k in np.flatnonzero(people.settlement == index).tolist()]
            assert sorted(at) == sorted(residents.get(item.settlement_id, []))
            index += 1
    counts = view.counts()
    assert counts["at_home"] + counts["away"] == living
    assert sum(n for *_, n in view.travellers) == counts["away"]
    assert set(people.duty.tolist()) <= set(range(len(DUTIES)))


def test_the_same_day_projects_the_same_and_marks_nobody_changed() -> None:
    state = _mixed_world()
    tables = {
        key: civilization.population.people.table
        for key, civilization in state.civilizations.items()
    }
    before = {key: table.objs_dirty.copy() for key, table in tables.items()}
    first = project_day(state)
    second = project_day(state)
    for name in ("civilization", "settlement", "sex", "age", "health", "duty", "q", "r"):
        assert np.array_equal(getattr(first.people, name), getattr(second.people, name)), name
    assert first.people.ids == second.people.ids
    assert first.settlements == second.settlements and first.owners == second.owners
    for key, table in tables.items():
        assert np.array_equal(table.objs_dirty, before[key])


def test_duties_follow_the_councils_orders() -> None:
    state = _baseline_days(grown_world(2_000, size=24, generator=3, rules=2), 3)
    view = project_day(state)
    position = {person_id: k for k, person_id in enumerate(view.people.ids)}
    seen: set[str] = set()
    for civilization_id in sorted(state.civilizations):
        civilization = state.civilizations[civilization_id]
        hall_staff = {
            person_id
            for institution in civilization.institutions
            if institution.kind.value == "hall"
            for person_id in institution.staff_ids
        }
        for person_id, label in _duties(state, civilization_id).items():
            if person_id not in position:
                continue
            duty = DUTIES[view.people.duty[position[person_id]]]
            seen.add(duty)
            if person_id in hall_staff:
                assert duty == "scholar", label
            if label.startswith("builder:"):
                assert duty == "builder", label
    assert seen, "the baseline gives people duties"
    idle = [
        k
        for k, person_id in enumerate(view.people.ids)
        if not any(person_id in _duties(state, c) for c in state.civilizations)
    ][:500]
    for k in idle:
        age = int(view.people.age[k])
        expected = "child" if age < 16 else "elder" if age >= 65 else "farmer"
        assert DUTIES[view.people.duty[k]] == expected
    assert DUTY["farmer"] in view.people.duty.tolist()


def test_housing_and_ranks_are_carried_under_rules_two() -> None:
    state = _baseline_days(grown_world(2_000, size=24, generator=3, rules=2), 2)
    view = project_day(state)
    capitals = [row for row in view.settlements if row.capital]
    assert capitals and all(row.houses for row in capitals)
    for row in capitals:
        assert row.slots == 5 * sum(row.houses.values())
        assert set(row.houses) <= {"hut", "house", "stone_house"}
        assert row.rank in {"village", "small_town", "town", "big_town", "city"}
    assert all(0 <= civ < len(view.civilizations) for *_, civ in view.owners)
    assert AWAY == 0xFFFF
