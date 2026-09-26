from uuid import UUID

from sovereign_world.events import DomainEvent, EventBatch, EventPhase


def _event(phase: EventPhase, actor: str) -> DomainEvent:
    return DomainEvent(
        run_id=UUID("00000000-0000-0000-0000-000000000001"),
        day=4,
        phase=phase,
        sequence=0,
        kind="test",
        actor_id=actor,
        subject_id=None,
        payload={"value": 1},
    )


def test_event_batch_assigns_documented_stable_order() -> None:
    batch = EventBatch.assign_sequences(
        [_event(EventPhase.DEATH, "person:2"), _event(EventPhase.BIRTH, "person:1")]
    )

    assert [event.phase for event in batch.events] == [EventPhase.BIRTH, EventPhase.DEATH]
    assert [event.sequence for event in batch.events] == [1, 2]


def test_event_json_is_canonical_for_payload_insertion_order() -> None:
    first = _event(EventPhase.WORK, "person:1").model_copy(
        update={"payload": {"b": 2, "a": 1}}
    )
    second = _event(EventPhase.WORK, "person:1").model_copy(
        update={"payload": {"a": 1, "b": 2}}
    )

    assert first.canonical_json() == second.canonical_json()

