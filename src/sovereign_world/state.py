"""Aggregate authoritative world state and canonical hashing."""

from __future__ import annotations

import hashlib
import json
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from sovereign_world.capabilities import CapabilityRecord, TeachingAssignment, regional_capability
from sovereign_world.config import RunManifest, WorldConfig
from sovereign_world.diplomacy import (
    ActiveTreaty,
    Contact,
    DiplomaticMessage,
    MissionStatus,
    TreatyOffer,
)
from sovereign_world.exploration import Expedition, ExpeditionStatus, Observation
from sovereign_world.hexmap import HexCoord, WorldMap
from sovereign_world.ids import EntityId, IdAllocator
from sovereign_world.logistics import (
    Journey,
    JourneyKind,
    JourneyOutcome,
    LogisticsNotice,
)
from sovereign_world.people import Population, create_founders
from sovereign_world.resources import Inventory, Resource
from sovereign_world.rng import StableRng
from sovereign_world.work import ConstructionProject, WorkOrder
from sovereign_world.worldgen import generate_world


class CivilizationState(BaseModel):
    civilization_id: EntityId
    start_center: HexCoord
    population: Population
    inventory: Inventory
    work_orders: tuple[WorkOrder, ...] = ()
    projects: dict[EntityId, ConstructionProject] = Field(default_factory=dict)
    known_tiles: tuple[HexCoord, ...] = ()
    observations: tuple[Observation, ...] = ()
    expeditions: tuple[Expedition, ...] = ()
    capabilities: tuple[CapabilityRecord, ...] = ()
    teaching_assignments: tuple[TeachingAssignment, ...] = ()
    contacts: tuple[Contact, ...] = ()
    received_messages: tuple[DiplomaticMessage, ...] = ()
    logistics_notices: tuple[LogisticsNotice, ...] = ()


