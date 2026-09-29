"""Treaty-bound trade shipments and migration journeys over explicit routes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from math import ceil

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sovereign_world.hexmap import HexCoord, Terrain, WorldMap
from sovereign_world.ids import EntityId
from sovereign_world.people import Person, go_hungry
from sovereign_world.resources import Resource
from sovereign_world.rng import StableRng
from sovereign_world.travel import MAX_PROGRESS, entry_cost, step, travel_days

CARGO_UNITS_PER_CARRIER = 50
MAX_TRAVELLERS = 16
HAZARD_THRESHOLD = 60
DELAY_THRESHOLD = 900
ROLL_SCALE = 10_000
TRAVEL_HAZARD_CAUSE = "travel hazard"
FORAGE_TERRAIN_BP: dict[Terrain, int] = {
    Terrain.FOREST: 4_000,
    Terrain.WATER: 3_500,
    Terrain.GRASSLAND: 3_000,
    Terrain.TUNDRA: 1_000,
    Terrain.MOUNTAIN: 800,
    Terrain.DESERT: 500,
}


class JourneyKind(StrEnum):
    SHIPMENT = "shipment"
    MIGRATION = "migration"
    SETTLEMENT = "settlement"
    GARRISON = "garrison"
    RELOCATION = "relocation"


INTERNAL_KINDS = frozenset({JourneyKind.SETTLEMENT, JourneyKind.GARRISON, JourneyKind.RELOCATION})
"""Journeys within one civilization: no treaty, and the sender is also the recipient."""


class JourneyPhase(StrEnum):
    OUTBOUND = "outbound"
    RETURNING = "returning"
    COMPLETE = "complete"


class JourneyOutcome(StrEnum):
    PENDING = "pending"
    DELIVERED = "delivered"
    LOST = "lost"
    FAILED = "failed"
    REFUSED = "refused"
    PERISHED = "perished"


class Journey(BaseModel):
    """One party of mortal travellers carrying goods or seeking a new home."""

    model_config = ConfigDict(frozen=True)

    journey_id: EntityId
    kind: JourneyKind
    treaty_id: EntityId | None = None
    sender_civilization_id: EntityId
    recipient_civilization_id: EntityId
    traveller_ids: tuple[EntityId, ...]
    route: tuple[HexCoord, ...]
    cargo: dict[Resource, int] = Field(default_factory=dict)
    carrying_cargo: bool = False
    provisions_packed: int = Field(default=0, ge=0)
    provisions: int = Field(default=0, ge=0)
    travel_progress: int = Field(default=0, ge=0, lt=MAX_PROGRESS)
    departed_day: int = Field(ge=0)
    route_index: int = Field(default=0, ge=0)
    phase: JourneyPhase = JourneyPhase.OUTBOUND
    outcome: JourneyOutcome = JourneyOutcome.PENDING
    delayed_days: int = Field(default=0, ge=0)
    arrived_day: int | None = Field(default=None, ge=0)
    completed_day: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def valid_shape(self) -> Journey:
        internal = self.kind in INTERNAL_KINDS
        if internal != (self.sender_civilization_id == self.recipient_civilization_id):
            raise ValueError("only internal journeys stay within their civilization")
        if internal != (self.treaty_id is None):
            raise ValueError("foreign journeys need a treaty and internal ones have none")
        if internal and (self.cargo or self.carrying_cargo):
            raise ValueError("internal journeys carry no trade cargo")
        if self.traveller_ids != tuple(sorted(set(self.traveller_ids))):
            raise ValueError("journey travellers must be unique and sorted")
        if not self.traveller_ids or len(self.traveller_ids) > MAX_TRAVELLERS:
            raise ValueError("journey requires between one and sixteen travellers")
        if len(self.route) < 2:
            raise ValueError("a journey route requires an origin and a destination")
        if self.route_index >= len(self.route):
            raise ValueError("journey route index exceeds route length")
        if any(quantity <= 0 for quantity in self.cargo.values()):
            raise ValueError("cargo quantities must be positive")
        if self.kind is JourneyKind.MIGRATION and (self.cargo or self.carrying_cargo):
            raise ValueError("migration journeys carry no trade cargo")
        if self.kind is JourneyKind.SHIPMENT and not self.cargo:
            raise ValueError("a shipment requires cargo")
        load = sum(self.cargo.values()) + self.provisions_packed
        if load > CARGO_UNITS_PER_CARRIER * len(self.traveller_ids):
            raise ValueError("cargo and provisions exceed carrier capacity")
        if self.provisions > self.provisions_packed:
            raise ValueError("a pack cannot hold more than was packed")
        if (self.phase is JourneyPhase.COMPLETE) != (self.completed_day is not None):
            raise ValueError("only completed journeys carry a completion day")
        if self.phase is JourneyPhase.OUTBOUND and self.outcome is not JourneyOutcome.PENDING:
            raise ValueError("outbound journeys have no outcome yet")
        return self

    @property
    def active(self) -> bool:
        return self.phase is not JourneyPhase.COMPLETE


def journey_days(
    kind: JourneyKind,
    world_map: WorldMap,
    route: tuple[HexCoord, ...],
) -> int:
    """Days on the road without delays: out and back for a shipment, one way for migrants."""
    days = travel_days(world_map, route[1:])
    if kind is JourneyKind.SHIPMENT:
        days += travel_days(world_map, tuple(reversed(route))[1:])
    return days


def provisions_needed(days: int, travellers: int, extra: int = 0) -> int:
    """Food packed at dispatch: the days on the road plus a margin for delays."""
    margin = max(2, ceil(days / 4))
    return travellers * (days + margin) + extra


def forage_chance_bp(world_map: WorldMap, coord: HexCoord) -> int:
    """Chance, in basis points, that one traveller finds a day's food on this tile."""
    tile = world_map.tile(coord)
    return FORAGE_TERRAIN_BP[tile.terrain] + tile.soil * 3 + (1_500 if tile.river else 0)


