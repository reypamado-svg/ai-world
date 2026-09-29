"""Atomic daily transition pipeline."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass

from sovereign_world.armoury import (
    BASIS,
    RECIPES,
    CraftJob,
    cargo_load,
    craft_materials,
    crew_needed,
    crewed_engines,
    engines_in,
    kit_assignment,
    personal_kits,
    settlement_bonus_after_engines,
)
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
    known_roads,
    known_tolls,
    trade_partners,
    validate_envelope,
)
from sovereign_world.diplomacy import (
    ActiveTreaty,
    Contact,
    DiplomaticMessage,
    MissionStatus,
    TreatyEndKind,
    TreatyKind,
    TreatyOffer,
    advance_diplomacy_day,
)
from sovereign_world.events import DomainEvent, EventBatch, EventPhase
from sovereign_world.exploration import (
    Expedition,
    ExpeditionStatus,
    Observation,
    advance_expeditions,
)
from sovereign_world.hexmap import HexCoord
from sovereign_world.ids import EntityId
from sovereign_world.logistics import (
    CARGO_UNITS_PER_CARRIER,
    INTERNAL_KINDS,
    MAX_TRAVELLERS,
    TRAVEL_HAZARD_CAUSE,
    Journey,
    JourneyKind,
    JourneyOutcome,
    JourneyPhase,
    LogisticsNotice,
    NoticeKind,
    RoadBuilt,
    TollEncounter,
    advance_journeys_day,
    journey_days,
    notice,
    provisions_needed,
)
from sovereign_world.people import advance_population_day, go_hungry, recover
from sovereign_world.resources import InventoryDelta, Resource
from sovereign_world.rng import StableRng
from sovereign_world.roads import Road, RoadView, grades_of
from sovereign_world.scripted import Sovereign
from sovereign_world.state import WorldState, validate_world
from sovereign_world.territory import (
    SETTLEMENT_SPACING,
    Claim,
    Garrison,
    Settlement,
    advance_territory,
)
from sovereign_world.tolls import TollGate, TollPost, TollRules, TollView
from sovereign_world.war import (
    ARMS,
    BATTLE_CAP,
    BATTLE_SURVIVED_POINTS,
    BATTLE_WON_POINTS,
    DRILL_CAP,
    DRILL_DAYS_PER_POINT,
    SETTLEMENT_DEFENCE_BP,
    Battle,
    BattleReport,
    Drill,
    War,
    WarObjective,
    able_to_fight,
    defence_bonus_bp,
    estimate,
    fighter,
    morale_bp,
    resolve_battle,
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
    civilization.inventory, waste = civilization.inventory.store_with_waste({Resource.FOOD: units})
    return units - waste.get(Resource.FOOD, 0)


FAILED_EVENT = {
    JourneyKind.SETTLEMENT: "founding_failed",
    JourneyKind.GARRISON: "garrison_failed",
}


def _arrival_allowed(state: WorldState, journey: Journey) -> bool:
    """Whether an internal party may found, garrison, join, or build where it stands today."""
    destination = journey.route[-1]
    civilization_id = journey.sender_civilization_id
    if journey.kind is JourneyKind.ROADWORK:
        here = journey.route[journey.route_index]
        allowed = {None, civilization_id} | trade_partners(state, civilization_id)
        return state.territory.owner_of().get(here) in allowed
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
    JourneyKind.ROADWORK: "road_crew_dispatched",
    JourneyKind.DEPOSIT: "toll_deposit_dispatched",
    JourneyKind.CAMPAIGN: "war_party_dispatched",
}
RETURNED_EVENT = {
    JourneyKind.ROADWORK: "road_crew_returned",
    JourneyKind.DEPOSIT: "toll_couriers_returned",
    JourneyKind.CAMPAIGN: "war_party_returned",
}
PLUNDER_ORDER = (
    Resource.FOOD,
    Resource.METAL,
    Resource.TOOL,
    Resource.AXE,
    Resource.ORE,
    Resource.PLANK,
    Resource.TIMBER,
    Resource.STONE,
)
"""What raiders carry off first when they cannot carry everything."""


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
    provisions, taken = journey_supplies(command, state, civilization_id)
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
        objective=command.war_objective if kind is JourneyKind.CAMPAIGN else None,
        road_grade=command.road_grade if kind is JourneyKind.ROADWORK else None,
        materials=(
            {
                resource: quantity
                for resource, quantity in sorted(taken.items())
                if resource is not Resource.FOOD and quantity
            }
            if kind is JourneyKind.ROADWORK
            else {}
        ),
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
            **({"grade": journey.road_grade.value} if journey.road_grade else {}),
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
        roads=grades_of(state.roads),
        tolls=_toll_rules(state),
        halts=lambda journey, tile: (
            journey.kind is JourneyKind.CAMPAIGN and _enemy_at(state, journey, tile) is not None
        ),
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
    for built in result.roads_built:
        events.append(_record_road(state, built))
    for halt in result.roadwork_stopped:
        events.append(
            _event(
                state,
                EventPhase.PROJECT,
                "road_work_stopped",
                str(halt.civilization_id),
                str(halt.journey_id),
                reason=halt.reason.value,
                q=halt.tile.q,
                r=halt.tile.r,
            )
        )
    for encounter in result.tolls:
        events.extend(_settle_toll(state, encounter))
    for journey in result.arrived:
        if journey.kind is JourneyKind.CAMPAIGN:
            continue
        if journey.kind is JourneyKind.DEPOSIT:
            events.extend(_deposit_arrival(state, journey))
            continue
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
        brought_home = journey.cargo if journey.journey_id in cargo_home else journey.materials
        if journey.kind is JourneyKind.CAMPAIGN:
            brought_home = _war_party_home(state, journey)
        if journey.kind is JourneyKind.DEPOSIT and brought_home:
            # Couriers turned back on the way carry the chest back to their post.
            _fill_chest(state, sender_id, journey.route[0], brought_home)
            restored = dict(brought_home)
            brought_home = {}
        if brought_home:
            sender = state.civilizations[sender_id]
            sender.inventory, waste = sender.inventory.store_with_waste(brought_home)
            restored = {
                resource: quantity - waste.get(resource, 0)
                for resource, quantity in brought_home.items()
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
                RETURNED_EVENT.get(journey.kind, f"{journey.kind.value}_returned"),
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


def _away(state: WorldState) -> set[EntityId]:
    """Everyone on a journey, an embassy, or an expedition today."""
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
    for civilization in state.civilizations.values():
        away.update(
            person_id
            for expedition in civilization.expeditions
            if expedition.status is ExpeditionStatus.ACTIVE
            for person_id in expedition.explorer_ids
        )
    return away


def _collectors(state: WorldState, post: TollPost, away: set[EntityId]) -> list[EntityId]:
    """The owner's living people standing at the post and not away, in id order."""
    people = state.civilizations[post.civilization_id].population.people
    return sorted(
        person_id
        for person_id, person in people.items()
        if person.alive and person.location == post.tile and person_id not in away
    )


