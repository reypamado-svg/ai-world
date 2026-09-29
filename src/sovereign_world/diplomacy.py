"""Deterministic, travel-bound contacts and ambassador messages."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sovereign_world.hexmap import HexCoord, WorldMap
from sovereign_world.ids import EntityId
from sovereign_world.people import Person
from sovereign_world.rng import StableRng
from sovereign_world.travel import DAY, MAX_PROGRESS, Roads, entry_cost


class MissionStatus(StrEnum):
    IN_TRANSIT = "in_transit"
    DELIVERED = "delivered"
    LOST = "lost"


class TreatyKind(StrEnum):
    PEACE = "peace"
    TRADE = "trade"
    MIGRATION = "migration"


class TreatyEndKind(StrEnum):
    CANCELLED = "cancelled"
    BREACHED = "breached"


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


class TreatyOffer(BaseModel):
    """A proposal that cannot take effect without a returned acceptance."""

    model_config = ConfigDict(frozen=True)

    offer_id: EntityId
    proposer_civilization_id: EntityId
    recipient_civilization_id: EntityId
    kind: TreatyKind
    proposed_day: int = Field(ge=0)


class ActiveTreaty(BaseModel):
    """A treaty with delivery evidence on both sides."""

    model_config = ConfigDict(frozen=True)

    treaty_id: EntityId
    proposer_civilization_id: EntityId
    recipient_civilization_id: EntityId
    kind: TreatyKind
    offered_day: int = Field(ge=0)
    activated_day: int = Field(ge=0)
    ended_day: int | None = Field(default=None, ge=0)
    end_kind: TreatyEndKind | None = None
    ended_by: EntityId | None = None

    @model_validator(mode="after")
    def valid_dates(self) -> ActiveTreaty:
        if self.activated_day < self.offered_day:
            raise ValueError("treaty cannot activate before its offer")
        ending = (self.ended_day, self.end_kind, self.ended_by)
        if any(value is None for value in ending) and any(
            value is not None for value in ending
        ):
            raise ValueError("an ended treaty records its day, kind, and party")
        if self.ended_day is not None and self.ended_day < self.activated_day:
            raise ValueError("treaty cannot end before it activates")
        if self.ended_by is not None and self.ended_by not in {
            self.proposer_civilization_id,
            self.recipient_civilization_id,
        }:
            raise ValueError("only a party can end a treaty")
        return self

    @property
    def in_force(self) -> bool:
        return self.ended_day is None

    def counterparty(self, civilization_id: EntityId) -> EntityId:
        if civilization_id == self.proposer_civilization_id:
            return self.recipient_civilization_id
        if civilization_id == self.recipient_civilization_id:
            return self.proposer_civilization_id
        raise ValueError(f"{civilization_id} is not a party to {self.treaty_id}")

    def ended(self, day: int, kind: TreatyEndKind, by: EntityId) -> ActiveTreaty:
        return self.model_copy(update={"ended_day": day, "end_kind": kind, "ended_by": by})


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
    travel_progress: int = Field(default=0, ge=0, lt=MAX_PROGRESS)
    status: MissionStatus = MissionStatus.IN_TRANSIT
    delivered_day: int | None = Field(default=None, ge=0)
    delivered_text: str | None = Field(default=None, max_length=1_100)
    treaty_offer: TreatyOffer | None = None
    acceptance_of: EntityId | None = None
    cancellation_of: EntityId | None = None
    declaration_of: EntityId | None = None
    """The war this message declares; the recipient learns of it on delivery."""

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
        carried = (self.treaty_offer, self.acceptance_of, self.cancellation_of)
        if sum(item is not None for item in carried) > 1:
            raise ValueError("a message carries at most one treaty act")
        if self.treaty_offer is not None and (
            self.treaty_offer.proposer_civilization_id != self.sender_civilization_id
            or self.treaty_offer.recipient_civilization_id != self.recipient_civilization_id
        ):
            raise ValueError("treaty offer parties must match message parties")
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
    world_map: WorldMap,
    roads: Roads | None = None,
) -> DiplomacyDayResult:
    """Advance each ambassador along its known route without revealing foreign state.

    Rough terrain takes more than a day to enter, and roads make it quicker.
    """
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
        progress = mission.travel_progress + DAY
        # A day's walking may cover several cheap road tiles.
        while route_index < len(mission.route):
            cost = entry_cost(world_map, mission.route[route_index], roads)
            if cost is None:
                raise ValueError("an ambassador route cannot enter impassable terrain")
            if progress < cost:
                break
            progress -= cost
            ambassador.location = mission.route[route_index]
            route_index += 1
        if route_index < len(mission.route):
            updated.append(
                mission.model_copy(
                    update={"next_route_index": route_index, "travel_progress": progress}
                )
            )
            continue
        completed = mission.model_copy(
            update={
                "next_route_index": route_index,
                "travel_progress": 0,
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