class NoticeKind(StrEnum):
    SHIPMENT_DISPATCHED = "shipment_dispatched"
    SHIPMENT_UNFUNDED = "shipment_unfunded"
    SHIPMENT_RECEIVED = "shipment_received"
    SHIPMENT_TURNED_AWAY = "shipment_turned_away"
    SHIPMENT_CARRIERS_RETURNED = "shipment_carriers_returned"
    MIGRATION_DEPARTED = "migration_departed"
    MIGRATION_UNFUNDED = "migration_unfunded"
    MIGRANTS_RECEIVED = "migrants_received"
    MIGRANTS_TURNED_AWAY = "migrants_turned_away"
    MIGRANTS_RETURNED = "migrants_returned"
    PARTY_DISPATCHED = "party_dispatched"
    PARTY_ARRIVED = "party_arrived"
    PARTY_RETURNED = "party_returned"
    PARTY_UNFUNDED = "party_unfunded"


class LogisticsNotice(BaseModel):
    """A civilization-private record of a journey it launched, received, or heard about."""

    model_config = ConfigDict(frozen=True)

    notice_id: str
    day: int = Field(ge=0)
    kind: NoticeKind
    journey_id: EntityId
    treaty_id: EntityId | None
    counterpart_civilization_id: EntityId
    cargo: dict[Resource, int] = Field(default_factory=dict)
    person_ids: tuple[EntityId, ...] = ()
    reported_outcome: JourneyOutcome | None = None


def notice(
    day: int,
    kind: NoticeKind,
    journey: Journey,
    counterpart: EntityId,
    *,
    cargo: dict[Resource, int] | None = None,
    person_ids: tuple[EntityId, ...] = (),
    reported_outcome: JourneyOutcome | None = None,
) -> LogisticsNotice:
    return LogisticsNotice(
        notice_id=f"{journey.journey_id}:{kind.value}",
        day=day,
        kind=kind,
        journey_id=journey.journey_id,
        treaty_id=journey.treaty_id,
        counterpart_civilization_id=counterpart,
        cargo=dict(cargo or {}),
        person_ids=person_ids,
        reported_outcome=reported_outcome,
    )


@dataclass(frozen=True, slots=True)
class HazardDeath:
    civilization_id: EntityId
    person_id: EntityId
    journey_id: EntityId


@dataclass(frozen=True, slots=True)
class Foraging:
    journey_id: EntityId
    fed: int
    hungry: int


@dataclass(frozen=True, slots=True)
class JourneyDayResult:
    journeys: tuple[Journey, ...]
    people_by_civilization: dict[EntityId, dict[EntityId, Person]]
    delayed_ids: tuple[EntityId, ...]
    lost_ids: tuple[EntityId, ...]
    perished_ids: tuple[EntityId, ...]
    hazard_deaths: tuple[HazardDeath, ...]
    arrived: tuple[Journey, ...]
    failed: tuple[Journey, ...]
    refused: tuple[Journey, ...]
    returned: tuple[Journey, ...]
    cargo_returned: tuple[Journey, ...]
    handed_over: dict[EntityId, int]
    exhausted_ids: tuple[EntityId, ...]
    foraging: tuple[Foraging, ...]
    fed_ids: tuple[EntityId, ...]


def _roll(rng: StableRng, day: int, journey: Journey, purpose: str) -> int:
    stream = rng.stream(f"day:{day}:logistics:{purpose}:{journey.journey_id}")
    return int(stream.integers(0, ROLL_SCALE))


