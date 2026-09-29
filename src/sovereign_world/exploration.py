"""Deterministic travel and civilization-private observation records."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sovereign_world.hexmap import HexCoord, WorldMap
from sovereign_world.ids import EntityId
from sovereign_world.people import Person
from sovereign_world.travel import MAX_PROGRESS, entry_cost, step


class ExpeditionStatus(StrEnum):
    ACTIVE = "active"
    RETURNED = "returned"
    FAILED = "failed"
    BLOCKED = "blocked"


class Observation(BaseModel):
    model_config = ConfigDict(frozen=True)

    tile: HexCoord
    observed_day: int = Field(ge=0)
    confidence_bp: int = Field(default=10_000, ge=0, le=10_000)
    observer_id: EntityId
    source: Literal["direct", "initial"] = "direct"


class Expedition(BaseModel):
    model_config = ConfigDict(frozen=True)

    expedition_id: EntityId
    explorer_ids: tuple[EntityId, ...]
    route: tuple[HexCoord, ...]
    next_route_index: int = Field(default=0, ge=0)
    travel_progress: int = Field(default=0, ge=0, lt=MAX_PROGRESS)
    status: ExpeditionStatus = ExpeditionStatus.ACTIVE

    @model_validator(mode="after")
    def valid_shape(self) -> Expedition:
        if self.explorer_ids != tuple(sorted(set(self.explorer_ids))):
            raise ValueError("expedition explorers must be unique and sorted")
        if not self.explorer_ids:
            raise ValueError("expedition requires at least one explorer")
        if not self.route:
            raise ValueError("expedition requires a route")
        if self.next_route_index > len(self.route):
            raise ValueError("expedition route index exceeds route length")
        return self


@dataclass(frozen=True, slots=True)
class ExpeditionDayResult:
    expeditions: tuple[Expedition, ...]
    people: dict[EntityId, Person]
    observations: tuple[Observation, ...]
    observed_tiles: tuple[HexCoord, ...]
    returned_ids: tuple[EntityId, ...]
    failed_ids: tuple[EntityId, ...]
    blocked_ids: tuple[EntityId, ...] = ()


def advance_expeditions(
    expeditions: tuple[Expedition, ...],
    people: dict[EntityId, Person],
    world_map: WorldMap,
    day: int,
    *,
    observations: tuple[Observation, ...] = (),
) -> ExpeditionDayResult:
    """Advance each active expedition toward its next tile and refresh its private map.

    Rough terrain takes more than a day to enter; water stops the expedition, which
    observes the water it cannot cross.
    """
    updated_people = {
        person_id: person.model_copy(deep=True) for person_id, person in people.items()
    }
    observation_by_tile = {observation.tile: observation for observation in observations}
    updated_expeditions: list[Expedition] = []
    observed_tiles: list[HexCoord] = []
    returned_ids: list[EntityId] = []
    failed_ids: list[EntityId] = []
    blocked_ids: list[EntityId] = []
    for expedition in sorted(expeditions, key=lambda item: item.expedition_id):
        if expedition.status is not ExpeditionStatus.ACTIVE:
            updated_expeditions.append(expedition)
            continue
        explorers = [updated_people.get(person_id) for person_id in expedition.explorer_ids]
        if any(explorer is None or not explorer.alive for explorer in explorers):
            updated_expeditions.append(
                expedition.model_copy(update={"status": ExpeditionStatus.FAILED})
            )
            failed_ids.append(expedition.expedition_id)
            continue
        living_explorers = [explorer for explorer in explorers if explorer is not None]
        locations = {explorer.location for explorer in living_explorers}
        if len(locations) != 1:
            updated_expeditions.append(
                expedition.model_copy(update={"status": ExpeditionStatus.FAILED})
            )
            failed_ids.append(expedition.expedition_id)
            continue
        location = living_explorers[0].location
        route_index = expedition.next_route_index
        if route_index < len(expedition.route) and expedition.route[route_index] == location:
            route_index += 1
        if route_index >= len(expedition.route):
            updated_expeditions.append(
                expedition.model_copy(
                    update={
                        "next_route_index": route_index,
                        "status": ExpeditionStatus.RETURNED,
                    }
                )
            )
            returned_ids.append(expedition.expedition_id)
            continue
        destination = expedition.route[route_index]
        if not world_map.contains(destination) or location.distance(destination) != 1:
            updated_expeditions.append(
                expedition.model_copy(update={"status": ExpeditionStatus.FAILED})
            )
            failed_ids.append(expedition.expedition_id)
            continue
        observer_id = min(expedition.explorer_ids)
        cost = entry_cost(world_map, destination)
        if cost is None:
            observation_by_tile[destination] = Observation(
                tile=destination,
                observed_day=day,
                observer_id=observer_id,
            )
            observed_tiles.append(destination)
            updated_expeditions.append(
                expedition.model_copy(
                    update={
                        "next_route_index": route_index,
                        "status": ExpeditionStatus.BLOCKED,
                        "travel_progress": 0,
                    }
                )
            )
            blocked_ids.append(expedition.expedition_id)
            continue
        entered, progress = step(expedition.travel_progress, cost)
        if not entered:
            updated_expeditions.append(
                expedition.model_copy(
                    update={"next_route_index": route_index, "travel_progress": progress}
                )
            )
            continue
        for explorer in living_explorers:
            explorer.location = destination
        observation_by_tile[destination] = Observation(
            tile=destination,
            observed_day=day,
            observer_id=observer_id,
        )
        observed_tiles.append(destination)
        route_index += 1
        status = (
            ExpeditionStatus.RETURNED
            if route_index >= len(expedition.route)
            else ExpeditionStatus.ACTIVE
        )
        updated_expeditions.append(
            expedition.model_copy(
                update={
                    "next_route_index": route_index,
                    "status": status,
                    "travel_progress": progress,
                }
            )
        )
        if status is ExpeditionStatus.RETURNED:
            returned_ids.append(expedition.expedition_id)
    return ExpeditionDayResult(
        expeditions=tuple(sorted(updated_expeditions, key=lambda item: item.expedition_id)),
        people=updated_people,
        observations=tuple(sorted(observation_by_tile.values(), key=lambda item: item.tile)),
        observed_tiles=tuple(sorted(set(observed_tiles))),
        returned_ids=tuple(sorted(returned_ids)),
        failed_ids=tuple(sorted(failed_ids)),
        blocked_ids=tuple(sorted(blocked_ids)),
    )