def _toll_rules(state: WorldState) -> TollRules:
    """Today's staffed tolls, who passes free, and what each civilization knows."""
    away = _away(state)
    gates = {
        post.tile: TollGate(post.civilization_id, post.cargo_rate_bp, post.food_per_head)
        for civilization in state.civilizations.values()
        for post in civilization.toll_posts
        if post.collecting and _collectors(state, post, away)
    }
    return TollRules(
        gates=gates,
        free_passage=frozenset(
            frozenset({treaty.proposer_civilization_id, treaty.recipient_civilization_id})
            for treaty in state.active_treaties
            if treaty.in_force and treaty.kind is TreatyKind.TRADE
        ),
        known_gates={
            civilization_id: {
                view.tile: TollGate(view.owner, view.cargo_rate_bp, view.food_per_head)
                for view in known_tolls(state, civilization_id)
            }
            for civilization_id in state.civilizations
        },
        known_tiles={
            civilization_id: frozenset(civilization.known_tiles)
            for civilization_id, civilization in state.civilizations.items()
        },
    )


def _post_at(state: WorldState, owner: EntityId, tile: HexCoord) -> TollPost | None:
    return next((post for post in state.civilizations[owner].toll_posts if post.tile == tile), None)


def _replace_post(
    state: WorldState, post: TollPost | None, tile: HexCoord, owner: EntityId
) -> None:
    civilization = state.civilizations[owner]
    posts = [item for item in civilization.toll_posts if item.tile != tile]
    if post is not None:
        posts.append(post)
    civilization.toll_posts = tuple(sorted(posts, key=lambda item: item.tile))


def _fill_chest(
    state: WorldState, owner: EntityId, tile: HexCoord, goods: Mapping[Resource, int]
) -> None:
    """Put takings in a post's chest, or straight into the store at the capital's gate."""
    post = _post_at(state, owner, tile)
    if post is None or post.at_storehouse:
        civilization = state.civilizations[owner]
        civilization.inventory, _ = civilization.inventory.store_with_waste(dict(goods))
        return
    chest = dict(post.chest)
    for resource, quantity in goods.items():
        chest[resource] = chest.get(resource, 0) + quantity
    _replace_post(
        state,
        post.model_copy(update={"chest": {key: chest[key] for key in sorted(chest)}}),
        tile,
        owner,
    )


def _learn_toll(state: WorldState, civilization_id: EntityId, view: TollView) -> None:
    civilization = state.civilizations[civilization_id]
    views = {item.tile: item for item in civilization.toll_intel}
    views[view.tile] = view
    civilization.toll_intel = tuple(views[tile] for tile in sorted(views))


def _settle_toll(state: WorldState, encounter: TollEncounter) -> list[DomainEvent]:
    """Record a party's meeting with a toll post: payment into the chest, or its way round."""
    gate = encounter.gate
    tile = encounter.tile
    _learn_toll(
        state,
        encounter.payer,
        TollView(
            tile=tile,
            owner=gate.owner,
            cargo_rate_bp=gate.cargo_rate_bp,
            food_per_head=gate.food_per_head,
            as_of_day=state.day,
        ),
    )
    journey = next(item for item in state.journeys if item.journey_id == encounter.journey_id)
    where = f"{tile.q},{tile.r}"
    location = {"q": tile.q, "r": tile.r}
    if encounter.avoided or encounter.turned_back:
        kind = NoticeKind.TOLL_AVOIDED if encounter.avoided else NoticeKind.TOLL_TURNED_BACK
        _add_notice(
            state,
            encounter.payer,
            LogisticsNotice(
                notice_id=f"{journey.journey_id}:{kind.value}:{where}",
                day=state.day,
                kind=kind,
                journey_id=journey.journey_id,
                treaty_id=journey.treaty_id,
                counterpart_civilization_id=gate.owner,
            ),
        )
        return [
            _event(
                state,
                EventPhase.MOVEMENT,
                "toll_avoided" if encounter.avoided else "toll_refused",
                str(encounter.payer),
                str(journey.journey_id),
                owner=str(gate.owner),
                **location,
            )
        ]
    if not encounter.paid:
        return []
    _fill_chest(state, gate.owner, tile, encounter.paid)
    for civilization_id, kind, counterpart in (
        (encounter.payer, NoticeKind.TOLL_PAID, gate.owner),
        (gate.owner, NoticeKind.TOLL_COLLECTED, encounter.payer),
    ):
        _add_notice(
            state,
            civilization_id,
            LogisticsNotice(
                notice_id=f"{journey.journey_id}:{kind.value}:{where}",
                day=state.day,
                kind=kind,
                journey_id=journey.journey_id,
                treaty_id=journey.treaty_id,
                counterpart_civilization_id=counterpart,
                cargo=dict(encounter.paid),
            ),
        )
    post = _post_at(state, gate.owner, tile)
    units = sum(encounter.paid.values())
    return [
        _event(
            state,
            EventPhase.MOVEMENT,
            "toll_paid",
            str(encounter.payer),
            str(journey.journey_id),
            owner=str(gate.owner),
            units=units,
            **location,
        ),
        _event(
            state,
            EventPhase.MOVEMENT,
            "toll_collected",
            str(gate.owner),
            str(post.post_id) if post is not None else _tile_id(tile),
            payer=str(encounter.payer),
            units=units,
            **location,
        ),
    ]


def _deposit_arrival(state: WorldState, journey: Journey) -> list[DomainEvent]:
    """Couriers reach the storehouse: the chest's contents are stored only now."""
    owner = journey.sender_civilization_id
    civilization = state.civilizations[owner]
    civilization.inventory, waste = civilization.inventory.store_with_waste(journey.cargo)
    stored = {
        resource: quantity - waste.get(resource, 0)
        for resource, quantity in journey.cargo.items()
        if quantity - waste.get(resource, 0) > 0
    }
    _add_notice(
        state, owner, notice(state.day, NoticeKind.TOLL_DEPOSITED, journey, owner, cargo=stored)
    )
    return [
        _event(
            state,
            EventPhase.MOVEMENT,
            "toll_deposited",
            str(owner),
            str(journey.journey_id),
            units=sum(stored.values()),
            wasted=sum(waste.values()),
        )
    ]


