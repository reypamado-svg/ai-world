"""Atomic daily transition pipeline."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass

from sovereign_world.capabilities import (
    CapabilityId,
    CapabilityRecord,
    KnowledgeState,
    TeachingAssignment,
    advance_knowledge_day,
)
from sovereign_world.commands import (
    JOURNEY_ORDERS,
    MESSAGE_ORDERS,
    Decree,
    DirectOrder,
    DirectOrderKind,
    ProjectKind,
    build_council_report,
    journey_supplies,
    validate_envelope,
)
from sovereign_world.diplomacy import (
    ActiveTreaty,
    Contact,
    DiplomaticMessage,
    MissionStatus,
    TreatyEndKind,
    TreatyOffer,
    advance_diplomacy_day,
)
from sovereign_world.events import DomainEvent, EventBatch, EventPhase
from sovereign_world.exploration import Expedition, ExpeditionStatus, advance_expeditions
from sovereign_world.hexmap import HexCoord
from sovereign_world.ids import EntityId
from sovereign_world.logistics import (
    INTERNAL_KINDS,
    TRAVEL_HAZARD_CAUSE,
    Journey,
    JourneyKind,
    LogisticsNotice,
    NoticeKind,
    advance_journeys_day,
    notice,
)
from sovereign_world.people import advance_population_day, go_hungry, recover
from sovereign_world.resources import InventoryDelta, Resource
from sovereign_world.rng import StableRng
from sovereign_world.scripted import Sovereign
from sovereign_world.state import WorldState, validate_world
from sovereign_world.territory import (
    SETTLEMENT_SPACING,
    Claim,
    Garrison,
    Settlement,
    advance_territory,
)
from sovereign_world.work import ConstructionProject, WorkKind, WorkOrder, execute_work_day


@dataclass(frozen=True, slots=True)
class TransitionResult:
    state: WorldState
    events: EventBatch


def _event(
    state: WorldState,
    phase: EventPhase,
    kind: str,
    actor_id: str | None,
    subject_id: str | None = None,
    **payload: int | str | bool,
) -> DomainEvent:
    return DomainEvent(
        run_id=state.run_id,
        day=state.day,
        phase=phase,
        sequence=0,
        kind=kind,
        actor_id=actor_id,
        subject_id=subject_id,
        payload=payload,
    )


def _add_notice(state: WorldState, civilization_id: EntityId, item: LogisticsNotice) -> None:
    civilization = state.civilizations[civilization_id]
    civilization.logistics_notices = tuple(
        sorted((*civilization.logistics_notices, item), key=lambda entry: entry.notice_id)
    )


def _end_treaty(
    state: WorldState,
    treaty_id: EntityId,
    kind: TreatyEndKind,
    by: EntityId,
) -> ActiveTreaty | None:
    """End a treaty still in force; an already-ended treaty keeps its first ending."""
    treaty = next((item for item in state.active_treaties if item.treaty_id == treaty_id), None)
    if treaty is None or not treaty.in_force:
        return None
    ended = treaty.ended(state.day, kind, by)
    state.active_treaties = tuple(
        ended if item.treaty_id == treaty_id else item for item in state.active_treaties
    )
    return ended


def _store_provisions(state: WorldState, civilization_id: EntityId, units: int) -> int:
    """Put a party's leftover food into a storehouse; return what fitted."""
    if not units:
        return 0
    civilization = state.civilizations[civilization_id]
    civilization.inventory, waste = civilization.inventory.store_with_waste(
        {Resource.FOOD: units}
    )
    return units - waste.get(Resource.FOOD, 0)


FAILED_EVENT = {
    JourneyKind.SETTLEMENT: "founding_failed",
    JourneyKind.GARRISON: "garrison_failed",
}


def _arrival_allowed(state: WorldState, journey: Journey) -> bool:
    """Whether an internal party may found, garrison, or join at its destination today."""
    destination = journey.route[-1]
    civilization_id = journey.sender_civilization_id
    owner = state.territory.owner_of().get(destination)
    settlements = [
        settlement.tile
        for civilization in state.civilizations.values()
        for settlement in civilization.settlements
    ]
    if journey.kind is JourneyKind.SETTLEMENT:
        return owner in {None, civilization_id} and all(
            tile.distance(destination) >= SETTLEMENT_SPACING for tile in settlements
        )
    if journey.kind is JourneyKind.GARRISON:
        return owner in {None, civilization_id} and destination not in settlements
    return True


def _settle_arrival(state: WorldState, journey: Journey, provisions: int) -> list[DomainEvent]:
    """Found a settlement, station a garrison, or join a settlement at the party's destination."""
    civilization_id = journey.sender_civilization_id
    civilization = state.civilizations[civilization_id]
    destination = journey.route[-1]
    arrivals = tuple(
        person_id
        for person_id in journey.traveller_ids
        if person_id in civilization.population.people
        and civilization.population.people[person_id].alive
    )
    stored = _store_provisions(state, civilization_id, provisions)
    _add_notice(
        state,
        civilization_id,
        notice(
            state.day,
            NoticeKind.PARTY_ARRIVED,
            journey,
            civilization_id,
            cargo={Resource.FOOD: stored} if stored else None,
            person_ids=arrivals,
        ),
    )
    location = {"q": destination.q, "r": destination.r}
    if journey.kind is JourneyKind.SETTLEMENT:
        settlement = Settlement(
            settlement_id=EntityId(
                f"settlement:{civilization_id.rsplit(':', 1)[-1]}-"
                f"{len(civilization.settlements) + 1:04d}"
            ),
            civilization_id=civilization_id,
            tile=destination,
            founded_day=state.day,
        )
        civilization.settlements = tuple(
            sorted((*civilization.settlements, settlement), key=lambda item: item.settlement_id)
        )
        return [
            _event(
                state,
                EventPhase.PROJECT,
                "settlement_founded",
                str(civilization_id),
                str(settlement.settlement_id),
                settlers=len(arrivals),
                **location,
            )
        ]
    if journey.kind is JourneyKind.GARRISON:
        existing = next(
            (garrison for garrison in civilization.garrisons if garrison.tile == destination), None
        )
        if existing is not None:
            stationed = existing.model_copy(
                update={"member_ids": tuple(sorted({*existing.member_ids, *arrivals}))}
            )
        else:
            stationed = Garrison(
                garrison_id=EntityId(f"garrison:{journey.journey_id}"),
                civilization_id=civilization_id,
                tile=destination,
                member_ids=tuple(sorted(arrivals)),
                since_day=state.day,
            )
        civilization.garrisons = tuple(
            sorted(
                (
                    *(item for item in civilization.garrisons if item.tile != destination),
                    stationed,
                ),
                key=lambda item: item.garrison_id,
            )
        )
        return [
            _event(
                state,
                EventPhase.MOVEMENT,
                "garrison_stationed",
                str(civilization_id),
                str(stationed.garrison_id),
                members=len(stationed.member_ids),
                **location,
            )
        ]
    return [
        _event(
            state,
            EventPhase.MOVEMENT,
            "group_relocated",
            str(civilization_id),
            str(journey.journey_id),
            people=len(arrivals),
            **location,
        )
    ]