def advance_journeys_day(
    journeys: tuple[Journey, ...],
    people_by_civilization: dict[EntityId, dict[EntityId, Person]],
    *,
    day: int,
    rng: StableRng,
    treaties_in_force: frozenset[EntityId],
    world_map: WorldMap,
    arrival_allowed: Callable[[Journey], bool] | None = None,
) -> JourneyDayResult:
    """Move each active party one route tile, resolving deaths, hazards, and delays.

    Arrival only marks the journey; the engine performs receipt and allegiance transfer.
    A party arriving under a treaty that is no longer in force is turned away.
    Every party still on the road then eats from its pack or forages; a party whose
    journey ends today hands its leftover pack to whichever storehouse it reached.
    """
    people = {
        civilization_id: {
            person_id: person.model_copy(deep=True) for person_id, person in population.items()
        }
        for civilization_id, population in people_by_civilization.items()
    }
    updated: list[Journey] = []
    delayed_ids: list[EntityId] = []
    lost_ids: list[EntityId] = []
    perished_ids: list[EntityId] = []
    hazard_deaths: list[HazardDeath] = []
    arrived: list[Journey] = []
    failed: list[Journey] = []
    refused: list[Journey] = []
    returned: list[Journey] = []
    cargo_returned: list[Journey] = []
    for journey in sorted(journeys, key=lambda item: item.journey_id):
        if not journey.active:
            updated.append(journey)
            continue
        party = people.get(journey.sender_civilization_id, {})
        living = [
            party[person_id]
            for person_id in journey.traveller_ids
            if person_id in party and party[person_id].alive
        ]
        if not living:
            outcome = (
                journey.outcome
                if journey.phase is JourneyPhase.RETURNING and not journey.carrying_cargo
                else JourneyOutcome.PERISHED
            )
            perished_ids.append(journey.journey_id)
            updated.append(
                journey.model_copy(
                    update={
                        "phase": JourneyPhase.COMPLETE,
                        "outcome": outcome,
                        "carrying_cargo": False,
                        "completed_day": day,
                    }
                )
            )
            continue
        if journey.phase is JourneyPhase.RETURNING and journey.route_index == 0:
            completed = journey.model_copy(
                update={
                    "phase": JourneyPhase.COMPLETE,
                    "completed_day": day,
                    "carrying_cargo": False,
                }
            )
            returned.append(completed)
            updated.append(completed)
            continue
        roll = _roll(rng, day, journey, "travel")
        if journey.phase is JourneyPhase.OUTBOUND and roll < HAZARD_THRESHOLD:
            if journey.kind is JourneyKind.SHIPMENT:
                lost_ids.append(journey.journey_id)
                updated.append(
                    journey.model_copy(
                        update={
                            "phase": JourneyPhase.RETURNING,
                            "outcome": JourneyOutcome.LOST,
                            "carrying_cargo": False,
                        }
                    )
                )
                continue
            victim_index = _roll(rng, day, journey, "hazard-victim") % len(living)
            victim = sorted(living, key=lambda person: person.person_id)[victim_index]
            victim.alive = False
            victim.death_day = day
            hazard_deaths.append(
                HazardDeath(
                    civilization_id=journey.sender_civilization_id,
                    person_id=victim.person_id,
                    journey_id=journey.journey_id,
                )
            )
            living = [person for person in living if person.alive]
            if not living:
                perished_ids.append(journey.journey_id)
                updated.append(
                    journey.model_copy(
                        update={
                            "phase": JourneyPhase.COMPLETE,
                            "outcome": JourneyOutcome.PERISHED,
                            "completed_day": day,
                        }
                    )
                )
                continue
            delayed_ids.append(journey.journey_id)
            updated.append(journey.model_copy(update={"delayed_days": journey.delayed_days + 1}))
            continue
        if roll < DELAY_THRESHOLD:
            delayed_ids.append(journey.journey_id)
            updated.append(journey.model_copy(update={"delayed_days": journey.delayed_days + 1}))
            continue
        direction = 1 if journey.phase is JourneyPhase.OUTBOUND else -1
        route_index = max(0, journey.route_index + direction)
        cost = entry_cost(world_map, journey.route[route_index])
        if cost is None:
            raise ValueError("a journey route cannot enter impassable terrain")
        entered, progress = step(journey.travel_progress, cost)
        if not entered:
            updated.append(journey.model_copy(update={"travel_progress": progress}))
            continue
        for traveller in living:
            traveller.location = journey.route[route_index]
        moved = journey.model_copy(
            update={"route_index": route_index, "travel_progress": progress}
        )
        if journey.phase is JourneyPhase.OUTBOUND and route_index == len(journey.route) - 1:
            if journey.kind in INTERNAL_KINDS:
                if arrival_allowed is None or arrival_allowed(moved):
                    moved = moved.model_copy(
                        update={
                            "arrived_day": day,
                            "outcome": JourneyOutcome.DELIVERED,
                            "phase": JourneyPhase.COMPLETE,
                            "completed_day": day,
                        }
                    )
                    arrived.append(moved)
                else:
                    moved = moved.model_copy(
                        update={
                            "arrived_day": day,
                            "outcome": JourneyOutcome.FAILED,
                            "phase": JourneyPhase.RETURNING,
                        }
                    )
                    failed.append(moved)
                updated.append(moved)
                continue
            recipient_living = any(
                person.alive
                for person in people.get(journey.recipient_civilization_id, {}).values()
            )
            if recipient_living and journey.treaty_id not in treaties_in_force:
                moved = moved.model_copy(
                    update={
                        "arrived_day": day,
                        "outcome": JourneyOutcome.REFUSED,
                        "phase": JourneyPhase.RETURNING,
                    }
                )
                refused.append(moved)
            elif recipient_living:
                moved = moved.model_copy(
                    update={
                        "arrived_day": day,
                        "outcome": JourneyOutcome.DELIVERED,
                        "carrying_cargo": False,
                        "phase": (
                            JourneyPhase.RETURNING
                            if journey.kind is JourneyKind.SHIPMENT
                            else JourneyPhase.COMPLETE
                        ),
                        "completed_day": (None if journey.kind is JourneyKind.SHIPMENT else day),
                    }
                )
                arrived.append(moved)
            else:
                moved = moved.model_copy(
                    update={
                        "arrived_day": day,
                        "outcome": JourneyOutcome.FAILED,
                        "phase": JourneyPhase.RETURNING,
                    }
                )
                failed.append(moved)
        elif journey.phase is JourneyPhase.RETURNING and route_index == 0:
            if moved.carrying_cargo:
                cargo_returned.append(moved)
            moved = moved.model_copy(
                update={
                    "phase": JourneyPhase.COMPLETE,
                    "completed_day": day,
                    "carrying_cargo": False,
                }
            )
            returned.append(moved)
        updated.append(moved)

    handed_over: dict[EntityId, int] = {}
    exhausted_ids: list[EntityId] = []
    foraging: list[Foraging] = []
    fed_ids: list[EntityId] = []
    fed_journeys: list[Journey] = []
    previous = {journey.journey_id: journey for journey in journeys}
    for journey in updated:
        was_active = previous[journey.journey_id].active
        if not journey.active:
            if was_active and journey.provisions:
                # Only a party that reached a storehouse alive hands its food over.
                if journey.journey_id not in perished_ids:
                    handed_over[journey.journey_id] = journey.provisions
                journey = journey.model_copy(update={"provisions": 0})
            fed_journeys.append(journey)
            continue
        party = people.get(journey.sender_civilization_id, {})
        hungry_mouths = sorted(
            (
                party[person_id]
                for person_id in journey.traveller_ids
                if person_id in party and party[person_id].alive
            ),
            key=lambda person: person.person_id,
        )
        eaten = min(len(hungry_mouths), journey.provisions)
        remaining = journey.provisions - eaten
        unfed = hungry_mouths[eaten:]
        fed_ids.extend(person.person_id for person in hungry_mouths[:eaten])
        if journey.provisions and not remaining:
            exhausted_ids.append(journey.journey_id)
        if unfed:
            chance = forage_chance_bp(world_map, journey.route[journey.route_index])
            stream = rng.stream(f"day:{day}:logistics:forage:{journey.journey_id}")
            found = 0
            for person in unfed:
                if int(stream.integers(0, ROLL_SCALE)) < chance:
                    found += 1
                    fed_ids.append(person.person_id)
                else:
                    go_hungry(person)
            foraging.append(
                Foraging(journey_id=journey.journey_id, fed=found, hungry=len(unfed) - found)
            )
        fed_journeys.append(journey.model_copy(update={"provisions": remaining}))

    return JourneyDayResult(
        journeys=tuple(sorted(fed_journeys, key=lambda item: item.journey_id)),
        people_by_civilization=people,
        delayed_ids=tuple(sorted(delayed_ids)),
        lost_ids=tuple(sorted(lost_ids)),
        perished_ids=tuple(sorted(perished_ids)),
        hazard_deaths=tuple(
            sorted(hazard_deaths, key=lambda death: (death.journey_id, death.person_id))
        ),
        arrived=tuple(arrived),
        failed=tuple(failed),
        refused=tuple(refused),
        returned=tuple(returned),
        cargo_returned=tuple(cargo_returned),
        handed_over=handed_over,
        exhausted_ids=tuple(sorted(exhausted_ids)),
        foraging=tuple(foraging),
        fed_ids=tuple(sorted(fed_ids)),
    )
