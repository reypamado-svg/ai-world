"""Treaty-bound trade shipments and migration journeys over explicit routes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from math import ceil

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sovereign_world.armoury import WAR_GEAR, cargo_load, slowed, slows
from sovereign_world.hexmap import HexCoord, Terrain, WorldMap
from sovereign_world.ids import EntityId
from sovereign_world.people import Person, go_hungry
from sovereign_world.resources import Resource
from sovereign_world.rng import StableRng
from sovereign_world.roads import (
    STEP_MATERIALS,
    STONE_LAYING,
    STONEWORKING,
    RoadGrade,
    next_grade,
    rank,
    step_labour,
    steps_to,
)
from sovereign_world.tolls import TollGate, TollRules, cargo_charge, detour, food_charge
from sovereign_world.travel import DAY, ENTRY_COST, MAX_PROGRESS, Roads, entry_cost, travel_days
from sovereign_world.war import WarObjective

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
    ROADWORK = "roadwork"
    DEPOSIT = "deposit"
    """Couriers carrying a toll post's chest to a storehouse, then back to their post."""
    CAMPAIGN = "campaign"
    """A war party: fighters marching on a foreign target, then home."""


INTERNAL_KINDS = frozenset(
    {
        JourneyKind.SETTLEMENT,
        JourneyKind.GARRISON,
        JourneyKind.RELOCATION,
        JourneyKind.ROADWORK,
        JourneyKind.DEPOSIT,
    }
)
ROUND_TRIP_KINDS = frozenset({JourneyKind.SHIPMENT, JourneyKind.DEPOSIT, JourneyKind.CAMPAIGN})
TREATY_KINDS = frozenset({JourneyKind.SHIPMENT, JourneyKind.MIGRATION})
"""Journeys that may only travel under a matching treaty."""
"""Parties that deliver goods, then walk back to where they set out."""
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
    STOPPED = "stopped"
    """A road crew turned home before finishing its route."""
    TURNED_BACK = "turned_back"
    """A party that could neither pay a toll nor find a way round it."""
    ROUTED = "routed"
    """A war party that broke in battle and fled for home."""
    AMBUSHED = "ambushed"
    """A party robbed or driven back by enemy fighters on the road."""


class StopReason(StrEnum):
    FOREIGN_LAND = "foreign_land"
    PROVISIONS = "provisions"
    NO_STONEWORKER = "no_stoneworker"
    MATERIALS = "materials"
    TOLL = "toll"


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
    road_grade: RoadGrade | None = None
    """The grade a road crew raises each tile of its route to."""
    materials: dict[Resource, int] = Field(default_factory=dict)
    """Road materials not yet laid; hauled separately from the crew's packs."""
    work_done: int = Field(default=0, ge=0)
    """Person-days already spent toward `work_grade` on the crew's current tile."""
    work_grade: RoadGrade | None = None
    """The grade the crew's labour so far was for; another crew reaching it first wastes it."""
    objective: WarObjective | None = None
    """What a war party does at the end of its route."""
    plunder: dict[Resource, int] = Field(default_factory=dict)
    """Goods a war party has seized and carries home."""
    battles: tuple[EntityId, ...] = ()
    """Battles this war party fought; home hears of them only when survivors return."""
    carry_per_person: int = Field(default=CARGO_UNITS_PER_CARRIER, ge=1)
    """What each traveller can bear; military logistics lets fighters carry more."""
    tolls_paid: tuple[HexCoord, ...] = ()
    """Toll posts this party has already passed, paying or free; each charges a journey once."""

    @model_validator(mode="after")
    def valid_shape(self) -> Journey:
        internal = self.kind in INTERNAL_KINDS
        campaign = self.kind is JourneyKind.CAMPAIGN
        if internal != (self.sender_civilization_id == self.recipient_civilization_id):
            raise ValueError("only internal journeys stay within their civilization")
        if (self.kind in TREATY_KINDS) != (self.treaty_id is not None):
            raise ValueError("trade and migration need a treaty, and nothing else carries one")
        if campaign != (self.objective is not None):
            raise ValueError("only a war party, and every war party, has an objective")
        if not campaign and (self.plunder or self.battles):
            raise ValueError("only a war party carries plunder or fights battles")
        if campaign and (set(self.cargo) - WAR_GEAR or self.carrying_cargo):
            raise ValueError("a war party carries only its kits and engines")
        if internal and self.kind is not JourneyKind.DEPOSIT and (
            self.cargo or self.carrying_cargo
        ):
            raise ValueError("internal journeys carry no trade cargo")
        if self.kind is JourneyKind.DEPOSIT and not self.cargo:
            raise ValueError("a deposit carries a chest of takings")
        roadwork = self.kind is JourneyKind.ROADWORK
        if roadwork != (self.road_grade is not None):
            raise ValueError("only a road crew, and every road crew, has a target grade")
        if not roadwork and (self.materials or self.work_done or self.work_grade):
            raise ValueError("only a road crew carries materials or does road work")
        if any(quantity <= 0 for quantity in self.materials.values()):
            raise ValueError("material quantities must be positive")
        if self.traveller_ids != tuple(sorted(set(self.traveller_ids))):
            raise ValueError("journey travellers must be unique and sorted")
        if not self.traveller_ids or (
            not campaign and len(self.traveller_ids) > MAX_TRAVELLERS
        ):
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
        capacity = self.carry_per_person * len(self.traveller_ids)
        gear = cargo_load(self.cargo) if campaign else sum(self.cargo.values())
        if gear + self.provisions_packed > capacity:
            raise ValueError("cargo and provisions exceed carrier capacity")
        if gear + self.provisions + sum(self.plunder.values()) > capacity:
            raise ValueError("plunder fills only the room that eaten food has freed")
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
    roads: Roads | None = None,
    *,
    heavy: bool = False,
) -> int:
    """Days on the road without delays: out and back for goods, one way for migrants.

    Heavy siege engines make every day of it half as long again.
    """
    days = travel_days(world_map, route[1:], roads)
    if kind in ROUND_TRIP_KINDS:
        days += travel_days(world_map, tuple(reversed(route))[1:], roads)
    return slowed(days) if heavy else days