DISPATCH_EVENT = {
    JourneyKind.SETTLEMENT: "settlers_dispatched",
    JourneyKind.GARRISON: "garrison_dispatched",
    JourneyKind.RELOCATION: "relocation_dispatched",
}


def _leave_garrisons(
    state: WorldState, civilization_id: EntityId, leaving: frozenset[EntityId]
) -> list[DomainEvent]:
    """Remove people from their garrisons; a garrison left with nobody is disbanded."""
    civilization = state.civilizations[civilization_id]
    kept: list[Garrison] = []
    events: list[DomainEvent] = []
    for garrison in civilization.garrisons:
        members = tuple(person_id for person_id in garrison.member_ids if person_id not in leaving)
        if members:
            kept.append(garrison.model_copy(update={"member_ids": members}))
        else:
            events.append(
                _event(
                    state,
                    EventPhase.MOVEMENT,
                    "garrison_disbanded",
                    str(civilization_id),
                    str(garrison.garrison_id),
                )
            )
    civilization.garrisons = tuple(kept)
    return events


def _dispatch_journey(
    state: WorldState,
    civilization_id: EntityId,
    command: DirectOrder,
) -> list[DomainEvent]:
    """Start a validated journey; goods and packed food leave the sender's storehouse now."""
    assert command.journey_id is not None
    kind = JOURNEY_ORDERS[command.kind]
    internal = kind in INTERNAL_KINDS
    recipient_id = civilization_id if internal else command.recipient_civilization_id
    assert recipient_id is not None
    civilization = state.civilizations[civilization_id]
    cargo = dict(sorted(command.cargo.items()))
    provisions, taken = journey_supplies(command, state)
    if any(
        civilization.inventory.quantities.get(resource, 0) < quantity
        for resource, quantity in taken.items()
    ):
        unfunded = {
            JourneyKind.SHIPMENT: NoticeKind.SHIPMENT_UNFUNDED,
            JourneyKind.MIGRATION: NoticeKind.MIGRATION_UNFUNDED,
        }.get(kind, NoticeKind.PARTY_UNFUNDED)
        _add_notice(
            state,
            civilization_id,
            LogisticsNotice(
                notice_id=f"{command.journey_id}:{unfunded.value}",
                day=state.day,
                kind=unfunded,
                journey_id=command.journey_id,
                treaty_id=None if internal else command.treaty_id,
                counterpart_civilization_id=recipient_id,
                cargo=dict(sorted(taken.items())),
            ),
        )
        return [
            _event(
                state,
                EventPhase.MOVEMENT,
                f"{kind.value}_unfunded",
                str(civilization_id),
                str(command.journey_id),
            )
        ]
    civilization.inventory = civilization.inventory.apply_delta(
        InventoryDelta(changes={resource: -quantity for resource, quantity in taken.items()})
    )
    journey = Journey(
        journey_id=command.journey_id,
        kind=kind,
        treaty_id=None if internal else command.treaty_id,
        sender_civilization_id=civilization_id,
        recipient_civilization_id=recipient_id,
        traveller_ids=tuple(sorted(command.traveller_ids)),
        route=command.route,
        cargo=cargo,
        carrying_cargo=kind is JourneyKind.SHIPMENT,
        provisions_packed=provisions,
        provisions=provisions,
        departed_day=state.day,
    )
    state.journeys = tuple(sorted((*state.journeys, journey), key=lambda item: item.journey_id))
    disbanded = (
        _leave_garrisons(state, civilization_id, frozenset(journey.traveller_ids))
        if kind is JourneyKind.RELOCATION
        else []
    )
    _add_notice(
        state,
        civilization_id,
        notice(
            state.day,
            {
                JourneyKind.SHIPMENT: NoticeKind.SHIPMENT_DISPATCHED,
                JourneyKind.MIGRATION: NoticeKind.MIGRATION_DEPARTED,
            }.get(kind, NoticeKind.PARTY_DISPATCHED),
            journey,
            journey.recipient_civilization_id,
            cargo=cargo,
            person_ids=journey.traveller_ids,
        ),
    )
    return [
        _event(
            state,
            EventPhase.MOVEMENT,
            DISPATCH_EVENT.get(kind, f"{kind.value}_dispatched"),
            str(civilization_id),
            str(journey.journey_id),
            recipient=str(journey.recipient_civilization_id),
            treaty=str(journey.treaty_id or ""),
            travellers=len(journey.traveller_ids),
            cargo_units=sum(cargo.values()),
            provisions=provisions,
            route_tiles=len(journey.route),
        ),
        *disbanded,
    ]


