"""Deterministic civilization knowledge, teaching, and skill loss."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sovereign_world.ids import EntityId
from sovereign_world.people import CopyOnRead, Person


class CapabilityId(StrEnum):
    CULTIVATION = "cultivation"
    IRRIGATION = "irrigation"
    FISHING = "fishing"
    TIMBERCRAFT = "timbercraft"
    STONEWORKING = "stoneworking"
    METALLURGY_AWARENESS = "metallurgy_awareness"
    NAVIGATION = "navigation"
    HERBAL_CARE = "herbal_care"
    WRITING = "writing"
    SURVEYING = "surveying"
    ORGANIZED_LOGISTICS = "organized_logistics"
    ARCHERY = "archery"
    BRONZE_WORKING = "bronze_working"
    SIEGECRAFT = "siegecraft"
    SPEAR_FORMATIONS = "spear_formations"
    DRILL_DOCTRINE = "drill_doctrine"
    MILITARY_LOGISTICS = "military_logistics"
    FORTIFICATION = "fortification"


REGIONAL_CAPABILITY_BY_STRENGTH: dict[str, CapabilityId] = {
    "fertile soil": CapabilityId.CULTIVATION,
    "timber": CapabilityId.TIMBERCRAFT,
    "stone": CapabilityId.STONEWORKING,
    "ore": CapabilityId.METALLURGY_AWARENESS,
}


def regional_capability(strength: str) -> CapabilityId:
    """Return the fixed initial capability represented by a start's strength."""
    try:
        return REGIONAL_CAPABILITY_BY_STRENGTH[strength]
    except KeyError as error:
        raise ValueError(f"unknown regional strength: {strength}") from error


class CapabilityRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    capability: CapabilityId
    practitioner_ids: tuple[EntityId, ...] = ()
    retained_record: bool = False
    discovered_day: int = Field(ge=0)

    @model_validator(mode="after")
    def practitioner_ids_are_unique_and_sorted(self) -> CapabilityRecord:
        if self.practitioner_ids != tuple(sorted(set(self.practitioner_ids))):
            raise ValueError("capability practitioners must be unique and sorted")
        return self


class TeachingAssignment(BaseModel):
    model_config = ConfigDict(frozen=True)

    assignment_id: EntityId
    teacher_id: EntityId
    apprentice_id: EntityId
    capability: CapabilityId
    started_day: int = Field(ge=0)
    required_days: int = Field(default=30, ge=1)


class KnowledgeState(BaseModel):
    model_config = ConfigDict(frozen=True)

    records: tuple[CapabilityRecord, ...] = ()
    assignments: tuple[TeachingAssignment, ...] = ()

    @model_validator(mode="after")
    def entries_are_unique_and_sorted(self) -> KnowledgeState:
        if self.records != tuple(sorted(self.records, key=lambda record: record.capability.value)):
            raise ValueError("capability records must be sorted")
        if len({record.capability for record in self.records}) != len(self.records):
            raise ValueError("capability records must be unique")
        sorted_assignments = tuple(
            sorted(self.assignments, key=lambda assignment: assignment.assignment_id)
        )
        if self.assignments != sorted_assignments:
            raise ValueError("teaching assignments must be sorted")
        assignment_ids = {assignment.assignment_id for assignment in self.assignments}
        if len(assignment_ids) != len(self.assignments):
            raise ValueError("teaching assignments must be unique")
        return self

    @property
    def known_capabilities(self) -> tuple[CapabilityId, ...]:
        return tuple(record.capability for record in self.records)


@dataclass(frozen=True, slots=True)
class KnowledgeDayResult:
    knowledge: KnowledgeState
    people: dict[EntityId, Person]
    learned: tuple[CapabilityId, ...]
    forgotten: tuple[CapabilityId, ...]


def advance_knowledge_day(
    knowledge: KnowledgeState,
    people: dict[EntityId, Person],
    day: int,
) -> KnowledgeDayResult:
    """Advance teaching and remove unrecorded capabilities with no living practitioner."""
    updated_people = CopyOnRead(people)
    records = {record.capability: record for record in knowledge.records}
    forgotten: list[CapabilityId] = []
    for capability, record in tuple(records.items()):
        living_practitioners = tuple(
            person_id
            for person_id in record.practitioner_ids
            if updated_people.get(person_id) is not None and updated_people[person_id].alive
        )
        if not living_practitioners and not record.retained_record:
            records.pop(capability)
            forgotten.append(capability)
        elif living_practitioners != record.practitioner_ids:
            records[capability] = record.model_copy(
                update={"practitioner_ids": living_practitioners}
            )

    learned: list[CapabilityId] = []
    remaining_assignments: list[TeachingAssignment] = []
    for assignment in knowledge.assignments:
        teacher = updated_people.get(assignment.teacher_id)
        apprentice = updated_people.get(assignment.apprentice_id)
        if teacher is None or apprentice is None or not teacher.alive or not apprentice.alive:
            continue
        if teacher.skills.get(assignment.capability.value, 0) <= 0:
            continue
        if day - assignment.started_day < assignment.required_days:
            remaining_assignments.append(assignment)
            continue
        apprentice.skills[assignment.capability.value] = max(
            100,
            apprentice.skills.get(assignment.capability.value, 0),
        )
        existing = records.get(assignment.capability)
        practitioner_ids = {assignment.teacher_id, assignment.apprentice_id}
        if existing is not None:
            practitioner_ids.update(existing.practitioner_ids)
            records[assignment.capability] = existing.model_copy(
                update={"practitioner_ids": tuple(sorted(practitioner_ids))}
            )
        else:
            records[assignment.capability] = CapabilityRecord(
                capability=assignment.capability,
                practitioner_ids=tuple(sorted(practitioner_ids)),
                discovered_day=day,
            )
        learned.append(assignment.capability)

    return KnowledgeDayResult(
        knowledge=KnowledgeState(
            records=tuple(sorted(records.values(), key=lambda record: record.capability.value)),
            assignments=tuple(
                sorted(remaining_assignments, key=lambda assignment: assignment.assignment_id)
            ),
        ),
        people=dict(updated_people),
        learned=tuple(learned),
        forgotten=tuple(sorted(forgotten, key=lambda capability: capability.value)),
    )
