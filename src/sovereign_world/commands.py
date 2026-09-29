"""Versioned sovereign reports, commands, and semantic validation."""

from __future__ import annotations

from enum import StrEnum
from itertools import pairwise
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from sovereign_world.capabilities import CapabilityId
from sovereign_world.diplomacy import Contact, DiplomaticMessage, TreatyKind
from sovereign_world.events import DomainEvent
from sovereign_world.hexmap import HexCoord
from sovereign_world.ids import EntityId
from sovereign_world.resources import Resource
from sovereign_world.state import WorldState


class DecreeKind(StrEnum):
    FOOD_RESERVE_TARGET = "food_reserve_target"
    LABOR_PRIORITY = "labor_priority"
    SETTLEMENT_RADIUS = "settlement_radius"
    POPULATION_GROWTH_POLICY = "population_growth_policy"


class DirectOrderKind(StrEnum):
    ASSIGN_WORK = "assign_work"
    START_PROJECT = "start_project"
    CANCEL_PROJECT = "cancel_project"
    RELOCATE_GROUP = "relocate_group"
    REQUEST_SURVEY = "request_survey"
    START_TEACHING = "start_teaching"
    START_EXPEDITION = "start_expedition"
    SEND_MESSAGE = "send_message"
    OFFER_TREATY = "offer_treaty"
    ACCEPT_TREATY = "accept_treaty"


class ProjectKind(StrEnum):
    SHELTER = "shelter"
    STORAGE = "storage"


class Decree(BaseModel):
    model_config = ConfigDict(frozen=True)

    command_id: str
    kind: DecreeKind
    value: int
    priority: int = Field(default=50, ge=0, le=100)
    duration_days: int = Field(default=30, ge=1)


class DirectOrder(BaseModel):
    model_config = ConfigDict(frozen=True)

    command_id: str
    kind: DirectOrderKind
    worker_ids: tuple[EntityId, ...] = ()
    project_id: EntityId | None = None
    project_kind: ProjectKind | None = None
    assignment_id: EntityId | None = None
    teacher_id: EntityId | None = None
    apprentice_id: EntityId | None = None
    capability: CapabilityId | None = None
    expedition_id: EntityId | None = None
    explorer_ids: tuple[EntityId, ...] = ()
    route: tuple[HexCoord, ...] = ()
    message_id: EntityId | None = None
    ambassador_id: EntityId | None = None
    recipient_civilization_id: EntityId | None = None
    message_text: str = Field(default="", max_length=1_000)
    treaty_id: EntityId | None = None
    treaty_kind: TreatyKind | None = None
    priority: int = Field(default=50, ge=0, le=100)


Command = Annotated[Decree | DirectOrder, Field(union_mode="left_to_right")]


class CommandEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int = Field(ge=1, le=1)
    civilization_id: EntityId
    council_day: int = Field(ge=0)
    correlation_id: str
    commands: tuple[Command, ...] = Field(default=(), max_length=8)
    rationale: str = Field(default="", max_length=4_000)


class CommandError(BaseModel):
    model_config = ConfigDict(frozen=True)

    command_id: str | None
    code: str
    message: str


class CommandValidation(BaseModel):
    model_config = ConfigDict(frozen=True)

    accepted: tuple[Command, ...]
    errors: tuple[CommandError, ...]


class CouncilReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    report_id: str
    civilization_id: EntityId
    day: int
    person_ids: tuple[EntityId, ...]
    start_center: HexCoord
    known_tiles: tuple[HexCoord, ...]
    inventory: dict[Resource, int]
    project_ids: tuple[EntityId, ...]
    active_decrees: dict[str, int]
    contacts: tuple[Contact, ...] = ()
    received_messages: tuple[DiplomaticMessage, ...] = ()
    recent_events: tuple[DomainEvent, ...] = ()


def build_council_report(
    state: WorldState,
    civilization_id: EntityId,
    recent_events: tuple[DomainEvent, ...] = (),
) -> CouncilReport:
    civilization = state.civilizations[civilization_id]
    visible_events = tuple(
        event
        for event in recent_events
        if event.actor_id == str(civilization_id) or event.subject_id == str(civilization_id)
    )
    return CouncilReport(
        report_id=f"report:{state.day}:{civilization_id}",
        civilization_id=civilization_id,
        day=state.day,
        person_ids=tuple(sorted(civilization.population.people)),
        start_center=civilization.start_center,
        known_tiles=tuple(sorted(civilization.known_tiles)),
        inventory=dict(civilization.inventory.quantities),
        project_ids=tuple(sorted(civilization.projects)),
        active_decrees=dict(state.active_decrees.get(civilization_id, {})),
        contacts=civilization.contacts,
        received_messages=civilization.received_messages,
        recent_events=visible_events,
    )


def _person_owner(state: WorldState, person_id: EntityId) -> EntityId | None:
    for civilization_id in sorted(state.civilizations):
        if person_id in state.civilizations[civilization_id].population.people:
            return civilization_id
    return None