def _transfer_migrants(state: WorldState, journey: Journey) -> tuple[EntityId, ...]:
    """Move living arrivals, with their history and any pregnancy, to the new civilization."""
    origin = state.civilizations[journey.sender_civilization_id]
    destination = state.civilizations[journey.recipient_civilization_id]
    origin_people = dict(origin.population.people)
    destination_people = dict(destination.population.people)
    arrivals = tuple(
        person_id
        for person_id in journey.traveller_ids
        if person_id in origin_people and origin_people[person_id].alive
    )
    for person_id in arrivals:
        person = origin_people.pop(person_id)
        destination_people[person_id] = person.model_copy(
            update={"civilization_id": destination.civilization_id}
        )
    following_mother = tuple(
        birth for birth in origin.population.scheduled_births if birth.parent_ids[0] in arrivals
    )
    origin.population = origin.population.model_copy(
        update={
            "people": origin_people,
            "scheduled_births": tuple(
                birth
                for birth in origin.population.scheduled_births
                if birth not in following_mother
            ),
        }
    )
    destination.population = destination.population.model_copy(
        update={
            "people": destination_people,
            "scheduled_births": tuple(
                sorted(
                    (*destination.population.scheduled_births, *following_mother),
                    key=lambda birth: (birth.due_day, birth.parent_ids),
                )
            ),
        }
    )
    return arrivals


def _adopt_migrant_capabilities(
    state: WorldState,
    civilization_id: EntityId,
    arrivals: tuple[EntityId, ...],
) -> tuple[CapabilityId, ...]:
    """Migrants bring practical skills; a new capability becomes known on arrival."""
    civilization = state.civilizations[civilization_id]
    records = {record.capability: record for record in civilization.capabilities}
    learned: list[CapabilityId] = []
    for capability in CapabilityId:
        practitioners = tuple(
            person_id
            for person_id in arrivals
            if civilization.population.people[person_id].skills.get(capability.value, 0) > 0
        )
        if not practitioners:
            continue
        existing = records.get(capability)
        if existing is None:
            learned.append(capability)
            records[capability] = CapabilityRecord(
                capability=capability,
                practitioner_ids=tuple(sorted(practitioners)),
                discovered_day=state.day,
            )
        else:
            records[capability] = existing.model_copy(
                update={
                    "practitioner_ids": tuple(sorted({*existing.practitioner_ids, *practitioners}))
                }
            )
    civilization.capabilities = tuple(
        sorted(records.values(), key=lambda record: record.capability.value)
    )
    return tuple(learned)