def _advance_tolls(state: WorldState) -> list[DomainEvent]:
    """Lapse or resume each post, and send couriers home with any chest that is due."""
    events: list[DomainEvent] = []
    owners = state.territory.owner_of()
    for civilization_id in sorted(state.civilizations):
        civilization = state.civilizations[civilization_id]
        for post in civilization.toll_posts:
            away = _away(state)
            collectors = _collectors(state, post, away)
            held = owners.get(post.tile) == civilization_id and collectors
            if post.collecting and not held:
                post = post.model_copy(update={"collecting": False})
                events.append(
                    _event(
                        state,
                        EventPhase.MOVEMENT,
                        "toll_lapsed",
                        str(civilization_id),
                        str(post.post_id),
                    )
                )
            elif not post.collecting and not post.lifted and held:
                post = post.model_copy(update={"collecting": True})
                events.append(
                    _event(
                        state,
                        EventPhase.MOVEMENT,
                        "toll_resumed",
                        str(civilization_id),
                        str(post.post_id),
                    )
                )
            if post.chest and state.day - post.last_deposit_day >= post.deposit_every_days:
                post, dispatched = _send_deposit(state, post, collectors)
                events.extend(dispatched)
            if post.lifted and not post.chest:
                _replace_post(state, None, post.tile, civilization_id)
            else:
                _replace_post(state, post, post.tile, civilization_id)
    return events


def _send_deposit(
    state: WorldState, post: TollPost, collectors: list[EntityId]
) -> tuple[TollPost, list[DomainEvent]]:
    """Send spare collectors home with as much of the chest as they can carry."""
    owner = post.civilization_id
    civilization = state.civilizations[owner]
    couriers = tuple(collectors[: min(len(collectors) - 1, MAX_TRAVELLERS)])
    journey_id = EntityId(f"journey:{post.post_id}:deposit:{state.day}")
    provisions = (
        provisions_needed(
            journey_days(
                JourneyKind.DEPOSIT, state.world_map, post.deposit_route, grades_of(state.roads)
            ),
            len(couriers),
        )
        if couriers
        else 0
    )
    room = CARGO_UNITS_PER_CARRIER * len(couriers) - provisions
    food = civilization.inventory.quantities.get(Resource.FOOD, 0)
    post = post.model_copy(update={"last_deposit_day": state.day})
    if not couriers or room <= 0 or food < provisions:
        cause = "no spare collector" if not couriers else "the couriers cannot be provisioned"
        _add_notice(
            state,
            owner,
            LogisticsNotice(
                notice_id=f"{journey_id}:{NoticeKind.TOLL_DEPOSIT_SKIPPED.value}",
                day=state.day,
                kind=NoticeKind.TOLL_DEPOSIT_SKIPPED,
                journey_id=journey_id,
                treaty_id=None,
                counterpart_civilization_id=owner,
                cargo=dict(post.chest),
            ),
        )
        return post, [
            _event(
                state,
                EventPhase.MOVEMENT,
                "toll_deposit_skipped",
                str(owner),
                str(post.post_id),
                cause=cause,
            )
        ]
    carried: dict[Resource, int] = {}
    chest = dict(post.chest)
    for resource in sorted(chest):
        taken = min(chest[resource], room)
        if taken:
            carried[resource] = taken
            chest[resource] -= taken
            room -= taken
    civilization.inventory = civilization.inventory.apply_delta(
        InventoryDelta(changes={Resource.FOOD: -provisions})
    )
    journey = Journey(
        journey_id=journey_id,
        kind=JourneyKind.DEPOSIT,
        sender_civilization_id=owner,
        recipient_civilization_id=owner,
        traveller_ids=couriers,
        route=post.deposit_route,
        cargo=carried,
        carrying_cargo=True,
        provisions_packed=provisions,
        provisions=provisions,
        departed_day=state.day,
    )
    state.journeys = tuple(sorted((*state.journeys, journey), key=lambda item: item.journey_id))
    post = post.model_copy(update={"chest": {key: left for key, left in chest.items() if left}})
    return post, [
        _event(
            state,
            EventPhase.MOVEMENT,
            "toll_deposit_dispatched",
            str(owner),
            str(journey_id),
            couriers=len(couriers),
            units=sum(carried.values()),
            left_behind=sum(post.chest.values()),
        )
    ]


def _set_toll(state: WorldState, civilization_id: EntityId, command: DirectOrder) -> DomainEvent:
    """Set, change, or lift the toll at a post; lifting keeps the chest until it is emptied."""
    assert command.toll_rate_bp is not None and command.toll_food_per_head is not None
    tile = command.route[0]
    existing = _post_at(state, civilization_id, tile)
    location = {"q": tile.q, "r": tile.r}
    if not command.toll_rate_bp and not command.toll_food_per_head:
        assert existing is not None
        _replace_post(
            state,
            existing.model_copy(update={"collecting": False, "lifted": True}),
            tile,
            civilization_id,
        )
        return _event(
            state, EventPhase.MOVEMENT, "toll_lifted", str(civilization_id), str(existing.post_id)
        )
    post = TollPost(
        post_id=(
            existing.post_id
            if existing is not None
            else EntityId(f"toll:{civilization_id.rsplit(':', 1)[-1]}:{tile.q},{tile.r}")
        ),
        civilization_id=civilization_id,
        tile=tile,
        cargo_rate_bp=command.toll_rate_bp,
        food_per_head=command.toll_food_per_head,
        deposit_every_days=command.deposit_interval_days,
        deposit_route=command.route,
        set_day=state.day,
        last_deposit_day=existing.last_deposit_day if existing is not None else state.day,
        chest=existing.chest if existing is not None else {},
    )
    _replace_post(state, post, tile, civilization_id)
    return _event(
        state,
        EventPhase.MOVEMENT,
        "toll_set",
        str(civilization_id),
        str(post.post_id),
        cargo_rate_bp=post.cargo_rate_bp,
        food_per_head=post.food_per_head,
        **location,
    )