def validate_envelope(envelope: CommandEnvelope, state: WorldState) -> CommandValidation:
    if envelope.civilization_id not in state.civilizations:
        return CommandValidation(
            accepted=(),
            errors=(CommandError(command_id=None, code="unknown_civilization", message="unknown"),),
        )
    if envelope.council_day != state.day:
        return CommandValidation(
            accepted=(),
            errors=(CommandError(command_id=None, code="wrong_day", message="stale council day"),),
        )

    accepted: list[Command] = []
    errors: list[CommandError] = []
    seen: set[str] = set()
    seen_assignments: set[EntityId] = set()
    seen_expeditions: set[EntityId] = set()
    seen_messages: set[EntityId] = set()
    seen_treaties: set[EntityId] = set()
    for command in envelope.commands:
        if command.command_id in seen:
            errors.append(
                CommandError(
                    command_id=command.command_id,
                    code="duplicate_command",
                    message="command ID is repeated",
                )
            )
            continue
        seen.add(command.command_id)
        if isinstance(command, DirectOrder):
            command_error: CommandError | None = None
            if command.kind is DirectOrderKind.START_PROJECT and (
                command.project_id is None or command.project_kind is None
            ):
                command_error = CommandError(
                    command_id=command.command_id,
                    code="invalid_project",
                    message="start-project order requires project ID and kind",
                )
            if command.kind is DirectOrderKind.START_TEACHING:
                required = (
                    command.assignment_id,
                    command.teacher_id,
                    command.apprentice_id,
                    command.capability,
                )
                if any(value is None for value in required):
                    command_error = CommandError(
                        command_id=command.command_id,
                        code="invalid_teaching",
                        message=(
                            "start-teaching order requires assignment, teacher, apprentice, "
                            "and capability"
                        ),
                    )
                else:
                    assert command.assignment_id is not None
                    existing_assignments = state.civilizations[
                        envelope.civilization_id
                    ].teaching_assignments
                    is_duplicate = command.assignment_id in seen_assignments or any(
                        assignment.assignment_id == command.assignment_id
                        for assignment in existing_assignments
                    )
                    if is_duplicate:
                        command_error = CommandError(
                            command_id=command.command_id,
                            code="duplicate_assignment",
                            message="teaching assignment ID is repeated",
                        )
                    else:
                        seen_assignments.add(command.assignment_id)
            if command.kind is DirectOrderKind.START_EXPEDITION:
                if command.expedition_id is None or not command.explorer_ids or not command.route:
                    command_error = CommandError(
                        command_id=command.command_id,
                        code="invalid_expedition",
                        message="start-expedition order requires an ID, explorers, and route",
                    )
                elif command.expedition_id in seen_expeditions or any(
                    expedition.expedition_id == command.expedition_id
                    for expedition in state.civilizations[envelope.civilization_id].expeditions
                ):
                    command_error = CommandError(
                        command_id=command.command_id,
                        code="duplicate_expedition",
                        message="expedition ID is repeated",
                    )
                else:
                    seen_expeditions.add(command.expedition_id)
            if command.kind in {
                DirectOrderKind.SEND_MESSAGE,
                DirectOrderKind.OFFER_TREATY,
                DirectOrderKind.ACCEPT_TREATY,
            }:
                message_required = (
                    command.message_id,
                    command.ambassador_id,
                    command.recipient_civilization_id,
                )
                if (
                    any(value is None for value in message_required)
                    or not command.message_text
                    or not command.route
                ):
                    command_error = CommandError(
                        command_id=command.command_id,
                        code="invalid_message",
                        message=(
                            "message requires an ID, ambassador, recipient, text, and route"
                        ),
                    )
                else:
                    assert command.message_id is not None
                    duplicate = command.message_id in seen_messages or any(
                        message.message_id == command.message_id
                        for message in state.diplomatic_missions
                    )
                    if duplicate:
                        command_error = CommandError(
                            command_id=command.command_id,
                            code="duplicate_message",
                            message="message ID is repeated",
                        )
                    else:
                        seen_messages.add(command.message_id)
            if command.kind is DirectOrderKind.OFFER_TREATY:
                if command.treaty_id is None or command.treaty_kind is None:
                    command_error = CommandError(
                        command_id=command.command_id,
                        code="invalid_treaty",
                        message="treaty offer requires an ID and kind",
                    )
                elif command.treaty_id in seen_treaties or any(
                    offer.offer_id == command.treaty_id for offer in state.treaty_offers
                ):
                    command_error = CommandError(
                        command_id=command.command_id,
                        code="duplicate_treaty",
                        message="treaty ID is repeated",
                    )
                else:
                    seen_treaties.add(command.treaty_id)
            if command.kind is DirectOrderKind.ACCEPT_TREATY and command.treaty_id is None:
                command_error = CommandError(
                    command_id=command.command_id,
                    code="invalid_treaty",
                    message="treaty acceptance requires the offered treaty ID",
                )
            person_ids = command.worker_ids
            if command.kind is DirectOrderKind.START_TEACHING and command_error is None:
                assert command.teacher_id is not None
                assert command.apprentice_id is not None
                person_ids += (command.teacher_id, command.apprentice_id)
            if command.kind is DirectOrderKind.START_EXPEDITION and command_error is None:
                person_ids += command.explorer_ids
            if command.kind in {
                DirectOrderKind.SEND_MESSAGE,
                DirectOrderKind.OFFER_TREATY,
                DirectOrderKind.ACCEPT_TREATY,
            } and command_error is None:
                assert command.ambassador_id is not None
                person_ids += (command.ambassador_id,)
            for person_id in person_ids:
                if command_error is not None:
                    break
                owner = _person_owner(state, person_id)
                if owner is None:
                    command_error = CommandError(
                        command_id=command.command_id,
                        code="unknown_person",
                        message=f"unknown person {person_id}",
                    )
                    break
                if owner != envelope.civilization_id:
                    command_error = CommandError(
                        command_id=command.command_id,
                        code="foreign_person",
                        message=f"person {person_id} belongs to another civilization",
                    )
                    break
                if not state.civilizations[owner].population.people[person_id].alive:
                    command_error = CommandError(
                        command_id=command.command_id,
                        code="dead_person",
                        message=f"person {person_id} is dead",
                    )
                    break
            if command.kind is DirectOrderKind.START_TEACHING and command_error is None:
                assert command.teacher_id is not None
                assert command.capability is not None
                teacher = state.civilizations[envelope.civilization_id].population.people[
                    command.teacher_id
                ]
                if teacher.skills.get(command.capability.value, 0) <= 0:
                    command_error = CommandError(
                        command_id=command.command_id,
                        code="unqualified_teacher",
                        message="teacher does not possess the requested capability",
                    )
            if command.kind is DirectOrderKind.START_EXPEDITION and command_error is None:
                locations = {
                    state.civilizations[envelope.civilization_id]
                    .population.people[person_id]
                    .location
                    for person_id in command.explorer_ids
                }
                route = command.route
                if (
                    len(locations) != 1
                    or route[0] not in state.civilizations[envelope.civilization_id].known_tiles
                    or route[0] not in locations
                    or any(not state.world_map.contains(tile) for tile in route)
                    or any(first.distance(second) != 1 for first, second in pairwise(route))
                ):
                    command_error = CommandError(
                        command_id=command.command_id,
                        code="invalid_route",
                        message=(
                            "expedition route must start locally and use adjacent in-bounds tiles"
                        ),
                    )
            if command.kind in {
                DirectOrderKind.SEND_MESSAGE,
                DirectOrderKind.OFFER_TREATY,
                DirectOrderKind.ACCEPT_TREATY,
            } and command_error is None:
                assert command.ambassador_id is not None
                assert command.recipient_civilization_id is not None
                civilization = state.civilizations[envelope.civilization_id]
                contact = next(
                    (
                        item
                        for item in civilization.contacts
                        if item.civilization_id == command.recipient_civilization_id
                    ),
                    None,
                )
                ambassador = civilization.population.people[command.ambassador_id]
                in_transit = any(
                    message.ambassador_id == command.ambassador_id
                    and message.status.value == "in_transit"
                    for message in state.diplomatic_missions
                )
                if command.recipient_civilization_id not in state.civilizations:
                    command_error = CommandError(
                        command_id=command.command_id,
                        code="unknown_recipient",
                        message="recipient civilization is unknown",
                    )
                elif contact is None:
                    command_error = CommandError(
                        command_id=command.command_id,
                        code="unknown_contact",
                        message="messages require a physically discovered foreign settlement",
                    )
                elif in_transit:
                    command_error = CommandError(
                        command_id=command.command_id,
                        code="ambassador_unavailable",
                        message="ambassador already carries a message",
                    )
                elif (
                    command.route[0] != ambassador.location
                    or command.route[-1] != contact.settlement
                    or any(tile not in civilization.known_tiles for tile in command.route)
                    or any(not state.world_map.contains(tile) for tile in command.route)
                    or any(first.distance(second) != 1 for first, second in pairwise(command.route))
                ):
                    command_error = CommandError(
                        command_id=command.command_id,
                        code="invalid_route",
                        message=(
                            "message route must be known, adjacent, and end at the "
                            "discovered settlement"
                        ),
                    )
            if command.kind is DirectOrderKind.ACCEPT_TREATY and command_error is None:
                assert command.treaty_id is not None
                assert command.recipient_civilization_id is not None
                civilization = state.civilizations[envelope.civilization_id]
                offered = next(
                    (
                        message.treaty_offer
                        for message in civilization.received_messages
                        if message.treaty_offer is not None
                        and message.treaty_offer.offer_id == command.treaty_id
                    ),
                    None,
                )
                if offered is None:
                    command_error = CommandError(
                        command_id=command.command_id,
                        code="unknown_treaty",
                        message="only a delivered treaty offer may be accepted",
                    )
                elif (
                    offered.proposer_civilization_id != command.recipient_civilization_id
                    or offered.recipient_civilization_id != envelope.civilization_id
                ):
                    command_error = CommandError(
                        command_id=command.command_id,
                        code="invalid_treaty_party",
                        message="acceptance must return to the treaty proposer",
                    )
            if command_error is not None:
                errors.append(command_error)
                continue
        accepted.append(command)
    return CommandValidation(accepted=tuple(accepted), errors=tuple(errors))