def _advance_journeys(
    state: WorldState, rng: StableRng
) -> tuple[list[DomainEvent], frozenset[EntityId]]:
    """Resolve travel, then receipt and allegiance transfer only for physical arrivals.

    Also returns the travellers who ate today, from the pack or by foraging.
    """
    if not state.journeys:
        return [], frozenset()
    result = advance_journeys_day(
        state.journeys,
        {
            civilization_id: civilization.population.people
            for civilization_id, civilization in state.civilizations.items()
        },
        day=state.day,
        rng=rng,
        treaties_in_force=frozenset(
            treaty.treaty_id for treaty in state.active_treaties if treaty.in_force
        ),
        world_map=state.world_map,
        arrival_allowed=lambda journey: _arrival_allowed(state, journey),
    )
    state.journeys = result.journeys
    for civilization_id, people in result.people_by_civilization.items():
        civilization = state.civilizations[civilization_id]
        civilization.population = civilization.population.model_copy(update={"people": people})
    kinds = {journey.journey_id: journey.kind for journey in result.journeys}
    events: list[DomainEvent] = []
    for journey_id in result.delayed_ids:
        events.append(
            _event(
                state,
                EventPhase.MOVEMENT,
                f"{kinds[journey_id].value}_delayed",
                None,
                str(journey_id),
            )
        )
    for journey_id in result.lost_ids:
        events.append(
            _event(
                state,
                EventPhase.MOVEMENT,
                "shipment_lost",
                None,
                str(journey_id),
                cause=TRAVEL_HAZARD_CAUSE,
            )
        )
    for death in result.hazard_deaths:
        events.append(
            _event(
                state,
                EventPhase.MOVEMENT,
                "migrant_lost",
                None,
                str(death.journey_id),
                person=str(death.person_id),
            )
        )
        events.append(
            _event(
                state,
                EventPhase.DEATH,
                "person_died",
                None,
                str(death.person_id),
                cause=TRAVEL_HAZARD_CAUSE,
                civilization=str(death.civilization_id),
            )
        )
    for journey_id in result.perished_ids:
        events.append(
            _event(
                state,
                EventPhase.MOVEMENT,
                f"{kinds[journey_id].value}_party_perished",
                None,
                str(journey_id),
            )
        )
    for journey in result.arrived:
        if journey.kind in INTERNAL_KINDS:
            events.extend(
                _settle_arrival(state, journey, result.handed_over.get(journey.journey_id, 0))
            )
            continue
        recipient_id = journey.recipient_civilization_id
        events.append(
            _event(
                state,
                EventPhase.MOVEMENT,
                f"{journey.kind.value}_arrived",
                str(recipient_id),
                str(journey.journey_id),
                sender=str(journey.sender_civilization_id),
            )
        )
        recipient = state.civilizations[recipient_id]
        if journey.kind is JourneyKind.SHIPMENT:
            recipient.inventory, waste = recipient.inventory.store_with_waste(journey.cargo)
            accepted = {
                resource: quantity - waste.get(resource, 0)
                for resource, quantity in journey.cargo.items()
                if quantity - waste.get(resource, 0) > 0
            }
            _add_notice(
                state,
                recipient_id,
                notice(
                    state.day,
                    NoticeKind.SHIPMENT_RECEIVED,
                    journey,
                    journey.sender_civilization_id,
                    cargo=accepted,
                ),
            )
            events.append(
                _event(
                    state,
                    EventPhase.MOVEMENT,
                    "shipment_received",
                    str(recipient_id),
                    str(journey.journey_id),
                    units=sum(accepted.values()),
                    wasted=sum(waste.values()),
                )
            )
            continue
        arrivals = _transfer_migrants(state, journey)
        provisions = _store_provisions(
            state, recipient_id, result.handed_over.get(journey.journey_id, 0)
        )
        _add_notice(
            state,
            recipient_id,
            notice(
                state.day,
                NoticeKind.MIGRANTS_RECEIVED,
                journey,
                journey.sender_civilization_id,
                cargo={Resource.FOOD: provisions} if provisions else None,
                person_ids=arrivals,
            ),
        )
        events.append(
            _event(
                state,
                EventPhase.MOVEMENT,
                "migrants_received",
                str(recipient_id),
                str(journey.journey_id),
                people=len(arrivals),
                provisions=provisions,
            )
        )
        for capability in _adopt_migrant_capabilities(state, recipient_id, arrivals):
            events.append(
                _event(
                    state,
                    EventPhase.WORK,
                    "capability_learned",
                    str(recipient_id),
                    capability=capability.value,
                    source="migration",
                )
            )
    for journey in result.failed:
        internal = journey.kind in INTERNAL_KINDS
        events.append(
            _event(
                state,
                EventPhase.MOVEMENT,
                FAILED_EVENT.get(journey.kind, f"{journey.kind.value}_failed"),
                str(journey.sender_civilization_id) if internal else None,
                str(journey.journey_id),
                cause="the site is no longer available" if internal else "no living recipients",
            )
        )
    for journey in result.refused:
        recipient_id = journey.recipient_civilization_id
        sender_people = state.civilizations[journey.sender_civilization_id].population.people
        turned_away = tuple(
            person_id
            for person_id in journey.traveller_ids
            if person_id in sender_people and sender_people[person_id].alive
        )
        _add_notice(
            state,
            recipient_id,
            notice(
                state.day,
                (
                    NoticeKind.SHIPMENT_TURNED_AWAY
                    if journey.kind is JourneyKind.SHIPMENT
                    else NoticeKind.MIGRANTS_TURNED_AWAY
                ),
                journey,
                journey.sender_civilization_id,
                cargo=journey.cargo,
                person_ids=turned_away,
            ),
        )
        events.append(
            _event(
                state,
                EventPhase.MOVEMENT,
                f"{journey.kind.value}_refused",
                str(recipient_id),
                str(journey.journey_id),
                treaty=str(journey.treaty_id),
            )
        )
    cargo_home = {journey.journey_id for journey in result.cargo_returned}
    for journey in result.returned:
        sender_id = journey.sender_civilization_id
        restored: dict[Resource, int] = {}
        if journey.journey_id in cargo_home:
            sender = state.civilizations[sender_id]
            sender.inventory, waste = sender.inventory.store_with_waste(journey.cargo)
            restored = {
                resource: quantity - waste.get(resource, 0)
                for resource, quantity in journey.cargo.items()
                if quantity - waste.get(resource, 0) > 0
            }
        provisions = _store_provisions(
            state, sender_id, result.handed_over.get(journey.journey_id, 0)
        )
        if provisions:
            restored[Resource.FOOD] = restored.get(Resource.FOOD, 0) + provisions
        home = journey.route[0]
        survivors = tuple(
            person_id
            for person_id in journey.traveller_ids
            if (person := state.civilizations[sender_id].population.people.get(person_id))
            is not None
            and person.alive
            and person.location == home
        )
        _add_notice(
            state,
            sender_id,
            notice(
                state.day,
                {
                    JourneyKind.SHIPMENT: NoticeKind.SHIPMENT_CARRIERS_RETURNED,
                    JourneyKind.MIGRATION: NoticeKind.MIGRANTS_RETURNED,
                }.get(journey.kind, NoticeKind.PARTY_RETURNED),
                journey,
                journey.recipient_civilization_id,
                cargo=restored,
                person_ids=survivors,
                reported_outcome=journey.outcome,
            ),
        )
        events.append(
            _event(
                state,
                EventPhase.MOVEMENT,
                f"{journey.kind.value}_returned",
                str(sender_id),
                str(journey.journey_id),
                outcome=journey.outcome.value,
                restored_units=sum(restored.values()) - provisions,
                provisions=provisions,
            )
        )
    for journey_id in result.exhausted_ids:
        events.append(
            _event(
                state,
                EventPhase.CONSUMPTION,
                f"{kinds[journey_id].value}_provisions_exhausted",
                None,
                str(journey_id),
            )
        )
    for foraging in result.foraging:
        events.append(
            _event(
                state,
                EventPhase.CONSUMPTION,
                f"{kinds[foraging.journey_id].value}_foraged",
                None,
                str(foraging.journey_id),
                fed=foraging.fed,
                hungry=foraging.hungry,
            )
        )
    return events, frozenset(result.fed_ids)


def _residents(state: WorldState) -> dict[EntityId, int]:
    """Living people at each settlement who are not away exploring, on embassy, or travelling."""
    away = {
        person_id
        for journey in state.journeys
        if journey.active
        for person_id in journey.traveller_ids
    }
    away.update(
        message.ambassador_id
        for message in state.diplomatic_missions
        if message.status is MissionStatus.IN_TRANSIT
    )
    counts: dict[EntityId, int] = {}
    for civilization in state.civilizations.values():
        away_here = away | {
            person_id
            for expedition in civilization.expeditions
            if expedition.status is ExpeditionStatus.ACTIVE
            for person_id in expedition.explorer_ids
        }
        for settlement in civilization.settlements:
            counts[settlement.settlement_id] = sum(
                person.alive and person.location == settlement.tile and person_id not in away_here
                for person_id, person in civilization.population.people.items()
            )
    return counts


def _tile_id(tile: HexCoord) -> str:
    return f"tile:{tile.q},{tile.r}"