def _share_maps(state: WorldState, treaty: ActiveTreaty) -> None:
    """Trade partners exchange road maps: each learns the other's roads and tolls, dated today."""
    first, second = treaty.proposer_civilization_id, treaty.recipient_civilization_id
    maps = {
        civilization_id: (known_roads(state, civilization_id), known_tolls(state, civilization_id))
        for civilization_id in (first, second)
    }
    for learner, teacher in ((first, second), (second, first)):
        civilization = state.civilizations[learner]
        shown_roads, shown_tolls = maps[teacher]
        road_views = {item.tile: item for item in civilization.road_intel}
        for road in shown_roads:
            road_views[road.tile] = RoadView(tile=road.tile, grade=road.grade, as_of_day=state.day)
        civilization.road_intel = tuple(road_views[tile] for tile in sorted(road_views))
        for toll in shown_tolls:
            _learn_toll(state, learner, toll.model_copy(update={"as_of_day": state.day}))


def _joined_roads(state: WorldState) -> list[DomainEvent]:
    """Note when a continuous road first links a settlement of each trade partner."""
    road_tiles = {road.tile for road in state.roads}
    joined: list[EntityId] = []
    events: list[DomainEvent] = []
    for treaty in state.active_treaties:
        if not treaty.in_force or treaty.kind is not TreatyKind.TRADE:
            continue
        starts = {
            settlement.tile
            for settlement in state.civilizations[treaty.proposer_civilization_id].settlements
        } & road_tiles
        goals = {
            settlement.tile
            for settlement in state.civilizations[treaty.recipient_civilization_id].settlements
        } & road_tiles
        if not starts or not goals:
            continue
        seen = set(starts)
        frontier = deque(sorted(starts))
        length = {tile: 1 for tile in starts}
        reached: HexCoord | None = None
        while frontier and reached is None:
            tile = frontier.popleft()
            if tile in goals:
                reached = tile
                break
            for neighbor in sorted(tile.neighbors()):
                if neighbor in road_tiles and neighbor not in seen:
                    seen.add(neighbor)
                    length[neighbor] = length[tile] + 1
                    frontier.append(neighbor)
        if reached is None:
            continue
        joined.append(treaty.treaty_id)
        if treaty.treaty_id not in state.joined_roads:
            events.append(
                _event(
                    state,
                    EventPhase.MOVEMENT,
                    "roads_joined",
                    str(treaty.proposer_civilization_id),
                    str(treaty.treaty_id),
                    partner=str(treaty.recipient_civilization_id),
                    road_tiles=length[reached],
                )
            )
    state.joined_roads = tuple(sorted(joined))
    return events


def _war_between(state: WorldState, first: EntityId, second: EntityId) -> War | None:
    return next((war for war in state.wars if war.active and war.involves(first, second)), None)


def _start_war(
    state: WorldState, aggressor: EntityId, defender: EntityId, *, declared: bool
) -> tuple[War, list[DomainEvent]]:
    """Record a war and break every treaty between the two, as the aggressor's breach."""
    war = War(
        war_id=EntityId(
            f"war:{aggressor.rsplit(':', 1)[-1]}:{defender.rsplit(':', 1)[-1]}:{state.day}"
        ),
        aggressor_id=aggressor,
        defender_id=defender,
        started_day=state.day,
        declared=declared,
    )
    state.wars = tuple(sorted((*state.wars, war), key=lambda item: item.war_id))
    events = [
        _event(
            state,
            EventPhase.COMMAND,
            "war_declared" if declared else "undeclared_attack",
            str(aggressor),
            str(war.war_id),
            defender=str(defender),
        )
    ]
    for treaty in state.active_treaties:
        if treaty.in_force and {aggressor, defender} == {
            treaty.proposer_civilization_id,
            treaty.recipient_civilization_id,
        }:
            broken = _end_treaty(state, treaty.treaty_id, TreatyEndKind.BREACHED, aggressor)
            if broken is not None:
                events.append(
                    _event(
                        state,
                        EventPhase.COMMAND,
                        "treaty_breached",
                        str(aggressor),
                        str(broken.treaty_id),
                        injured=str(defender),
                    )
                )
    return war, events


def _learn_war(state: WorldState, war: War, civilization_id: EntityId) -> list[DomainEvent]:
    """A defender learns of its war the first time the news or the enemy reaches it."""
    if civilization_id != war.defender_id or war.defender_learned_day is not None:
        return []
    learned = war.model_copy(update={"defender_learned_day": state.day})
    state.wars = tuple(learned if item.war_id == war.war_id else item for item in state.wars)
    return [
        _event(state, EventPhase.MOVEMENT, "war_learned", str(civilization_id), str(war.war_id))
    ]


def _on_non_fighting_journey(state: WorldState) -> set[EntityId]:
    """Everyone travelling who does not fight where they stand: convoys, migrants, crews."""
    return {
        person_id
        for journey in state.journeys
        if journey.active and journey.kind is not JourneyKind.CAMPAIGN
        for person_id in journey.traveller_ids
    }


def _enemy_at(state: WorldState, journey: Journey, tile: HexCoord) -> EntityId | None:
    """The civilization a war party would fight on this tile, if any.

    A party fights whoever it is at war with; heading out, it also falls on its target.
    Its own target comes first, then the lowest civilization id.
    """
    sender = journey.sender_civilization_id
    hostile: list[EntityId] = []
    for civilization_id in sorted(state.civilizations):
        if civilization_id == sender:
            continue
        at_war = _war_between(state, sender, civilization_id) is not None
        targeted = (
            civilization_id == journey.recipient_civilization_id
            and journey.phase is JourneyPhase.OUTBOUND
        )
        if not (at_war or targeted):
            continue
        if any(
            person.alive and person.location == tile
            for person in state.civilizations[civilization_id].population.people.values()
        ):
            hostile.append(civilization_id)
    if journey.recipient_civilization_id in hostile:
        return journey.recipient_civilization_id
    return hostile[0] if hostile else None


def _defenders(
    state: WorldState, civilization_id: EntityId, tile: HexCoord
) -> tuple[list[EntityId], list[EntityId]]:
    """Able people of a civilization who would fight on a tile: at home, and in war parties."""
    busy = _on_non_fighting_journey(state)
    marching = {
        person_id
        for journey in state.journeys
        if journey.active and journey.kind is JourneyKind.CAMPAIGN
        for person_id in journey.traveller_ids
    }
    people = state.civilizations[civilization_id].population.people
    here = sorted(
        person_id
        for person_id, person in people.items()
        if person.location == tile and able_to_fight(person) and person_id not in busy
    )
    return [item for item in here if item not in marching], [
        item for item in here if item in marching
    ]


