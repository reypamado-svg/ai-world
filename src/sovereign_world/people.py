"""Individual people, kinship, founders, births, aging, and mortality."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from sovereign_world.hexmap import HexCoord
from sovereign_world.ids import EntityId, IdAllocator
from sovereign_world.worldgen import StartingRegion


class Sex(StrEnum):
    FEMALE = "female"
    MALE = "male"


class Person(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    person_id: EntityId
    civilization_id: EntityId
    sex: Sex
    birth_day: int
    age_days: int = Field(ge=0)
    location: HexCoord
    parent_ids: tuple[EntityId, ...] = ()
    health_bp: int = Field(default=10_000, ge=0, le=10_000)
    nutrition_debt: int = Field(default=0, ge=0)
    disease_load: int = Field(default=0, ge=0, le=10_000)
    skills: dict[str, int] = Field(default_factory=dict)
    alive: bool = True
    death_day: int | None = None


class ScheduledBirth(BaseModel):
    model_config = ConfigDict(frozen=True)

    due_day: int
    parent_ids: tuple[EntityId, EntityId]


class Population(BaseModel):
    civilization_id: EntityId
    people: dict[EntityId, Person]
    scheduled_births: tuple[ScheduledBirth, ...] = ()
    next_sequence: int = Field(default=1, ge=1)

    @property
    def living_ids(self) -> tuple[EntityId, ...]:
        return tuple(sorted(person_id for person_id, person in self.people.items() if person.alive))

    @property
    def dead_ids(self) -> tuple[EntityId, ...]:
        return tuple(
            sorted(person_id for person_id, person in self.people.items() if not person.alive)
        )


FoundingPopulation = Population


@dataclass(frozen=True, slots=True)
class BirthRecord:
    person_id: EntityId
    parent_ids: tuple[EntityId, EntityId]


@dataclass(frozen=True, slots=True)
class DeathRecord:
    person_id: EntityId
    cause: str


@dataclass(frozen=True, slots=True)
class PopulationDayResult:
    population: Population
    births: tuple[BirthRecord, ...]
    deaths: tuple[DeathRecord, ...]

    @property
    def people(self) -> dict[EntityId, Person]:
        return self.population.people


def create_founders(
    civilization_id: EntityId,
    start: StartingRegion,
    count: int,
    rng: np.random.Generator,
    allocator: IdAllocator,
) -> FoundingPopulation:
    if count < 2 or count % 2:
        raise ValueError("founder count must be an even number of at least two")
    sexes = [Sex.FEMALE] * (count // 2) + [Sex.MALE] * (count // 2)
    rng.shuffle(sexes)
    people: dict[EntityId, Person] = {}
    for sex in sexes:
        person_id = allocator.allocate()
        age_days = int(rng.integers(18 * 365, 45 * 365 + 1))
        regional_skill = int(rng.integers(300, 701))
        people[person_id] = Person(
            person_id=person_id,
            civilization_id=civilization_id,
            sex=sex,
            birth_day=-age_days,
            age_days=age_days,
            location=start.center,
            health_bp=int(rng.integers(8_000, 10_001)),
            skills={start.viability.strength: regional_skill, "survival": 400},
        )
    return Population(
        civilization_id=civilization_id,
        people=people,
        next_sequence=allocator.next_sequence,
    )


def birth_person_id(civilization_id: EntityId, sequence: int) -> EntityId:
    """Scope birth IDs by birth civilization so person IDs stay globally unique."""
    return EntityId(f"person:{civilization_id.rsplit(':', 1)[-1]}-{sequence:010d}")


def _mortality_threshold(person: Person) -> tuple[int, str]:
    if person.health_bp <= 0:
        return 1_000_000, "critical health"
    nutrition = min(700_000, person.nutrition_debt * 1_000)
    disease = min(700_000, person.disease_load * 50)
    years = person.age_days // 365
    natural = max(0, years - 65) ** 2 * 40
    threshold = min(999_999, nutrition + disease + natural)
    if disease >= nutrition and disease >= natural and disease:
        return threshold, "disease"
    if nutrition >= natural and nutrition:
        return threshold, "malnutrition"
    return threshold, "natural causes"


def _eligible_pairs(people: dict[EntityId, Person]) -> list[tuple[Person, Person]]:
    females = [
        person
        for person in people.values()
        if person.alive and person.sex is Sex.FEMALE and 18 * 365 <= person.age_days <= 42 * 365
    ]
    males = [
        person
        for person in people.values()
        if person.alive and person.sex is Sex.MALE and 18 * 365 <= person.age_days <= 60 * 365
    ]
    pairs: list[tuple[Person, Person]] = []
    for female in sorted(females, key=lambda person: person.person_id):
        for male in sorted(males, key=lambda person: person.person_id):
            if female.parent_ids and set(female.parent_ids) & set(male.parent_ids):
                continue
            pairs.append((female, male))
            break
    return pairs


def advance_population_day(
    population: Population,
    day: int,
    rng: np.random.Generator,
    *,
    food_days: int = 0,
    shelter_slots: int = 0,
) -> PopulationDayResult:
    candidate = population.model_copy(deep=True)
    births: list[BirthRecord] = []
    deaths: list[DeathRecord] = []
    pending: list[ScheduledBirth] = []

    for scheduled in candidate.scheduled_births:
        if scheduled.due_day != day:
            pending.append(scheduled)
            continue
        mother = candidate.people[scheduled.parent_ids[0]]
        person_id = birth_person_id(candidate.civilization_id, candidate.next_sequence)
        candidate.next_sequence += 1
        child = Person(
            person_id=person_id,
            civilization_id=candidate.civilization_id,
            sex=Sex.FEMALE if int(rng.integers(0, 2)) == 0 else Sex.MALE,
            birth_day=day,
            age_days=0,
            location=mother.location,
            parent_ids=scheduled.parent_ids,
            health_bp=9_000,
        )
        candidate.people[person_id] = child
        births.append(BirthRecord(person_id=person_id, parent_ids=scheduled.parent_ids))

    candidate.scheduled_births = tuple(pending)
    for person_id in candidate.living_ids:
        person = candidate.people[person_id]
        person.age_days += 1

    for person_id in candidate.living_ids:
        person = candidate.people[person_id]
        threshold, cause = _mortality_threshold(person)
        if int(rng.integers(0, 1_000_000)) < threshold:
            person.alive = False
            person.death_day = day
            deaths.append(DeathRecord(person_id=person_id, cause=cause))

    if day % 30 == 0 and food_days >= 90 and shelter_slots >= len(candidate.living_ids):
        already_expectant = {birth.parent_ids[0] for birth in candidate.scheduled_births}
        for female, male in _eligible_pairs(candidate.people):
            if female.person_id in already_expectant:
                continue
            if int(rng.integers(0, 1_000_000)) < 40_000:
                pending.append(
                    ScheduledBirth(
                        due_day=day + 280,
                        parent_ids=(female.person_id, male.person_id),
                    )
                )
    candidate.scheduled_births = tuple(sorted(pending, key=lambda birth: birth.due_day))
    return PopulationDayResult(
        population=candidate,
        births=tuple(births),
        deaths=tuple(deaths),
    )