class WorldState(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    run_id: UUID
    manifest_hash: str
    config: WorldConfig
    day: int = Field(default=0, ge=0)
    world_map: WorldMap
    civilizations: dict[EntityId, CivilizationState]
    active_decrees: dict[EntityId, dict[str, int]] = Field(default_factory=dict)
    diplomatic_missions: tuple[DiplomaticMessage, ...] = ()
    treaty_offers: tuple[TreatyOffer, ...] = ()
    active_treaties: tuple[ActiveTreaty, ...] = ()
    journeys: tuple[Journey, ...] = ()


def _canonical_payload(state: WorldState) -> str:
    return json.dumps(
        state.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )


def state_hash(state: WorldState) -> str:
    return hashlib.sha256(_canonical_payload(state).encode()).hexdigest()


def build_initial_state(manifest: RunManifest) -> WorldState:
    stable_rng = StableRng(manifest.config.seed)
    generated = generate_world(manifest.config, stable_rng)
    civilization_ids = IdAllocator("civilization")
    person_ids = IdAllocator("person")
    civilizations: dict[EntityId, CivilizationState] = {}
    for start in generated.starts:
        civilization_id = civilization_ids.allocate()
        population = create_founders(
            civilization_id=civilization_id,
            start=start,
            count=manifest.config.founders_per_civilization,
            rng=stable_rng.stream(f"founders:{start.civilization_index}"),
            allocator=person_ids,
        )
        capability = regional_capability(start.viability.strength)
        practitioner_ids: list[EntityId] = []
        for person_id, person in population.people.items():
            skill = person.skills[start.viability.strength]
            person.skills[capability.value] = skill
            practitioner_ids.append(person_id)
        known_tiles = tuple(
            tile.coord
            for tile in generated.world_map.tiles
            if tile.coord.distance(start.center) <= 4
        )
        observations = tuple(
            Observation(
                tile=tile,
                observed_day=0,
                observer_id=practitioner_ids[0],
                source="initial",
            )
            for tile in known_tiles
        )
        inventory = Inventory(
            capacity=100_000,
            quantities={
                Resource.FOOD: manifest.config.founders_per_civilization * 730,
                Resource.WATER: manifest.config.founders_per_civilization * 30,
                Resource.TIMBER: 500,
                Resource.STONE: 300,
                Resource.AXE: 8,
            },
        )
        civilizations[civilization_id] = CivilizationState(
            civilization_id=civilization_id,
            start_center=start.center,
            population=population,
            inventory=inventory,
            known_tiles=known_tiles,
            observations=observations,
            capabilities=(
                CapabilityRecord(
                    capability=capability,
                    practitioner_ids=tuple(sorted(practitioner_ids)),
                    discovered_day=0,
                ),
            ),
        )
    return WorldState(
        run_id=manifest.run_id,
        manifest_hash=manifest.content_hash(),
        config=manifest.config,
        world_map=generated.world_map,
        civilizations=civilizations,
    )


def validate_world(state: WorldState) -> None:
    if state.day < 0:
        raise ValueError("world day cannot be negative")
    for civilization_id, civilization in state.civilizations.items():
        if civilization_id != civilization.civilization_id:
            raise ValueError("civilization key does not match state")
        if civilization.population.civilization_id != civilization_id:
            raise ValueError("population belongs to another civilization")
        if any(quantity < 0 for quantity in civilization.inventory.quantities.values()):
            raise ValueError("inventory quantity cannot be negative")
        observation_tiles = tuple(observation.tile for observation in civilization.observations)
        if observation_tiles != tuple(sorted(set(observation_tiles))):
            raise ValueError("observations must be unique and sorted")
        if civilization.known_tiles != observation_tiles:
            raise ValueError("known tiles must mirror private observations")
        expeditions = civilization.expeditions
        sorted_expeditions = tuple(
            sorted(expeditions, key=lambda expedition: expedition.expedition_id)
        )
        if expeditions != sorted_expeditions:
            raise ValueError("expeditions must be sorted")
        if len({expedition.expedition_id for expedition in expeditions}) != len(expeditions):
            raise ValueError("expeditions must be unique")
        for expedition in expeditions:
            if expedition.status is not ExpeditionStatus.ACTIVE:
                continue
            for person_id in expedition.explorer_ids:
                if person_id not in civilization.population.people:
                    raise ValueError("expedition explorer must be local")
        capabilities = tuple(record.capability for record in civilization.capabilities)
        sorted_capabilities = tuple(
            sorted(set(capabilities), key=lambda capability: capability.value)
        )
        if capabilities != sorted_capabilities:
            raise ValueError("civilization capabilities must be unique and sorted")
        for record in civilization.capabilities:
            for person_id in record.practitioner_ids:
                person = civilization.population.people.get(person_id)
                if person is None or not person.alive:
                    raise ValueError("capability practitioner must be living and local")
                if person.skills.get(record.capability.value, 0) <= 0:
                    raise ValueError("capability practitioner must have matching skill")
        assignments = civilization.teaching_assignments
        sorted_assignments = tuple(
            sorted(assignments, key=lambda assignment: assignment.assignment_id)
        )
        if assignments != sorted_assignments:
            raise ValueError("teaching assignments must be sorted")
        if len({assignment.assignment_id for assignment in assignments}) != len(assignments):
            raise ValueError("teaching assignments must be unique")
        contacts = civilization.contacts
        if contacts != tuple(sorted(contacts, key=lambda contact: contact.civilization_id)):
            raise ValueError("contacts must be sorted")
        if len({contact.civilization_id for contact in contacts}) != len(contacts):
            raise ValueError("contacts must be unique")
        for contact in contacts:
            foreign = state.civilizations.get(contact.civilization_id)
            if foreign is None or contact.civilization_id == civilization_id:
                raise ValueError("contact must identify a foreign civilization")
            if contact.settlement != foreign.start_center:
                raise ValueError("contact settlement must match the known foreign start")
        received = civilization.received_messages
        if received != tuple(sorted(received, key=lambda message: message.message_id)):
            raise ValueError("received messages must be sorted")
        if len({message.message_id for message in received}) != len(received):
            raise ValueError("received messages must be unique")
        if any(
            message.recipient_civilization_id != civilization_id
            or message.status is not MissionStatus.DELIVERED
            for message in received
        ):
            raise ValueError("received messages must be delivered to this civilization")
        notices = civilization.logistics_notices
        if notices != tuple(sorted(notices, key=lambda item: item.notice_id)):
            raise ValueError("logistics notices must be sorted")
        if len({item.notice_id for item in notices}) != len(notices):
            raise ValueError("logistics notices must be unique")
    person_owners: dict[EntityId, EntityId] = {}
    for civilization_id, civilization in state.civilizations.items():
        for person_id, person in civilization.population.people.items():
            if person_id in person_owners:
                raise ValueError("person IDs must be globally unique")
            if person.person_id != person_id or person.civilization_id != civilization_id:
                raise ValueError("person record must match its civilization")
            person_owners[person_id] = civilization_id
    missions = state.diplomatic_missions
    if missions != tuple(sorted(missions, key=lambda message: message.message_id)):
        raise ValueError("diplomatic missions must be sorted")
    if len({message.message_id for message in missions}) != len(missions):
        raise ValueError("diplomatic missions must be unique")
    for message in missions:
        sender = state.civilizations.get(message.sender_civilization_id)
        if sender is None or message.recipient_civilization_id not in state.civilizations:
            raise ValueError("diplomatic mission must name existing civilizations")
        if (
            message.status is MissionStatus.IN_TRANSIT
            and message.ambassador_id not in sender.population.people
        ):
            raise ValueError("diplomatic ambassador must belong to sender")
    offers = state.treaty_offers
    if offers != tuple(sorted(offers, key=lambda offer: offer.offer_id)):
        raise ValueError("treaty offers must be sorted")
    if len({offer.offer_id for offer in offers}) != len(offers):
        raise ValueError("treaty offers must be unique")
    for offer in offers:
        if (
            offer.proposer_civilization_id not in state.civilizations
            or offer.recipient_civilization_id not in state.civilizations
            or offer.proposer_civilization_id == offer.recipient_civilization_id
        ):
            raise ValueError("treaty offer parties must be distinct existing civilizations")
    treaties = state.active_treaties
    if treaties != tuple(sorted(treaties, key=lambda treaty: treaty.treaty_id)):
        raise ValueError("active treaties must be sorted")
    if len({treaty.treaty_id for treaty in treaties}) != len(treaties):
        raise ValueError("active treaties must be unique")
    if any(treaty.treaty_id not in {offer.offer_id for offer in offers} for treaty in treaties):
        raise ValueError("active treaty requires a recorded offer")

    journeys = state.journeys
    if journeys != tuple(sorted(journeys, key=lambda journey: journey.journey_id)):
        raise ValueError("journeys must be sorted")
    if len({journey.journey_id for journey in journeys}) != len(journeys):
        raise ValueError("journeys must be unique")
    treaties_by_id = {treaty.treaty_id: treaty for treaty in treaties}
    required_kind = {JourneyKind.SHIPMENT: "trade", JourneyKind.MIGRATION: "migration"}
    busy: set[EntityId] = set()
    for journey in journeys:
        sender = state.civilizations.get(journey.sender_civilization_id)
        if sender is None or journey.recipient_civilization_id not in state.civilizations:
            raise ValueError("journey must name existing civilizations")
        treaty = treaties_by_id.get(journey.treaty_id)
        if (
            treaty is None
            or treaty.kind.value != required_kind[journey.kind]
            or {treaty.proposer_civilization_id, treaty.recipient_civilization_id}
            != {journey.sender_civilization_id, journey.recipient_civilization_id}
        ):
            raise ValueError("journey requires a matching active treaty")
        if not journey.active:
            continue
        for person_id in journey.traveller_ids:
            if person_id not in sender.population.people:
                raise ValueError("travelling party must belong to its sender")
            if person_id in busy:
                raise ValueError("a person cannot travel on two journeys at once")
            busy.add(person_id)
            person = sender.population.people[person_id]
            if person.alive and person.location != journey.route[journey.route_index]:
                raise ValueError("living travellers must stand on their route position")
        if journey.carrying_cargo and journey.outcome not in {
            JourneyOutcome.PENDING,
            JourneyOutcome.FAILED,
        }:
            raise ValueError("only undelivered cargo can still be carried")