def _advance_territory(state: WorldState) -> list[DomainEvent]:
    """Derive today's control from settlements and terrain; claims are never consulted."""
    events: list[DomainEvent] = []
    for civilization_id, civilization in sorted(state.civilizations.items()):
        people = civilization.population.people
        fallen = frozenset(
            person_id
            for garrison in civilization.garrisons
            for person_id in garrison.member_ids
            if not people[person_id].alive
        )
        if fallen:
            events.extend(_leave_garrisons(state, civilization_id, fallen))
    garrisons = [
        garrison
        for civilization in state.civilizations.values()
        for garrison in civilization.garrisons
    ]
    garrisoned = {
        garrison.garrison_id: sum(
            state.civilizations[garrison.civilization_id].population.people[person_id].location
            == garrison.tile
            for person_id in garrison.member_ids
        )
        for garrison in garrisons
    }
    result = advance_territory(
        state.territory,
        state.world_map,
        (
            settlement
            for civilization in state.civilizations.values()
            for settlement in civilization.settlements
        ),
        _residents(state),
        state.day,
        garrisons,
        garrisoned,
    )
    state.territory = result.territory
    owner_of_source = {
        **{
            settlement.settlement_id: settlement.civilization_id
            for civilization in state.civilizations.values()
            for settlement in civilization.settlements
        },
        **{garrison.garrison_id: garrison.civilization_id for garrison in garrisons},
    }
    for kind, sources in (("route_severed", result.severed), ("route_restored", result.restored)):
        events.extend(
            _event(
                state,
                EventPhase.MOVEMENT,
                kind,
                str(owner_of_source.get(source_id, "")) or None,
                str(source_id),
            )
            for source_id in sources
        )
    return events + [
        _event(
            state,
            EventPhase.PROJECT,
            "control_gained" if change.gained else "control_lost",
            str(change.civilization_id),
            _tile_id(change.tile),
            q=change.tile.q,
            r=change.tile.r,
            **(
                {"from": str(change.previous_owner)}
                if change.gained and change.previous_owner is not None
                else {}
            ),
        )
        for change in result.changes
    ]


def _run_councils(
    state: WorldState,
    sovereigns: Mapping[EntityId, Sovereign],
) -> list[DomainEvent]:
    events: list[DomainEvent] = []
    if state.day % state.config.council_interval_days != 0:
        return events
    for civilization_id in sorted(sovereigns):
        if civilization_id not in state.civilizations:
            continue
        sovereign = sovereigns[civilization_id]
        try:
            envelope = sovereign.decide(build_council_report(state, civilization_id))
        except Exception as exception:
            events.append(
                _event(
                    state,
                    EventPhase.COMMAND,
                    "sovereign_unavailable",
                    str(civilization_id),
                    error_type=type(exception).__name__,
                )
            )
            continue
        validation = validate_envelope(envelope, state)
        decrees = state.active_decrees.setdefault(civilization_id, {})
        for command in validation.accepted:
            if isinstance(command, Decree):
                decrees[command.kind.value] = command.value
                decrees[f"{command.kind.value}_expires"] = state.day + command.duration_days
            elif (
                isinstance(command, DirectOrder)
                and command.kind is DirectOrderKind.START_PROJECT
                and command.project_id is not None
                and command.project_kind is not None
            ):
                civilization = state.civilizations[civilization_id]
                if command.project_id not in civilization.projects:
                    is_storage = command.project_kind is ProjectKind.STORAGE
                    resource = Resource.STONE if is_storage else Resource.TIMBER
                    quantity = 30 if is_storage else 40
                    if civilization.inventory.quantities.get(resource, 0) >= quantity:
                        civilization.inventory = civilization.inventory.apply_delta(
                            InventoryDelta(changes={resource: -quantity})
                        )
                        civilization.projects[command.project_id] = ConstructionProject(
                            project_id=command.project_id,
                            location=civilization.start_center,
                            required_materials={resource: quantity},
                            delivered_materials={resource: quantity},
                            required_labor_minutes=480 * len(command.worker_ids),
                        )
                        civilization.work_orders += (
                            WorkOrder(
                                order_id=EntityId(f"work:{command.project_id}"),
                                kind=WorkKind.CONSTRUCT,
                                worker_ids=command.worker_ids,
                                project_id=command.project_id,
                            ),
                        )
                        events.append(
                            _event(
                                state,
                                EventPhase.PROJECT,
                                "project_started",
                                str(civilization_id),
                                str(command.project_id),
                            )
                        )
            elif (
                isinstance(command, DirectOrder)
                and command.kind is DirectOrderKind.START_TEACHING
                and command.assignment_id is not None
                and command.teacher_id is not None
                and command.apprentice_id is not None
                and command.capability is not None
            ):
                civilization = state.civilizations[civilization_id]
                civilization.teaching_assignments = tuple(
                    sorted(
                        (*civilization.teaching_assignments, TeachingAssignment(
                            assignment_id=command.assignment_id,
                            teacher_id=command.teacher_id,
                            apprentice_id=command.apprentice_id,
                            capability=command.capability,
                            started_day=state.day,
                        )),
                        key=lambda assignment: assignment.assignment_id,
                    )
                )
            elif (
                isinstance(command, DirectOrder)
                and command.kind is DirectOrderKind.START_EXPEDITION
                and command.expedition_id is not None
                and command.explorer_ids
                and command.route
            ):
                civilization = state.civilizations[civilization_id]
                civilization.expeditions = tuple(
                    sorted(
                        (
                            *civilization.expeditions,
                            Expedition(
                                expedition_id=command.expedition_id,
                                explorer_ids=command.explorer_ids,
                                route=command.route,
                            ),
                        ),
                        key=lambda expedition: expedition.expedition_id,
                    )
                )
                events.append(
                    _event(
                        state,
                        EventPhase.MOVEMENT,
                        "expedition_started",
                        str(civilization_id),
                        str(command.expedition_id),
                    )
                )
            elif isinstance(command, DirectOrder) and command.kind is DirectOrderKind.CLAIM_BORDER:
                claim = Claim(
                    claim_id=f"claim:{state.day}:{command.command_id}",
                    civilization_id=civilization_id,
                    claimed_day=state.day,
                    tiles=tuple(sorted(set(command.claimed_tiles))),
                )
                civilization = state.civilizations[civilization_id]
                civilization.claims = tuple(
                    sorted((*civilization.claims, claim), key=lambda item: item.claim_id)
                )
                events.append(
                    _event(
                        state,
                        EventPhase.COMMAND,
                        "claim_recorded",
                        str(civilization_id),
                        claim.claim_id,
                        tiles=len(claim.tiles),
                    )
                )
            elif isinstance(command, DirectOrder) and command.kind in JOURNEY_ORDERS:
                events.extend(_dispatch_journey(state, civilization_id, command))
            elif (
                isinstance(command, DirectOrder)
                and command.kind is DirectOrderKind.REPUDIATE_TREATY
                and command.treaty_id is not None
            ):
                breached = _end_treaty(
                    state, command.treaty_id, TreatyEndKind.BREACHED, civilization_id
                )
                if breached is not None:
                    events.append(
                        _event(
                            state,
                            EventPhase.COMMAND,
                            "treaty_breached",
                            str(civilization_id),
                            str(breached.treaty_id),
                            injured=str(breached.counterparty(civilization_id)),
                        )
                    )
            elif (
                isinstance(command, DirectOrder)
                and command.kind in MESSAGE_ORDERS
                and command.message_id is not None
                and command.ambassador_id is not None
                and command.recipient_civilization_id is not None
                and command.route
            ):
                state.diplomatic_missions = tuple(
                    sorted(
                        (
                            *state.diplomatic_missions,
                            DiplomaticMessage(
                                message_id=command.message_id,
                                sender_civilization_id=civilization_id,
                                recipient_civilization_id=command.recipient_civilization_id,
                                ambassador_id=command.ambassador_id,
                                route=command.route,
                                source_text=command.message_text,
                                departed_day=state.day,
                                treaty_offer=(
                                    TreatyOffer(
                                        offer_id=command.treaty_id,
                                        proposer_civilization_id=civilization_id,
                                        recipient_civilization_id=(
                                            command.recipient_civilization_id
                                        ),
                                        kind=command.treaty_kind,
                                        proposed_day=state.day,
                                    )
                                    if command.kind is DirectOrderKind.OFFER_TREATY
                                    and command.treaty_id is not None
                                    and command.treaty_kind is not None
                                    else None
                                ),
                                acceptance_of=(
                                    command.treaty_id
                                    if command.kind is DirectOrderKind.ACCEPT_TREATY
                                    else None
                                ),
                                cancellation_of=(
                                    command.treaty_id
                                    if command.kind is DirectOrderKind.CANCEL_TREATY
                                    else None
                                ),
                            ),
                        ),
                        key=lambda message: message.message_id,
                    )
                )
                if (
                    command.kind is DirectOrderKind.OFFER_TREATY
                    and command.treaty_id is not None
                    and command.treaty_kind is not None
                ):
                    state.treaty_offers = tuple(
                        sorted(
                            (
                                *state.treaty_offers,
                                TreatyOffer(
                                    offer_id=command.treaty_id,
                                    proposer_civilization_id=civilization_id,
                                    recipient_civilization_id=(
                                        command.recipient_civilization_id
                                    ),
                                    kind=command.treaty_kind,
                                    proposed_day=state.day,
                                ),
                            ),
                            key=lambda offer: offer.offer_id,
                        )
                    )
                events.append(
                    _event(
                        state,
                        EventPhase.MOVEMENT,
                        "message_dispatched",
                        str(civilization_id),
                        str(command.message_id),
                        recipient=str(command.recipient_civilization_id),
                    )
                )
                if command.kind is DirectOrderKind.OFFER_TREATY:
                    events.append(
                        _event(
                            state,
                            EventPhase.COMMAND,
                            "treaty_offered",
                            str(civilization_id),
                            str(command.treaty_id),
                        )
                    )
            events.append(
                _event(
                    state,
                    EventPhase.COMMAND,
                    "command_accepted",
                    str(civilization_id),
                    command_id=command.command_id,
                )
            )
        for validation_error in validation.errors:
            events.append(
                _event(
                    state,
                    EventPhase.COMMAND,
                    "command_rejected",
                    str(civilization_id),
                    code=validation_error.code,
                )
            )
    return events