def _hurt(state: WorldState, battle: Battle) -> list[DomainEvent]:
    """Apply wounds and deaths, then battle experience for everyone who lived."""
    events: list[DomainEvent] = []
    for casualty in battle.casualties:
        person = state.civilizations[casualty.civilization_id].population.people[casualty.person_id]
        if casualty.died:
            person.health_bp = 0
            person.alive = False
            person.death_day = state.day
            events.append(
                _event(
                    state,
                    EventPhase.DEATH,
                    "person_died",
                    str(casualty.civilization_id),
                    str(casualty.person_id),
                    cause="battle",
                )
            )
        else:
            person.health_bp = max(person.health_bp - casualty.damage, 1)
            events.append(
                _event(
                    state,
                    EventPhase.DEATH,
                    "person_wounded",
                    str(casualty.civilization_id),
                    str(casualty.person_id),
                    damage=casualty.damage,
                )
            )
    for side, civilization_id in (
        (battle.attackers, battle.attacker_id),
        (battle.defenders, battle.defender_id),
    ):
        gain = BATTLE_WON_POINTS if civilization_id == battle.winner_id else BATTLE_SURVIVED_POINTS
        people = state.civilizations[civilization_id].population.people
        for person_id in side:
            person = people[person_id]
            if person.alive:
                current = person.skills.get(ARMS, 0)
                person.skills = {
                    **person.skills,
                    ARMS: max(current, min(current + gain, BATTLE_CAP)),
                }
    return events


def _battle_report(battle: Battle, civilization_id: EntityId) -> BattleReport:
    """One side's account: its own losses by name, the enemy's by estimate."""
    own_side = battle.attackers if civilization_id == battle.attacker_id else battle.defenders
    enemy_side = battle.defenders if civilization_id == battle.attacker_id else battle.attackers
    own = set(own_side)
    won = civilization_id == battle.winner_id
    enemy_casualties = [item for item in battle.casualties if item.person_id not in own]
    return BattleReport(
        battle_id=battle.battle_id,
        day=battle.day,
        tile=battle.tile,
        enemy_id=battle.defender_id
        if civilization_id == battle.attacker_id
        else battle.attacker_id,
        won=won,
        own_fighters=len(own_side),
        own_dead=tuple(
            item.person_id for item in battle.casualties if item.person_id in own and item.died
        ),
        own_wounded=tuple(
            item.person_id for item in battle.casualties if item.person_id in own and not item.died
        ),
        enemy_fighters_estimate=estimate(len(enemy_side)),
        # The side that holds the field counts the enemy dead left on it.
        enemy_dead_seen=sum(item.died for item in enemy_casualties) if won else 0,
        enemy_losses_estimate=estimate(len(enemy_casualties)),
    )


def _file_report(state: WorldState, civilization_id: EntityId, report: BattleReport) -> None:
    civilization = state.civilizations[civilization_id]
    reports = {item.battle_id: item for item in civilization.war_reports}
    reports[report.battle_id] = report
    civilization.war_reports = tuple(reports[key] for key in sorted(reports))


def _replace_journey(state: WorldState, journey: Journey) -> None:
    state.journeys = tuple(
        journey if item.journey_id == journey.journey_id else item for item in state.journeys
    )


def _fight(
    state: WorldState, party: Journey, enemy: EntityId, rng: StableRng
) -> tuple[Journey, list[DomainEvent]]:
    """A war party falls on the enemy standing on its tile; the battle lasts one day."""
    events: list[DomainEvent] = []
    sender = party.sender_civilization_id
    tile = party.route[party.route_index]
    war = _war_between(state, sender, enemy)
    if war is None:
        war, started = _start_war(state, sender, enemy, declared=False)
        events.extend(started)
    events.extend(_learn_war(state, war, enemy))
    events.extend(_learn_war(state, war, sender))
    home_side, marching = _defenders(state, enemy, tile)
    attackers_people = state.civilizations[sender].population.people
    attacker_ids = [
        person_id
        for person_id in party.traveller_ids
        if attackers_people[person_id].alive and able_to_fight(attackers_people[person_id])
    ]
    enemy_homes = {settlement.tile for settlement in state.civilizations[enemy].settlements}
    at_home = tile in enemy_homes
    # Engines need hands: their crews, the last fighters in id order, fight at half strength.
    working = crewed_engines(len(attacker_ids), engines_in(party.cargo)) if at_home else {}
    crew = set(attacker_ids[len(attacker_ids) - crew_needed(working) :]) if working else set()
    issued = kit_assignment(attacker_ids, personal_kits(party.cargo))
    attackers = [
        fighter(
            attackers_people[person_id],
            issued.get(person_id),
            attacking=True,
            crewing=person_id in crew,
        )
        for person_id in attacker_ids
    ]
    enemy_people = state.civilizations[enemy].population.people
    store = state.civilizations[enemy].inventory.quantities
    defending_parties = [
        journey
        for journey in state.journeys
        if journey.active
        and journey.kind is JourneyKind.CAMPAIGN
        and journey.sender_civilization_id == enemy
        and any(person_id in marching for person_id in journey.traveller_ids)
    ]
    # Home defenders arm from their store; defending war parties use what they carry.
    defender_kits = kit_assignment(home_side, personal_kits(dict(store)))
    for journey in defending_parties:
        defender_kits.update(
            kit_assignment(
                [item for item in journey.traveller_ids if item in marching],
                personal_kits(journey.cargo),
            )
        )
    defenders = [
        fighter(enemy_people[person_id], defender_kits.get(person_id), attacking=False)
        for person_id in [*home_side, *marching]
    ]
    terrain_bp = defence_bonus_bp(state.world_map.tile(tile).terrain, settlement=False)
    walls_bp = settlement_bonus_after_engines(SETTLEMENT_DEFENCE_BP, working) if at_home else BASIS
    battle_id = EntityId(f"battle:{state.day:06d}:{party.journey_id}")
    outcome = resolve_battle(
        attackers,
        defenders,
        defence_bp=terrain_bp * walls_bp // BASIS,
        attacker_morale_bp=morale_bp(attackers, at_home=False, supplied=True),
        defender_morale_bp=(
            morale_bp(defenders, at_home=True, supplied=True)
            if at_home and home_side
            else morale_bp(defenders, at_home=False, supplied=True)
        ),
        rng=rng,
        stream=f"day:{state.day}:war:{battle_id}",
        catapults=working.get(Resource.CATAPULT, 0),
    )
    battle = Battle(
        battle_id=battle_id,
        day=state.day,
        tile=tile,
        attacker_id=sender,
        defender_id=enemy,
        attackers=tuple(attacker_ids),
        defenders=tuple(sorted([*home_side, *marching])),
        rounds=outcome.rounds,
        winner_id=sender if outcome.attackers_won else enemy,
        casualties=outcome.casualties,
    )
    state.battles = tuple(sorted((*state.battles, battle), key=lambda item: item.battle_id))
    events.append(
        _event(
            state,
            EventPhase.MOVEMENT,
            "battle_joined",
            str(sender),
            str(battle_id),
            defender=str(enemy),
            attackers=len(attacker_ids),
            defenders=len(battle.defenders),
            q=tile.q,
            r=tile.r,
        )
    )
    events.extend(_hurt(state, battle))
    events.append(
        _event(
            state,
            EventPhase.MOVEMENT,
            "battle_won",
            str(battle.winner_id),
            str(battle_id),
            rounds=battle.rounds,
            dead=sum(item.died for item in battle.casualties),
            wounded=sum(not item.died for item in battle.casualties),
        )
    )
    # Home defenders tell their council at once; war parties only when survivors return.
    if home_side:
        _file_report(state, enemy, _battle_report(battle, enemy))
    for journey in defending_parties:
        held = not outcome.attackers_won
        journey = journey.model_copy(
            update={
                "battles": (*journey.battles, battle_id),
                **(
                    {}
                    if held
                    else {"phase": JourneyPhase.RETURNING, "outcome": JourneyOutcome.ROUTED}
                ),
            }
        )
        _replace_journey(state, journey)
    party = party.model_copy(update={"battles": (*party.battles, battle_id)})
    if not outcome.attackers_won:
        abandoned = engines_in(party.cargo)
        party = party.model_copy(
            update={
                "phase": JourneyPhase.RETURNING,
                "outcome": JourneyOutcome.ROUTED,
                "cargo": personal_kits(party.cargo),
            }
        )
        if abandoned:
            # A routed party leaves its engines behind; defenders at home take them in.
            if home_side:
                victor = state.civilizations[enemy]
                victor.inventory, _ = victor.inventory.store_with_waste(abandoned)
            events.append(
                _event(
                    state,
                    EventPhase.MOVEMENT,
                    "engines_abandoned",
                    str(sender),
                    str(battle_id),
                    engines=sum(abandoned.values()),
                    taken=bool(home_side),
                )
            )
        events.append(_event(state, EventPhase.MOVEMENT, "side_broke", str(sender), str(battle_id)))
    else:
        events.append(_event(state, EventPhase.MOVEMENT, "side_broke", str(enemy), str(battle_id)))
    _replace_journey(state, party)
    return party, events


