from sovereign_world.hexmap import HexCoord
from sovereign_world.ids import EntityId, IdAllocator
from sovereign_world.people import (
    Person,
    Population,
    ScheduledBirth,
    Sex,
    advance_population_day,
    create_founders,
)
from sovereign_world.rng import StableRng
from sovereign_world.worldgen import StartingRegion, StartViability


def _start() -> StartingRegion:
    return StartingRegion(
        civilization_index=0,
        center=HexCoord(8, 9),
        viability=StartViability(
            has_water=True,
            food_units_per_day=100,
            construction_units=100,
            strength="timber",
            vulnerability="ore",
            score=1_000,
        ),
    )


def test_create_founders_builds_balanced_deterministic_population() -> None:
    first = create_founders(
        civilization_id=EntityId("civilization:0000000001"),
        start=_start(),
        count=32,
        rng=StableRng(4).stream("founders"),
        allocator=IdAllocator("person"),
    )
    second = create_founders(
        civilization_id=EntityId("civilization:0000000001"),
        start=_start(),
        count=32,
        rng=StableRng(4).stream("founders"),
        allocator=IdAllocator("person"),
    )

    assert first == second
    assert len(first.people) == 32
    assert len(set(first.people)) == 32
    assert sum(person.sex is Sex.FEMALE for person in first.people.values()) == 16
    assert sum(person.sex is Sex.MALE for person in first.people.values()) == 16
    assert all(18 * 365 <= person.age_days <= 45 * 365 for person in first.people.values())
    assert all(person.parent_ids == () for person in first.people.values())
    assert all(person.location == HexCoord(8, 9) for person in first.people.values())
    assert all(person.skills["timber"] > 0 for person in first.people.values())


def test_birth_and_parent_death_preserve_kinship() -> None:
    civilization_id = EntityId("civilization:0000000001")
    first_parent = Person(
        person_id=EntityId("person:0000000001"),
        civilization_id=civilization_id,
        sex=Sex.FEMALE,
        birth_day=-9_000,
        age_days=9_000,
        location=HexCoord(2, 2),
        health_bp=0,
    )
    second_parent = Person(
        person_id=EntityId("person:0000000002"),
        civilization_id=civilization_id,
        sex=Sex.MALE,
        birth_day=-9_200,
        age_days=9_200,
        location=HexCoord(2, 2),
    )
    population = Population(
        civilization_id=civilization_id,
        people={first_parent.person_id: first_parent, second_parent.person_id: second_parent},
        scheduled_births=(
            ScheduledBirth(
                due_day=300,
                parent_ids=(first_parent.person_id, second_parent.person_id),
            ),
        ),
        next_sequence=3,
    )

    result = advance_population_day(
        population,
        day=300,
        rng=StableRng(7).stream("birth-death-test"),
    )

    child = result.people[result.births[0].person_id]
    assert child.parent_ids == result.births[0].parent_ids
    assert all(parent_id in result.people for parent_id in child.parent_ids)
    assert any(not result.people[parent_id].alive for parent_id in child.parent_ids)


def test_dead_people_remain_historical_records() -> None:
    person = Person(
        person_id=EntityId("person:0000000001"),
        civilization_id=EntityId("civilization:0000000001"),
        sex=Sex.FEMALE,
        birth_day=-20_000,
        age_days=20_000,
        location=HexCoord(0, 0),
        health_bp=0,
    )
    population = Population(
        civilization_id=person.civilization_id,
        people={person.person_id: person},
        next_sequence=2,
    )

    first = advance_population_day(population, day=1, rng=StableRng(1).stream("day:1"))
    second = advance_population_day(first.population, day=2, rng=StableRng(1).stream("day:2"))

    assert first.population.living_ids == ()
    assert second.people[person.person_id].death_day == 1
    assert not second.people[person.person_id].alive