def roadwork_days(
    world_map: WorldMap,
    route: tuple[HexCoord, ...],
    target: RoadGrade,
    roads: Roads,
    crew: int,
) -> int:
    """Days for a crew to walk out, raise every route tile to the target grade, and walk home."""
    work = 0
    for tile in dict.fromkeys(route):
        base = ENTRY_COST[world_map.tile(tile).terrain]
        if base is None:
            continue
        labour = sum(step_labour(base, grade) for grade in steps_to(roads.get(tile), target))
        work += ceil(labour / max(crew, 1))
    home = tuple(reversed(route))[1:]
    return travel_days(world_map, route[1:], roads) + work + travel_days(world_map, home, roads)


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
    TOLL_PAID = "toll_paid"
    TOLL_COLLECTED = "toll_collected"
    TOLL_AVOIDED = "toll_avoided"
    TOLL_TURNED_BACK = "toll_turned_back"
    TOLL_DEPOSITED = "toll_deposited"
    TOLL_DEPOSIT_SKIPPED = "toll_deposit_skipped"


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
class RoadBuilt:
    journey_id: EntityId
    civilization_id: EntityId
    tile: HexCoord
    grade: RoadGrade


@dataclass(frozen=True, slots=True)
class RoadworkStopped:
    journey_id: EntityId
    civilization_id: EntityId
    tile: HexCoord
    reason: StopReason


