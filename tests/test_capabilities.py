from sovereign_world.capabilities import (
    CapabilityId,
    CapabilityRecord,
    KnowledgeState,
    TeachingAssignment,
    advance_knowledge_day,
)
from sovereign_world.hexmap import HexCoord
from sovereign_world.ids import EntityId
from sovereign_world.people import Person, Sex


def _person(sequence: int, *, alive: bool = True, skills: dict[str, int] | None = None) -> Person:
    return Person(
        person_id=EntityId(f"person:{sequence:010d}"),
        civilization_id=EntityId("civilization:0000000001"),
        sex=Sex.FEMALE,
        birth_day=-9_000,
        age_days=9_000,
        location=HexCoord(1, 1),
        skills=skills or {},
        alive=alive,
        death_day=None if alive else 4,
    )


def test_teaching_awards_surveying_only_when_required_days_complete() -> None:
    teacher = _person(1, skills={CapabilityId.SURVEYING.value: 500})
    apprentice = _person(2)
    knowledge = KnowledgeState(
        records=(
            CapabilityRecord(
                capability=CapabilityId.SURVEYING,
                practitioner_ids=(teacher.person_id,),
                discovered_day=0,
            ),
        ),
        assignments=(
            TeachingAssignment(
                assignment_id=EntityId("teaching:0000000001"),
                teacher_id=teacher.person_id,
                apprentice_id=apprentice.person_id,
                capability=CapabilityId.SURVEYING,
                started_day=0,
                required_days=2,
            ),
        ),
    )

    first = advance_knowledge_day(knowledge, {teacher.person_id: teacher, apprentice.person_id: apprentice}, 1)
    second = advance_knowledge_day(first.knowledge, {teacher.person_id: teacher, apprentice.person_id: apprentice}, 2)

    assert first.learned == ()
    assert second.learned == (CapabilityId.SURVEYING,)
    assert second.people[apprentice.person_id].skills[CapabilityId.SURVEYING.value] == 100


def test_retained_record_prevents_last_practitioner_capability_loss() -> None:
    deceased = _person(1, alive=False, skills={CapabilityId.WRITING.value: 500})
    knowledge = KnowledgeState(
        records=(
            CapabilityRecord(
                capability=CapabilityId.WRITING,
                practitioner_ids=(deceased.person_id,),
                retained_record=True,
                discovered_day=0,
            ),
        )
    )

    result = advance_knowledge_day(knowledge, {deceased.person_id: deceased}, 1)

    assert result.forgotten == ()
    assert result.knowledge.records[0].capability is CapabilityId.WRITING
