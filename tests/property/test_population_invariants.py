from hypothesis import given, settings
from hypothesis import strategies as st

from sovereign_world.hexmap import HexCoord
from sovereign_world.ids import EntityId
from sovereign_world.people import Person, Population, Sex, advance_population_day
from sovereign_world.rng import StableRng


@given(
    nutrition_debt=st.integers(min_value=0, max_value=500),
    disease_load=st.integers(min_value=0, max_value=10_000),
    starting_age=st.integers(min_value=0, max_value=100 * 365),
)
@settings(max_examples=75)
def test_population_invariants_survive_daily_transitions(
    nutrition_debt: int,
    disease_load: int,
    starting_age: int,
) -> None:
    person = Person(
        person_id=EntityId("person:0000000001"),
        civilization_id=EntityId("civilization:0000000001"),
        sex=Sex.FEMALE,
        birth_day=-starting_age,
        age_days=starting_age,
        location=HexCoord(1, 1),
        nutrition_debt=nutrition_debt,
        disease_load=disease_load,
    )
    population = Population(
        civilization_id=person.civilization_id,
        people={person.person_id: person},
        next_sequence=2,
    )

    previous_age = starting_age
    for day in range(1, 6):
        result = advance_population_day(
            population,
            day=day,
            rng=StableRng(99).stream(f"day:{day}"),
        )
        population = result.population
        current = population.people[person.person_id]
        assert current.age_days >= previous_age
        assert len(population.people) == len(set(population.people))
        assert all(
            parent_id in population.people
            for candidate in population.people.values()
            for parent_id in candidate.parent_ids
        )
        assert len(population.people) == len(population.living_ids) + len(population.dead_ids)
        if not current.alive:
            assert current.death_day is not None
        previous_age = current.age_days
