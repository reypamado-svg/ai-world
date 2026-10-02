"""Houses: every five people need one (rules version 2).

A settlement's houses give it room for its people. Women conceive only at a settlement
with a house for everyone already there and food in its store, so a people grows by
building. Huts need only timber; houses and stone houses need the craft to raise them.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, field_validator

from sovereign_world.capabilities import CapabilityId, CapabilityRecord
from sovereign_world.hexmap import HexCoord
from sovereign_world.ids import EntityId
from sovereign_world.resources import Resource
from sovereign_world.stores import store_id_at

if TYPE_CHECKING:
    from sovereign_world.state import CivilizationState, WorldState

HOUSEHOLD = 5
"""People one house holds: a household, as in early towns."""


class HouseGrade(StrEnum):
    HUT = "hut"
    HOUSE = "house"
    STONE_HOUSE = "stone_house"


@dataclass(frozen=True, slots=True)
class HouseSpec:
    materials: dict[Resource, int]
    person_days: int
    needs: CapabilityId | None


HOUSE_GRADES: dict[HouseGrade, HouseSpec] = {
    HouseGrade.HUT: HouseSpec({Resource.TIMBER: 10}, 5, None),
    HouseGrade.HOUSE: HouseSpec(
        {Resource.TIMBER: 20, Resource.STONE: 10}, 10, CapabilityId.TIMBERCRAFT
    ),
    HouseGrade.STONE_HOUSE: HouseSpec(
        {Resource.TIMBER: 10, Resource.STONE: 30}, 15, CapabilityId.STONEWORKING
    ),
}
GRADE_ORDER: tuple[HouseGrade, ...] = (HouseGrade.HUT, HouseGrade.HOUSE, HouseGrade.STONE_HOUSE)


class Housing(BaseModel):
    """One settlement's houses, by grade."""

    model_config = ConfigDict(frozen=True)

    houses: dict[HouseGrade, int] = Field(default_factory=dict)
    empty_since: int | None = Field(default=None, ge=0)
    """The day the settlement was last left with no one at home."""

    @field_validator("houses")
    @classmethod
    def _positive(cls, houses: dict[HouseGrade, int]) -> dict[HouseGrade, int]:
        if any(count <= 0 for count in houses.values()):
            raise ValueError("house counts are positive; a grade with none is left out")
        return houses

    @property
    def count(self) -> int:
        return sum(self.houses.values())

    @property
    def slots(self) -> int:
        return self.count * HOUSEHOLD

    def plus(self, grade: HouseGrade, count: int) -> Housing:
        houses = dict(self.houses)
        houses[grade] = houses.get(grade, 0) + count
        return self.model_copy(update={"houses": _ordered(houses)})

    def minus(self, count: int) -> tuple[Housing, int]:
        """Lose up to `count` houses, the meanest first; return what is left and how many fell."""
        houses = dict(self.houses)
        lost = 0
        for grade in GRADE_ORDER:
            while lost < count and houses.get(grade, 0) > 0:
                houses[grade] -= 1
                lost += 1
            if houses.get(grade) == 0:
                houses.pop(grade)
        return self.model_copy(update={"houses": _ordered(houses)}), lost


def _ordered(houses: dict[HouseGrade, int]) -> dict[HouseGrade, int]:
    return {grade: houses[grade] for grade in GRADE_ORDER if houses.get(grade)}


def founding_housing(people: int) -> Housing:
    """The huts founders or settlers raise as they arrive: one for every five, rounded up."""
    return Housing(houses={HouseGrade.HUT: max(1, -(-people // HOUSEHOLD))})


def best_grade(capabilities: tuple[CapabilityRecord, ...]) -> HouseGrade:
    """The best house the civilization knows how to build."""
    known = {record.capability for record in capabilities}
    for grade in reversed(GRADE_ORDER):
        needs = HOUSE_GRADES[grade].needs
        if needs is None or needs in known:
            return grade
    return HouseGrade.HUT


def slots_of(civilization: CivilizationState, settlement_id: EntityId) -> int:
    housing = civilization.housing.get(settlement_id)
    return 0 if housing is None else housing.slots


def residents_by_settlement(
    state: WorldState, civilization_id: EntityId
) -> dict[EntityId, list[EntityId]]:
    """Who each settlement feeds and houses: its people at home and on its fields, and the
    captives held there. People on the road are counted nowhere."""
    civilization = state.civilizations[civilization_id]
    if not civilization.settlements:
        return {}
    away = {
        person_id
        for journey in state.journeys
        if journey.active and journey.sender_civilization_id == civilization_id
        for person_id in journey.traveller_ids
    }
    residents: dict[EntityId, list[EntityId]] = {}
    store_of: dict[HexCoord, EntityId | None] = {}
    people = civilization.population.people
    for person_id in civilization.population.living_ids:
        person = people[person_id]
        if person_id in away or person.captive_of is not None:
            continue
        location = person.location
        if location not in store_of:
            store_of[location] = store_id_at(civilization, location)
        store_id = store_of[location]
        assert store_id is not None
        residents.setdefault(store_id, []).append(person_id)
    own_settlements = {item.settlement_id for item in civilization.settlements}
    held = sorted(
        (person_id, person.held_at)
        for other_id, other in state.civilizations.items()
        if other_id != civilization_id
        for person_id, person in other.population.people.items()
        if person.alive
        and person.captive_of == civilization_id
        and person.held_at in own_settlements
    )
    for person_id, held_at in held:
        assert held_at is not None
        residents.setdefault(held_at, []).append(person_id)
    return residents
