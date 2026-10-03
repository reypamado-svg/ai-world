"""Individual people, kinship, founders, births, aging, and mortality."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from sovereign_world.ids import EntityId, IdAllocator
from sovereign_world.people_store import AllegianceChange as AllegianceChange
from sovereign_world.people_store import PeopleView as PeopleView
from sovereign_world.people_store import Person as Person
from sovereign_world.people_store import PersonRecord as PersonRecord
from sovereign_world.people_store import Sex as Sex
from sovereign_world.worldgen import StartingRegion

# Unfed days the body's reserves absorb before hunger adds any risk of death.
HUNGER_GRACE_DAYS = 10
CHILDHOOD_TONGUE = 50
"""A child grows up speaking a newcomer mother's language this well."""
# Body condition is lost on unfed days about three times faster than it returns on fed days.
UNFED_HEALTH_LOSS_BP = 100
FED_HEALTH_GAIN_BP = 35
# Below this body condition a person cannot conceive.
FERTILE_HEALTH_BP = 8_000


SETTLING_DAYS = 365
"""A person who changes civilization has a quarter of each skill held back this long."""


class ScheduledBirth(BaseModel):
    model_config = ConfigDict(frozen=True)

    due_day: int
    parent_ids: tuple[EntityId, EntityId]


class Population(BaseModel):
    civilization_id: EntityId
    people: PeopleView
    """Everyone this civilization has had, alive or dead, kept in columns."""
    scheduled_births: tuple[ScheduledBirth, ...] = ()
    next_sequence: int = Field(default=1, ge=1)

    @property
    def living_ids(self) -> tuple[EntityId, ...]:
        return self.people.living_ids()

    @property
    def dead_ids(self) -> tuple[EntityId, ...]:
        return self.people.dead_ids()


FoundingPopulation = Population


class CopyOnRead(dict[EntityId, Person]):
    """People that are copied only when looked up by id, the first time.

    A step that changes a few people (travellers, ambassadors, apprentices) works on
    copies of just those, instead of copying everyone; the people passed in are never
    changed. Look people up by key (`[]`, `get`) before changing them: iterating values
    yields the shared originals, for reading only. Turn the result back into a plain
    dict with `dict(...)`, which keeps every copy made and the original order.
    """

    __slots__ = ("_copied",)

    def __init__(self, people: Mapping[EntityId, Person]) -> None:
        super().__init__(people)
        self._copied: set[EntityId] = set()

    def __getitem__(self, person_id: EntityId) -> Person:
        person = super().__getitem__(person_id)
        if person_id not in self._copied:
            person = person.model_copy(deep=True)
            super().__setitem__(person_id, person)
            self._copied.add(person_id)
        return person

    def get(self, person_id: EntityId, default: Person | None = None) -> Person | None:  # type: ignore[override]
        if person_id not in self:
            return default
        return self[person_id]


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
    def people(self) -> PeopleView:
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
        people=PeopleView.of(people),
        next_sequence=allocator.next_sequence,
    )


def go_hungry(person: Person) -> None:
    """An unfed day: acute hunger builds and the body wastes."""
    person.nutrition_debt += 1
    person.health_bp = max(0, person.health_bp - UNFED_HEALTH_LOSS_BP)


def recover(person: Person) -> None:
    """A fed day, applied after that day's death roll.

    Acute hunger halves, so its danger fades within days of eating again, while body
    condition rebuilds slowly over months.
    """
    person.nutrition_debt //= 2
    person.health_bp = min(10_000, person.health_bp + FED_HEALTH_GAIN_BP)


def birth_person_id(civilization_id: EntityId, sequence: int) -> EntityId:
    """Scope birth IDs by birth civilization so person IDs stay globally unique."""
    return EntityId(f"person:{civilization_id.rsplit(':', 1)[-1]}-{sequence:010d}")