@dataclass(frozen=True, slots=True)
class TollEncounter:
    """A party at a toll post: what it paid, or whether it went round or turned back."""

    journey_id: EntityId
    payer: EntityId
    owner: EntityId
    tile: HexCoord
    gate: TollGate
    paid: dict[Resource, int]
    avoided: bool = False
    turned_back: bool = False


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
    roads_built: tuple[RoadBuilt, ...] = ()
    roadwork_stopped: tuple[RoadworkStopped, ...] = ()
    tolls: tuple[TollEncounter, ...] = ()


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
    roads: Roads | None = None,
    tolls: TollRules | None = None,
    halts: Callable[[Journey, HexCoord], bool] | None = None,
) -> JourneyDayResult:
    """Move each active party one route tile, resolving deaths, hazards, and delays.

    Arrival only marks the journey; the engine performs receipt and allegiance transfer.
    A party arriving under a treaty that is no longer in force is turned away.
    Every party still on the road then eats from its pack or forages; a party whose
    journey ends today hands its leftover pack to whichever storehouse it reached.

    A road crew works instead of walking while its tile is below the target grade, and
    turns home early when the tile ahead is foreign, when its pack holds only enough for
    the walk home, or when stone must be laid and no stoneworker is left alive.

    A foreign party pays each staffed toll post on its way out once. A party whose pack
    would not then last the rest of its trip cannot pay: it takes the shortest way round
    over land its civilization knows, if its pack covers the longer road, and otherwise
    turns back with its goods. A road crew that cannot pay stops.
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
    grades = dict(roads or {})
    roads_built: list[RoadBuilt] = []
    stopped: list[RoadworkStopped] = []
    encounters: list[TollEncounter] = []
    toll_rules = tolls or TollRules()

    def stop(journey: Journey, reason: StopReason) -> Journey:
        stopped.append(
            RoadworkStopped(
                journey_id=journey.journey_id,
                civilization_id=journey.sender_civilization_id,
                tile=journey.route[journey.route_index],
                reason=reason,
            )
        )
        return journey.model_copy(
            update={
                "phase": JourneyPhase.RETURNING,
                "outcome": JourneyOutcome.STOPPED,
                "work_done": 0,
                "work_grade": None,
            }
        )

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
        if (
            journey.kind is JourneyKind.ROADWORK
            and journey.phase is JourneyPhase.OUTBOUND
            and arrival_allowed is not None
            and not arrival_allowed(journey)
        ):
            # The land underfoot has turned foreign, or a treaty allowing work here ended.
            journey = stop(journey, StopReason.FOREIGN_LAND)
        if journey.kind is JourneyKind.ROADWORK and journey.phase is JourneyPhase.OUTBOUND:
            worked = _roadwork_day(journey, living, world_map, grades, stop)
            if isinstance(worked, RoadBuilt | None):
                if worked is not None:
                    grades[worked.tile] = worked.grade
                    roads_built.append(worked)
                updated.append(_worked_journey(journey, living, world_map, grades, worked))
                continue
            journey = worked
        moved, reached = _walk(
            journey,
            living,
            world_map,
            grades,
            arrival_allowed,
            stop,
            toll_rules,
            encounters,
            halts,
        )
        if not reached:
            updated.append(moved)
            continue
        route_index = moved.route_index
        if journey.phase is JourneyPhase.OUTBOUND and route_index == len(journey.route) - 1:
            if journey.kind is JourneyKind.CAMPAIGN:
                # The engine fights for the objective and turns the party home today.
                moved = moved.model_copy(update={"arrived_day": day})
                arrived.append(moved)
                updated.append(moved)
                continue
            if journey.kind is JourneyKind.DEPOSIT:
                moved = moved.model_copy(
                    update={
                        "arrived_day": day,
                        "outcome": JourneyOutcome.DELIVERED,
                        "carrying_cargo": False,
                        "phase": JourneyPhase.RETURNING,
                    }
                )
                arrived.append(moved)
                updated.append(moved)
                continue
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
        roads_built=tuple(roads_built),
        roadwork_stopped=tuple(stopped),
        tolls=tuple(encounters),
    )


def _walk(
    journey: Journey,
    living: list[Person],
    world_map: WorldMap,
    grades: dict[HexCoord, RoadGrade],
    arrival_allowed: Callable[[Journey], bool] | None,
    stop: Callable[[Journey, StopReason], Journey],
    tolls: TollRules,
    encounters: list[TollEncounter],
    halts: Callable[[Journey, HexCoord], bool] | None = None,
) -> tuple[Journey, bool]:
    """Spend one day walking, entering as many tiles as the day covers.

    Returns the party and whether it reached the end of its leg today. A road crew
    heading out halts on the first tile it must work, or that it finds foreign. A party
    that halts keeps less than a day of unspent walking.
    """
    outbound = journey.phase is JourneyPhase.OUTBOUND
    end = len(journey.route) - 1 if outbound else 0
    crew = journey.kind is JourneyKind.ROADWORK and outbound
    index = journey.route_index
    if index == end:
        return journey, True
    progress = journey.travel_progress + DAY
    while index != end:
        ahead = index + (1 if outbound else -1)
        cost = entry_cost(world_map, journey.route[ahead], grades)
        if cost is None:
            raise ValueError("a journey route cannot enter impassable terrain")
        if journey.kind is JourneyKind.CAMPAIGN and slows(journey.cargo):
            cost = slowed(cost)
        if progress < cost:
            break
        if outbound:
            passage = _pass_toll(journey, index, living, world_map, grades, tolls, encounters)
            if passage is not journey:
                if passage.phase is JourneyPhase.RETURNING:
                    if crew:
                        return stop(passage, StopReason.TOLL), False
                    return passage.model_copy(
                        update={"travel_progress": min(progress, DAY - 1)}
                    ), False
                if passage.route != journey.route:
                    # A way round: the party sets out on it tomorrow.
                    return passage.model_copy(
                        update={"route_index": index, "travel_progress": min(progress, DAY - 1)}
                    ), False
                journey = passage
        progress -= cost
        index = ahead
        for traveller in living:
            traveller.location = journey.route[index]
        if (
            halts is not None
            and index != end
            and halts(journey, journey.route[index])
        ):
            # A war party stops where enemies stand; the engine then fights there.
            return journey.model_copy(
                update={"route_index": index, "travel_progress": min(progress, DAY - 1)}
            ), False
        if crew:
            here = journey.model_copy(
                update={"route_index": index, "travel_progress": min(progress, DAY - 1)}
            )
            if arrival_allowed is not None and not arrival_allowed(here):
                return stop(here, StopReason.FOREIGN_LAND), False
            assert journey.road_grade is not None
            if index == end or rank(grades.get(journey.route[index])) < rank(journey.road_grade):
                return here, False
    reached = index == end and not crew
    if reached:
        progress = min(progress, DAY - 1)
    moved = journey.model_copy(update={"route_index": index, "travel_progress": progress})
    return moved, reached


def _pass_toll(
    journey: Journey,
    index: int,
    living: list[Person],
    world_map: WorldMap,
    grades: dict[HexCoord, RoadGrade],
    tolls: TollRules,
    encounters: list[TollEncounter],
) -> Journey:
    """Settle the toll on the tile ahead: pay it, go round it, or turn back.

    Returns the journey unchanged when nothing is owed, with the toll deducted when
    paid, with a rewritten route when going round, or returning when turned back.
    """
    ahead = journey.route[index + 1]
    gate = tolls.gates.get(ahead)
    payer = journey.sender_civilization_id
    if (
        gate is None
        or gate.owner == payer
        or ahead in journey.tolls_paid
        or journey.kind is JourneyKind.CAMPAIGN
    ):
        return journey
    if tolls.exempt(payer, gate.owner):
        # Passing free, the party still sees the post and what it charges others.
        encounters.append(_encounter(journey, gate, ahead, {}))
        return journey.model_copy(update={"tolls_paid": (*journey.tolls_paid, ahead)})
    if journey.carrying_cargo:
        owed = cargo_charge(journey.cargo, gate.cargo_rate_bp)
        cargo = {
            resource: quantity - owed.get(resource, 0)
            for resource, quantity in journey.cargo.items()
            if quantity - owed.get(resource, 0) > 0
        }
        paid = journey.model_copy(
            update={"cargo": cargo, "tolls_paid": (*journey.tolls_paid, ahead)}
        )
        encounters.append(_encounter(journey, gate, ahead, owed))
        return paid
    food = food_charge(gate, len(living))
    if food + len(living) * _days_left(journey.route, index, journey.kind, world_map, grades) <= (
        journey.provisions
    ):
        encounters.append(
            _encounter(journey, gate, ahead, {Resource.FOOD: food} if food else {})
        )
        return journey.model_copy(
            update={
                "provisions": journey.provisions - food,
                "tolls_paid": (*journey.tolls_paid, ahead),
            }
        )
    if journey.kind is not JourneyKind.ROADWORK:
        known_gates = tolls.known_gates.get(payer, {})
        avoid = frozenset(
            {ahead}
            | {
                tile
                for tile, known in known_gates.items()
                if not tolls.exempt(payer, known.owner) and known.food_per_head
            }
        )
        rerouted = detour(
            world_map, tolls.known_tiles.get(payer, frozenset()), journey.route, index, avoid
        )
        if rerouted is not None:
            days = _days_left(rerouted, index, journey.kind, world_map, grades)
            if len(living) * days <= journey.provisions:
                encounters.append(_encounter(journey, gate, ahead, {}, avoided=True))
                return journey.model_copy(update={"route": rerouted})
    encounters.append(_encounter(journey, gate, ahead, {}, turned_back=True))
    return journey.model_copy(
        update={"phase": JourneyPhase.RETURNING, "outcome": JourneyOutcome.TURNED_BACK}
    )


def _days_left(
    route: tuple[HexCoord, ...],
    index: int,
    kind: JourneyKind,
    world_map: WorldMap,
    grades: dict[HexCoord, RoadGrade],
) -> int:
    """Days of walking still ahead from route[index], and back again for a round trip."""
    days = travel_days(world_map, route[index + 1 :], grades)
    if kind in ROUND_TRIP_KINDS or kind is JourneyKind.ROADWORK:
        days += travel_days(world_map, tuple(reversed(route))[1:], grades)
    return days


def _encounter(
    journey: Journey,
    gate: TollGate,
    tile: HexCoord,
    paid: dict[Resource, int],
    *,
    avoided: bool = False,
    turned_back: bool = False,
) -> TollEncounter:
    return TollEncounter(
        journey_id=journey.journey_id,
        payer=journey.sender_civilization_id,
        owner=gate.owner,
        tile=tile,
        gate=gate,
        paid=paid,
        avoided=avoided,
        turned_back=turned_back,
    )


def _roadwork_day(
    journey: Journey,
    living: list[Person],
    world_map: WorldMap,
    grades: dict[HexCoord, RoadGrade],
    stop: Callable[[Journey, StopReason], Journey],
) -> RoadBuilt | Journey | None:
    """One day of a road crew on its current tile.

    Returns the grade finished today, None for a day of unfinished work, or the journey
    to move today: onward when the tile is done, homeward when the crew stops or is done.
    """
    assert journey.road_grade is not None
    here = journey.route[journey.route_index]
    homeward = tuple(reversed(journey.route[: journey.route_index + 1]))[1:]
    if journey.provisions <= provisions_needed(
        travel_days(world_map, homeward, grades), len(living)
    ):
        return stop(journey, StopReason.PROVISIONS)
    upcoming = next_grade(grades.get(here))
    if upcoming is None or rank(upcoming) > rank(journey.road_grade):
        if journey.route_index == len(journey.route) - 1:
            return journey.model_copy(
                update={
                    "phase": JourneyPhase.RETURNING,
                    "outcome": JourneyOutcome.DELIVERED,
                    "work_done": 0,
                    "work_grade": None,
                }
            )
        return journey.model_copy(update={"work_done": 0, "work_grade": None})
    if upcoming in STONE_LAYING and not any(
        person.skills.get(STONEWORKING, 0) > 0 for person in living
    ):
        return stop(journey, StopReason.NO_STONEWORKER)
    if any(
        journey.materials.get(resource, 0) < quantity
        for resource, quantity in STEP_MATERIALS[upcoming].items()
    ):
        return stop(journey, StopReason.MATERIALS)
    base = ENTRY_COST[world_map.tile(here).terrain]
    assert base is not None
    if _labour(journey, upcoming) + len(living) < step_labour(base, upcoming):
        return None
    return RoadBuilt(
        journey_id=journey.journey_id,
        civilization_id=journey.sender_civilization_id,
        tile=here,
        grade=upcoming,
    )


def _labour(journey: Journey, grade: RoadGrade | None) -> int:
    """Labour this crew has already put toward the grade; none if that grade changed."""
    return journey.work_done if journey.work_grade == grade else 0


def _worked_journey(
    journey: Journey,
    living: list[Person],
    world_map: WorldMap,
    grades: dict[HexCoord, RoadGrade],
    built: RoadBuilt | None,
) -> Journey:
    """The crew after a day of work: labour counted, and materials laid for a finished grade."""
    if built is None:
        upcoming = next_grade(grades.get(journey.route[journey.route_index]))
        return journey.model_copy(
            update={
                "work_done": _labour(journey, upcoming) + len(living),
                "work_grade": upcoming,
            }
        )
    work_done = _labour(journey, built.grade) + len(living)
    base = ENTRY_COST[world_map.tile(built.tile).terrain]
    assert base is not None
    materials = dict(journey.materials)
    for resource, quantity in STEP_MATERIALS[built.grade].items():
        materials[resource] -= quantity
    return journey.model_copy(
        update={
            "work_done": work_done - step_labour(base, built.grade),
            "work_grade": next_grade(built.grade),
            "materials": {resource: left for resource, left in materials.items() if left},
        }
    )
