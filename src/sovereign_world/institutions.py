"""Institutions: lasting practices housed in a building at a settlement and kept by staff.

An institution is founded by the people who will keep it. They raise its building first,
then stay on as its staff: they eat, but do no other work. While its building stands and
at least one of its staff is there, it has its effect.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sovereign_world.capabilities import CapabilityId
from sovereign_world.hexmap import HexCoord
from sovereign_world.ids import EntityId
from sovereign_world.resources import Resource

if TYPE_CHECKING:
    from sovereign_world.state import CivilizationState

MAX_STAFF = 4
SCHOOL_TEACHING_DAYS = 15
"""Teaching at a school takes half the usual thirty days."""
SCHOOL_APPRENTICES = 2
"""A teacher at a school may take two apprentices at once; elsewhere, one."""
HEALING_FACTOR = 2
"""People at a settlement with a healers' house recover twice as fast."""
WORKSHOP_DAY = 4
"""Every fourth day, each worker at a workshop's settlement puts in an extra day: 25% faster."""
SERVICE_FIDELITY = 20
"""A diplomatic service briefs its envoys: messages keep this much more of their words."""


class InstitutionKind(StrEnum):
    ARCHIVE = "archive"
    SCHOOL = "school"
    HEALERS_HOUSE = "healers_house"
    WORKSHOP = "workshop"
    DIPLOMATIC_SERVICE = "diplomatic_service"


@dataclass(frozen=True, slots=True)
class InstitutionSpec:
    needs: frozenset[CapabilityId]
    """The civilization must know at least one of these."""
    materials: dict[Resource, int]
    person_days: int


INSTITUTIONS: dict[InstitutionKind, InstitutionSpec] = {
    InstitutionKind.ARCHIVE: InstitutionSpec(
        frozenset({CapabilityId.WRITING}), {Resource.TIMBER: 30, Resource.STONE: 30}, 20
    ),
    InstitutionKind.SCHOOL: InstitutionSpec(
        frozenset({CapabilityId.WRITING}), {Resource.TIMBER: 40}, 20
    ),
    InstitutionKind.HEALERS_HOUSE: InstitutionSpec(
        frozenset({CapabilityId.HERBAL_CARE}), {Resource.TIMBER: 30}, 15
    ),
    InstitutionKind.WORKSHOP: InstitutionSpec(
        frozenset({CapabilityId.TIMBERCRAFT, CapabilityId.STONEWORKING}),
        {Resource.TIMBER: 30, Resource.STONE: 20},
        20,
    ),
    InstitutionKind.DIPLOMATIC_SERVICE: InstitutionSpec(
        frozenset({CapabilityId.WRITING}), {Resource.TIMBER: 30}, 15
    ),
}


class Institution(BaseModel):
    """One institution at one settlement: its building, then its staff."""

    model_config = ConfigDict(frozen=True)

    institution_id: EntityId
    kind: InstitutionKind
    settlement_id: EntityId
    tile: HexCoord
    staff_ids: tuple[EntityId, ...] = ()
    founded_day: int = Field(ge=0)
    person_days_done: int = Field(default=0, ge=0)
    opened_day: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def valid_staff(self) -> Institution:
        if self.staff_ids != tuple(sorted(set(self.staff_ids))):
            raise ValueError("staff are unique and sorted")
        if len(self.staff_ids) > MAX_STAFF:
            raise ValueError(f"an institution keeps at most {MAX_STAFF} staff")
        if (self.opened_day is not None) != self.built:
            raise ValueError("an institution opens the day its building is finished")
        return self

    @property
    def built(self) -> bool:
        return self.person_days_done >= INSTITUTIONS[self.kind].person_days


def serving_tiles(
    civilization: CivilizationState, kind: InstitutionKind, away: set[EntityId]
) -> set[HexCoord]:
    """Where an institution of this kind is open: built, with one of its staff there."""
    people = civilization.population.people
    return {
        institution.tile
        for institution in civilization.institutions
        if institution.kind is kind
        and institution.built
        and any(
            (person := people.get(person_id)) is not None
            and person.alive
            and person.captive_of is None
            and person.location == institution.tile
            and person_id not in away
            for person_id in institution.staff_ids
        )
    }


def staff_of(civilization: CivilizationState) -> set[EntityId]:
    """Everyone building or keeping an institution: they do no other work."""
    return {
        person_id
        for institution in civilization.institutions
        for person_id in institution.staff_ids
    }
