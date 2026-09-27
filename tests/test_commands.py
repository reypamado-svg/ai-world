from sovereign_world.capabilities import CapabilityId
from sovereign_world.commands import (
    CommandEnvelope,
    Decree,
    DecreeKind,
    DirectOrder,
    DirectOrderKind,
    build_council_report,
    validate_envelope,
)
from sovereign_world.config import RunManifest, WorldConfig
from sovereign_world.diplomacy import Contact, DiplomaticMessage, MissionStatus
from sovereign_world.hexmap import HexCoord
from sovereign_world.ids import EntityId
from sovereign_world.state import build_initial_state


def _state():
    config = WorldConfig(seed=21, width=48, height=48)
    return build_initial_state(RunManifest.new(config=config, engine_version="0.1.0"))


def test_validation_accepts_valid_command_and_rejects_unknown_person() -> None:
    state = _state()
    civilization_id = sorted(state.civilizations)[0]
    envelope = CommandEnvelope(
        schema_version=1,
        civilization_id=civilization_id,
        council_day=0,
        correlation_id="report:0",
        commands=(
            Decree(
                command_id="decree:1",
                kind=DecreeKind.FOOD_RESERVE_TARGET,
                value=90,
                priority=100,
                duration_days=60,
            ),
            DirectOrder(
                command_id="order:1",
                kind=DirectOrderKind.ASSIGN_WORK,
                worker_ids=(EntityId("person:9999999999"),),
                priority=50,
            ),
        ),
    )

    result = validate_envelope(envelope, state)

    assert [command.command_id for command in result.accepted] == ["decree:1"]
    assert [error.code for error in result.errors] == ["unknown_person"]
    assert state.active_decrees == {}


def test_validation_rejects_cross_civilization_person() -> None:
    state = _state()
    civilization_ids = sorted(state.civilizations)
    foreign_person = state.civilizations[civilization_ids[1]].population.living_ids[0]
    envelope = CommandEnvelope(
        schema_version=1,
        civilization_id=civilization_ids[0],
        council_day=0,
        correlation_id="report:0",
        commands=(
            DirectOrder(
                command_id="order:1",
                kind=DirectOrderKind.ASSIGN_WORK,
                worker_ids=(foreign_person,),
            ),
        ),
    )

    result = validate_envelope(envelope, state)

    assert result.accepted == ()
    assert result.errors[0].code == "foreign_person"


def test_foreign_teacher_order_is_rejected() -> None:
    state = _state()
    civilization_ids = sorted(state.civilizations)
    foreign_teacher = state.civilizations[civilization_ids[1]].population.living_ids[0]
    apprentice = state.civilizations[civilization_ids[0]].population.living_ids[0]
    envelope = CommandEnvelope(
        schema_version=1,
        civilization_id=civilization_ids[0],
        council_day=0,
        correlation_id="report:0",
        commands=(
            DirectOrder(
                command_id="order:teach:foreign",
                kind=DirectOrderKind.START_TEACHING,
                assignment_id=EntityId("teaching:foreign"),
                teacher_id=foreign_teacher,
                apprentice_id=apprentice,
                capability=CapabilityId.CULTIVATION,
            ),
        ),
    )

    result = validate_envelope(envelope, state)

    assert result.accepted == ()
    assert result.errors[0].code == "foreign_person"


def test_dead_teacher_and_duplicate_assignment_are_rejected() -> None:
    state = _state()
    civilization_id = sorted(state.civilizations)[0]
    people = state.civilizations[civilization_id].population.people
    teacher, apprentice = sorted(people)[:2]
    people[teacher].alive = False
    order = DirectOrder(
        command_id="order:teach:dead",
        kind=DirectOrderKind.START_TEACHING,
        assignment_id=EntityId("teaching:duplicate"),
        teacher_id=teacher,
        apprentice_id=apprentice,
        capability=CapabilityId.CULTIVATION,
    )
    duplicate = order.model_copy(update={"command_id": "order:teach:duplicate"})
    envelope = CommandEnvelope(
        schema_version=1,
        civilization_id=civilization_id,
        council_day=0,
        correlation_id="report:0",
        commands=(order, duplicate),
    )

    result = validate_envelope(envelope, state)

    assert [error.code for error in result.errors] == ["dead_person", "duplicate_assignment"]


def test_council_report_contains_only_civilizations_private_knowledge() -> None:
    state = _state()
    civilization_ids = sorted(state.civilizations)
    own_id, foreign_id = civilization_ids[0], civilization_ids[1]
    report = build_council_report(state, own_id)

    assert set(report.person_ids) == set(state.civilizations[own_id].population.people)
    assert set(report.person_ids).isdisjoint(state.civilizations[foreign_id].population.people)
    assert set(report.known_tiles) == set(state.civilizations[own_id].known_tiles)
    assert len(report.known_tiles) < len(state.world_map.tiles)


def test_message_requires_contact_and_a_known_route() -> None:
    state = _state()
    sender, recipient = sorted(state.civilizations)[:2]
    civilization = state.civilizations[sender]
    ambassador = civilization.population.living_ids[0]
    origin = civilization.population.people[ambassador].location
    destination = state.world_map.neighbors(origin)[0]
    order = DirectOrder(
        command_id="message:without-contact",
        kind=DirectOrderKind.SEND_MESSAGE,
        message_id=EntityId("message:1"),
        ambassador_id=ambassador,
        recipient_civilization_id=recipient,
        message_text="We seek peace.",
        route=(origin, destination),
    )
    envelope = CommandEnvelope(
        schema_version=1,
        civilization_id=sender,
        council_day=0,
        correlation_id="report:0",
        commands=(order,),
    )

    rejected = validate_envelope(envelope, state)
    assert rejected.errors[0].code == "unknown_contact"

    civilization.contacts = (
        Contact(
            civilization_id=recipient,
            settlement=destination,
            first_contact_day=0,
            last_seen_day=0,
        ),
    )
    state.civilizations[recipient].start_center = destination

    accepted = validate_envelope(envelope, state)
    assert accepted.accepted == (order,)


def test_report_shows_only_received_messages() -> None:
    state = _state()
    sender, recipient = sorted(state.civilizations)[:2]
    recipient_state = state.civilizations[recipient]
    ambassador = state.civilizations[sender].population.living_ids[0]
    message = DiplomaticMessage(
        message_id=EntityId("message:received"),
        sender_civilization_id=sender,
        recipient_civilization_id=recipient,
        ambassador_id=ambassador,
        route=(HexCoord(0, 0),),
        source_text="Peace.",
        departed_day=0,
        status=MissionStatus.DELIVERED,
        delivered_day=1,
        delivered_text="Peace.",
    )
    recipient_state.received_messages = (message,)

    report = build_council_report(state, recipient)
    assert report.received_messages == (message,)
    assert build_council_report(state, sender).received_messages == ()

