"""Atomic daily transition pipeline."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from itertools import batched

import numpy as np

from sovereign_world.armoury import (
    BASIS,
    CATAPULT_HITS_BP,
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
from sovereign_world.bridges import Bridge, bridged_edges
from sovereign_world.capabilities import (
    CapabilityId,
    CapabilityRecord,
    KnowledgeState,
    TeachingAssignment,
    advance_knowledge_day,
)
from sovereign_world.commands import (
    CAMP_ORDERS,
    JOURNEY_ORDERS,
    MESSAGE_ORDERS,
    WALL_ORDERS,
    Decree,
    DirectOrder,
    DirectOrderKind,
    ProjectKind,
    build_council_report,
    crisis_council_due,
    journey_supplies,
    known_roads,
    known_spans,
    known_tolls,
    sight_of,
    trade_partners,
    validate_envelope,
    war_party_carry,
)
from sovereign_world.culture import ASSIMILATION_INTERVAL, ancestry, assimilate, culture
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
from sovereign_world.endings import (
    BREAKUP_GRACE_DAYS,
    BREAKUP_SHARE,
    Ending,
    EndingKind,
    Ruin,
    RuinView,
)
from sovereign_world.espionage import (
    COURIER_CAUGHT_BP,
    SPYCRAFT,
    WATCH_CAUGHT_BP,
    CaughtSpy,
    SpyReport,
    caught_chance_bp,
    observe,
)
from sovereign_world.events import DomainEvent, EventBatch, EventPhase
from sovereign_world.exploration import (
    Expedition,
    ExpeditionStatus,
    Observation,
    advance_expeditions,
)
from sovereign_world.hexmap import HexCoord, WorldMap
from sovereign_world.housing import (
    ABANDONED_DECAY_DAYS,
    ABANDONED_GRACE_DAYS,
    STORMED_SHARE,
    HouseJob,
    Housing,
    affordable_grade,
    best_grade,
    founding_housing,
    house_materials,
    residents_by_settlement,
    slots_of,
)
from sovereign_world.ids import EntityId
from sovereign_world.institutions import (
    ARMOURY_DAY,
    HALL_STRENGTH,
    HEALING_FACTOR,
    INSTITUTIONS,
    SCHOOL_TEACHING_DAYS,
    TRAINING_CAP_BONUS,
    WORKSHOP_DAY,
    Institution,
    InstitutionKind,
    serving_tiles,
    staff_of,
)
from sovereign_world.land import food_capacity, stone_capacity, timber_capacity
from sovereign_world.languages import LEARNING_INTERVAL, learn, native
from sovereign_world.logistics import (
    CARGO_UNITS_PER_CARRIER,
    INTERNAL_KINDS,
    MAX_TRAVELLERS,
    SPYING_KINDS,
    TRAVEL_HAZARD_CAUSE,
    BridgeBuilt,
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
from sovereign_world.people import (
    SETTLING_DAYS,
    AllegianceChange,
    Person,
    advance_population_day,
    go_hungry,
    recover,
)
from sovereign_world.ranks import (
    CITY_RESEARCH_BONUS,
    CIVIL_HALF_RATE_RANK,
    RealmRank,
    SettlementRank,
    institutions_open,
    next_realm_rank,
    next_settlement_rank,
    realm_at_least,
    settlement_facts,
)
from sovereign_world.research import (
    CIVIL_TOPICS,
    DISCOVERED_SKILL,
    DOCTRINE_DRILL_CAP,
    POINTS_PER_SCHOLAR,
    TOPICS,
    WRITING_BONUS,
    ResearchAssignment,
    knows,
)
from sovereign_world.resources import Inventory, InventoryDelta, Resource
from sovereign_world.rng import StableRng
from sovereign_world.roads import Road, RoadView, grade_below, grades_of
from sovereign_world.rules import rules_for
from sovereign_world.scripted import Sovereign
from sovereign_world.sites import (
    FIND_KINDS,
    FINDS,
    PRODUCT,
    RUIN_LORE,
    YIELD_PER_WORKER_DAY,
    SiteKind,
)
from sovereign_world.state import WorldState, validate_world
from sovereign_world.stores import (
    STOREHOUSE_GRADES,
    Storehouse,
    StorehouseGrade,
    StorehouseJob,
    enlarge,
    has,
    holdings,
    put,
    set_store,
    settlement_at,
    shrink,
    step_materials,
    store,
    store_at,
    store_id_at,
    supplying,
    take,
)
from sovereign_world.stores import grade_below as storehouse_grade_below
from sovereign_world.territory import (
    SETTLEMENT_SPACING,
    Claim,
    Garrison,
    Settlement,
    advance_territory,
    visible_tiles,
)
from sovereign_world.tolls import TollGate, TollPost, TollRules, TollView
from sovereign_world.travel import crossing, travel_days, way_to
from sovereign_world.walls import (
    WALL_GRADES,
    WALL_HIT,
    WallJob,
    Walls,
    battered,
    manned_towers,
    repair_materials,
    tower_materials,
    wall_bonus_after_engines,
)
from sovereign_world.walls import step_materials as wall_step_materials
from sovereign_world.war import (
    ARMS,
    BATTLE_CAP,
    BATTLE_SURVIVED_POINTS,
    BATTLE_WON_POINTS,
    DRILL_CAP,
    DRILL_DAYS_PER_POINT,
    ESCAPE_BP,
    MIN_BESIEGERS,
    RISING_RATIO,
    SETTLEMENT_DEFENCE_BP,
    WOUND_MAX,
    WOUND_MIN,
    Battle,
    BattleReport,
    Drill,
    Occupation,
    OccupationEnd,
    Siege,
    SiegeEnd,
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
    *,
    told: bool = False,
) -> ActiveTreaty | None:
    """End a treaty still in force; an already-ended treaty keeps its first ending.

    The other party knows at once only when `told`; otherwise it learns later.
    """
    treaty = next((item for item in state.active_treaties if item.treaty_id == treaty_id), None)
    if treaty is None or not treaty.in_force:
        return None
    ended = treaty.ended(state.day, kind, by)
    if told:
        ended = ended.model_copy(update={"notice_day": state.day})
    state.active_treaties = tuple(
        ended if item.treaty_id == treaty_id else item for item in state.active_treaties
    )
    return ended


def _tell_treaty_ends(state: WorldState, learner: EntityId, other: EntityId) -> None:
    """The learner hears that treaties the other party ended with it are over."""
    state.active_treaties = tuple(
        treaty.model_copy(update={"notice_day": state.day})
        if treaty.ended_by == other
        and treaty.notice_day is None
        and {learner, other} == {treaty.proposer_civilization_id, treaty.recipient_civilization_id}
        else treaty
        for treaty in state.active_treaties
    )


def _store_provisions(
    state: WorldState, civilization_id: EntityId, tile: HexCoord, units: int
) -> int:
    """Put a party's leftover food into the store it reached; return what fitted."""
    if not units:
        return 0
    waste = put(state.civilizations[civilization_id], tile, {Resource.FOOD: units})
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
    if journey.kind is JourneyKind.SETTLEMENT:
        settlement = Settlement(
            settlement_id=_next_settlement_id(state, civilization_id),
            civilization_id=civilization_id,
            tile=destination,
            founded_day=state.day,
        )
        civilization.settlements = tuple(
            sorted((*civilization.settlements, settlement), key=lambda item: item.settlement_id)
        )
        if rules_for(state.rules_version).houses:
            # Settlers raise huts as they arrive.
            civilization.housing = dict(
                sorted(
                    {
                        **civilization.housing,
                        settlement.settlement_id: founding_housing(len(arrivals)),
                    }.items()
                )
            )
        ruin = next((item for item in state.ruins if item.tile == destination), None)
        if ruin is not None:
            _resettle(state, civilization_id, settlement.settlement_id, ruin)
    # Settlers' leftover food is their new settlement's first store.
    stored = _store_provisions(state, civilization_id, destination, provisions)
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
    JourneyKind.HAUL: "haul_dispatched",
    JourneyKind.PETITION: "people_released",
    JourneyKind.SALVAGE: "salvagers_dispatched",
    JourneyKind.SPY: "spies_dispatched",
    JourneyKind.EXTRACTION: "extractors_dispatched",
}
RETURNED_EVENT = {
    JourneyKind.ROADWORK: "road_crew_returned",
    JourneyKind.DEPOSIT: "toll_couriers_returned",
    JourneyKind.CAMPAIGN: "war_party_returned",
    JourneyKind.HAUL: "haulers_returned",
    JourneyKind.PETITION: "petitioners_returned",
    JourneyKind.SALVAGE: "salvagers_returned",
    JourneyKind.SPY: "spies_returned",
    JourneyKind.EXTRACTION: "extractors_returned",
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
    if not has(civilization, command.route[0], taken):
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
    take(civilization, command.route[0], taken)
    journey = Journey(
        journey_id=command.journey_id,
        kind=kind,
        treaty_id=None if internal else command.treaty_id,
        sender_civilization_id=civilization_id,
        recipient_civilization_id=recipient_id,
        traveller_ids=tuple(sorted(command.traveller_ids)),
        route=command.route,
        cargo=cargo,
        carrying_cargo=kind in {JourneyKind.SHIPMENT, JourneyKind.HAUL},
        provisions_packed=provisions,
        provisions=provisions,
        departed_day=state.day,
        objective=command.war_objective if kind is JourneyKind.CAMPAIGN else None,
        watch_days=command.watch_days if kind is JourneyKind.SPY else 0,
        work_days=command.work_days if kind is JourneyKind.EXTRACTION else 0,
        wreck_roads=command.wreck_roads and kind is JourneyKind.CAMPAIGN,
        carry_per_person=(
            war_party_carry(civilization)
            if kind is JourneyKind.CAMPAIGN
            else CARGO_UNITS_PER_CARRIER
        ),
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


def _next_settlement_id(state: WorldState, civilization_id: EntityId) -> EntityId:
    """A new settlement's id: one past the highest this civilization ever founded, wherever
    those settlements now belong."""
    prefix = f"settlement:{civilization_id.rsplit(':', 1)[-1]}-"
    numbers = [
        int(item.settlement_id.removeprefix(prefix))
        for civilization in state.civilizations.values()
        for item in civilization.settlements
        if item.settlement_id.startswith(prefix)
    ]
    return EntityId(f"{prefix}{max(numbers, default=0) + 1:04d}")


def _transfer_migrants(state: WorldState, journey: Journey) -> tuple[EntityId, ...]:
    """Move living arrivals, with their history and any pregnancy, to the new civilization."""
    origin = state.civilizations[journey.sender_civilization_id]
    arrivals = tuple(
        person_id
        for person_id in journey.traveller_ids
        if person_id in origin.population.people and origin.population.people[person_id].alive
    )
    _change_allegiance(
        state,
        arrivals,
        journey.sender_civilization_id,
        journey.recipient_civilization_id,
        "migration",
    )
    return arrivals


def _change_allegiance(
    state: WorldState,
    person_ids: tuple[EntityId, ...],
    origin_id: EntityId,
    destination_id: EntityId,
    reason: str,
) -> None:
    """Move people to another civilization with their whole life: id, family, health and
    any pregnancy. A quarter of each skill is held back for a year while they settle."""
    origin = state.civilizations[origin_id]
    destination = state.civilizations[destination_id]
    _release_duties(state, origin_id, frozenset(person_ids))
    origin_people = origin.population.people
    destination_people = destination.population.people
    for person_id in person_ids:
        person = origin_people.pop(person_id)
        held = {skill: value // 4 for skill, value in person.skills.items() if value // 4}
        destination_people[person_id] = person.model_copy(
            update={
                "civilization_id": destination_id,
                "native_language": native(person),
                # A newcomer keeps their culture until they assimilate; one coming home
                # is simply home again.
                "culture": None if culture(person) == destination_id else culture(person),
                "assimilation": 0,
                "ancestry": ancestry(person),
                "skills": {
                    skill: value - held.get(skill, 0) for skill, value in person.skills.items()
                },
                "held_skills": {
                    skill: person.held_skills.get(skill, 0) + held.get(skill, 0)
                    for skill in sorted({*person.held_skills, *held})
                },
                "settled_day": state.day + SETTLING_DAYS,
                "allegiances": (
                    *person.allegiances,
                    AllegianceChange(
                        day=state.day,
                        from_civilization_id=origin_id,
                        to_civilization_id=destination_id,
                        reason=reason,
                    ),
                ),
            }
        )
    moving = set(person_ids)
    following_mother = tuple(
        birth for birth in origin.population.scheduled_births if birth.parent_ids[0] in moving
    )
    origin.population = origin.population.model_copy(
        update={
            "scheduled_births": tuple(
                birth
                for birth in origin.population.scheduled_births
                if birth not in following_mother
            ),
        }
    )
    destination.population = destination.population.model_copy(
        update={
            "scheduled_births": tuple(
                sorted(
                    (*destination.population.scheduled_births, *following_mother),
                    key=lambda birth: (birth.due_day, birth.parent_ids),
                )
            ),
        }
    )


def _release_duties(
    state: WorldState, civilization_id: EntityId, leaving: frozenset[EntityId]
) -> None:
    """People leaving a civilization drop out of its drills, workshops, studies and garrisons."""
    if not leaving:
        return
    civilization = state.civilizations[civilization_id]
    civilization.drills = tuple(
        drill.model_copy(update={"person_ids": kept})
        for drill in civilization.drills
        if (kept := tuple(item for item in drill.person_ids if item not in leaving))
    )
    civilization.craft_jobs = tuple(
        job.model_copy(update={"worker_ids": kept})
        for job in civilization.craft_jobs
        if (kept := tuple(item for item in job.worker_ids if item not in leaving))
    )
    civilization.research = tuple(
        assignment.model_copy(update={"scholar_ids": kept})
        for assignment in civilization.research
        if (kept := tuple(item for item in assignment.scholar_ids if item not in leaving))
    )
    civilization.storehouse_jobs = tuple(
        job.model_copy(update={"worker_ids": kept})
        for job in civilization.storehouse_jobs
        if (kept := tuple(item for item in job.worker_ids if item not in leaving))
    )
    civilization.wall_jobs = tuple(
        job.model_copy(update={"worker_ids": kept})
        for job in civilization.wall_jobs
        if (kept := tuple(item for item in job.worker_ids if item not in leaving))
    )
    civilization.teaching_assignments = tuple(
        assignment
        for assignment in civilization.teaching_assignments
        if assignment.teacher_id not in leaving and assignment.apprentice_id not in leaving
    )
    civilization.institutions = tuple(
        institution.model_copy(
            update={
                "staff_ids": tuple(item for item in institution.staff_ids if item not in leaving)
            }
        )
        for institution in civilization.institutions
    )
    _leave_garrisons(state, civilization_id, leaving)


def _settle_newcomers(state: WorldState) -> None:
    """A year after changing civilization, a person has their held-back skill again."""
    for civilization in state.civilizations.values():
        for person in civilization.population.people.values():
            if person.settled_day is not None and state.day >= person.settled_day:
                person.skills = {
                    skill: person.skills.get(skill, 0) + person.held_skills.get(skill, 0)
                    for skill in sorted({*person.skills, *person.held_skills})
                }
                person.held_skills = {}
                person.settled_day = None


def _cede(state: WorldState, treaty: ActiveTreaty) -> list[DomainEvent]:
    """A ceded settlement changes hands with its store, buildings, walls, gate and people."""
    terms = treaty.terms
    if terms is None or terms.ceded_settlement is None:
        return []
    sides = (treaty.proposer_civilization_id, treaty.recipient_civilization_id)
    giver_id = next(
        (
            side
            for side in sides
            if any(
                item.settlement_id == terms.ceded_settlement
                for item in state.civilizations[side].settlements
            )
        ),
        None,
    )
    if giver_id is None:
        return []
    taker_id = treaty.counterparty(giver_id)
    giver, taker = state.civilizations[giver_id], state.civilizations[taker_id]
    settlement = next(
        item for item in giver.settlements if item.settlement_id == terms.ceded_settlement
    )
    if settlement.capital:
        return []
    sid, tile = settlement.settlement_id, settlement.tile
    away = _away(state)
    residents = tuple(
        sorted(
            person_id
            for person_id, person in giver.population.people.items()
            if person.alive
            and person.location == tile
            and person_id not in away
            and person.captive_of is None
        )
    )
    held_there = tuple(
        person_id
        for other in state.civilizations.values()
        for person_id, person in other.population.people.items()
        if person.alive and person.captive_of == giver_id and person.held_at == sid
    )
    events = _free_captives(state, held_there, "released")
    for job in giver.house_jobs:
        if job.settlement_id == sid:
            put(giver, tile, job.unused_materials())
    giver.house_jobs = tuple(item for item in giver.house_jobs if item.settlement_id != sid)
    inventory = store(giver, sid)
    giver.settlements = tuple(item for item in giver.settlements if item.settlement_id != sid)
    giver.stores = {key: value for key, value in giver.stores.items() if key != sid}
    taker.settlements = tuple(
        sorted(
            (*taker.settlements, settlement.model_copy(update={"civilization_id": taker_id})),
            key=lambda item: item.settlement_id,
        )
    )
    taker.stores = {**taker.stores, sid: inventory}
    taker.storehouses = tuple(
        sorted(
            (
                *taker.storehouses,
                *(item for item in giver.storehouses if item.settlement_id == sid),
            ),
            key=lambda item: item.storehouse_id,
        )
    )
    giver.storehouses = tuple(item for item in giver.storehouses if item.settlement_id != sid)
    taker.walls = tuple(
        sorted(
            (*taker.walls, *(item for item in giver.walls if item.settlement_id == sid)),
            key=lambda item: item.settlement_id,
        )
    )
    giver.walls = tuple(item for item in giver.walls if item.settlement_id != sid)
    if sid in giver.housing:
        taker.housing = dict(sorted({**taker.housing, sid: giver.housing[sid]}.items()))
        giver.housing = {key: value for key, value in giver.housing.items() if key != sid}
    if sid in giver.ranks_reached:
        taker.ranks_reached = dict(
            sorted({**taker.ranks_reached, sid: giver.ranks_reached[sid]}.items())
        )
        giver.ranks_reached = {
            key: value for key, value in giver.ranks_reached.items() if key != sid
        }
    giver.storehouse_jobs = tuple(
        item for item in giver.storehouse_jobs if item.settlement_id != sid
    )
    giver.wall_jobs = tuple(item for item in giver.wall_jobs if item.settlement_id != sid)
    giver.craft_jobs = tuple(item for item in giver.craft_jobs if item.workshop != tile)
    # The gate at the settlement passes over; posts that emptied their chests here lapse,
    # and what their chests held goes into the settlement's store.
    posts: list[TollPost] = []
    for post in giver.toll_posts:
        if post.tile == tile:
            taker.toll_posts = tuple(
                sorted(
                    (*taker.toll_posts, post.model_copy(update={"civilization_id": taker_id})),
                    key=lambda item: item.tile,
                )
            )
        elif post.deposit_route[-1] == tile:
            if post.chest:
                put(taker, tile, post.chest)
        else:
            posts.append(post)
    giver.toll_posts = tuple(posts)
    _change_allegiance(state, residents, giver_id, taker_id, "cession")
    events.append(
        _event(
            state,
            EventPhase.MOVEMENT,
            "settlement_ceded",
            str(giver_id),
            str(sid),
            to=str(taker_id),
            people=len(residents),
        )
    )
    for capability in _adopt_migrant_capabilities(state, taker_id, residents):
        events.append(
            _event(
                state,
                EventPhase.WORK,
                "capability_learned",
                str(taker_id),
                capability=capability.value,
                source="cession",
            )
        )
    return events


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
        forage_bonus=rules_for(state.rules_version).cover_mechanics,
        arrival_allowed=lambda journey: _arrival_allowed(state, journey),
        roads=grades_of(state.roads),
        tolls=_toll_rules(state),
        halts=lambda journey, tile: (
            journey.kind is JourneyKind.CAMPAIGN
            and (_enemy_at(state, journey, tile) is not None or _wreckable(state, journey, tile))
        ),
        bridges=bridged_edges(state.bridges),
    )
    state.journeys = result.journeys
    for civilization_id, people in result.people_by_civilization.items():
        civilization = state.civilizations[civilization_id]
        civilization.population.people.update(people)
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
    for journey in result.journeys:
        if journey.journey_id in result.perished_ids and journey.captive_ids:
            events.extend(_free_captives(state, journey.captive_ids, "rescued"))
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
    for spanned in result.bridges_built:
        events.append(_record_bridge(state, spanned))
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
        if journey.kind is JourneyKind.PETITION:
            events.append(
                _event(
                    state,
                    EventPhase.MOVEMENT,
                    "petition_arrived",
                    str(journey.recipient_civilization_id),
                    str(journey.journey_id),
                    sender=str(journey.sender_civilization_id),
                    people=len(journey.traveller_ids),
                )
            )
            continue
        if journey.kind is JourneyKind.DEPOSIT:
            events.extend(_deposit_arrival(state, journey))
            continue
        if journey.kind is JourneyKind.HAUL:
            events.extend(_haul_arrival(state, journey))
            continue
        if journey.kind is JourneyKind.SALVAGE:
            events.extend(_salvage(state, journey))
            continue
        if journey.kind is JourneyKind.EXTRACTION:
            events.append(
                _event(
                    state,
                    EventPhase.MOVEMENT,
                    "extractors_at_work",
                    str(journey.sender_civilization_id),
                    str(journey.journey_id),
                )
            )
            continue
        if journey.kind is JourneyKind.SPY:
            events.append(
                _event(
                    state,
                    EventPhase.MOVEMENT,
                    "spies_on_watch",
                    str(journey.sender_civilization_id),
                    str(journey.journey_id),
                )
            )
            continue
        if journey.kind is JourneyKind.COURIER:
            _store_provisions(
                state,
                journey.sender_civilization_id,
                journey.route[-1],
                result.handed_over.get(journey.journey_id, 0),
            )
            events.append(_file_spy_report(state, journey, by_courier=True))
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
            waste = put(recipient, journey.route[-1], journey.cargo)
            accepted = {
                resource: quantity - waste.get(resource, 0)
                for resource, quantity in journey.cargo.items()
                if quantity - waste.get(resource, 0) > 0
            }
            events.extend(_count_tribute(state, journey))
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
            state, recipient_id, journey.route[-1], result.handed_over.get(journey.journey_id, 0)
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
        # Turning a party away at the gate shows that the treaty it came under is over.
        _tell_treaty_ends(state, journey.recipient_civilization_id, journey.sender_civilization_id)
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
        if journey.outcome is JourneyOutcome.REFUSED:
            # A party sent home from the other's gate brings word the treaty is over.
            _tell_treaty_ends(state, sender_id, journey.recipient_civilization_id)
        restored: dict[Resource, int] = {}
        brought_home = journey.cargo if journey.journey_id in cargo_home else journey.materials
        if journey.kind is JourneyKind.CAMPAIGN:
            brought_home = _war_party_home(state, journey)
            home_settlement = supplying(state.civilizations[sender_id], journey.route[0])
            for person_id in journey.captive_ids:
                captive = _captive(state, person_id)
                if captive is not None and captive.captive_of is not None and home_settlement:
                    captive.held_at = home_settlement.settlement_id
                    captive.location = home_settlement.tile
        if journey.kind is JourneyKind.DEPOSIT and brought_home:
            # Couriers turned back on the way carry the chest back to their post.
            _fill_chest(state, sender_id, journey.route[0], brought_home)
            restored = dict(brought_home)
            brought_home = {}
        if brought_home:
            waste = put(state.civilizations[sender_id], journey.route[0], brought_home)
            restored = {
                resource: quantity - waste.get(resource, 0)
                for resource, quantity in brought_home.items()
                if quantity - waste.get(resource, 0) > 0
            }
        provisions = _store_provisions(
            state, sender_id, journey.route[0], result.handed_over.get(journey.journey_id, 0)
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
        if journey.kind is JourneyKind.SPY and survivors:
            spies = state.civilizations[sender_id].population.people
            for person_id in survivors:
                spies[person_id].skills = {
                    **spies[person_id].skills,
                    SPYCRAFT: spies[person_id].skills.get(SPYCRAFT, 0) + 1,
                }
            if journey.findings is not None:
                events.append(_file_spy_report(state, journey, by_courier=False))
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
        known_bridges={
            civilization_id: known_spans(state, civilization_id)
            for civilization_id in state.civilizations
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
    """Put takings in a post's chest, or straight into the store of a settlement's gate."""
    post = _post_at(state, owner, tile)
    if post is None or post.at_storehouse:
        put(state.civilizations[owner], tile, goods)
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
    waste = put(state.civilizations[owner], journey.route[-1], journey.cargo)
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


def _haul_arrival(state: WorldState, journey: Journey) -> list[DomainEvent]:
    """Carriers reach the other settlement: the goods go into its store, and they turn home."""
    owner = journey.sender_civilization_id
    waste = put(state.civilizations[owner], journey.route[-1], journey.cargo)
    stored = {
        resource: quantity - waste.get(resource, 0)
        for resource, quantity in journey.cargo.items()
        if quantity - waste.get(resource, 0) > 0
    }
    _add_notice(
        state, owner, notice(state.day, NoticeKind.GOODS_HAULED, journey, owner, cargo=stored)
    )
    return [
        _event(
            state,
            EventPhase.MOVEMENT,
            "goods_hauled",
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
                JourneyKind.DEPOSIT,
                state.world_map,
                post.deposit_route,
                grades_of(state.roads),
                bridges=bridged_edges(state.bridges),
            ),
            len(couriers),
        )
        if couriers
        else 0
    )
    room = CARGO_UNITS_PER_CARRIER * len(couriers) - provisions
    # Couriers are provisioned by the settlement that supplies the post.
    food = store_at(civilization, post.tile).quantities.get(Resource.FOOD, 0)
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
    take(civilization, post.tile, {Resource.FOOD: provisions})
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
    spans = bridged_edges(state.bridges)
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
                    # A road is continuous only where a traveller can cross: a deep river
                    # between two road tiles joins them only once it is bridged.
                    if crossing(state.world_map, tile, neighbor, spans) is None:
                        continue
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
    # War is news enough that every treaty the aggressor broke with it is over.
    _tell_treaty_ends(state, civilization_id, war.aggressor_id)
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
            person.alive and person.location == tile and person.captive_of is None
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
        if person.location == tile
        and able_to_fight(person)
        and person_id not in busy
        and person.captive_of is None
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
        own_captured=tuple(person_id for person_id in battle.captured if person_id in own),
    )


def _file_report(state: WorldState, civilization_id: EntityId, report: BattleReport) -> None:
    civilization = state.civilizations[civilization_id]
    reports = {item.battle_id: item for item in civilization.war_reports}
    reports[report.battle_id] = report
    civilization.war_reports = tuple(reports[key] for key in sorted(reports))
    # The council now knows which of its people were taken, and holds them as captives.
    civilization.known_captives = tuple(
        sorted({*civilization.known_captives, *report.own_captured})
    )


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
    issued = kit_assignment(
        attacker_ids,
        personal_kits(party.cargo),
        formations=knows(state.civilizations[sender].capabilities, CapabilityId.SPEAR_FORMATIONS),
    )
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
    supplies = store_at(state.civilizations[enemy], tile).quantities
    defending_parties = [
        journey
        for journey in state.journeys
        if journey.active
        and journey.kind is JourneyKind.CAMPAIGN
        and journey.sender_civilization_id == enemy
        and any(person_id in marching for person_id in journey.traveller_ids)
    ]
    # Home defenders arm from their store; defending war parties use what they carry.
    formations = knows(state.civilizations[enemy].capabilities, CapabilityId.SPEAR_FORMATIONS)
    defender_kits = kit_assignment(home_side, personal_kits(dict(supplies)), formations=formations)
    for journey in defending_parties:
        defender_kits.update(
            kit_assignment(
                [item for item in journey.traveller_ids if item in marching],
                personal_kits(journey.cargo),
                formations=formations,
            )
        )
    # Each tower needs two home defenders to man it; they shoot from it and still fight.
    walls = _walls_at(state, enemy, tile) if at_home else None
    towers = manned_towers(walls.towers, len(home_side)) if walls is not None else 0
    defenders = [
        fighter(enemy_people[person_id], defender_kits.get(person_id), attacking=False)
        for person_id in [*home_side, *marching]
    ]
    terrain_bp = defence_bonus_bp(state.world_map.tile(tile).terrain, settlement=False)
    walls_bp = (
        settlement_bonus_after_engines(SETTLEMENT_DEFENCE_BP, working)
        * wall_bonus_after_engines(walls.grade if walls is not None else None, working)
        // BASIS
        if at_home
        else BASIS
    )
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
        towers=towers,
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
        captured=outcome.captured,
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
                    else {
                        "phase": JourneyPhase.RETURNING,
                        "outcome": JourneyOutcome.ROUTED,
                        "encamped": False,
                    }
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
                "encamped": False,
            }
        )
        if abandoned:
            # A routed party leaves its engines behind; defenders at home take them in.
            if home_side:
                put(state.civilizations[enemy], tile, abandoned)
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
        stormed = settlement_at(state.civilizations[enemy], tile) if at_home else None
        if stormed is not None:
            events.extend(_lose_houses(state, enemy, stormed.settlement_id, "stormed"))
    _replace_journey(state, party)
    # A routed party lets its prisoners go; then the winners take their own captives.
    routed = [
        journey
        for journey in state.journeys
        if journey.outcome is JourneyOutcome.ROUTED
        and journey.captive_ids
        and battle_id in journey.battles
    ]
    for journey in routed:
        events.extend(_free_captives(state, journey.captive_ids, "rescued"))
    events.extend(_take_captives(state, battle, at_home))
    party = next(item for item in state.journeys if item.journey_id == party.journey_id)
    return party, events


