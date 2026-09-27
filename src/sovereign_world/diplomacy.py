"""Deterministic, travel-bound contacts and ambassador messages."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sovereign_world.hexmap import HexCoord
from sovereign_world.ids import EntityId
from sovereign_world.people import Person
from sovereign_world.rng import StableRng


class MissionStatus(StrEnum):
    IN_TRANSIT = "in_transit"
    DELIVERED = "delivered"
    LOST = "lost"


class Contact(BaseModel):
    """A single civilization's dated sighting of a foreign settlement."""

    model_config = ConfigDict(frozen=True)

    civilization_id: EntityId
    settlement: HexCoord
    first_contact_day: int = Field(ge=0)
    last_seen_day: int = Field(ge=0)

    @model_validator(mode="after")
    def valid_dates(self) -> Contact:
        if self.last_seen_day < self.first_contact_day:
            raise ValueError("contact cannot be seen before first contact")
        return self


class DiplomaticMessage(BaseModel):
    """An immutable source message carried by one mortal ambassador."""

    model_config = ConfigDict(frozen=True)

    message_id: EntityId
    sender_civilization_id: EntityId
    recipient_civilization_id: EntityId
    ambassador_id: EntityId
    route: tuple[HexCoord, ...]
    source_text: str = Field(min_length=1, max_length=1_000)
    departed_day: int = Field(ge=0)
    next_route_index: int = Field(default=0, ge=0)
    status: MissionStatus = MissionStatus.IN_TRANSIT
    delivered_day: int | None = Field(default=None, ge=0)
    delivered_text: str | None = Field(default=None, max_length=1_100)

    @model_validator(mode="after")
    def valid_shape(self) -> DiplomaticMessage:
        if self.sender_civilization_id == self.recipient_civilization_id:
            raise ValueError("a message requires a foreign recipient")
        if not self.route:
            raise ValueError("a diplomatic message requires a route")
        if self.next_route_index > len(self.route):
            raise ValueError("message route index exceeds route length")
        if self.status is MissionStatus.DELIVERED and self.delivered_text is None:
            raise ValueError("delivered messages require delivered text")
        if self.status is not MissionStatus.DELIVERED and self.delivered_text is not None:
            raise ValueError("only delivered messages can contain delivered text")
        return self


@dataclass(frozen=True, slots=True)
class DiplomacyDayResult:
    missions: tuple[DiplomaticMessage, ...]
    people_by_civilization: dict[EntityId, dict[EntityId, Person]]
    delivered: tuple[DiplomaticMessage, ...]
    delayed_ids: tuple[EntityId, ...]
    lost_ids: tuple[EntityId, ...]


def _delivered_words(message: DiplomaticMessage, rng: StableRng, day: int) -> str:
    roll = int(
        rng.stream(f"day:{day}:diplomacy:delivery:{message.message_id}").integers(0, 10_000)
    )
    if roll < 1_500:
        return f"[distorted by ambassador] {message.source_text}"
    return message.source_text


def advance_diplomacy_day(
    missions: tuple[DiplomaticMessage, ...],
    people_by_civilization: dict[EntityId, dict[EntityId, Person]],
    *,
    day: int,
    rng: StableRng,
) -> DiplomacyDayResult:
    """Advance each ambassador one known route tile without revealing foreign state."""
    people = {
        civilization_id: {
            person_id: person.model_copy(deep=True)
            for person_id, person in population.items()
        }
        for civilization_id, population in people_by_civilization.items()
    }
    updated: list[DiplomaticMessage] = []
    delivered: list[DiplomaticMessage] = []
    delayed_ids: list[EntityId] = []
    lost_ids: list[EntityId] = []
    for mission in sorted(missions, key=lambda item: item.message_id):
        if mission.status is not MissionStatus.IN_TRANSIT:
            updated.append(mission)
            continue
        ambassador = people.get(mission.sender_civilization_id, {}).get(mission.ambassador_id)
        if ambassador is None or not ambassador.alive:
            updated.append(mission.model_copy(update={"status": MissionStatus.LOST}))
            lost_ids.append(mission.message_id)
            continue
        roll = int(
            rng.stream(f"day:{day}:diplomacy:travel:{mission.message_id}").integers(0, 10_000)
        )
        if roll < 80:
            updated.append(mission.model_copy(update={"status": MissionStatus.LOST}))
            lost_ids.append(mission.message_id)
            continue
        if roll < 900:
            updated.append(mission)
            delayed_ids.append(mission.message_id)
            continue
        route_index = mission.next_route_index
        if route_index < len(mission.route) and mission.route[route_index] == ambassador.location:
            route_index += 1
        if route_index < len(mission.route):
            ambassador.location = mission.route[route_index]
            route_index += 1
        if route_index < len(mission.route):
            updated.append(mission.model_copy(update={"next_route_index": route_index}))
            continue
        completed = mission.model_copy(
            update={
                "next_route_index": route_index,
                "status": MissionStatus.DELIVERED,
                "delivered_day": day,
                "delivered_text": _delivered_words(mission, rng, day),
            }
        )
        updated.append(completed)
        delivered.append(completed)
    return DiplomacyDayResult(
        missions=tuple(sorted(updated, key=lambda item: item.message_id)),
        people_by_civilization=people,
        delivered=tuple(sorted(delivered, key=lambda item: item.message_id)),
        delayed_ids=tuple(sorted(delayed_ids)),
        lost_ids=tuple(sorted(lost_ids)),
    )