def _mortality_threshold(person: Person) -> tuple[int, str]:
    if person.health_bp <= 0:
        return 1_000_000, "critical health"
    nutrition = min(700_000, max(0, person.nutrition_debt - HUNGER_GRACE_DAYS) * 1_000)
    disease = min(700_000, person.disease_load * 50)
    years = person.age_days // 365
    natural = max(0, years - 65) ** 2 * 40
    threshold = min(999_999, nutrition + disease + natural)
    if disease >= nutrition and disease >= natural and disease:
        return threshold, "disease"
    if nutrition >= natural and nutrition:
        return threshold, "malnutrition"
    return threshold, "natural causes"


def _eligible_pairs(people: Mapping[EntityId, Person]) -> list[tuple[Person, Person]]:
    females = [
        person
        for person in people.values()
        if person.alive
        and person.sex is Sex.FEMALE
        and 18 * 365 <= person.age_days <= 42 * 365
        and person.health_bp >= FERTILE_HEALTH_BP
    ]
    males = [
        person
        for person in people.values()
        if person.alive
        and person.sex is Sex.MALE
        and 18 * 365 <= person.age_days <= 60 * 365
        and person.health_bp >= FERTILE_HEALTH_BP
    ]
    pairs: list[tuple[Person, Person]] = []
    males_by_id = sorted(males, key=lambda person: person.person_id)
    for female in sorted(females, key=lambda person: person.person_id):
        for male in males_by_id:
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
    in_place: bool = False,
    eligible_mothers: frozenset[EntityId] | None = None,
) -> PopulationDayResult:
    """Births, aging, deaths and conceptions for one day.

    With `in_place` the population itself is updated (the engine passes its own
    working copy); otherwise a copy is, and the population passed in is left alone.

    Under houses (rules version 2) the engine decides room and food settlement by
    settlement and passes `eligible_mothers`: only these women may conceive, and
    `food_days` and `shelter_slots` are not consulted.
    """
    candidate = population if in_place else population.model_copy(deep=True)
    births: list[BirthRecord] = []
    deaths: list[DeathRecord] = []
    pending: list[ScheduledBirth] = []

    for scheduled in candidate.scheduled_births:
        if scheduled.due_day != day:
            pending.append(scheduled)
            continue
        mother = candidate.people[scheduled.parent_ids[0]]
        if not mother.alive:
            continue
        person_id = birth_person_id(candidate.civilization_id, candidate.next_sequence)
        candidate.next_sequence += 1
        parents = [
            parent
            for parent_id in scheduled.parent_ids
            if (parent := candidate.people.get(parent_id)) is not None
        ]
        lineage = tuple(
            sorted(
                {
                    item
                    for parent in parents
                    for item in (
                        parent.ancestry or (parent.native_language or parent.civilization_id,)
                    )
                }
            )
        )
        tongue = mother.native_language or mother.civilization_id
        child = Person(
            person_id=person_id,
            civilization_id=candidate.civilization_id,
            sex=Sex.FEMALE if int(rng.integers(0, 2)) == 0 else Sex.MALE,
            birth_day=day,
            age_days=0,
            location=mother.location,
            parent_ids=scheduled.parent_ids,
            health_bp=9_000,
            # Children take the culture of the civilization they are born into, keep their
            # forebears' ancestry, and grow up speaking a newcomer mother's tongue too.
            ancestry=() if lineage == (candidate.civilization_id,) else lineage,
            languages=({tongue: CHILDHOOD_TONGUE} if tongue != candidate.civilization_id else {}),
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

    if eligible_mothers is None:
        conceiving = (
            day % 30 == 0 and food_days >= 90 and shelter_slots >= len(candidate.living_ids)
        )
    else:
        conceiving = day % 30 == 0 and bool(eligible_mothers)
    if conceiving:
        already_expectant = {birth.parent_ids[0] for birth in candidate.scheduled_births}
        for female, male in _eligible_pairs(candidate.people):
            if female.person_id in already_expectant:
                continue
            if eligible_mothers is not None and female.person_id not in eligible_mothers:
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