def _room(state: WorldState, party: Journey) -> int:
    people = state.civilizations[party.sender_civilization_id].population.people
    living = sum(people[person_id].alive for person_id in party.traveller_ids)
    load = cargo_load(party.cargo) + party.provisions + sum(party.plunder.values())
    return max(
        min(CARGO_UNITS_PER_CARRIER * living, CARGO_UNITS_PER_CARRIER * len(party.traveller_ids))
        - load,
        0,
    )


def _plunder(
    state: WorldState, party: Journey, enemy: EntityId
) -> tuple[Journey, list[DomainEvent]]:
    """Raiders standing in an enemy settlement carry off what they can bear."""
    tile = party.route[party.route_index]
    victim = state.civilizations[enemy]
    if tile not in {settlement.tile for settlement in victim.settlements}:
        return party, []
    room = _room(state, party)
    taken: dict[Resource, int] = {}
    post = _post_at(state, enemy, tile)
    if post is not None and post.chest:
        chest = dict(post.chest)
        for resource in PLUNDER_ORDER:
            grab = min(chest.get(resource, 0), room)
            if grab:
                taken[resource] = taken.get(resource, 0) + grab
                chest[resource] -= grab
                room -= grab
        _replace_post(
            state,
            post.model_copy(update={"chest": {key: left for key, left in chest.items() if left}}),
            tile,
            enemy,
        )
    from_store: dict[Resource, int] = {}
    for resource in PLUNDER_ORDER:
        grab = min(victim.inventory.quantities.get(resource, 0), room)
        if grab:
            from_store[resource] = grab
            taken[resource] = taken.get(resource, 0) + grab
            room -= grab
    if from_store:
        victim.inventory = victim.inventory.apply_delta(
            InventoryDelta(changes={resource: -grab for resource, grab in from_store.items()})
        )
    plunder = dict(party.plunder)
    for resource, grab in taken.items():
        plunder[resource] = plunder.get(resource, 0) + grab
    party = party.model_copy(update={"plunder": {key: plunder[key] for key in sorted(plunder)}})
    _replace_journey(state, party)
    return party, [
        _event(
            state,
            EventPhase.MOVEMENT,
            "settlement_raided",
            str(party.sender_civilization_id),
            str(party.journey_id),
            victim=str(enemy),
            units=sum(taken.values()),
            q=tile.q,
            r=tile.r,
        )
    ]


def _ambush(state: WorldState, party: Journey) -> list[DomainEvent]:
    """Enemy convoys and travellers caught on a war party's tile lose their goods and flee."""
    tile = party.route[party.route_index]
    sender = party.sender_civilization_id
    events: list[DomainEvent] = []
    for journey in sorted(state.journeys, key=lambda item: item.journey_id):
        if (
            not journey.active
            or journey.kind is JourneyKind.CAMPAIGN
            or journey.phase is not JourneyPhase.OUTBOUND
            or journey.sender_civilization_id == sender
        ):
            continue
        owner = journey.sender_civilization_id
        people = state.civilizations[owner].population.people
        if not any(
            people[person_id].alive and people[person_id].location == tile
            for person_id in journey.traveller_ids
        ):
            continue
        war = _war_between(state, sender, owner)
        targeted = owner == party.recipient_civilization_id and party.phase is JourneyPhase.OUTBOUND
        if war is None and not targeted:
            continue
        if war is None:
            war, started = _start_war(state, sender, owner, declared=False)
            events.extend(started)
        events.extend(_learn_war(state, war, owner))
        seized: dict[Resource, int] = {}
        update: dict[str, object] = {
            "phase": JourneyPhase.RETURNING,
            "outcome": JourneyOutcome.AMBUSHED,
        }
        if journey.carrying_cargo:
            cargo = dict(journey.cargo)
            room = _room(state, party)
            for resource in PLUNDER_ORDER:
                grab = min(cargo.get(resource, 0), room)
                if grab:
                    seized[resource] = grab
                    cargo[resource] -= grab
                    room -= grab
            plunder = dict(party.plunder)
            for resource, grab in seized.items():
                plunder[resource] = plunder.get(resource, 0) + grab
            party = party.model_copy(
                update={"plunder": {key: plunder[key] for key in sorted(plunder)}}
            )
            _replace_journey(state, party)
            left = {key: value for key, value in cargo.items() if value}
            # What was not seized is carried home; if nothing is left, the record keeps the loss.
            update["cargo"] = left or seized
            update["carrying_cargo"] = bool(left)
        _replace_journey(state, journey.model_copy(update=update))
        events.append(
            _event(
                state,
                EventPhase.MOVEMENT,
                "convoy_ambushed",
                str(sender),
                str(journey.journey_id),
                victim=str(owner),
                units=sum(seized.values()),
                q=tile.q,
                r=tile.r,
            )
        )
    return events