def advance_day(
    state: WorldState,
    rng: StableRng,
    *,
    sovereigns: Mapping[EntityId, Sovereign] | None = None,
) -> TransitionResult:
    candidate = state.model_copy(deep=False)
    candidate.civilizations = {
        civilization_id: civilization.model_copy(deep=True)
        for civilization_id, civilization in state.civilizations.items()
    }
    candidate.active_decrees = deepcopy(state.active_decrees)
    events: list[DomainEvent] = []
    if sovereigns is not None:
        events.extend(_run_councils(candidate, sovereigns))

    for civilization_id in sorted(candidate.civilizations):
        civilization = candidate.civilizations[civilization_id]
        expedition_result = advance_expeditions(
            civilization.expeditions,
            civilization.population.people,
            candidate.world_map,
            candidate.day,
            observations=civilization.observations,
            owners=candidate.territory.owner_of(),
        )
        civilization.expeditions = expedition_result.expeditions
        civilization.observations = expedition_result.observations
        civilization.known_tiles = tuple(
            observation.tile for observation in civilization.observations
        )
        civilization.population = civilization.population.model_copy(
            update={"people": expedition_result.people}
        )
        for tile in expedition_result.observed_tiles:
            events.append(
                _event(
                    candidate,
                    EventPhase.MOVEMENT,
                    "tile_observed",
                    str(civilization_id),
                    tile_q=tile.q,
                    tile_r=tile.r,
                )
            )
        for expedition_id in expedition_result.returned_ids:
            events.append(
                _event(
                    candidate,
                    EventPhase.MOVEMENT,
                    "expedition_returned",
                    str(civilization_id),
                    str(expedition_id),
                )
            )
        for expedition_id in expedition_result.failed_ids:
            events.append(
                _event(
                    candidate,
                    EventPhase.MOVEMENT,
                    "expedition_failed",
                    str(civilization_id),
                    str(expedition_id),
                )
            )
        for expedition_id in expedition_result.blocked_ids:
            events.append(
                _event(
                    candidate,
                    EventPhase.MOVEMENT,
                    "expedition_blocked",
                    str(civilization_id),
                    str(expedition_id),
                )
            )
        for tile in expedition_result.observed_tiles:
            for foreign_id, foreign in sorted(candidate.civilizations.items()):
                if foreign_id == civilization_id or foreign.start_center != tile:
                    continue
                existing = next(
                    (
                        contact
                        for contact in civilization.contacts
                        if contact.civilization_id == foreign_id
                    ),
                    None,
                )
                contact = Contact(
                    civilization_id=foreign_id,
                    settlement=foreign.start_center,
                    first_contact_day=(
                        candidate.day if existing is None else existing.first_contact_day
                    ),
                    last_seen_day=candidate.day,
                )
                civilization.contacts = tuple(
                    sorted(
                        (
                            *(
                            item
                            for item in civilization.contacts
                            if item.civilization_id != foreign_id
                            ),
                            contact,
                        ),
                        key=lambda item: item.civilization_id,
                    )
                )
                if existing is None:
                    events.append(
                        _event(
                            candidate,
                            EventPhase.MOVEMENT,
                            "foreign_settlement_sighted",
                            str(civilization_id),
                            str(foreign_id),
                            tile_q=tile.q,
                            tile_r=tile.r,
                        )
                    )

    diplomacy_result = advance_diplomacy_day(
        candidate.diplomatic_missions,
        {
            civilization_id: civilization.population.people
            for civilization_id, civilization in candidate.civilizations.items()
        },
        day=candidate.day,
        rng=rng,
        world_map=candidate.world_map,
    )
    candidate.diplomatic_missions = diplomacy_result.missions
    for civilization_id, people in diplomacy_result.people_by_civilization.items():
        civilization = candidate.civilizations[civilization_id]
        civilization.population = civilization.population.model_copy(update={"people": people})
    for message in diplomacy_result.delivered:
        recipient = candidate.civilizations[message.recipient_civilization_id]
        recipient.received_messages = tuple(
            sorted((*recipient.received_messages, message), key=lambda item: item.message_id)
        )
        events.append(
            _event(
                candidate,
                EventPhase.MOVEMENT,
                "message_delivered",
                str(message.sender_civilization_id),
                str(message.recipient_civilization_id),
                message_id=str(message.message_id),
                distorted=message.delivered_text != message.source_text,
            )
        )
        if message.treaty_offer is not None:
            events.append(
                _event(
                    candidate,
                    EventPhase.MOVEMENT,
                    "treaty_offer_received",
                    str(message.sender_civilization_id),
                    str(message.treaty_offer.offer_id),
                )
            )
        if message.cancellation_of is not None:
            notified = next(
                (
                    treaty
                    for treaty in candidate.active_treaties
                    if treaty.treaty_id == message.cancellation_of
                ),
                None,
            )
            cancelled = (
                _end_treaty(
                    candidate,
                    message.cancellation_of,
                    TreatyEndKind.CANCELLED,
                    message.sender_civilization_id,
                )
                if notified is not None
                and {message.sender_civilization_id, message.recipient_civilization_id}
                == {notified.proposer_civilization_id, notified.recipient_civilization_id}
                else None
            )
            if cancelled is not None:
                events.append(
                    _event(
                        candidate,
                        EventPhase.MOVEMENT,
                        "treaty_cancelled",
                        str(message.recipient_civilization_id),
                        str(cancelled.treaty_id),
                        by=str(message.sender_civilization_id),
                    )
                )
        if message.acceptance_of is not None:
            offer = next(
                (
                    item
                    for item in candidate.treaty_offers
                    if item.offer_id == message.acceptance_of
                ),
                None,
            )
            if (
                offer is not None
                and offer.proposer_civilization_id == message.recipient_civilization_id
                and offer.recipient_civilization_id == message.sender_civilization_id
                and not any(
                    treaty.treaty_id == offer.offer_id
                    for treaty in candidate.active_treaties
                )
            ):
                candidate.active_treaties = tuple(
                    sorted(
                        (
                            *candidate.active_treaties,
                            ActiveTreaty(
                                treaty_id=offer.offer_id,
                                proposer_civilization_id=offer.proposer_civilization_id,
                                recipient_civilization_id=offer.recipient_civilization_id,
                                kind=offer.kind,
                                offered_day=offer.proposed_day,
                                activated_day=candidate.day,
                            ),
                        ),
                        key=lambda treaty: treaty.treaty_id,
                    )
                )
                events.append(
                    _event(
                        candidate,
                        EventPhase.MOVEMENT,
                        "treaty_activated",
                        str(offer.proposer_civilization_id),
                        str(offer.offer_id),
                        recipient=str(offer.recipient_civilization_id),
                    )
                )
    for message_id in diplomacy_result.delayed_ids:
        events.append(
            _event(candidate, EventPhase.MOVEMENT, "message_delayed", None, str(message_id))
        )
    for message_id in diplomacy_result.lost_ids:
        events.append(
            _event(candidate, EventPhase.MOVEMENT, "message_lost", None, str(message_id))
        )

    journey_events, fed_on_the_road = _advance_journeys(candidate, rng)
    events.extend(journey_events)

    for civilization_id in sorted(candidate.civilizations):
        civilization = candidate.civilizations[civilization_id]
        # Travellers on the road eat from their own packs, not from home stores.
        away = {
            person_id
            for journey in candidate.journeys
            if journey.active and journey.sender_civilization_id == civilization_id
            for person_id in journey.traveller_ids
        }
        stationed = {
            person_id for garrison in civilization.garrisons for person_id in garrison.member_ids
        }
        home_living = tuple(
            person_id
            for person_id in civilization.population.living_ids
            if person_id not in away
        )
        living_count = len(home_living)
        decrees = candidate.active_decrees.get(civilization_id, {})
        reserve_days = decrees.get("food_reserve_target", 0)
        labor_priority = decrees.get("labor_priority", 0)
        current_food = civilization.inventory.quantities.get(Resource.FOOD, 0)
        target_food = living_count * reserve_days
        if living_count and labor_priority > 0 and current_food < target_food:
            capacity = civilization.inventory.capacity - civilization.inventory.total_units
            farm_capacity = sum(
                (candidate.world_map.tile(coord).soil // 200)
                + (2 if candidate.world_map.tile(coord).has_water else 0)
                for coord in civilization.known_tiles
            )
            produced = min(living_count, farm_capacity, target_food - current_food, capacity)
            if produced:
                civilization.inventory = civilization.inventory.apply_delta(
                    InventoryDelta(changes={Resource.FOOD: produced})
                )
                events.append(
                    _event(
                        candidate,
                        EventPhase.WORK,
                        "food_produced",
                        str(civilization_id),
                        units=produced,
                    )
                )
        available_food = civilization.inventory.quantities.get(Resource.FOOD, 0)
        consumed = min(living_count, available_food)
        if consumed:
            civilization.inventory = civilization.inventory.apply_delta(
                InventoryDelta(changes={Resource.FOOD: -consumed})
            )
        events.append(
            _event(
                candidate,
                EventPhase.CONSUMPTION,
                "food_consumed",
                str(civilization_id),
                units=consumed,
            )
        )
        # When food runs short, the hungriest eat first, so shortage is shared.
        people = civilization.population.people
        by_need = sorted(
            home_living,
            key=lambda person_id: (
                -people[person_id].nutrition_debt,
                people[person_id].health_bp,
                person_id,
            ),
        )
        fed_today = set(by_need[:consumed]) | (fed_on_the_road & set(people))
        if consumed < living_count:
            shortage = living_count - consumed
            for person_id in by_need[consumed:]:
                go_hungry(people[person_id])
            events.append(
                _event(
                    candidate,
                    EventPhase.CONSUMPTION,
                    "food_shortage",
                    str(civilization_id),
                    people=shortage,
                )
            )

        work_result = execute_work_day(
            civilization.work_orders,
            {
                person_id: person
                for person_id, person in civilization.population.people.items()
                if person_id not in away and person_id not in stationed
            },
            civilization.inventory,
            civilization.projects,
        )
        civilization.inventory = work_result.inventory
        civilization.projects = work_result.projects
        for order_id in work_result.completed_order_ids:
            events.append(
                _event(candidate, EventPhase.WORK, "work_completed", str(order_id))
            )

        knowledge_result = advance_knowledge_day(
            KnowledgeState(
                records=civilization.capabilities,
                assignments=civilization.teaching_assignments,
            ),
            civilization.population.people,
            candidate.day,
        )
        civilization.capabilities = knowledge_result.knowledge.records
        civilization.teaching_assignments = knowledge_result.knowledge.assignments
        civilization.population = civilization.population.model_copy(
            update={"people": knowledge_result.people}
        )
        for capability in knowledge_result.learned:
            events.append(
                _event(
                    candidate,
                    EventPhase.WORK,
                    "capability_learned",
                    str(civilization_id),
                    capability=capability.value,
                )
            )
        for capability in knowledge_result.forgotten:
            events.append(
                _event(
                    candidate,
                    EventPhase.WORK,
                    "capability_forgotten",
                    str(civilization_id),
                    capability=capability.value,
                )
            )

        current_living = max(
            1,
            sum(
                person_id not in away for person_id in civilization.population.living_ids
            ),
        )
        food_days = civilization.inventory.quantities.get(Resource.FOOD, 0) // current_living
        growth_policy = candidate.active_decrees.get(civilization_id, {}).get(
            "population_growth_policy",
            0,
        )
        population_result = advance_population_day(
            civilization.population,
            day=candidate.day,
            rng=rng.stream(f"day:{candidate.day}:population:{civilization_id}"),
            food_days=food_days,
            shelter_slots=current_living + 64 if growth_policy > 0 else 0,
        )
        civilization.population = population_result.population
        mortality_knowledge_result = advance_knowledge_day(
            KnowledgeState(
                records=civilization.capabilities,
                assignments=civilization.teaching_assignments,
            ),
            civilization.population.people,
            candidate.day,
        )
        civilization.capabilities = mortality_knowledge_result.knowledge.records
        civilization.teaching_assignments = mortality_knowledge_result.knowledge.assignments
        civilization.population = civilization.population.model_copy(
            update={"people": mortality_knowledge_result.people}
        )
        for capability in mortality_knowledge_result.learned:
            events.append(
                _event(
                    candidate,
                    EventPhase.WORK,
                    "capability_learned",
                    str(civilization_id),
                    capability=capability.value,
                )
            )
        for capability in mortality_knowledge_result.forgotten:
            events.append(
                _event(
                    candidate,
                    EventPhase.WORK,
                    "capability_forgotten",
                    str(civilization_id),
                    capability=capability.value,
                )
            )
        for birth in population_result.births:
            events.append(
                _event(
                    candidate,
                    EventPhase.BIRTH,
                    "person_born",
                    str(civilization_id),
                    str(birth.person_id),
                )
            )
        for death in population_result.deaths:
            events.append(
                _event(
                    candidate,
                    EventPhase.DEATH,
                    "person_died",
                    str(civilization_id),
                    str(death.person_id),
                    cause=death.cause,
                )
            )
        for project_id in work_result.completed_project_ids:
            events.append(
                _event(
                    candidate,
                    EventPhase.PROJECT,
                    "building_completed",
                    str(civilization_id),
                    str(project_id),
                )
            )
        # Recovery follows the death roll, so the day food returns is still a dangerous one.
        for person_id in sorted(fed_today):
            person = civilization.population.people.get(person_id)
            if person is not None and person.alive:
                recover(person)

    events.extend(_advance_territory(candidate))

    candidate.day += 1
    validate_world(candidate)
    return TransitionResult(state=candidate, events=EventBatch.assign_sequences(events))