def _room(state: WorldState, party: Journey) -> int:
    people = state.civilizations[party.sender_civilization_id].population.people
    living = sum(people[person_id].alive for person_id in party.traveller_ids)
    load = cargo_load(party.cargo) + party.provisions + sum(party.plunder.values())
    return max(
        party.carry_per_person * min(living, len(party.traveller_ids)) - load,
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
    # Raiders empty the store of the settlement they beat, never the whole civilization's.
    from_store: dict[Resource, int] = {}
    for resource in PLUNDER_ORDER:
        grab = min(store_at(victim, tile).quantities.get(resource, 0), room)
        if grab:
            from_store[resource] = grab
            taken[resource] = taken.get(resource, 0) + grab
            room -= grab
    take(victim, tile, from_store)
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
            or journey.watching
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


def _captive(state: WorldState, person_id: EntityId) -> Person | None:
    for civilization in state.civilizations.values():
        person = civilization.population.people.get(person_id)
        if person is not None:
            return person
    return None


def _take_captives(state: WorldState, battle: Battle, at_home: bool) -> list[DomainEvent]:
    """The winners hold the fighters they caught: at home, or marching with the party."""
    if not battle.captured:
        return []
    captor = battle.winner_id
    taken = set(battle.captured)
    # Captured fighters leave the party they marched with; a party caught whole is ended.
    journeys: list[Journey] = []
    for journey in state.journeys:
        if journey.active and taken & set(journey.traveller_ids):
            left = tuple(item for item in journey.traveller_ids if item not in taken)
            journey = (
                journey.model_copy(update={"traveller_ids": left})
                if left
                else journey.model_copy(
                    update={
                        "phase": JourneyPhase.COMPLETE,
                        "outcome": JourneyOutcome.ROUTED,
                        "completed_day": state.day,
                        "encamped": False,
                    }
                )
            )
        journeys.append(journey)
    state.journeys = tuple(journeys)
    settlement = settlement_at(state.civilizations[captor], battle.tile)
    keepers = [
        journey
        for journey in state.journeys
        if journey.active
        and journey.kind is JourneyKind.CAMPAIGN
        and journey.sender_civilization_id == captor
        and journey.route[journey.route_index] == battle.tile
        and battle.battle_id in journey.battles
    ]
    held_at = settlement.settlement_id if settlement is not None and at_home else None
    if held_at is None and not keepers:
        # With nowhere to keep them, the captives are let go.
        return _free_captives(state, battle.captured, "released")
    for person_id in battle.captured:
        person = _captive(state, person_id)
        assert person is not None
        person.captive_of = captor
        person.held_at = held_at
    if held_at is None:
        keeper = keepers[0]
        _replace_journey(
            state,
            keeper.model_copy(
                update={"captive_ids": tuple(sorted({*keeper.captive_ids, *battle.captured}))}
            ),
        )
    return [
        _event(
            state,
            EventPhase.MOVEMENT,
            "captured",
            str(captor),
            str(person_id),
            battle=str(battle.battle_id),
        )
        for person_id in battle.captured
    ]


def _truth(state: WorldState, target_id: EntityId, settlement: Settlement) -> dict[str, int]:
    """What there is to see at a settlement today."""
    target = state.civilizations[target_id]
    residents = [
        person
        for person in target.population.people.values()
        if person.alive and person.captive_of is None and person.location == settlement.tile
    ]
    return {
        "residents": len(residents),
        "fighters": sum(1 for person in residents if able_to_fight(person)),
        "store_units": store(target, settlement.settlement_id).total_units,
        "works": sum(
            1 for project in target.projects.values() if project.location == settlement.tile
        )
        + sum(1 for job in target.storehouse_jobs if job.settlement_id == settlement.settlement_id)
        + sum(1 for job in target.wall_jobs if job.settlement_id == settlement.settlement_id),
    }


def _catch(
    state: WorldState, journey: Journey, target_id: EntityId, settlement: Settlement
) -> list[DomainEvent]:
    """Spies or a courier found out: held prisoner, their findings lost, their sender known."""
    target = state.civilizations[target_id]
    people = state.civilizations[journey.sender_civilization_id].population.people
    caught = tuple(
        person_id
        for person_id in journey.traveller_ids
        if person_id in people and people[person_id].alive
    )
    events: list[DomainEvent] = []
    for person_id in caught:
        person = people[person_id]
        person.captive_of = target_id
        person.held_at = settlement.settlement_id
        person.location = settlement.tile
        target.caught_spies = (
            *target.caught_spies,
            CaughtSpy(
                day=state.day,
                person_id=person_id,
                sender_civilization_id=journey.sender_civilization_id,
                settlement_id=settlement.settlement_id,
            ),
        )
        events.append(
            _event(
                state,
                EventPhase.MOVEMENT,
                "spy_caught",
                str(target_id),
                str(person_id),
                sender=str(journey.sender_civilization_id),
                journey=str(journey.journey_id),
                courier=journey.kind is JourneyKind.COURIER,
            )
        )
    _replace_journey(
        state,
        journey.model_copy(
            update={
                "phase": JourneyPhase.COMPLETE,
                "outcome": JourneyOutcome.CAUGHT,
                "completed_day": state.day,
                "watching": False,
                "findings": journey.findings if journey.kind is JourneyKind.COURIER else None,
                "provisions": 0,
            }
        ),
    )
    return events


def _advance_extraction(state: WorldState) -> list[DomainEvent]:
    """Rules version 2: workers at a deposit or quarry take what they can each day, and turn
    home when their days are done, their packs are full, the site is spent, or none is left."""
    events: list[DomainEvent] = []
    sites = {site.tile: site for site in state.sites}
    for journey in sorted(state.journeys, key=lambda item: item.journey_id):
        if not journey.working:
            continue
        people = state.civilizations[journey.sender_civilization_id].population.people
        living = sum(
            (person := people.get(person_id)) is not None and person.alive
            for person_id in journey.traveller_ids
        )
        site = sites[journey.route[-1]]
        room = (
            journey.carry_per_person * len(journey.traveller_ids)
            - journey.provisions_packed
            - sum(journey.cargo.values())
        )
        taken = min(living * YIELD_PER_WORKER_DAY[site.kind], site.remaining, max(room, 0))
        cargo = dict(journey.cargo)
        if taken:
            product = PRODUCT[site.kind]
            cargo[product] = cargo.get(product, 0) + taken
            site = site.model_copy(
                update={
                    "remaining": site.remaining - taken,
                    "opened_day": site.opened_day if site.opened_day is not None else state.day,
                    "spent_day": state.day if site.remaining == taken else None,
                }
            )
            sites[site.tile] = site
            events.append(
                _event(
                    state,
                    EventPhase.WORK,
                    "site_worked",
                    str(journey.sender_civilization_id),
                    str(site.site_id),
                    units=taken,
                    product=product.value,
                )
            )
            if site.remaining == 0:
                events.append(
                    _event(
                        state,
                        EventPhase.WORK,
                        "site_exhausted",
                        str(journey.sender_civilization_id),
                        str(site.site_id),
                    )
                )
        worked = journey.days_worked + (1 if living else 0)
        reason = (
            "no_one_left"
            if not living
            else "spent"
            if site.remaining == 0
            else "full"
            if room - taken <= 0
            else "done"
            if worked >= journey.work_days
            else None
        )
        update: dict[str, object] = {"cargo": cargo, "days_worked": worked}
        if reason is not None:
            update |= {
                "working": False,
                "phase": JourneyPhase.RETURNING,
                "outcome": JourneyOutcome.DELIVERED if cargo else JourneyOutcome.FAILED,
                "carrying_cargo": bool(cargo),
            }
            events.append(
                _event(
                    state,
                    EventPhase.MOVEMENT,
                    "extractors_left_site",
                    str(journey.sender_civilization_id),
                    str(journey.journey_id),
                    reason=reason,
                    units=sum(cargo.values()),
                )
            )
        _replace_journey(state, journey.model_copy(update=update))
    state.sites = tuple(sites[tile] for tile in sorted(sites))
    return events


def _advance_espionage(state: WorldState, rng: StableRng) -> list[DomainEvent]:
    """Spies on watch look around, or are found out; couriers on foreign land may be stopped."""
    events: list[DomainEvent] = []
    owners = state.territory.owner_of()
    for journey in sorted(state.journeys, key=lambda item: item.journey_id):
        if not journey.active or journey.kind not in SPYING_KINDS:
            continue
        target_id = journey.recipient_civilization_id
        target = state.civilizations[target_id]
        people = state.civilizations[journey.sender_civilization_id].population.people
        living = [
            people[person_id]
            for person_id in journey.traveller_ids
            if person_id in people and people[person_id].alive
        ]
        if not living or target.eliminated_day is not None:
            if journey.watching:
                _replace_journey(
                    state,
                    journey.model_copy(
                        update={
                            "watching": False,
                            "phase": JourneyPhase.RETURNING,
                            "outcome": JourneyOutcome.FAILED,
                        }
                    ),
                )
            continue
        roll = rng.stream(f"day:{state.day}:espionage:{journey.journey_id}")
        if journey.kind is JourneyKind.COURIER:
            tile = journey.route[journey.route_index]
            nearest = supplying(target, tile)
            if (
                journey.phase is JourneyPhase.OUTBOUND
                and owners.get(tile) == target_id
                and nearest is not None
                and int(roll.integers(0, 10_000))
                < caught_chance_bp(living, target_id, COURIER_CAUGHT_BP)
            ):
                events.extend(_catch(state, journey, target_id, nearest))
            continue
        if not journey.watching:
            continue
        settlement = settlement_at(target, journey.route[-1])
        if settlement is None:
            # The settlement changed hands or fell; there is nothing left to watch.
            _replace_journey(
                state,
                journey.model_copy(
                    update={
                        "watching": False,
                        "phase": JourneyPhase.RETURNING,
                        "outcome": JourneyOutcome.FAILED,
                    }
                ),
            )
            events.append(
                _event(
                    state,
                    EventPhase.MOVEMENT,
                    "spies_left_watch",
                    str(journey.sender_civilization_id),
                    str(journey.journey_id),
                    reason="gone",
                )
            )
            continue
        if int(roll.integers(0, 10_000)) < caught_chance_bp(living, target_id, WATCH_CAUGHT_BP):
            events.extend(_catch(state, journey, target_id, settlement))
            continue
        walls = next(
            (item for item in target.walls if item.settlement_id == settlement.settlement_id),
            None,
        )
        truth = _truth(state, target_id, settlement)
        seen = observe(
            settlement_id=settlement.settlement_id,
            civilization_id=target_id,
            tile=settlement.tile,
            day=state.day,
            residents=truth["residents"],
            fighters=truth["fighters"],
            store_units=truth["store_units"],
            wall_grade=None if walls is None else walls.grade,
            towers=0 if walls is None else walls.towers,
            works=truth["works"],
            spies=living,
            roll=roll,
        )
        watched = journey.watched + 1
        done = watched >= journey.watch_days
        _replace_journey(
            state,
            journey.model_copy(
                update={
                    "watched": watched,
                    "findings": seen,
                    **(
                        {
                            "watching": False,
                            "phase": JourneyPhase.RETURNING,
                            "outcome": JourneyOutcome.DELIVERED,
                        }
                        if done
                        else {}
                    ),
                }
            ),
        )
        if done:
            events.append(
                _event(
                    state,
                    EventPhase.MOVEMENT,
                    "spies_left_watch",
                    str(journey.sender_civilization_id),
                    str(journey.journey_id),
                    reason="done",
                )
            )
    return events


def _send_courier(
    state: WorldState, civilization_id: EntityId, command: DirectOrder
) -> DomainEvent:
    """One spy walks home ahead with the findings so far and a share of the food."""
    party = next(item for item in state.journeys if item.journey_id == command.journey_id)
    [courier_id] = command.traveller_ids
    share = party.provisions // len(party.traveller_ids)
    left = party.provisions - share
    courier = Journey(
        journey_id=EntityId(f"{party.journey_id}:courier:{state.day}"),
        kind=JourneyKind.COURIER,
        sender_civilization_id=civilization_id,
        recipient_civilization_id=party.recipient_civilization_id,
        traveller_ids=(courier_id,),
        route=command.route,
        provisions_packed=share,
        provisions=share,
        departed_day=state.day,
        findings=party.findings,
    )
    _replace_journey(
        state,
        party.model_copy(
            update={
                "traveller_ids": tuple(item for item in party.traveller_ids if item != courier_id),
                "provisions": left,
                "provisions_packed": left,
            }
        ),
    )
    state.journeys = tuple(sorted((*state.journeys, courier), key=lambda item: item.journey_id))
    return _event(
        state,
        EventPhase.MOVEMENT,
        "courier_sent",
        str(civilization_id),
        str(courier.journey_id),
        spies=str(party.journey_id),
    )


def _file_spy_report(state: WorldState, journey: Journey, *, by_courier: bool) -> DomainEvent:
    """Findings reach home: the council will read them at its next sitting."""
    assert journey.findings is not None
    sender = state.civilizations[journey.sender_civilization_id]
    parent = journey.journey_id.split(":courier:")[0] if by_courier else journey.journey_id
    report = SpyReport(
        report_id=f"spy-report:{journey.journey_id}",
        journey_id=EntityId(parent),
        delivered_day=state.day,
        by_courier=by_courier,
        estimate=journey.findings,
    )
    sender.spy_reports = (*sender.spy_reports, report)
    return _event(
        state,
        EventPhase.MOVEMENT,
        "spy_report_delivered",
        str(journey.sender_civilization_id),
        str(journey.journey_id),
        target=str(journey.findings.civilization_id),
        by_courier=by_courier,
    )


def _free_captives(
    state: WorldState, person_ids: tuple[EntityId, ...], reason: str
) -> list[DomainEvent]:
    """Captives go free where they stand and walk home to their nearest settlement."""
    events: list[DomainEvent] = []
    walking: dict[tuple[EntityId, HexCoord], list[EntityId]] = {}
    for person_id in sorted(set(person_ids)):
        person = _captive(state, person_id)
        if person is None or person.captive_of is None:
            continue
        captor = person.captive_of
        person.captive_of = None
        person.held_at = None
        if not person.alive:
            continue
        events.append(
            _event(
                state,
                EventPhase.MOVEMENT,
                "captive_freed",
                str(person.civilization_id),
                str(person_id),
                captor=str(captor),
                reason=reason,
            )
        )
        walking.setdefault((person.civilization_id, person.location), []).append(person_id)
    freed = set(person_ids)
    state.journeys = tuple(
        journey.model_copy(
            update={"captive_ids": tuple(item for item in journey.captive_ids if item not in freed)}
        )
        if freed & set(journey.captive_ids)
        else journey
        for journey in state.journeys
    )
    taken_ids = {journey.journey_id for journey in state.journeys}
    for (civilization_id, tile), group in sorted(walking.items()):
        homes = frozenset(item.tile for item in state.civilizations[civilization_id].settlements)
        route = way_to(state.world_map, tile, homes, bridges=bridged_edges(state.bridges))
        if route is None or len(route) < 2:
            continue
        for chunk in batched(group, MAX_TRAVELLERS):
            number = 0
            while (
                journey_id := EntityId(
                    f"journey:{civilization_id}:freed:{state.day:06d}:{tile.q}:{tile.r}:{number}"
                )
            ) in taken_ids:
                number += 1
            taken_ids.add(journey_id)
            state.journeys = tuple(
                sorted(
                    (
                        *state.journeys,
                        Journey(
                            journey_id=journey_id,
                            kind=JourneyKind.RELOCATION,
                            sender_civilization_id=civilization_id,
                            recipient_civilization_id=civilization_id,
                            traveller_ids=tuple(sorted(chunk)),
                            route=route,
                            departed_day=state.day,
                        ),
                    ),
                    key=lambda item: item.journey_id,
                )
            )
    return events


def _march_captives(state: WorldState) -> frozenset[EntityId]:
    """Captives walk with the war party holding them and eat from its packs, if any is left."""
    fed: set[EntityId] = set()
    journeys: list[Journey] = []
    for journey in state.journeys:
        if journey.active and journey.captive_ids:
            tile = journey.route[journey.route_index]
            provisions = journey.provisions
            for person_id in journey.captive_ids:
                person = _captive(state, person_id)
                if person is None or not person.alive:
                    continue
                person.location = tile
                if provisions:
                    provisions -= 1
                    fed.add(person_id)
                else:
                    go_hungry(person)
            journey = journey.model_copy(update={"provisions": provisions})
        journeys.append(journey)
    state.journeys = tuple(journeys)
    return frozenset(fed)


def _escapes(state: WorldState, rng: StableRng) -> list[DomainEvent]:
    """At every council, each captive has a chance to slip away and walk home."""
    roll = rng.stream(f"day:{state.day}:captives:escape")
    escaped = tuple(
        person_id
        for civilization_id in sorted(state.civilizations)
        for person_id, person in sorted(
            state.civilizations[civilization_id].population.people.items()
        )
        if person.alive
        and person.captive_of is not None
        and int(roll.integers(0, BASIS)) < ESCAPE_BP
    )
    return _free_captives(state, escaped, "escaped") if escaped else []


def _make_peace(state: WorldState, treaty: ActiveTreaty) -> list[DomainEvent]:
    """Peace ends the war: camps and occupiers go home, war parties turn back, and
    prisoners are freed as the terms say."""
    sides = {treaty.proposer_civilization_id, treaty.recipient_civilization_id}
    events: list[DomainEvent] = []
    state.wars = tuple(
        war.model_copy(update={"ended_day": state.day})
        if war.active and war.involves(*sides)
        else war
        for war in state.wars
    )
    journeys = {journey.journey_id: journey for journey in state.journeys}
    for siege in state.sieges:
        if siege.active and {siege.besieger_id, siege.defender_id} == sides:
            events.extend(_break_camp(state, journeys[siege.journey_id], SiegeEnd.PEACE))
    for occupation in state.occupations:
        if occupation.active and {occupation.occupier_id, occupation.owner_id} == sides:
            events.extend(_withdraw(state, journeys[occupation.journey_id], OccupationEnd.PEACE))
    state.journeys = tuple(
        journey.model_copy(
            update={"phase": JourneyPhase.RETURNING, "outcome": JourneyOutcome.DELIVERED}
        )
        if journey.active
        and journey.kind is JourneyKind.CAMPAIGN
        and journey.phase is JourneyPhase.OUTBOUND
        and {journey.sender_civilization_id, journey.recipient_civilization_id} == sides
        else journey
        for journey in state.journeys
    )
    terms = treaty.terms
    if terms is not None:
        freeing = {
            captor
            for captor, frees in (
                (treaty.proposer_civilization_id, terms.proposer_frees),
                (treaty.recipient_civilization_id, terms.recipient_frees),
            )
            if frees
        }
        freed = tuple(
            person_id
            for civilization_id in sorted(sides)
            for person_id, person in sorted(
                state.civilizations[civilization_id].population.people.items()
            )
            if person.alive
            and person.captive_of in freeing
            and person.captive_of != civilization_id
        )
        events.extend(_free_captives(state, freed, "peace"))
    events.extend(_cede(state, treaty))
    events.append(
        _event(
            state,
            EventPhase.MOVEMENT,
            "peace_made",
            str(treaty.proposer_civilization_id),
            str(treaty.treaty_id),
            recipient=str(treaty.recipient_civilization_id),
            truce_until=treaty.truce_until or state.day,
        )
    )
    return events


def _count_tribute(state: WorldState, journey: Journey) -> list[DomainEvent]:
    """A shipment under a peace treaty is tribute; what arrived counts toward the debt."""
    treaty = next(
        (item for item in state.active_treaties if item.treaty_id == journey.treaty_id), None
    )
    if treaty is None or treaty.kind is not TreatyKind.PEACE or treaty.terms is None:
        return []
    received = dict(treaty.tribute_received)
    for resource, quantity in journey.cargo.items():
        received[resource] = received.get(resource, 0) + quantity
    updated = treaty.model_copy(update={"tribute_received": dict(sorted(received.items()))})
    state.active_treaties = tuple(
        updated if item.treaty_id == treaty.treaty_id else item for item in state.active_treaties
    )
    return [
        _event(
            state,
            EventPhase.MOVEMENT,
            "tribute_received",
            str(journey.recipient_civilization_id),
            str(treaty.treaty_id),
            units=sum(journey.cargo.values()),
        )
    ]


def _check_tribute(state: WorldState) -> list[DomainEvent]:
    """A payer whose tribute is overdue past its grace has broken the peace."""
    events: list[DomainEvent] = []
    for treaty in state.active_treaties:
        if not treaty.in_force or treaty.terms is None or treaty.terms.tribute_payer is None:
            continue
        if not treaty.tribute_overdue(state.day):
            continue
        payer = treaty.terms.tribute_payer
        ended = _end_treaty(state, treaty.treaty_id, TreatyEndKind.BREACHED, payer, told=True)
        if ended is not None:
            events.append(
                _event(
                    state,
                    EventPhase.MOVEMENT,
                    "tribute_defaulted",
                    str(payer),
                    str(treaty.treaty_id),
                    payee=str(treaty.counterparty(payer)),
                )
            )
    return events


def _answer_petition(state: WorldState, command: DirectOrder) -> list[DomainEvent]:
    journey = next(item for item in state.journeys if item.journey_id == command.journey_id)
    return _admit(state, journey) if command.admit else _refuse(state, journey)


def _admit(state: WorldState, journey: Journey) -> list[DomainEvent]:
    """The petitioners are taken in: they change allegiance, and their packs go to the store."""
    sender, receiver = journey.sender_civilization_id, journey.recipient_civilization_id
    people = state.civilizations[sender].population.people
    arrivals = tuple(
        person_id
        for person_id in journey.traveller_ids
        if person_id in people and people[person_id].alive
    )
    _change_allegiance(state, arrivals, sender, receiver, "release")
    if journey.provisions:
        put(state.civilizations[receiver], journey.route[-1], {Resource.FOOD: journey.provisions})
    _replace_journey(
        state,
        journey.model_copy(
            update={
                "waiting": False,
                "phase": JourneyPhase.COMPLETE,
                "outcome": JourneyOutcome.DELIVERED,
                "completed_day": state.day,
                "provisions": 0,
            }
        ),
    )
    events = [
        _event(
            state,
            EventPhase.MOVEMENT,
            "petition_admitted",
            str(receiver),
            str(journey.journey_id),
            sender=str(sender),
            people=len(arrivals),
        )
    ]
    for capability in _adopt_migrant_capabilities(state, receiver, arrivals):
        events.append(
            _event(
                state,
                EventPhase.WORK,
                "capability_learned",
                str(receiver),
                capability=capability.value,
                source="release",
            )
        )
    return events


def _refuse(state: WorldState, journey: Journey) -> list[DomainEvent]:
    """Refused petitioners turn and walk home."""
    _replace_journey(
        state,
        journey.model_copy(
            update={
                "waiting": False,
                "phase": JourneyPhase.RETURNING,
                "outcome": JourneyOutcome.REFUSED,
            }
        ),
    )
    return [
        _event(
            state,
            EventPhase.MOVEMENT,
            "petition_refused",
            str(journey.recipient_civilization_id),
            str(journey.journey_id),
            sender=str(journey.sender_civilization_id),
        )
    ]


def _refuse_unanswered(state: WorldState, civilization_id: EntityId) -> list[DomainEvent]:
    """Petitioners a council let wait past a whole council without an answer are refused."""
    interval = state.config.council_interval_days
    events: list[DomainEvent] = []
    for journey in state.journeys:
        if (
            journey.waiting
            and journey.recipient_civilization_id == civilization_id
            and journey.arrived_day is not None
            and journey.arrived_day < state.day - interval
        ):
            events.extend(_refuse(state, journey))
    return events


def _working(state: WorldState, civilization_id: EntityId) -> bool:
    """Whether any settlement still has its own free people and no enemy holding it."""
    civilization = state.civilizations[civilization_id]
    away = _away(state)
    held = {item.settlement_id for item in state.occupations if item.active}
    homes = {
        person.location
        for person_id, person in civilization.population.people.items()
        if person.alive and person.captive_of is None and person_id not in away
    }
    return any(
        settlement.tile in homes and settlement.settlement_id not in held
        for settlement in civilization.settlements
    )


def _advance_civilizations(state: WorldState) -> list[DomainEvent]:
    """Eliminate the civilizations with no one left, track the homeless and break them up,
    and record the endings of the world."""
    events: list[DomainEvent] = []
    council = state.day % state.config.council_interval_days == 0
    for civilization_id in sorted(state.civilizations):
        civilization = state.civilizations[civilization_id]
        if civilization.eliminated_day is not None:
            continue
        if not civilization.population.living_ids:
            events.extend(_eliminate(state, civilization_id))
            continue
        working = _working(state, civilization_id)
        if not working and civilization.homeless_since is None:
            civilization.homeless_since = state.day
            events.append(
                _event(state, EventPhase.MOVEMENT, "civilization_homeless", str(civilization_id))
            )
        elif working and civilization.homeless_since is not None:
            civilization.homeless_since = None
            events.append(
                _event(state, EventPhase.MOVEMENT, "civilization_recovered", str(civilization_id))
            )
        if (
            council
            and civilization.homeless_since is not None
            and state.day - civilization.homeless_since >= BREAKUP_GRACE_DAYS
        ):
            events.extend(_break_up(state, civilization_id))
    alive = sorted(
        civilization_id
        for civilization_id, civilization in state.civilizations.items()
        if civilization.eliminated_day is None
    )
    recorded = {ending.kind for ending in state.endings}
    ending: Ending | None = None
    if len(alive) == 1 and EndingKind.LAST_CIVILIZATION not in recorded:
        survivor = alive[0]
        ending = Ending(
            kind=EndingKind.LAST_CIVILIZATION,
            day=state.day,
            survivor_id=survivor,
            population=len(state.civilizations[survivor].population.living_ids),
        )
    elif not alive and EndingKind.NO_CIVILIZATION not in recorded:
        ending = Ending(kind=EndingKind.NO_CIVILIZATION, day=state.day)
    if ending is not None:
        state.endings = (*state.endings, ending)
        events.append(
            _event(
                state,
                EventPhase.MOVEMENT,
                ending.kind.value,
                str(ending.survivor_id) if ending.survivor_id else None,
                population=ending.population,
            )
        )
    return events


def _break_up(state: WorldState, civilization_id: EntityId) -> list[DomainEvent]:
    """A quarter of a homeless civilization's free people set out to ask the nearest
    civilization they know to take them in."""
    civilization = state.civilizations[civilization_id]
    away = _away(state)
    free = sorted(
        person_id
        for person_id, person in civilization.population.people.items()
        if person.alive and person.captive_of is None and person_id not in away
    )
    known = [
        contact
        for contact in civilization.contacts
        if state.civilizations[contact.civilization_id].eliminated_day is None
    ]
    if not free or not known:
        return []
    leaving = free[: max(1, len(free) // BREAKUP_SHARE)]
    people = civilization.population.people
    groups: dict[tuple[HexCoord, EntityId, HexCoord], list[EntityId]] = {}
    for person_id in leaving:
        here = people[person_id].location
        contact = min(
            known, key=lambda item: (item.settlement.distance(here), item.civilization_id)
        )
        groups.setdefault((here, contact.civilization_id, contact.settlement), []).append(person_id)
    events: list[DomainEvent] = []
    taken = {journey.journey_id for journey in state.journeys}
    for (here, recipient, gate), group in sorted(groups.items()):
        route = way_to(
            state.world_map, here, frozenset({gate}), bridges=bridged_edges(state.bridges)
        )
        if route is None or len(route) < 2:
            continue
        for chunk in batched(group, MAX_TRAVELLERS):
            number = 0
            while (
                journey_id := EntityId(
                    f"journey:{civilization_id}:drift:{state.day:06d}:{here.q}:{here.r}:{number}"
                )
            ) in taken:
                number += 1
            taken.add(journey_id)
            journey = Journey(
                journey_id=journey_id,
                kind=JourneyKind.PETITION,
                sender_civilization_id=civilization_id,
                recipient_civilization_id=recipient,
                traveller_ids=tuple(sorted(chunk)),
                route=route,
                departed_day=state.day,
            )
            state.journeys = tuple(
                sorted((*state.journeys, journey), key=lambda item: item.journey_id)
            )
            events.append(
                _event(
                    state,
                    EventPhase.MOVEMENT,
                    "people_drifted",
                    str(civilization_id),
                    str(journey_id),
                    to=str(recipient),
                    people=len(chunk),
                )
            )
    return events


def _eliminate(state: WorldState, civilization_id: EntityId) -> list[DomainEvent]:
    """No one is left: wars and treaties end, captives go free, settlements become ruins."""
    civilization = state.civilizations[civilization_id]
    civilization.eliminated_day = state.day
    events: list[DomainEvent] = []
    state.wars = tuple(
        war.model_copy(update={"ended_day": state.day})
        if war.active and civilization_id in {war.aggressor_id, war.defender_id}
        else war
        for war in state.wars
    )
    for treaty in state.active_treaties:
        if treaty.in_force and civilization_id in {
            treaty.proposer_civilization_id,
            treaty.recipient_civilization_id,
        }:
            _end_treaty(state, treaty.treaty_id, TreatyEndKind.CANCELLED, civilization_id)
    held = tuple(
        person_id
        for other in state.civilizations.values()
        for person_id, person in sorted(other.population.people.items())
        if person.alive and person.captive_of == civilization_id
    )
    events.extend(_free_captives(state, held, "released"))
    journeys = {journey.journey_id: journey for journey in state.journeys}
    for siege in state.sieges:
        if siege.active and siege.defender_id == civilization_id:
            events.extend(_break_camp(state, journeys[siege.journey_id], SiegeEnd.GONE))
    for occupation in state.occupations:
        if occupation.active and occupation.owner_id == civilization_id:
            events.extend(_withdraw(state, journeys[occupation.journey_id], OccupationEnd.GONE))
    for journey in state.journeys:
        if journey.waiting and journey.recipient_civilization_id == civilization_id:
            events.extend(_refuse(state, journey))
    ruins = list(state.ruins)
    for settlement in civilization.settlements:
        sid = settlement.settlement_id
        ruins.append(
            Ruin(
                ruin_id=EntityId(f"ruin:{sid}"),
                tile=settlement.tile,
                former_settlement_id=sid,
                former_civilization_id=civilization_id,
                since_day=state.day,
                store=store(civilization, sid),
                storehouses=tuple(
                    item for item in civilization.storehouses if item.settlement_id == sid
                ),
                walls=next(
                    (item for item in civilization.walls if item.settlement_id == sid), None
                ),
            )
        )
        events.append(
            _event(state, EventPhase.MOVEMENT, "settlement_ruined", str(civilization_id), str(sid))
        )
    state.ruins = tuple(sorted(ruins, key=lambda item: item.tile))
    # With no one left to hold it, the civilization's land is no one's.
    territory = state.territory
    state.territory = territory.model_copy(
        update={
            "held": tuple(
                item for item in territory.held if item.civilization_id != civilization_id
            ),
            "owners": tuple(
                item for item in territory.owners if item.civilization_id != civilization_id
            ),
            "challenges": tuple(
                item for item in territory.challenges if item.civilization_id != civilization_id
            ),
        }
    )
    civilization.settlements = ()
    civilization.housing = {}
    civilization.house_jobs = ()
    civilization.ranks_reached = {}
    civilization.realm_rank_reached = RealmRank.CHIEFDOM
    civilization.stores = {}
    civilization.inventory = Inventory(capacity=0)
    civilization.storehouses = ()
    civilization.walls = ()
    civilization.toll_posts = ()
    civilization.garrisons = ()
    civilization.storehouse_jobs = ()
    civilization.wall_jobs = ()
    civilization.craft_jobs = ()
    civilization.drills = ()
    civilization.research = ()
    civilization.teaching_assignments = ()
    civilization.work_orders = ()
    events.append(_event(state, EventPhase.DEATH, "civilization_eliminated", str(civilization_id)))
    return events


def _salvage(state: WorldState, journey: Journey) -> list[DomainEvent]:
    """Salvagers at a ruin load what they can bear from its store and turn for home."""
    ruin = next((item for item in state.ruins if item.tile == journey.route[-1]), None)
    if ruin is None:
        return _find(state, journey) if rules_for(state.rules_version).sites else []
    # Room is what the party set out able to bear, less the food it packed.
    room = journey.carry_per_person * len(journey.traveller_ids) - journey.provisions_packed
    left = dict(ruin.store.quantities)
    taken: dict[Resource, int] = {}
    order = (*PLUNDER_ORDER, *(item for item in Resource if item not in PLUNDER_ORDER))
    for resource in order:
        grab = min(left.get(resource, 0), max(room, 0))
        if grab:
            taken[resource] = grab
            left[resource] -= grab
            room -= grab
    updated = ruin.model_copy(
        update={
            "store": Inventory(
                capacity=ruin.store.capacity,
                quantities={key: value for key, value in left.items() if value},
            )
        }
    )
    state.ruins = tuple(updated if item.tile == ruin.tile else item for item in state.ruins)
    if taken:
        _replace_journey(
            state,
            journey.model_copy(
                update={"cargo": dict(sorted(taken.items())), "carrying_cargo": True}
            ),
        )
    return [
        _event(
            state,
            EventPhase.MOVEMENT,
            "ruin_salvaged",
            str(journey.sender_civilization_id),
            str(ruin.ruin_id),
            units=sum(taken.values()),
        )
    ]


def _find(state: WorldState, journey: Journey) -> list[DomainEvent]:
    """Rules version 2: the first party at an ancient ruin or a trove takes what it holds."""
    site = next(
        (
            item
            for item in state.sites
            if item.tile == journey.route[-1] and item.kind in FIND_KINDS
        ),
        None,
    )
    sender = journey.sender_civilization_id
    if site is None or site.remaining == 0:
        _replace_journey(state, journey.model_copy(update={"outcome": JourneyOutcome.FAILED}))
        return [
            _event(
                state,
                EventPhase.MOVEMENT,
                "site_empty",
                str(sender),
                str(journey.journey_id),
            )
        ]
    room = journey.carry_per_person * len(journey.traveller_ids) - journey.provisions_packed
    taken: dict[Resource, int] = {}
    for resource, quantity in FINDS[site.kind].items():
        grab = min(quantity, max(room, 0))
        if grab:
            taken[resource] = grab
            room -= grab
    state.sites = tuple(
        item.model_copy(update={"remaining": 0, "opened_day": state.day, "spent_day": state.day})
        if item.site_id == site.site_id
        else item
        for item in state.sites
    )
    if taken:
        _replace_journey(
            state,
            journey.model_copy(
                update={"cargo": dict(sorted(taken.items())), "carrying_cargo": True}
            ),
        )
    lore: dict[str, int | str] = {}
    if site.kind is SiteKind.ANCIENT_RUIN:
        # The ruin's writings teach a civil art the finders do not know, once studied.
        civilization = state.civilizations[sender]
        topic = next(
            (item for item in CIVIL_TOPICS if not knows(civilization.capabilities, item)), None
        )
        if topic is not None:
            civilization.research_points = dict(
                sorted(
                    {
                        **civilization.research_points,
                        topic: civilization.research_points.get(topic, 0) + RUIN_LORE,
                    }.items()
                )
            )
            lore = {"topic": topic.value, "points": RUIN_LORE}
    return [
        _event(
            state,
            EventPhase.MOVEMENT,
            "ruin_explored" if site.kind is SiteKind.ANCIENT_RUIN else "trove_found",
            str(sender),
            str(site.site_id),
            units=sum(taken.values()),
            **lore,
        )
    ]


def _resettle(
    state: WorldState, civilization_id: EntityId, settlement_id: EntityId, ruin: Ruin
) -> None:
    """Settlers on a ruin take over what is left of it: its store, storehouses and walls."""
    civilization = state.civilizations[civilization_id]
    civilization.stores = {**civilization.stores, settlement_id: ruin.store}
    civilization.storehouses = tuple(
        sorted(
            (
                *civilization.storehouses,
                *(
                    item.model_copy(update={"settlement_id": settlement_id})
                    for item in ruin.storehouses
                ),
            ),
            key=lambda item: item.storehouse_id,
        )
    )
    if ruin.walls is not None:
        civilization.walls = tuple(
            sorted(
                (
                    *civilization.walls,
                    ruin.walls.model_copy(update={"settlement_id": settlement_id}),
                ),
                key=lambda item: item.settlement_id,
            )
        )
    state.ruins = tuple(item for item in state.ruins if item.tile != ruin.tile)


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
        # A party fights at most one battle a day, whether it attacked or was attacked.
        fought_today = any(
            battle_id.startswith(f"battle:{state.day:06d}:") for battle_id in party.battles
        )
        home_side, marching = _defenders(state, enemy, tile) if enemy is not None else ([], [])
        # Occupiers live among the residents; they fight only an enemy force that comes.
        holding = party.encamped and party.objective is WarObjective.OCCUPY
        if (
            enemy is not None
            and not fought_today
            and (marching if holding else (home_side or marching))
        ):
            party, fought = _fight(state, party, enemy, rng)
            events.extend(fought)
        if party.phase is JourneyPhase.OUTBOUND:
            events.extend(_ambush(state, party))
            party = next(item for item in state.journeys if item.journey_id == journey_id)
        if party.phase is JourneyPhase.OUTBOUND and _wreckable(state, party, tile):
            party, wrecked = _wreck(state, party, tile)
            events.append(wrecked)
            continue
        if party.encamped and party.objective is WarObjective.OCCUPY:
            events.extend(_hold_occupation(state, party, rng))
            continue
        if party.encamped:
            events.extend(_hold_camp(state, party, rng))
            continue
        at_target = party.route_index == len(party.route) - 1
        if (
            party.phase is JourneyPhase.OUTBOUND
            and at_target
            and (party.objective is WarObjective.BESIEGE)
        ):
            events.extend(_encamp(state, party))
            continue
        if (
            party.phase is JourneyPhase.OUTBOUND
            and at_target
            and (party.objective is WarObjective.OCCUPY)
        ):
            events.extend(_occupy(state, party))
            continue
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
    events.extend(_end_sieges(state))
    events.extend(_end_occupations(state))
    return events


def _wreckable(state: WorldState, party: Journey, tile: HexCoord) -> bool:
    """A road on enemy land that this road-wrecking party has not yet pulled down."""
    if not party.wreck_roads or party.phase is not JourneyPhase.OUTBOUND or party.encamped:
        return False
    if tile in party.wrecked or tile not in grades_of(state.roads):
        return False
    owner = state.territory.owner_of().get(tile)
    sender = party.sender_civilization_id
    return (
        owner is not None
        and owner != sender
        and (
            owner == party.recipient_civilization_id
            or _war_between(state, sender, owner) is not None
        )
    )


def _wreck(state: WorldState, party: Journey, tile: HexCoord) -> tuple[Journey, DomainEvent]:
    """The party spends the day pulling the road down one grade; a footpath is lost."""
    road = next(item for item in state.roads if item.tile == tile)
    below = grade_below(road.grade)
    state.roads = tuple(
        item
        for item in (
            (
                road.model_copy(update={"grade": below, "graded_day": state.day})
                if below is not None
                else None
            )
            if item.tile == tile
            else item
            for item in state.roads
        )
        if item is not None
    )
    party = party.model_copy(update={"wrecked": (*party.wrecked, tile)})
    _replace_journey(state, party)
    return party, _event(
        state,
        EventPhase.MOVEMENT,
        "road_wrecked",
        str(party.sender_civilization_id),
        str(party.journey_id),
        grade=below.value if below is not None else "none",
        q=tile.q,
        r=tile.r,
    )


def _occupation_of(state: WorldState, journey_id: EntityId) -> Occupation | None:
    return next(
        (item for item in state.occupations if item.active and item.journey_id == journey_id),
        None,
    )


def _occupy(state: WorldState, party: Journey) -> list[DomainEvent]:
    """The party has beaten or found no defenders: it stays and holds the settlement."""
    events: list[DomainEvent] = []
    sender, target = party.sender_civilization_id, party.recipient_civilization_id
    tile = party.route[-1]
    settlement = settlement_at(state.civilizations[target], tile)
    held = {item.settlement_id for item in state.occupations if item.active}
    if settlement is None or settlement.settlement_id in held:
        _replace_journey(
            state,
            party.model_copy(
                update={"phase": JourneyPhase.RETURNING, "outcome": JourneyOutcome.FAILED}
            ),
        )
        return events
    war = _war_between(state, sender, target)
    if war is None:
        war, started = _start_war(state, sender, target, declared=False)
        events.extend(started)
    events.extend(_learn_war(state, war, target))
    _replace_journey(state, party.model_copy(update={"encamped": True}))
    occupation = Occupation(
        occupation_id=EntityId(f"occupation:{state.day:06d}:{party.journey_id}"),
        journey_id=party.journey_id,
        occupier_id=sender,
        owner_id=target,
        settlement_id=settlement.settlement_id,
        tile=tile,
        started_day=state.day,
    )
    state.occupations = tuple(
        sorted((*state.occupations, occupation), key=lambda item: item.occupation_id)
    )
    events.append(
        _event(
            state,
            EventPhase.MOVEMENT,
            "settlement_occupied",
            str(sender),
            str(occupation.occupation_id),
            owner=str(target),
            settlement=str(settlement.settlement_id),
        )
    )
    return events


def _close_occupation(
    state: WorldState, journey_id: EntityId, end: OccupationEnd
) -> list[DomainEvent]:
    occupation = _occupation_of(state, journey_id)
    if occupation is None:
        return []
    ended = occupation.model_copy(update={"ended_day": state.day, "end": end})
    state.occupations = tuple(
        ended if item.occupation_id == occupation.occupation_id else item
        for item in state.occupations
    )
    return [
        _event(
            state,
            EventPhase.MOVEMENT,
            "occupation_ended",
            str(occupation.occupier_id),
            str(occupation.occupation_id),
            owner=str(occupation.owner_id),
            reason=end.value,
        )
    ]


def _withdraw(state: WorldState, party: Journey, end: OccupationEnd) -> list[DomainEvent]:
    _replace_journey(
        state,
        party.model_copy(
            update={
                "encamped": False,
                "phase": JourneyPhase.RETURNING,
                "outcome": JourneyOutcome.DELIVERED,
            }
        ),
    )
    return _close_occupation(state, party.journey_id, end)


def _end_occupations(state: WorldState) -> list[DomainEvent]:
    """Occupiers routed by a relieving force or wiped out have lost the settlement."""
    events: list[DomainEvent] = []
    journeys = {item.journey_id: item for item in state.journeys}
    for occupation in state.occupations:
        if not occupation.active:
            continue
        held = journeys.get(occupation.journey_id)
        if held is None or not (held.active and held.encamped):
            events.extend(_close_occupation(state, occupation.journey_id, OccupationEnd.BEATEN))
    return events


def _hold_occupation(state: WorldState, party: Journey, rng: StableRng) -> list[DomainEvent]:
    """A day holding the settlement: residents may rise; occupiers take food and chests."""
    occupation = _occupation_of(state, party.journey_id)
    if occupation is None:
        return []
    events: list[DomainEvent] = []
    people = state.civilizations[party.sender_civilization_id].population.people
    living = [
        person_id
        for person_id in party.traveller_ids
        if people[person_id].alive and able_to_fight(people[person_id])
    ]
    if len(living) < MIN_BESIEGERS:
        return _withdraw(state, party, OccupationEnd.TOO_FEW)
    residents, _ = _defenders(state, occupation.owner_id, occupation.tile)
    fought_today = any(
        battle_id.startswith(f"battle:{state.day:06d}:") for battle_id in party.battles
    )
    if len(residents) >= RISING_RATIO * len(living) and not fought_today:
        events.append(
            _event(
                state,
                EventPhase.MOVEMENT,
                "residents_rose",
                str(occupation.owner_id),
                str(occupation.occupation_id),
                residents=len(residents),
                occupiers=len(living),
            )
        )
        party, fought = _fight(state, party, occupation.owner_id, rng)
        events.extend(fought)
        if not party.encamped:
            events.extend(_close_occupation(state, party.journey_id, OccupationEnd.ROSE))
            return events
    owner = state.civilizations[occupation.owner_id]
    # Occupiers take their food from the settlement's store before its people eat.
    food = store_at(owner, occupation.tile).quantities.get(Resource.FOOD, 0)
    captives = sum(
        (captive := _captive(state, person_id)) is not None and captive.alive
        for person_id in party.captive_ids
    )
    taken = min(len(living) + captives, food)
    if taken:
        take(owner, occupation.tile, {Resource.FOOD: taken})
        party = party.model_copy(update={"provisions": party.provisions + taken})
        _replace_journey(state, party)
    post = _post_at(state, occupation.owner_id, occupation.tile)
    if post is not None and post.chest:
        room = _room(state, party)
        chest = dict(post.chest)
        plunder = dict(party.plunder)
        for resource in PLUNDER_ORDER:
            grab = min(chest.get(resource, 0), room)
            if grab:
                plunder[resource] = plunder.get(resource, 0) + grab
                chest[resource] -= grab
                room -= grab
        _replace_post(
            state,
            post.model_copy(update={"chest": {key: left for key, left in chest.items() if left}}),
            occupation.tile,
            occupation.owner_id,
        )
        party = party.model_copy(update={"plunder": dict(sorted(plunder.items()))})
        _replace_journey(state, party)
    home = travel_days(
        state.world_map,
        tuple(reversed(party.route))[1:],
        grades_of(state.roads),
        start=party.route[-1],
        bridges=bridged_edges(state.bridges),
    )
    if not taken and party.provisions < len(living) * home:
        events.extend(_withdraw(state, party, OccupationEnd.STARVED))
    return events


def _burn_storehouse(state: WorldState, command: DirectOrder) -> list[DomainEvent]:
    """Occupiers set fire to a storehouse: it falls a grade and the room it held is lost."""
    occupation = _occupation_of(state, command.journey_id or EntityId(""))
    assert occupation is not None
    owner = state.civilizations[occupation.owner_id]
    house = next(item for item in owner.storehouses if item.storehouse_id == command.storehouse_id)
    below = storehouse_grade_below(house.grade)
    lost_room = STOREHOUSE_GRADES[house.grade].capacity - (
        STOREHOUSE_GRADES[below].capacity if below is not None else 0
    )
    burned = shrink(owner, occupation.tile, lost_room)
    housing_events = _lose_houses(state, occupation.owner_id, house.settlement_id, "burned")
    owner.storehouses = tuple(
        item
        for item in (
            (house.model_copy(update={"grade": below}) if below is not None else None)
            if item.storehouse_id == house.storehouse_id
            else item
            for item in owner.storehouses
        )
        if item is not None
    )
    return [
        _event(
            state,
            EventPhase.MOVEMENT,
            "storehouse_burned",
            str(occupation.occupier_id),
            str(house.storehouse_id),
            grade=below.value if below is not None else "none",
            lost=sum(burned.values()),
        ),
        *housing_events,
    ]


def _besieged_settlement(state: WorldState, party: Journey) -> Settlement | None:
    """The target's settlement next to the end of a besieging party's route."""
    camp = party.route[-1]
    return next(
        (
            settlement
            for settlement in state.civilizations[party.recipient_civilization_id].settlements
            if settlement.tile.distance(camp) == 1
        ),
        None,
    )


def _encamp(state: WorldState, party: Journey) -> list[DomainEvent]:
    """A besieging party reaches its camp: the siege and, if need be, the war begin."""
    events: list[DomainEvent] = []
    sender, target = party.sender_civilization_id, party.recipient_civilization_id
    settlement = _besieged_settlement(state, party)
    if settlement is None:
        # The settlement it came for is gone; the party marches home.
        _replace_journey(
            state,
            party.model_copy(
                update={"phase": JourneyPhase.RETURNING, "outcome": JourneyOutcome.FAILED}
            ),
        )
        return events
    war = _war_between(state, sender, target)
    if war is None:
        war, started = _start_war(state, sender, target, declared=False)
        events.extend(started)
    events.extend(_learn_war(state, war, target))
    _replace_journey(state, party.model_copy(update={"encamped": True}))
    siege = Siege(
        siege_id=EntityId(f"siege:{state.day:06d}:{party.journey_id}"),
        journey_id=party.journey_id,
        besieger_id=sender,
        defender_id=target,
        settlement_id=settlement.settlement_id,
        settlement_tile=settlement.tile,
        camp=party.route[-1],
        started_day=state.day,
    )
    state.sieges = tuple(sorted((*state.sieges, siege), key=lambda item: item.siege_id))
    events.append(
        _event(
            state,
            EventPhase.MOVEMENT,
            "siege_began",
            str(sender),
            str(siege.siege_id),
            defender=str(target),
            settlement=str(settlement.settlement_id),
        )
    )
    return events


def _siege_of(state: WorldState, journey_id: EntityId) -> Siege | None:
    return next(
        (item for item in state.sieges if item.active and item.journey_id == journey_id), None
    )


def _break_camp(state: WorldState, party: Journey, end: SiegeEnd) -> list[DomainEvent]:
    """The camp marches home; the siege ends for the reason given."""
    _replace_journey(
        state,
        party.model_copy(
            update={
                "encamped": False,
                "phase": JourneyPhase.RETURNING,
                "outcome": JourneyOutcome.DELIVERED,
            }
        ),
    )
    return _close_siege(state, party.journey_id, end)


def _close_siege(state: WorldState, journey_id: EntityId, end: SiegeEnd) -> list[DomainEvent]:
    siege = _siege_of(state, journey_id)
    if siege is None:
        return []
    ended = siege.model_copy(update={"ended_day": state.day, "end": end})
    state.sieges = tuple(
        ended if item.siege_id == siege.siege_id else item for item in state.sieges
    )
    return [
        _event(
            state,
            EventPhase.MOVEMENT,
            "siege_lifted",
            str(siege.besieger_id),
            str(siege.siege_id),
            defender=str(siege.defender_id),
            reason=end.value,
        )
    ]


def _end_sieges(state: WorldState) -> list[DomainEvent]:
    """A siege whose camp was routed or wiped out is over."""
    events: list[DomainEvent] = []
    journeys = {item.journey_id: item for item in state.journeys}
    for siege in state.sieges:
        if not siege.active:
            continue
        camp = journeys.get(siege.journey_id)
        if camp is None or not (camp.active and camp.encamped):
            events.extend(_close_siege(state, siege.journey_id, SiegeEnd.BROKEN))
    return events


def _hold_camp(state: WorldState, party: Journey, rng: StableRng) -> list[DomainEvent]:
    """A day in camp: the catapults bombard, and the camp goes home if it cannot stay."""
    siege = _siege_of(state, party.journey_id)
    if siege is None:
        return []
    people = state.civilizations[party.sender_civilization_id].population.people
    living = [
        person_id
        for person_id in party.traveller_ids
        if people[person_id].alive and able_to_fight(people[person_id])
    ]
    if len(living) < MIN_BESIEGERS:
        return _break_camp(state, party, SiegeEnd.TOO_FEW)
    home = travel_days(
        state.world_map,
        tuple(reversed(party.route))[1:],
        grades_of(state.roads),
        start=party.route[-1],
        bridges=bridged_edges(state.bridges),
    )
    if party.provisions < len(living) * home:
        return _break_camp(state, party, SiegeEnd.STARVED)
    catapults = crewed_engines(len(living), engines_in(party.cargo)).get(Resource.CATAPULT, 0)
    if not catapults:
        return []
    roll = rng.stream(f"day:{state.day}:siege:{siege.siege_id}")
    hits = sum(int(roll.integers(0, BASIS)) < CATAPULT_HITS_BP for _ in range(catapults))
    return _bombard(state, siege, hits, roll) if hits else []


def _bombard(
    state: WorldState, siege: Siege, hits: int, roll: np.random.Generator
) -> list[DomainEvent]:
    """Catapult hits batter the walls, or wound people in a settlement without them."""
    events: list[DomainEvent] = []
    walls = _walls_at(state, siege.defender_id, siege.settlement_tile)
    if walls is not None:
        after, fell = battered(walls, hits * WALL_HIT)
        civilization = state.civilizations[siege.defender_id]
        civilization.walls = tuple(
            item
            for item in (
                after if item.settlement_id == siege.settlement_id else item
                for item in civilization.walls
            )
            if item is not None
        )
        events.append(
            _event(
                state,
                EventPhase.MOVEMENT,
                "walls_fell" if fell else "walls_damaged",
                str(siege.besieger_id),
                str(siege.settlement_id),
                grade=after.grade.value if after is not None else "none",
                strength=after.strength if after is not None else 0,
            )
        )
        return events
    people = state.civilizations[siege.defender_id].population.people
    inside = sorted(
        person_id
        for person_id, person in people.items()
        if person.alive and person.location == siege.settlement_tile
    )
    for _ in range(hits):
        if not inside:
            break
        victim = people[inside.pop(int(roll.integers(0, len(inside))))]
        wound = int(roll.integers(WOUND_MIN, WOUND_MAX + 1))
        if wound >= victim.health_bp:
            victim.health_bp = 0
            victim.alive = False
            victim.death_day = state.day
            events.append(
                _event(
                    state,
                    EventPhase.DEATH,
                    "person_died",
                    str(siege.defender_id),
                    str(victim.person_id),
                    cause="bombardment",
                )
            )
        else:
            victim.health_bp -= wound
            events.append(
                _event(
                    state,
                    EventPhase.MOVEMENT,
                    "person_wounded",
                    str(siege.defender_id),
                    str(victim.person_id),
                    damage=wound,
                )
            )
    return events


def _siege_order(
    state: WorldState, civilization_id: EntityId, command: DirectOrder
) -> list[DomainEvent]:
    """Lift a siege and march home, or storm the settlement from the camp."""
    party = next(item for item in state.journeys if item.journey_id == command.journey_id)
    if command.kind is DirectOrderKind.BURN_STOREHOUSE:
        return _burn_storehouse(state, command)
    if command.kind is DirectOrderKind.LIFT_SIEGE and party.objective is WarObjective.OCCUPY:
        return _withdraw(state, party, OccupationEnd.RECALLED)
    if command.kind is DirectOrderKind.LIFT_SIEGE:
        return _break_camp(state, party, SiegeEnd.RECALLED)
    siege = _siege_of(state, party.journey_id)
    assert siege is not None
    # The camp marches the last step into the settlement and fights there tomorrow.
    _replace_journey(
        state,
        party.model_copy(
            update={
                "encamped": False,
                "route": (*party.route, siege.settlement_tile),
                "objective": command.war_objective or WarObjective.ATTACK,
            }
        ),
    )
    return _close_siege(state, party.journey_id, SiegeEnd.STORMED)


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


def _start_craft(state: WorldState, civilization_id: EntityId, command: DirectOrder) -> DomainEvent:
    """Take the materials now; the workers then make the items day by day."""
    assert command.craft_item is not None
    civilization = state.civilizations[civilization_id]
    materials = craft_materials(command.craft_item, command.craft_quantity)
    job_id = EntityId(f"craft:{civilization_id}:{state.day}:{command.command_id}")
    workshop = civilization.population.people[command.worker_ids[0]].location
    if not has(civilization, workshop, materials):
        return _event(state, EventPhase.WORK, "craft_unfunded", str(civilization_id), str(job_id))
    take(civilization, workshop, materials)
    job = CraftJob(
        job_id=job_id,
        item=command.craft_item,
        quantity=command.craft_quantity,
        worker_ids=tuple(sorted(command.worker_ids)),
        workshop=workshop,
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


def _raise_storehouse(
    state: WorldState,
    civilization_id: EntityId,
    storehouse_id: EntityId,
    settlement_id: EntityId,
    old: StorehouseGrade | None,
    new: StorehouseGrade,
) -> DomainEvent:
    """Record a storehouse's new grade and add the room it gained to its settlement's store."""
    civilization = state.civilizations[civilization_id]
    site = next(item for item in civilization.settlements if item.settlement_id == settlement_id)
    gained = STOREHOUSE_GRADES[new].capacity - (
        0 if old is None else STOREHOUSE_GRADES[old].capacity
    )
    capacity = enlarge(civilization, site.tile, gained)
    built = Storehouse(
        storehouse_id=storehouse_id,
        settlement_id=settlement_id,
        grade=new,
        built_day=state.day,
    )
    civilization.storehouses = tuple(
        sorted(
            (
                *(item for item in civilization.storehouses if item.storehouse_id != storehouse_id),
                built,
            ),
            key=lambda item: item.storehouse_id,
        )
    )
    return _event(
        state,
        EventPhase.PROJECT,
        "storehouse_built" if old is None else "storehouse_upgraded",
        str(civilization_id),
        str(storehouse_id),
        grade=new.value,
        settlement=str(settlement_id),
        capacity=capacity,
    )


def _start_storehouse(
    state: WorldState, civilization_id: EntityId, command: DirectOrder
) -> DomainEvent:
    """Take every step's materials now; the builders then raise the grades one by one."""
    assert command.storehouse_grade is not None
    civilization = state.civilizations[civilization_id]
    tile = civilization.population.people[command.worker_ids[0]].location
    site = settlement_at(civilization, tile)
    assert site is not None
    current = next(
        (
            item.grade
            for item in civilization.storehouses
            if item.storehouse_id == command.storehouse_id
        ),
        None,
    )
    job_id = EntityId(f"storehouse-job:{civilization_id}:{state.day}:{command.command_id}")
    materials = step_materials(current, command.storehouse_grade)
    if not has(civilization, tile, materials):
        return _event(
            state, EventPhase.PROJECT, "storehouse_unfunded", str(civilization_id), str(job_id)
        )
    take(civilization, tile, materials)
    storehouse_id = command.storehouse_id or EntityId(
        f"storehouse:{site.settlement_id}:{state.day:06d}:{command.command_id}"
    )
    job = StorehouseJob(
        job_id=job_id,
        storehouse_id=storehouse_id,
        settlement_id=site.settlement_id,
        tile=tile,
        worker_ids=tuple(sorted(command.worker_ids)),
        start_grade=current,
        target=command.storehouse_grade,
        started_day=state.day,
    )
    civilization.storehouse_jobs = (*civilization.storehouse_jobs, job)
    return _event(
        state,
        EventPhase.PROJECT,
        "storehouse_work_started",
        str(civilization_id),
        str(storehouse_id),
        target=job.target.value,
    )


def _advance_storehouses(state: WorldState) -> list[DomainEvent]:
    """Builders at the site put in a day each; every finished grade adds its room at once."""
    events: list[DomainEvent] = []
    away = _away(state)
    for civilization_id in sorted(state.civilizations):
        civilization = state.civilizations[civilization_id]
        people = civilization.population.people
        kept: list[StorehouseJob] = []
        for job in civilization.storehouse_jobs:
            living = [person_id for person_id in job.worker_ids if people[person_id].alive]
            present = sum(
                people[person_id].location == job.tile and person_id not in away
                for person_id in living
            )
            before = job.built()
            present += _workshop_bonus(state, civilization_id, job.tile, present, away)
            job = job.model_copy(update={"person_days_done": job.person_days_done + present})
            after = job.built()
            if after is not None and after != before:
                events.append(
                    _raise_storehouse(
                        state, civilization_id, job.storehouse_id, job.settlement_id, before, after
                    )
                )
            if after is job.target:
                continue
            if not living:
                # With every builder dead, the unused materials go back into the store.
                put(civilization, job.tile, step_materials(after, job.target))
                events.append(
                    _event(
                        state,
                        EventPhase.PROJECT,
                        "storehouse_work_stopped",
                        str(civilization_id),
                        str(job.storehouse_id),
                        grade=after.value if after is not None else "none",
                    )
                )
                continue
            kept.append(job)
        civilization.storehouse_jobs = tuple(kept)
    return events


def _gather(
    world_map: WorldMap,
    tiles: Sequence[HexCoord],
    larder: Inventory,
    hands: int,
    target: int,
) -> tuple[Inventory, dict[Resource, int]]:
    """Rules version 2: spare hands gather timber up to the target, then stone up to half
    of it, as fast as the woods and rock allow. A tool in store doubles one gatherer's day."""
    gathered: dict[Resource, int] = {}
    for resource, goal, most in (
        (Resource.TIMBER, target, timber_capacity(world_map, tiles)),
        (Resource.STONE, target // 2, stone_capacity(world_map, tiles)),
    ):
        if hands <= 0:
            break
        tools = min(larder.quantities.get(Resource.TOOL, 0), hands)
        room = larder.capacity - larder.total_units
        units = min(hands + tools, most, goal - larder.quantities.get(resource, 0), room)
        if units <= 0:
            continue
        larder = larder.apply_delta(InventoryDelta(changes={resource: units}))
        gathered[resource] = units
        # A gatherer with a tool brings in two units, so fewer hands are spent.
        hands -= units - min(tools, units // 2)
    return larder, gathered


def _expire_decrees(state: WorldState) -> list[DomainEvent]:
    """Rules version 2: a decree ends when its days run out, unless a council renews it."""
    events: list[DomainEvent] = []
    for civilization_id in sorted(state.active_decrees):
        decrees = state.active_decrees[civilization_id]
        for kind in sorted(key for key in decrees if not key.endswith("_expires")):
            expires = decrees.get(f"{kind}_expires")
            if expires is not None and expires <= state.day:
                del decrees[kind]
                del decrees[f"{kind}_expires"]
                events.append(
                    _event(
                        state,
                        EventPhase.COMMAND,
                        "decree_expired",
                        str(civilization_id),
                        decree=kind,
                    )
                )
    return events


def _start_houses(
    state: WorldState, civilization_id: EntityId, command: DirectOrder
) -> list[DomainEvent]:
    """Builders take the timber and stone for their houses from their settlement's store."""
    civilization = state.civilizations[civilization_id]
    assert command.project_id is not None
    site = settlement_at(
        civilization, civilization.population.people[command.worker_ids[0]].location
    )
    assert site is not None
    grade = command.house_grade or best_grade(civilization.capabilities)
    return [
        _open_house_job(
            state,
            civilization_id,
            HouseJob(
                job_id=command.project_id,
                settlement_id=site.settlement_id,
                tile=site.tile,
                worker_ids=tuple(sorted(command.worker_ids)),
                grade=grade,
                count=command.house_count,
                started_day=state.day,
            ),
            "council",
        )
    ]


def _open_house_job(
    state: WorldState, civilization_id: EntityId, job: HouseJob, source: str
) -> DomainEvent:
    civilization = state.civilizations[civilization_id]
    take(civilization, job.tile, house_materials(job.grade, job.count))
    civilization.house_jobs = tuple(
        sorted((*civilization.house_jobs, job), key=lambda item: item.job_id)
    )
    return _event(
        state,
        EventPhase.PROJECT,
        "house_work_started",
        str(civilization_id),
        str(job.job_id),
        settlement=str(job.settlement_id),
        grade=job.grade.value,
        count=job.count,
        source=source,
    )


def _apply_housing_policy(state: WorldState) -> list[DomainEvent]:
    """Rules version 2: under a housing policy, a settlement short of spare room sets its two
    lowest-numbered idle grown-ups to raising a house, if its store can pay for one."""
    events: list[DomainEvent] = []
    for civilization_id in sorted(state.civilizations):
        civilization = state.civilizations[civilization_id]
        target = state.active_decrees.get(civilization_id, {}).get("housing_policy", 0)
        if target <= 0 or civilization.eliminated_day is not None:
            continue
        residents = residents_by_settlement(state, civilization_id)
        busy = _busy_at_home(state, civilization_id)
        building = {job.settlement_id for job in civilization.house_jobs}
        people = civilization.population.people
        for settlement in civilization.settlements:
            sid = settlement.settlement_id
            if sid in building:
                continue
            count = len(residents.get(sid, ()))
            spare = slots_of(civilization, sid) - count
            if spare * 100 >= target * max(count, 1):
                continue
            # The best house the store can pay for; huts when stone runs short.
            grade = (
                affordable_grade(
                    civilization.capabilities,
                    store_at(civilization, settlement.tile).quantities,
                )
                if rules_for(state.rules_version).cover_mechanics
                else best_grade(civilization.capabilities)
            )
            if grade is None or not has(civilization, settlement.tile, house_materials(grade, 1)):
                continue
            idle = [
                person_id
                for person_id in civilization.population.living_ids
                if person_id not in busy
                and people[person_id].location == settlement.tile
                and people[person_id].captive_of is None
                and people[person_id].age_days >= GROWN_DAYS
            ][:2]
            if len(idle) < 2:
                continue
            events.append(
                _open_house_job(
                    state,
                    civilization_id,
                    HouseJob(
                        job_id=EntityId(f"house-job:{sid}:{state.day}"),
                        settlement_id=sid,
                        tile=settlement.tile,
                        worker_ids=tuple(idle),
                        grade=grade,
                        count=1,
                        started_day=state.day,
                    ),
                    "housing_policy",
                )
            )
            busy.update(idle)
    return events


GROWN_DAYS = 16 * 365
"""The age at which a person can be set to building."""


def _busy_at_home(state: WorldState, civilization_id: EntityId) -> set[EntityId]:
    """People already bound to a duty, on the road, or in a garrison."""
    civilization = state.civilizations[civilization_id]
    return (
        _away(state)
        | {person_id for garrison in civilization.garrisons for person_id in garrison.member_ids}
        | {person_id for drill in civilization.drills for person_id in drill.person_ids}
        | {person_id for job in civilization.craft_jobs for person_id in job.worker_ids}
        | {person_id for job in civilization.storehouse_jobs for person_id in job.worker_ids}
        | {person_id for job in civilization.wall_jobs for person_id in job.worker_ids}
        | {person_id for job in civilization.house_jobs for person_id in job.worker_ids}
        | {person_id for item in civilization.research for person_id in item.scholar_ids}
        | {
            person_id
            for item in civilization.teaching_assignments
            for person_id in (item.teacher_id, item.apprentice_id)
        }
        | {person_id for order in civilization.work_orders for person_id in order.worker_ids}
        | staff_of(civilization)
    )


def _advance_houses(state: WorldState) -> list[DomainEvent]:
    """Builders at the site put in a day each, and each house stands as soon as it is done.
    Houses of a settlement left empty for a year begin to fall, one a month."""
    events: list[DomainEvent] = []
    away = _away(state)
    for civilization_id in sorted(state.civilizations):
        civilization = state.civilizations[civilization_id]
        people = civilization.population.people
        kept: list[HouseJob] = []
        for job in civilization.house_jobs:
            living = [person_id for person_id in job.worker_ids if people[person_id].alive]
            present = sum(
                people[person_id].location == job.tile and person_id not in away
                for person_id in living
            )
            before = job.built()
            present += _workshop_bonus(state, civilization_id, job.tile, present, away)
            job = job.model_copy(update={"person_days_done": job.person_days_done + present})
            after = job.built()
            if after > before:
                housing = civilization.housing.get(job.settlement_id) or Housing()
                civilization.housing = dict(
                    sorted(
                        {
                            **civilization.housing,
                            job.settlement_id: housing.plus(job.grade, after - before),
                        }.items()
                    )
                )
                events.append(
                    _event(
                        state,
                        EventPhase.PROJECT,
                        "house_built",
                        str(civilization_id),
                        str(job.job_id),
                        settlement=str(job.settlement_id),
                        grade=job.grade.value,
                        count=after - before,
                        slots=slots_of(civilization, job.settlement_id),
                    )
                )
            if after == job.count:
                continue
            if not living:
                # With every builder dead, the unused materials go back into the store.
                put(civilization, job.tile, job.unused_materials())
                events.append(
                    _event(
                        state,
                        EventPhase.PROJECT,
                        "house_work_stopped",
                        str(civilization_id),
                        str(job.job_id),
                        built=after,
                    )
                )
                continue
            kept.append(job)
        civilization.house_jobs = tuple(kept)
        if not civilization.housing:
            continue
        residents = residents_by_settlement(state, civilization_id)
        for sid in sorted(civilization.housing):
            housing = civilization.housing[sid]
            if residents.get(sid):
                if housing.empty_since is not None:
                    civilization.housing = {
                        **civilization.housing,
                        sid: housing.model_copy(update={"empty_since": None}),
                    }
                continue
            if housing.empty_since is None:
                civilization.housing = {
                    **civilization.housing,
                    sid: housing.model_copy(update={"empty_since": state.day}),
                }
                continue
            empty_days = state.day - housing.empty_since
            if (
                empty_days >= ABANDONED_GRACE_DAYS
                and (empty_days - ABANDONED_GRACE_DAYS) % ABANDONED_DECAY_DAYS == 0
            ):
                events.extend(_lose_houses(state, civilization_id, sid, "abandoned"))
    return events


def _lose_houses(
    state: WorldState, civilization_id: EntityId, settlement_id: EntityId, cause: str
) -> list[DomainEvent]:
    """Rules version 2: a stormed or burned settlement loses a quarter of its houses, an
    abandoned one a house at a time; the meanest fall first."""
    if not rules_for(state.rules_version).houses:
        return []
    civilization = state.civilizations[civilization_id]
    housing = civilization.housing.get(settlement_id)
    if housing is None or not housing.count:
        return []
    count = 1 if cause == "abandoned" else housing.count // STORMED_SHARE
    if not count:
        return []
    left, lost = housing.minus(count)
    civilization.housing = {**civilization.housing, settlement_id: left}
    return [
        _event(
            state,
            EventPhase.MOVEMENT,
            "houses_lost",
            str(civilization_id),
            str(settlement_id),
            cause=cause,
            count=lost,
        )
    ]


def _armoury_bonus(
    state: WorldState,
    civilization_id: EntityId,
    tile: HexCoord,
    present: int,
    away: set[EntityId],
) -> int:
    """Rules version 2: every second day, each worker making equipment at a settlement with an
    open armoury does a day extra."""
    if not present or state.day % ARMOURY_DAY or not rules_for(state.rules_version).ranks:
        return 0
    civilization = state.civilizations[civilization_id]
    site = supplying(civilization, tile)
    tiles = serving_tiles(civilization, InstitutionKind.ARMOURY, away)
    return present if site is not None and site.tile in tiles else 0


def _workshop_bonus(
    state: WorldState,
    civilization_id: EntityId,
    tile: HexCoord,
    present: int,
    away: set[EntityId],
) -> int:
    """Every fourth day, each worker at a settlement with an open workshop does a day extra."""
    if not present or state.day % WORKSHOP_DAY:
        return 0
    civilization = state.civilizations[civilization_id]
    site = supplying(civilization, tile)
    tiles = serving_tiles(civilization, InstitutionKind.WORKSHOP, away)
    return present if site is not None and site.tile in tiles else 0


def _keep_archive(state: WorldState, civilization_id: EntityId, away: set[EntityId]) -> None:
    """While an archive is open, everything its civilization knows is written down there."""
    civilization = state.civilizations[civilization_id]
    archived = bool(serving_tiles(civilization, InstitutionKind.ARCHIVE, away))
    civilization.capabilities = tuple(
        record
        if record.retained_record == archived
        else record.model_copy(update={"retained_record": archived})
        for record in civilization.capabilities
    )


def _learn_fallen(state: WorldState, learner: EntityId, fallen_id: EntityId) -> list[DomainEvent]:
    """The learner comes to know a civilization has died out, and its treaties with it."""
    civilization = state.civilizations[learner]
    if fallen_id == learner or fallen_id in civilization.fallen:
        return []
    if state.civilizations[fallen_id].eliminated_day is None:
        return []
    civilization.fallen = {**civilization.fallen, fallen_id: state.day}
    _tell_treaty_ends(state, learner, fallen_id)
    return [_event(state, EventPhase.MOVEMENT, "fall_learned", str(learner), str(fallen_id))]


def _learn_by_sight(state: WorldState) -> list[DomainEvent]:
    """What each civilization comes to know today by seeing it, or by its people's return.

    It reaches the council from the next sitting on.
    """
    events: list[DomainEvent] = []
    sight = {
        civilization_id: sight_of(state, civilization_id)
        for civilization_id in sorted(state.civilizations)
    }
    # A defender learns of a siege, and an owner of an occupation, once it is in sight;
    # from then on it follows it to its end, since the camp is at its gates.
    state.sieges = tuple(
        siege.model_copy(update={"defender_learned_day": state.day})
        if siege.defender_learned_day is None
        and siege.active
        and (
            siege.camp in sight[siege.defender_id]
            or siege.settlement_tile in sight[siege.defender_id]
        )
        else siege
        for siege in state.sieges
    )
    state.occupations = tuple(
        occupation.model_copy(update={"owner_learned_day": state.day})
        if occupation.owner_learned_day is None
        and occupation.active
        and occupation.tile in sight[occupation.owner_id]
        else occupation
        for occupation in state.occupations
    )
    ruins = {ruin.tile: ruin for ruin in state.ruins}
    for civilization_id in sorted(state.civilizations):
        civilization = state.civilizations[civilization_id]
        if civilization.eliminated_day is not None:
            continue
        people = civilization.population.people
        # Ruins in sight of its settlements, or of its own people wherever they stand.
        seen = sight[civilization_id] | visible_tiles(
            state.world_map,
            (
                person.location
                for person in people.values()
                if person.alive and person.captive_of is None
            ),
        )
        intel = {view.ruin.tile: view for view in civilization.ruin_intel}
        for tile in sorted(seen & set(ruins)):
            intel[tile] = RuinView(ruin=ruins[tile], as_of_day=state.day)
            events.extend(_learn_fallen(state, civilization_id, ruins[tile].former_civilization_id))
        civilization.ruin_intel = tuple(intel[tile] for tile in sorted(intel))
        # Those who joined a civilization on the day it died out know it is gone.
        for change in (change for person in people.values() for change in person.allegiances):
            origin = state.civilizations.get(change.from_civilization_id)
            if (
                origin is not None
                and change.to_civilization_id == civilization_id
                and change.day == origin.eliminated_day
            ):
                events.extend(_learn_fallen(state, civilization_id, origin.civilization_id))
    for civilization_id in sorted(state.civilizations):
        civilization = state.civilizations[civilization_id]
        people = civilization.population.people
        homes = {settlement.tile for settlement in civilization.settlements}
        # A captive the council knew of is off its list once home and free, or gone from it.
        civilization.known_captives = tuple(
            person_id
            for person_id in civilization.known_captives
            if person_id in people
            and not (people[person_id].captive_of is None and people[person_id].location in homes)
        )
    return events


def _assimilate(state: WorldState) -> list[DomainEvent]:
    """A month among their new people brings each newcomer closer to them."""
    events: list[DomainEvent] = []
    for civilization_id in sorted(state.civilizations):
        people = state.civilizations[civilization_id].population.people
        for person_id in sorted(people):
            person = people[person_id]
            origin = person.culture
            if assimilate(person):
                events.append(
                    _event(
                        state,
                        EventPhase.MOVEMENT,
                        "person_assimilated",
                        str(civilization_id),
                        str(person_id),
                        culture=str(origin),
                    )
                )
    return events


def _study_languages(state: WorldState) -> None:
    """The staff of an open diplomatic service study the tongue of every civilization known."""
    away = _away(state)
    for civilization in state.civilizations.values():
        if not serving_tiles(civilization, InstitutionKind.DIPLOMATIC_SERVICE, away):
            continue
        tongues = sorted({contact.civilization_id for contact in civilization.contacts})
        people = civilization.population.people
        for institution in civilization.institutions:
            if institution.kind is not InstitutionKind.DIPLOMATIC_SERVICE or not institution.built:
                continue
            for person_id in institution.staff_ids:
                person = people.get(person_id)
                if person is None or not person.alive or person.location != institution.tile:
                    continue
                person.languages = {
                    **person.languages,
                    **{
                        tongue: min(100, person.languages.get(tongue, 0) + 1)
                        for tongue in tongues
                        if tongue != native(person)
                    },
                }


def _found_institution(
    state: WorldState, civilization_id: EntityId, command: DirectOrder
) -> DomainEvent:
    """Take the building's materials now; the founders raise it, then keep it."""
    assert command.institution_kind is not None
    civilization = state.civilizations[civilization_id]
    tile = civilization.population.people[command.worker_ids[0]].location
    site = settlement_at(civilization, tile)
    assert site is not None
    kind = command.institution_kind
    institution_id = EntityId(f"institution:{site.settlement_id}:{kind.value}")
    materials = INSTITUTIONS[kind].materials
    if not has(civilization, tile, materials):
        return _event(
            state, EventPhase.PROJECT, "institution_unfunded", str(civilization_id), institution_id
        )
    take(civilization, tile, materials)
    civilization.institutions = tuple(
        sorted(
            (
                *civilization.institutions,
                Institution(
                    institution_id=institution_id,
                    kind=kind,
                    settlement_id=site.settlement_id,
                    tile=site.tile,
                    staff_ids=tuple(sorted(command.worker_ids)),
                    founded_day=state.day,
                ),
            ),
            key=lambda item: item.institution_id,
        )
    )
    return _event(
        state,
        EventPhase.PROJECT,
        "institution_founded",
        str(civilization_id),
        institution_id,
        institution=kind.value,
    )


def _staff_institution(
    state: WorldState, civilization_id: EntityId, command: DirectOrder
) -> DomainEvent:
    civilization = state.civilizations[civilization_id]
    civilization.institutions = tuple(
        item.model_copy(update={"staff_ids": tuple(sorted(command.worker_ids))})
        if item.institution_id == command.institution_id
        else item
        for item in civilization.institutions
    )
    return _event(
        state,
        EventPhase.PROJECT,
        "institution_staffed",
        str(civilization_id),
        str(command.institution_id),
        staff=len(command.worker_ids),
    )


def _advance_institutions(state: WorldState) -> list[DomainEvent]:
    """Founders raise their building; an institution lost with its settlement is gone."""
    events: list[DomainEvent] = []
    away = _away(state)
    for civilization_id in sorted(state.civilizations):
        civilization = state.civilizations[civilization_id]
        people = civilization.population.people
        own = {settlement.settlement_id for settlement in civilization.settlements}
        kept: list[Institution] = []
        for institution in civilization.institutions:
            if institution.settlement_id not in own:
                events.append(
                    _event(
                        state,
                        EventPhase.PROJECT,
                        "institution_lost",
                        str(civilization_id),
                        str(institution.institution_id),
                        institution=institution.kind.value,
                    )
                )
                continue
            staff = tuple(
                person_id
                for person_id in institution.staff_ids
                if person_id in people and people[person_id].alive
            )
            if staff != institution.staff_ids:
                institution = institution.model_copy(update={"staff_ids": staff})
            if not institution.built:
                if not staff:
                    # With every founder gone, the materials go back into the store.
                    put(civilization, institution.tile, INSTITUTIONS[institution.kind].materials)
                    events.append(
                        _event(
                            state,
                            EventPhase.PROJECT,
                            "institution_abandoned",
                            str(civilization_id),
                            str(institution.institution_id),
                        )
                    )
                    continue
                present = sum(
                    people[person_id].location == institution.tile and person_id not in away
                    for person_id in staff
                )
                present += _workshop_bonus(state, civilization_id, institution.tile, present, away)
                institution = institution.model_copy(
                    update={"person_days_done": institution.person_days_done + present}
                )
                if institution.built:
                    institution = institution.model_copy(update={"opened_day": state.day})
                    events.append(
                        _event(
                            state,
                            EventPhase.PROJECT,
                            "institution_opened",
                            str(civilization_id),
                            str(institution.institution_id),
                            institution=institution.kind.value,
                        )
                    )
            kept.append(institution)
        civilization.institutions = tuple(kept)
    return events


def _walls_at(state: WorldState, civilization_id: EntityId, tile: HexCoord) -> Walls | None:
    civilization = state.civilizations[civilization_id]
    site = settlement_at(civilization, tile)
    if site is None:
        return None
    return next(
        (item for item in civilization.walls if item.settlement_id == site.settlement_id), None
    )


def _start_walls(state: WorldState, civilization_id: EntityId, command: DirectOrder) -> DomainEvent:
    """Take every step's or tower's materials now; the builders then work day by day."""
    civilization = state.civilizations[civilization_id]
    tile = civilization.population.people[command.worker_ids[0]].location
    site = settlement_at(civilization, tile)
    assert site is not None
    walls = _walls_at(state, civilization_id, tile)
    current = walls.grade if walls is not None else None
    job_id = EntityId(f"wall-job:{civilization_id}:{state.day}:{command.command_id}")
    if command.kind is DirectOrderKind.BUILD_TOWERS:
        assert current is not None
        materials = tower_materials(current, command.tower_count)
    elif command.kind is DirectOrderKind.REPAIR_WALLS:
        assert current is not None
        materials = repair_materials(current)
    else:
        assert command.wall_grade is not None
        materials = wall_step_materials(current, command.wall_grade)
    if not has(civilization, tile, materials):
        return _event(
            state, EventPhase.PROJECT, "walls_unfunded", str(civilization_id), str(job_id)
        )
    take(civilization, tile, materials)
    job = WallJob(
        job_id=job_id,
        settlement_id=site.settlement_id,
        tile=tile,
        worker_ids=tuple(sorted(command.worker_ids)),
        start_grade=current,
        target=command.wall_grade if command.kind is DirectOrderKind.BUILD_WALLS else None,
        towers=command.tower_count if command.kind is DirectOrderKind.BUILD_TOWERS else 0,
        repair=command.kind is DirectOrderKind.REPAIR_WALLS,
        started_day=state.day,
    )
    civilization.wall_jobs = (*civilization.wall_jobs, job)
    return _event(
        state,
        EventPhase.PROJECT,
        "wall_work_started",
        str(civilization_id),
        str(site.settlement_id),
        target=job.target.value if job.target is not None else "repair" if job.repair else "towers",
        towers=job.towers,
    )


def _set_walls(
    state: WorldState, civilization_id: EntityId, settlement_id: EntityId, walls: Walls
) -> None:
    civilization = state.civilizations[civilization_id]
    civilization.walls = tuple(
        sorted(
            (*(item for item in civilization.walls if item.settlement_id != settlement_id), walls),
            key=lambda item: item.settlement_id,
        )
    )


def _advance_walls(state: WorldState) -> list[DomainEvent]:
    """Builders at the site put in a day each; each finished grade or tower stands at once."""
    events: list[DomainEvent] = []
    away = _away(state)
    for civilization_id in sorted(state.civilizations):
        civilization = state.civilizations[civilization_id]
        people = civilization.population.people
        kept: list[WallJob] = []
        for job in civilization.wall_jobs:
            living = [person_id for person_id in job.worker_ids if people[person_id].alive]
            present = sum(
                people[person_id].location == job.tile and person_id not in away
                for person_id in living
            )
            before_grade, before_towers = job.built(), job.towers_built()
            present += _workshop_bonus(state, civilization_id, job.tile, present, away)
            job = job.model_copy(update={"person_days_done": job.person_days_done + present})
            after_grade, after_towers = job.built(), job.towers_built()
            walls = _walls_at(state, civilization_id, job.tile)
            if after_grade is not None and after_grade != before_grade:
                # A new grade stands at full strength and keeps the towers it has.
                _set_walls(
                    state,
                    civilization_id,
                    job.settlement_id,
                    Walls(
                        settlement_id=job.settlement_id,
                        grade=after_grade,
                        strength=WALL_GRADES[after_grade].strength,
                        towers=walls.towers if walls is not None else 0,
                        built_day=state.day,
                    ),
                )
                events.append(
                    _event(
                        state,
                        EventPhase.PROJECT,
                        "walls_built" if before_grade is None else "walls_raised",
                        str(civilization_id),
                        str(job.settlement_id),
                        grade=after_grade.value,
                    )
                )
            if after_towers > before_towers and walls is not None:
                _set_walls(
                    state,
                    civilization_id,
                    job.settlement_id,
                    walls.model_copy(
                        update={"towers": walls.towers + after_towers - before_towers}
                    ),
                )
                events.append(
                    _event(
                        state,
                        EventPhase.PROJECT,
                        "towers_built",
                        str(civilization_id),
                        str(job.settlement_id),
                        towers=walls.towers + after_towers - before_towers,
                    )
                )
            if job.repair and job.done:
                standing = _walls_at(state, civilization_id, job.tile)
                if standing is not None:
                    _set_walls(
                        state,
                        civilization_id,
                        job.settlement_id,
                        standing.model_copy(
                            update={"strength": WALL_GRADES[standing.grade].strength}
                        ),
                    )
                    events.append(
                        _event(
                            state,
                            EventPhase.PROJECT,
                            "walls_repaired",
                            str(civilization_id),
                            str(job.settlement_id),
                            grade=standing.grade.value,
                        )
                    )
            if job.done:
                continue
            if not living:
                # With every builder dead, the materials not yet used go back into the store.
                if job.target is not None:
                    unused = wall_step_materials(after_grade, job.target)
                elif job.repair:
                    assert job.start_grade is not None
                    unused = repair_materials(job.start_grade)
                else:
                    assert job.start_grade is not None
                    unused = tower_materials(job.start_grade, job.towers - after_towers)
                put(civilization, job.tile, unused)
                events.append(
                    _event(
                        state,
                        EventPhase.PROJECT,
                        "wall_work_stopped",
                        str(civilization_id),
                        str(job.settlement_id),
                    )
                )
                continue
            kept.append(job)
        civilization.wall_jobs = tuple(kept)
    return events


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
            present += _workshop_bonus(state, civilization_id, job.workshop, present, away)
            present += _armoury_bonus(state, civilization_id, job.workshop, present, away)
            job = job.model_copy(update={"person_days_done": job.person_days_done + present})
            if not job.done:
                kept.append(job)
                continue
            waste = put(civilization, job.workshop, {job.item: job.quantity})
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


def _advance_research(state: WorldState) -> list[DomainEvent]:
    """Scholars at home add points to their topic; enough points make a discovery."""
    events: list[DomainEvent] = []
    away = _away(state)
    for civilization_id in sorted(state.civilizations):
        civilization = state.civilizations[civilization_id]
        homes = {settlement.tile for settlement in civilization.settlements}
        people = civilization.population.people
        points = dict(civilization.research_points)
        cities = {
            settlement.tile
            for settlement in civilization.settlements
            if civilization.ranks_reached.get(settlement.settlement_id) is SettlementRank.CITY
        }
        # A great realm's civil learning needs a school or archive to keep pace.
        slow_civil = (
            rules_for(state.rules_version).civil_research
            and realm_at_least(civilization.realm_rank_reached, CIVIL_HALF_RATE_RANK)
            and not serving_tiles(civilization, InstitutionKind.SCHOOL, away)
            and not serving_tiles(civilization, InstitutionKind.ARCHIVE, away)
        )
        kept: list[ResearchAssignment] = []
        for assignment in civilization.research:
            present = [
                people[person_id]
                for person_id in assignment.scholar_ids
                if people[person_id].alive
                and people[person_id].location in homes
                and person_id not in away
            ]
            earned = sum(
                POINTS_PER_SCHOLAR
                + (WRITING_BONUS if person.skills.get(CapabilityId.WRITING.value, 0) > 0 else 0)
                + (CITY_RESEARCH_BONUS if person.location in cities else 0)
                for person in present
            )
            if assignment.topic in CIVIL_TOPICS and slow_civil:
                earned //= 2
            points[assignment.topic] = points.get(assignment.topic, 0) + earned
            assignment = assignment.model_copy(update={"days_done": assignment.days_done + 1})
            if assignment.active:
                kept.append(assignment)
            else:
                events.append(
                    _event(
                        state,
                        EventPhase.WORK,
                        "research_paused",
                        str(civilization_id),
                        str(assignment.assignment_id),
                        topic=assignment.topic.value,
                        points=points[assignment.topic],
                    )
                )
        for topic in sorted(points):
            if points[topic] < TOPICS[topic].cost or knows(civilization.capabilities, topic):
                continue
            # The discovery belongs to everyone who worked on it and is alive to know it.
            discoverers = sorted(
                {
                    person_id
                    for assignment in (*civilization.research, *kept)
                    if assignment.topic is topic
                    for person_id in assignment.scholar_ids
                    if people[person_id].alive
                }
            )
            if not discoverers:
                continue
            for person_id in discoverers:
                person = people[person_id]
                person.skills = {
                    **person.skills,
                    topic.value: max(person.skills.get(topic.value, 0), DISCOVERED_SKILL),
                }
            civilization.capabilities = tuple(
                sorted(
                    (
                        *civilization.capabilities,
                        CapabilityRecord(
                            capability=topic,
                            practitioner_ids=tuple(discoverers),
                            discovered_day=state.day,
                        ),
                    ),
                    key=lambda record: record.capability.value,
                )
            )
            del points[topic]
            kept = [assignment for assignment in kept if assignment.topic is not topic]
            events.append(
                _event(
                    state,
                    EventPhase.WORK,
                    "research_completed",
                    str(civilization_id),
                    topic.value,
                    scholars=len(discoverers),
                )
            )
        civilization.research = tuple(kept)
        civilization.research_points = points
    return events


def _advance_drills(state: WorldState) -> list[DomainEvent]:
    """Drilling people at their settlements gain arms slowly, up to the drill cap."""
    events: list[DomainEvent] = []
    away = _away(state)
    for civilization_id in sorted(state.civilizations):
        civilization = state.civilizations[civilization_id]
        homes = {settlement.tile for settlement in civilization.settlements}
        people = civilization.population.people
        grounds = (
            serving_tiles(civilization, InstitutionKind.TRAINING_GROUNDS, away)
            if rules_for(state.rules_version).ranks
            else set()
        )
        kept: list[Drill] = []
        for drill in civilization.drills:
            present = [
                person_id
                for person_id in drill.person_ids
                if people[person_id].alive
                and people[person_id].location in homes
                and person_id not in away
            ]
            # Drill doctrine teaches twice as fast, and further.
            pace, cap = (
                (2, DOCTRINE_DRILL_CAP)
                if knows(civilization.capabilities, CapabilityId.DRILL_DOCTRINE)
                else (1, DRILL_CAP)
            )
            before = drill.days_done * pace // DRILL_DAYS_PER_POINT
            drill = drill.model_copy(update={"days_done": drill.days_done + 1})
            gained = drill.days_done * pace // DRILL_DAYS_PER_POINT - before
            if gained:
                for person_id in present:
                    person = people[person_id]
                    current = person.skills.get(ARMS, 0)
                    reach = cap + TRAINING_CAP_BONUS if person.location in grounds else cap
                    if current < reach:
                        person.skills = {**person.skills, ARMS: min(current + gained, reach)}
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


def _record_bridge(state: WorldState, built: BridgeBuilt) -> DomainEvent:
    """Bridge a river border; every traveller crosses it at plain cost from now on."""
    bridge = Bridge(
        a=built.a, b=built.b, civilization_id=built.civilization_id, built_day=state.day
    )
    state.bridges = tuple(sorted((*state.bridges, bridge), key=lambda item: (item.a, item.b)))
    return _event(
        state,
        EventPhase.PROJECT,
        "bridge_built",
        str(built.civilization_id),
        _tile_id(built.a),
        depth=built.depth,
        journey=str(built.journey_id),
        q=built.a.q,
        r=built.a.r,
        across_q=built.b.q,
        across_r=built.b.r,
    )


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
                person.alive
                and person.location == settlement.tile
                and person_id not in away_here
                and person.captive_of is None
                for person_id, person in civilization.population.people.items()
            )
    return counts


def _tile_id(tile: HexCoord) -> str:
    return f"tile:{tile.q},{tile.r}"


def _hall_bonuses(state: WorldState) -> dict[EntityId, int]:
    """Rules version 2: settlements whose hall is open reach a little further."""
    away = _away(state)
    bonuses: dict[EntityId, int] = {}
    for civilization in state.civilizations.values():
        halls = serving_tiles(civilization, InstitutionKind.HALL, away)
        for settlement in civilization.settlements:
            if settlement.tile in halls:
                bonuses[settlement.settlement_id] = HALL_STRENGTH
    return bonuses


def _advance_ranks(state: WorldState) -> list[DomainEvent]:
    """Rules version 2: each settlement, and then the realm, moves at most one rank a month."""
    events: list[DomainEvent] = []
    away = _away(state)
    for civilization_id in sorted(state.civilizations):
        civilization = state.civilizations[civilization_id]
        if civilization.eliminated_day is not None:
            continue
        own = civilization.population.people
        residents = {
            sid: sum(person_id in own for person_id in people)
            for sid, people in residents_by_settlement(state, civilization_id).items()
        }
        open_at = institutions_open(civilization, away)
        known = frozenset(record.capability for record in civilization.capabilities)
        ranks: dict[EntityId, SettlementRank] = {}
        for settlement in civilization.settlements:
            sid = settlement.settlement_id
            current = civilization.ranks_reached.get(sid, SettlementRank.VILLAGE)
            facts = settlement_facts(civilization, sid, residents.get(sid, 0), open_at)
            rank = next_settlement_rank(current, facts, known)
            if rank is not current:
                events.append(
                    _event(
                        state,
                        EventPhase.WORK,
                        "settlement_rank_changed",
                        str(civilization_id),
                        str(sid),
                        rank=rank.value,
                        previous=current.value,
                    )
                )
            if rank is not SettlementRank.VILLAGE:
                ranks[sid] = rank
        civilization.ranks_reached = ranks
        current_realm = civilization.realm_rank_reached
        everyone = {
            settlement.settlement_id: ranks.get(settlement.settlement_id, SettlementRank.VILLAGE)
            for settlement in civilization.settlements
        }
        realm = next_realm_rank(current_realm, state, civilization_id, everyone, open_at)
        if realm is not current_realm:
            civilization.realm_rank_reached = realm
            events.append(
                _event(
                    state,
                    EventPhase.WORK,
                    "realm_rank_changed",
                    str(civilization_id),
                    rank=realm.value,
                    previous=current_realm.value,
                )
            )
    return events


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
        besieged=frozenset(siege.settlement_id for siege in state.sieges if siege.active),
        bridges=bridged_edges(state.bridges),
        occupied={
            occupation.settlement_id: occupation.occupier_id
            for occupation in state.occupations
            if occupation.active
        },
        bonuses=_hall_bonuses(state) if rules_for(state.rules_version).ranks else None,
    )
    state.territory = result.territory
    owner_of_source = {
        **{
            settlement.settlement_id: settlement.civilization_id
            for civilization in state.civilizations.values()
            for settlement in civilization.settlements
        },
        **{garrison.garrison_id: garrison.civilization_id for garrison in garrisons},
        **{
            EntityId(f"occupation:{occupation.settlement_id}"): occupation.occupier_id
            for occupation in state.occupations
            if occupation.active
        },
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
    monthly = state.day % state.config.council_interval_days == 0
    for civilization_id in sorted(sovereigns):
        if (
            civilization_id not in state.civilizations
            or state.civilizations[civilization_id].eliminated_day is not None
        ):
            continue
        sovereign = sovereigns[civilization_id]
        # Sovereigns that take them are also called to council by a crisis.
        crisis = (
            not monthly
            and getattr(sovereign, "crisis_councils", False)
            and crisis_council_due(state, civilization_id)
        )
        if not (monthly or crisis):
            continue
        if crisis:
            state.civilizations[civilization_id].last_crisis_council = state.day
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
                and command.project_kind is ProjectKind.SHELTER
                and rules_for(state.rules_version).houses
            ):
                events.extend(_start_houses(state, civilization_id, command))
            elif (
                isinstance(command, DirectOrder)
                and command.kind is DirectOrderKind.START_PROJECT
                and command.project_id is not None
                and command.project_kind is not None
            ):
                civilization = state.civilizations[civilization_id]
                if command.project_id not in civilization.projects:
                    is_storage = command.project_kind is ProjectKind.STORAGE
                    granary = STOREHOUSE_GRADES[StorehouseGrade.GRANARY]
                    materials = dict(granary.materials) if is_storage else {Resource.TIMBER: 40}
                    # A storehouse stands in the settlement of the people who build it.
                    site = (
                        supplying(
                            civilization,
                            civilization.population.people[command.worker_ids[0]].location,
                        )
                        if is_storage and command.worker_ids
                        else None
                    )
                    location = civilization.start_center if site is None else site.tile
                    if has(civilization, location, materials):
                        take(civilization, location, materials)
                        civilization.projects[command.project_id] = ConstructionProject(
                            project_id=command.project_id,
                            location=location,
                            required_materials=materials,
                            delivered_materials=materials,
                            required_labor_minutes=480
                            * (granary.person_days if is_storage else len(command.worker_ids)),
                            adds_capacity=granary.capacity if is_storage else 0,
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
            elif (
                isinstance(command, DirectOrder)
                and command.kind is DirectOrderKind.BUILD_STOREHOUSE
                and command.storehouse_grade is not None
            ):
                events.append(_start_storehouse(state, civilization_id, command))
            elif isinstance(command, DirectOrder) and command.kind in WALL_ORDERS:
                events.append(_start_walls(state, civilization_id, command))
            elif isinstance(command, DirectOrder) and command.kind in CAMP_ORDERS:
                events.extend(_siege_order(state, civilization_id, command))
            elif (
                isinstance(command, DirectOrder)
                and command.kind is DirectOrderKind.FOUND_INSTITUTION
            ):
                events.append(_found_institution(state, civilization_id, command))
            elif (
                isinstance(command, DirectOrder)
                and command.kind is DirectOrderKind.STAFF_INSTITUTION
            ):
                events.append(_staff_institution(state, civilization_id, command))
            elif isinstance(command, DirectOrder) and command.kind is DirectOrderKind.SEND_COURIER:
                events.append(_send_courier(state, civilization_id, command))
            elif (
                isinstance(command, DirectOrder) and command.kind is DirectOrderKind.ANSWER_PETITION
            ):
                events.extend(_answer_petition(state, command))
            elif (
                isinstance(command, DirectOrder)
                and command.kind is DirectOrderKind.RELEASE_PRISONERS
            ):
                events.extend(_free_captives(state, command.captive_ids, "released"))
            elif (
                isinstance(command, DirectOrder)
                and command.kind is DirectOrderKind.RESEARCH
                and command.research_topic is not None
            ):
                civilization = state.civilizations[civilization_id]
                assignment = ResearchAssignment(
                    assignment_id=EntityId(
                        f"research:{civilization_id}:{state.day}:{command.command_id}"
                    ),
                    topic=command.research_topic,
                    scholar_ids=tuple(sorted(command.worker_ids)),
                    started_day=state.day,
                    days=command.research_days,
                )
                civilization.research = (*civilization.research, assignment)
                events.append(
                    _event(
                        state,
                        EventPhase.WORK,
                        "research_started",
                        str(civilization_id),
                        str(assignment.assignment_id),
                        topic=assignment.topic.value,
                        scholars=len(assignment.scholar_ids),
                    )
                )
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
                                required_days=(
                                    SCHOOL_TEACHING_DAYS
                                    if civilization.population.people[command.teacher_id].location
                                    in serving_tiles(
                                        civilization, InstitutionKind.SCHOOL, _away(state)
                                    )
                                    else 30
                                ),
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
                                        terms=command.peace_terms,
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
                                    terms=command.peace_terms,
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
        events.append(
            _event(
                state,
                EventPhase.COMMAND,
                "council_held",
                str(civilization_id),
                commands=len(envelope.commands),
                accepted=len(validation.accepted),
                rejected=len(validation.errors),
                crisis=crisis,
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
    if rules_for(candidate.rules_version).decrees_expire:
        events.extend(_expire_decrees(candidate))
    if candidate.day % candidate.config.council_interval_days == 0:
        for civilization_id in sorted(candidate.civilizations):
            events.extend(_refuse_unanswered(candidate, civilization_id))
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
            bridges=bridged_edges(candidate.bridges),
        )
        civilization.expeditions = expedition_result.expeditions
        civilization.observations = expedition_result.observations
        civilization.known_tiles = tuple(
            observation.tile for observation in civilization.observations
        )
        civilization.population.people.update(expedition_result.people)
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
        bridges=bridged_edges(candidate.bridges),
        briefed=frozenset(
            civilization_id
            for civilization_id, civilization in candidate.civilizations.items()
            if serving_tiles(civilization, InstitutionKind.DIPLOMATIC_SERVICE, _away(candidate))
        ),
    )
    candidate.diplomatic_missions = diplomacy_result.missions
    for civilization_id, people in diplomacy_result.people_by_civilization.items():
        civilization = candidate.civilizations[civilization_id]
        civilization.population.people.update(people)
    for message in diplomacy_result.delivered:
        # Any word from a party that broke a treaty makes plain the treaty is over.
        _tell_treaty_ends(
            candidate, message.recipient_civilization_id, message.sender_civilization_id
        )
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
                    told=True,
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
                                terms=offer.terms,
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
                if offer.kind is TreatyKind.PEACE:
                    events.extend(
                        _make_peace(
                            candidate,
                            next(
                                item
                                for item in candidate.active_treaties
                                if item.treaty_id == offer.offer_id
                            ),
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
    events.extend(_advance_espionage(candidate, rng))
    if rules_for(candidate.rules_version).sites:
        events.extend(_advance_extraction(candidate))
    fed_on_the_road = fed_on_the_road | _march_captives(candidate)
    events.extend(_check_tribute(candidate))
    _settle_newcomers(candidate)
    if candidate.day % candidate.config.council_interval_days == 0:
        events.extend(_escapes(candidate, rng))
    events.extend(_advance_drills(candidate))
    events.extend(_advance_crafting(candidate))
    events.extend(_advance_storehouses(candidate))
    if rules_for(candidate.rules_version).houses:
        events.extend(_apply_housing_policy(candidate))
        events.extend(_advance_houses(candidate))
    events.extend(_advance_walls(candidate))
    events.extend(_advance_institutions(candidate))
    events.extend(_advance_research(candidate))
    events.extend(_advance_tolls(candidate))

    institution_away = _away(candidate)
    for civilization_id in sorted(candidate.civilizations):
        civilization = candidate.civilizations[civilization_id]
        if civilization.eliminated_day is not None:
            continue
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
        drilling = (
            {person_id for drill in civilization.drills for person_id in drill.person_ids}
            | {person_id for job in civilization.craft_jobs for person_id in job.worker_ids}
            | {person_id for job in civilization.storehouse_jobs for person_id in job.worker_ids}
            | {person_id for job in civilization.wall_jobs for person_id in job.worker_ids}
            | {person_id for job in civilization.house_jobs for person_id in job.worker_ids}
            | {
                person_id
                for assignment in civilization.research
                for person_id in assignment.scholar_ids
            }
            | staff_of(civilization)
        )
        # Captives are fed by whoever holds them, not by their own civilization.
        home_living = tuple(
            person_id
            for person_id in civilization.population.living_ids
            if person_id not in away
            and civilization.population.people[person_id].captive_of is None
        )
        decrees = candidate.active_decrees.get(civilization_id, {})
        reserve_days = decrees.get("food_reserve_target", 0)
        labor_priority = decrees.get("labor_priority", 0)
        own_settlements = {item.settlement_id for item in civilization.settlements}
        held = {
            person_id: person
            for other_id, other in sorted(candidate.civilizations.items())
            if other_id != civilization_id
            for person_id, person in other.population.people.items()
            if person.alive
            and person.captive_of == civilization_id
            and person.held_at in own_settlements
        }
        people = {**civilization.population.people, **held}
        # Everyone eats and farms at the settlement that supplies where they stand; captives
        # eat and farm where they are held.
        residents: dict[EntityId, list[EntityId]] = {}
        store_of: dict[HexCoord, EntityId | None] = {}
        for person_id in home_living:
            location = people[person_id].location
            if location not in store_of:
                store_of[location] = store_id_at(civilization, location)
            store_id = store_of[location]
            assert store_id is not None
            residents.setdefault(store_id, []).append(person_id)
        for person_id, person in sorted(held.items()):
            assert person.held_at is not None
            residents.setdefault(person.held_at, []).append(person_id)
        fields: dict[EntityId, list[HexCoord]] = {}
        for coord in civilization.known_tiles:
            store_id = store_id_at(civilization, coord)
            assert store_id is not None
            fields.setdefault(store_id, []).append(coord)
        fed_today = fed_on_the_road & set(people)
        blockaded = {
            siege.settlement_id
            for siege in candidate.sieges
            if siege.active and siege.defender_id == civilization_id
        }
        for store_id in sorted(residents):
            local = residents[store_id]
            living_count = len(local)
            larder = store(civilization, store_id)
            current_food = larder.quantities.get(Resource.FOOD, 0)
            target_food = living_count * reserve_days
            produced = 0
            # Under siege the fields lie outside the walls, beyond reach.
            if labor_priority > 0 and current_food < target_food and store_id not in blockaded:
                capacity = larder.capacity - larder.total_units
                if rules_for(candidate.rules_version).cover_mechanics:
                    farm_capacity = food_capacity(
                        candidate.world_map,
                        fields.get(store_id, ()),
                        irrigation=knows(civilization.capabilities, CapabilityId.IRRIGATION),
                        fishing=knows(civilization.capabilities, CapabilityId.FISHING),
                    )
                else:
                    farm_capacity = sum(
                        (candidate.world_map.tile(coord).soil // 200)
                        + (2 if candidate.world_map.tile(coord).has_water else 0)
                        for coord in fields.get(store_id, ())
                    )
                # People at drill eat but do not work the fields.
                workers = sum(person_id not in drilling for person_id in local)
                produced = min(workers, farm_capacity, target_food - current_food, capacity)
                if produced:
                    larder = larder.apply_delta(InventoryDelta(changes={Resource.FOOD: produced}))
                    events.append(
                        _event(
                            candidate,
                            EventPhase.WORK,
                            "food_produced",
                            str(civilization_id),
                            str(store_id),
                            units=produced,
                        )
                    )
            materials_target = decrees.get("materials_reserve_target", 0)
            if (
                materials_target > 0
                and labor_priority > 0
                and store_id not in blockaded
                and rules_for(candidate.rules_version).cover_mechanics
            ):
                # Hands not needed in the fields cut timber and break stone.
                hands = sum(person_id not in drilling for person_id in local) - produced
                larder, gathered = _gather(
                    candidate.world_map, fields.get(store_id, ()), larder, hands, materials_target
                )
                for resource, units in gathered.items():
                    events.append(
                        _event(
                            candidate,
                            EventPhase.WORK,
                            f"{resource.value}_gathered",
                            str(civilization_id),
                            str(store_id),
                            units=units,
                        )
                    )
            consumed = min(living_count, larder.quantities.get(Resource.FOOD, 0))
            if consumed:
                larder = larder.apply_delta(InventoryDelta(changes={Resource.FOOD: -consumed}))
            set_store(civilization, store_id, larder)
            events.append(
                _event(
                    candidate,
                    EventPhase.CONSUMPTION,
                    "food_consumed",
                    str(civilization_id),
                    str(store_id),
                    units=consumed,
                )
            )
            # When food runs short, the hungriest eat first, so shortage is shared.
            by_need = sorted(
                local,
                key=lambda person_id: (
                    -people[person_id].nutrition_debt,
                    people[person_id].health_bp,
                    person_id,
                ),
            )
            fed_today |= set(by_need[:consumed])
            if consumed < living_count:
                for person_id in by_need[consumed:]:
                    go_hungry(people[person_id])
                events.append(
                    _event(
                        candidate,
                        EventPhase.CONSUMPTION,
                        "food_shortage",
                        str(civilization_id),
                        str(store_id),
                        people=living_count - consumed,
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

        _keep_archive(candidate, civilization_id, institution_away)
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
        civilization.population.people.update(knowledge_result.people)
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
        food_days = holdings(civilization).get(Resource.FOOD, 0) // current_living
        growth_policy = candidate.active_decrees.get(civilization_id, {}).get(
            "population_growth_policy",
            0,
        )
        eligible_mothers: frozenset[EntityId] | None = None
        if rules_for(candidate.rules_version).houses:
            # A settlement's women conceive only with a house for everyone it already feeds
            # and three months' food in its store.
            mothers: set[EntityId] = set()
            if growth_policy > 0:
                for store_id in sorted(residents):
                    local = residents[store_id]
                    food_here = store(civilization, store_id).quantities.get(Resource.FOOD, 0)
                    if food_here // max(1, len(local)) >= 90 and slots_of(
                        civilization, store_id
                    ) >= len(local):
                        mothers.update(
                            person_id
                            for person_id in local
                            if person_id in civilization.population.people
                        )
            eligible_mothers = frozenset(mothers)
        population_result = advance_population_day(
            civilization.population,
            day=candidate.day,
            rng=rng.stream(f"day:{candidate.day}:population:{civilization_id}"),
            food_days=food_days,
            shelter_slots=current_living + 64 if growth_policy > 0 else 0,
            in_place=True,
            eligible_mothers=eligible_mothers,
        )
        civilization.population = population_result.population
        _keep_archive(candidate, civilization_id, institution_away)
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
        civilization.population.people.update(mortality_knowledge_result.people)
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
            project = civilization.projects[project_id]
            if project.adds_capacity:
                # A storage project builds one new granary where it stands.
                site = supplying(civilization, project.location)
                assert site is not None
                events.append(
                    _raise_storehouse(
                        candidate,
                        civilization_id,
                        EntityId(f"storehouse:{site.settlement_id}:{project_id}"),
                        site.settlement_id,
                        None,
                        StorehouseGrade.GRANARY,
                    )
                )
        # Recovery follows the death roll, so the day food returns is still a dangerous one.
        healing = serving_tiles(civilization, InstitutionKind.HEALERS_HOUSE, institution_away)
        for person_id in sorted(fed_today):
            eater = civilization.population.people.get(person_id) or held.get(person_id)
            if eater is not None and eater.alive:
                for _ in range(HEALING_FACTOR if eater.location in healing else 1):
                    recover(eater)

    events.extend(_advance_civilizations(candidate))
    events.extend(_advance_territory(candidate))
    if (
        rules_for(candidate.rules_version).ranks
        and candidate.day % candidate.config.council_interval_days == 0
    ):
        events.extend(_advance_ranks(candidate))
    events.extend(_joined_roads(candidate))
    if candidate.day % LEARNING_INTERVAL == 0:
        learn(
            person
            for civilization in candidate.civilizations.values()
            for person in civilization.population.people.values()
        )
        _study_languages(candidate)
    if candidate.day % ASSIMILATION_INTERVAL == 0:
        events.extend(_assimilate(candidate))
    events.extend(_learn_by_sight(candidate))

    candidate.day += 1
    validate_world(candidate)
    return TransitionResult(state=candidate, events=EventBatch.assign_sequences(events))