def _resolve_war(state: WorldState, rng: StableRng) -> list[DomainEvent]:
    """Each war party fights whoever stands against it, ambushes convoys, and at its target
    fights for its objective; then it turns for home."""
    events: list[DomainEvent] = []
    for journey_id in sorted(
        item.journey_id
        for item in state.journeys
        if item.active and item.kind is JourneyKind.CAMPAIGN
    ):
        party = next(item for item in state.journeys if item.journey_id == journey_id)
        if not party.active:
            continue
        people = state.civilizations[party.sender_civilization_id].population.people
        if not any(people[person_id].alive for person_id in party.traveller_ids):
            continue
        tile = party.route[party.route_index]
        enemy = _enemy_at(state, party, tile)
        if enemy is not None and _defenders(state, enemy, tile) != ([], []):
            party, fought = _fight(state, party, enemy, rng)
            events.extend(fought)
        if party.phase is JourneyPhase.OUTBOUND:
            events.extend(_ambush(state, party))
            party = next(item for item in state.journeys if item.journey_id == journey_id)
        at_target = party.route_index == len(party.route) - 1
        if party.phase is JourneyPhase.OUTBOUND and at_target:
            target = party.recipient_civilization_id
            if _war_between(state, party.sender_civilization_id, target) is None:
                war, started = _start_war(
                    state, party.sender_civilization_id, target, declared=False
                )
                events.extend(started)
                events.extend(_learn_war(state, war, target))
            if party.objective is WarObjective.RAID:
                party, raided = _plunder(state, party, target)
                events.extend(raided)
            party = party.model_copy(
                update={"phase": JourneyPhase.RETURNING, "outcome": JourneyOutcome.DELIVERED}
            )
            _replace_journey(state, party)
    return events


def _war_party_home(state: WorldState, party: Journey) -> dict[Resource, int]:
    """Survivors bring home their axes, their plunder, and news of their battles."""
    sender = party.sender_civilization_id
    people = state.civilizations[sender].population.people
    goods = dict(party.plunder)
    issued = kit_assignment(list(party.traveller_ids), personal_kits(party.cargo))
    for person_id, kit in issued.items():
        if people[person_id].alive:
            goods[kit.resource] = goods.get(kit.resource, 0) + 1
    for resource, count in engines_in(party.cargo).items():
        goods[resource] = goods.get(resource, 0) + count
    battles = {battle.battle_id: battle for battle in state.battles}
    for battle_id in party.battles:
        _file_report(state, sender, _battle_report(battles[battle_id], sender))
    return goods


def _start_craft(
    state: WorldState, civilization_id: EntityId, command: DirectOrder
) -> DomainEvent:
    """Take the materials now; the workers then make the items day by day."""
    assert command.craft_item is not None
    civilization = state.civilizations[civilization_id]
    materials = craft_materials(command.craft_item, command.craft_quantity)
    job_id = EntityId(f"craft:{civilization_id}:{state.day}:{command.command_id}")
    if any(
        civilization.inventory.quantities.get(resource, 0) < quantity
        for resource, quantity in materials.items()
    ):
        return _event(state, EventPhase.WORK, "craft_unfunded", str(civilization_id), str(job_id))
    civilization.inventory = civilization.inventory.apply_delta(
        InventoryDelta(changes={resource: -quantity for resource, quantity in materials.items()})
    )
    job = CraftJob(
        job_id=job_id,
        item=command.craft_item,
        quantity=command.craft_quantity,
        worker_ids=tuple(sorted(command.worker_ids)),
        started_day=state.day,
        person_days_needed=RECIPES[command.craft_item].person_days * command.craft_quantity,
    )
    civilization.craft_jobs = (*civilization.craft_jobs, job)
    return _event(
        state,
        EventPhase.WORK,
        "craft_started",
        str(civilization_id),
        str(job_id),
        item=job.item.value,
        quantity=job.quantity,
    )


def _advance_crafting(state: WorldState) -> list[DomainEvent]:
    """Workers at their settlements put in a day each; finished items go into the store."""
    events: list[DomainEvent] = []
    away = _away(state)
    for civilization_id in sorted(state.civilizations):
        civilization = state.civilizations[civilization_id]
        homes = {settlement.tile for settlement in civilization.settlements}
        people = civilization.population.people
        kept: list[CraftJob] = []
        for job in civilization.craft_jobs:
            present = sum(
                people[person_id].alive
                and people[person_id].location in homes
                and person_id not in away
                for person_id in job.worker_ids
            )
            job = job.model_copy(update={"person_days_done": job.person_days_done + present})
            if not job.done:
                kept.append(job)
                continue
            civilization.inventory, waste = civilization.inventory.store_with_waste(
                {job.item: job.quantity}
            )
            events.append(
                _event(
                    state,
                    EventPhase.WORK,
                    "equipment_crafted",
                    str(civilization_id),
                    str(job.job_id),
                    item=job.item.value,
                    quantity=job.quantity - waste.get(job.item, 0),
                )
            )
        civilization.craft_jobs = tuple(kept)
    return events


