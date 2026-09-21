"""Aggregate authoritative world state and canonical hashing."""

from __future__ import annotations

import hashlib
import json
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from sovereign_world.config import RunManifest, WorldConfig
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
        known_tiles = tuple(
            tile.coord
            for tile in generated.world_map.tiles
            if tile.coord.distance(start.center) <= 4
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

