"""Aggregate authoritative world state and canonical hashing."""

from __future__ import annotations

import hashlib
import json
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from sovereign_world.capabilities import CapabilityRecord, TeachingAssignment, regional_capability
from sovereign_world.config import RunManifest, WorldConfig
from sovereign_world.exploration import Expedition, Observation
from sovereign_world.hexmap import HexCoord, WorldMap
from sovereign_world.ids import EntityId, IdAllocator
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


class WorldState(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    run_id: UUID
    manifest_hash: str
    config: WorldConfig
    day: int = Field(default=0, ge=0)
    world_map: WorldMap
    civilizations: dict[EntityId, CivilizationState]
    active_decrees: dict[EntityId, dict[str, int]] = Field(default_factory=dict)


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