def _advance_drills(state: WorldState) -> list[DomainEvent]:
    """Drilling people at their settlements gain arms slowly, up to the drill cap."""
    events: list[DomainEvent] = []
    away = _away(state)
    for civilization_id in sorted(state.civilizations):
        civilization = state.civilizations[civilization_id]
        homes = {settlement.tile for settlement in civilization.settlements}
        people = civilization.population.people
        kept: list[Drill] = []
        for drill in civilization.drills:
            present = [
                person_id
                for person_id in drill.person_ids
                if people[person_id].alive
                and people[person_id].location in homes
                and person_id not in away
            ]
            drill = drill.model_copy(update={"days_done": drill.days_done + 1})
            if drill.days_done % DRILL_DAYS_PER_POINT == 0:
                for person_id in present:
                    person = people[person_id]
                    current = person.skills.get(ARMS, 0)
                    if current < DRILL_CAP:
                        person.skills = {**person.skills, ARMS: current + 1}
            if drill.active:
                kept.append(drill)
            else:
                events.append(
                    _event(
                        state,
                        EventPhase.WORK,
                        "drill_completed",
                        str(civilization_id),
                        str(drill.drill_id),
                    )
                )
        civilization.drills = tuple(kept)
    return events


def _record_road(state: WorldState, built: RoadBuilt) -> DomainEvent:
    """Raise a tile's road by one grade; the crew that built it sees what it made."""
    existing = next((road for road in state.roads if road.tile == built.tile), None)
    road = Road(
        tile=built.tile,
        grade=built.grade,
        civilization_id=built.civilization_id,
        built_day=existing.built_day if existing is not None else state.day,
        graded_day=state.day,
    )
    state.roads = tuple(
        sorted(
            (*(item for item in state.roads if item.tile != built.tile), road),
            key=lambda item: item.tile,
        )
    )
    civilization = state.civilizations[built.civilization_id]
    journey = next(item for item in state.journeys if item.journey_id == built.journey_id)
    people = civilization.population.people
    observer = min(person_id for person_id in journey.traveller_ids if people[person_id].alive)
    observations = {item.tile: item for item in civilization.observations}
    observations[built.tile] = Observation(
        tile=built.tile,
        observed_day=state.day,
        observer_id=observer,
        observed_owner=state.territory.owner_of().get(built.tile),
        observed_road=built.grade,
    )
    civilization.observations = tuple(observations[tile] for tile in sorted(observations))
    civilization.known_tiles = tuple(sorted(observations))
    return _event(
        state,
        EventPhase.PROJECT,
        "road_built",
        str(built.civilization_id),
        _tile_id(built.tile),
        grade=built.grade.value,
        journey=str(built.journey_id),
        q=built.tile.q,
        r=built.tile.r,
    )


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
        grades_of(state.roads),
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
            elif isinstance(command, DirectOrder) and command.kind is DirectOrderKind.SET_TOLL:
                events.append(_set_toll(state, civilization_id, command))
            elif (
                isinstance(command, DirectOrder)
                and command.kind is DirectOrderKind.CRAFT_EQUIPMENT
                and command.craft_item is not None
            ):
                events.append(_start_craft(state, civilization_id, command))
            elif isinstance(command, DirectOrder) and command.kind is DirectOrderKind.DRILL:
                civilization = state.civilizations[civilization_id]
                drill = Drill(
                    drill_id=EntityId(f"drill:{civilization_id}:{state.day}:{command.command_id}"),
                    person_ids=tuple(sorted(command.worker_ids)),
                    started_day=state.day,
                    days=command.drill_days,
                )
                civilization.drills = (*civilization.drills, drill)
                events.append(
                    _event(
                        state,
                        EventPhase.WORK,
                        "drill_started",
                        str(civilization_id),
                        str(drill.drill_id),
                        people=len(drill.person_ids),
                        days=drill.days,
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
                        (
                            *civilization.teaching_assignments,
                            TeachingAssignment(
                                assignment_id=command.assignment_id,
                                teacher_id=command.teacher_id,
                                apprentice_id=command.apprentice_id,
                                capability=command.capability,
                                started_day=state.day,
                            ),
                        ),
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
                declared: EntityId | None = None
                if command.kind is DirectOrderKind.DECLARE_WAR:
                    # The war begins as the herald sets out; the enemy learns when he arrives.
                    war, started = _start_war(
                        state, civilization_id, command.recipient_civilization_id, declared=True
                    )
                    declared = war.war_id
                    events.extend(started)
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
                                declaration_of=declared,
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
                                    recipient_civilization_id=(command.recipient_civilization_id),
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
            roads=grades_of(candidate.roads),
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
        roads=grades_of(candidate.roads),
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
        if message.declaration_of is not None:
            declared_war = next(
                (war for war in candidate.wars if war.war_id == message.declaration_of), None
            )
            if declared_war is not None:
                events.extend(
                    _learn_war(candidate, declared_war, message.recipient_civilization_id)
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
                    treaty.treaty_id == offer.offer_id for treaty in candidate.active_treaties
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
                if offer.kind is TreatyKind.TRADE:
                    _share_maps(
                        candidate,
                        next(
                            item
                            for item in candidate.active_treaties
                            if item.treaty_id == offer.offer_id
                        ),
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
        events.append(_event(candidate, EventPhase.MOVEMENT, "message_lost", None, str(message_id)))

    journey_events, fed_on_the_road = _advance_journeys(candidate, rng)
    events.extend(journey_events)
    events.extend(_resolve_war(candidate, rng))
    events.extend(_advance_drills(candidate))
    events.extend(_advance_crafting(candidate))
    events.extend(_advance_tolls(candidate))

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
        # People at drill or in the armoury eat but neither farm nor do other work.
        drilling = {
            person_id for drill in civilization.drills for person_id in drill.person_ids
        } | {person_id for job in civilization.craft_jobs for person_id in job.worker_ids}
        home_living = tuple(
            person_id for person_id in civilization.population.living_ids if person_id not in away
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
            # People at drill eat but do not work the fields.
            workers = sum(person_id not in drilling for person_id in home_living)
            produced = min(workers, farm_capacity, target_food - current_food, capacity)
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
                if person_id not in away
                and person_id not in stationed
                and person_id not in drilling
            },
            civilization.inventory,
            civilization.projects,
        )
        civilization.inventory = work_result.inventory
        civilization.projects = work_result.projects
        for order_id in work_result.completed_order_ids:
            events.append(_event(candidate, EventPhase.WORK, "work_completed", str(order_id)))

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
            sum(person_id not in away for person_id in civilization.population.living_ids),
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
    events.extend(_joined_roads(candidate))

    candidate.day += 1
    validate_world(candidate)
    return TransitionResult(state=candidate, events=EventBatch.assign_sequences(events))
