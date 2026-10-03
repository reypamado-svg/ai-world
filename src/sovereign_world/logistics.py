"""Treaty-bound trade shipments and migration journeys over explicit routes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from math import ceil

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)

from sovereign_world.armoury import WAR_GEAR, cargo_load, slowed, slows
from sovereign_world.bridges import (
    BRIDGE_LABOUR,
    BRIDGE_MATERIALS,
    MASONRY,
    bridge_labour_days,
    can_bridge,
    span_needed,
    spans_planned,
)
from sovereign_world.cover import forage_bp
from sovereign_world.espionage import MAX_WATCH_DAYS, Estimate
from sovereign_world.hexmap import HexCoord, Terrain, WorldMap, edge_key
from sovereign_world.ids import EntityId
from sovereign_world.people import CopyOnRead, Person, go_hungry
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
from sovereign_world.sites import MAX_WORK_DAYS
from sovereign_world.tolls import TollGate, TollRules, cargo_charge, detour, food_charge
from sovereign_world.travel import (
    CROSSING_COST,
    DAY,
    ENTRY_COST,
    MAX_PROGRESS,
    NO_BRIDGES,
    Bridges,
    Depth,
    Roads,
    entry_cost,
    travel_days,
)
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
    Terrain.HILLS: 1_500,
    Terrain.SNOW: 0,
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
    HAUL = "haul"
    """Carriers taking goods from one of their settlements' stores to another, then home."""
    PETITION = "petition"
    """Released people asking to join another civilization; they wait for its answer."""
    SALVAGE = "salvage"
    """Carriers going to a ruin to bring back what they can bear from its store."""
    SPY = "spy"
    """Spies watching a foreign settlement for a while, then bringing home what they saw."""
    COURIER = "courier"
    """A spy's companion carrying the findings so far home ahead of the others."""
    EXTRACTION = "extraction"
    """Workers going to a deposit or quarry, working it for some days, then carrying home
    what they can bear (rules version 2)."""


INTERNAL_KINDS = frozenset(
    {
        JourneyKind.SETTLEMENT,
        JourneyKind.GARRISON,
        JourneyKind.RELOCATION,
        JourneyKind.ROADWORK,
        JourneyKind.DEPOSIT,
        JourneyKind.HAUL,
        JourneyKind.SALVAGE,
        JourneyKind.EXTRACTION,
    }
)
ROUND_TRIP_KINDS = frozenset(
    {
        JourneyKind.SHIPMENT,
        JourneyKind.DEPOSIT,
        JourneyKind.CAMPAIGN,
        JourneyKind.HAUL,
        JourneyKind.PETITION,
        JourneyKind.SALVAGE,
        JourneyKind.SPY,
        JourneyKind.EXTRACTION,
    }
)
LOADING_KINDS = frozenset({JourneyKind.SALVAGE, JourneyKind.EXTRACTION})
"""Internal parties that load goods at the end of their route and carry them home."""
SPYING_KINDS = frozenset({JourneyKind.SPY, JourneyKind.COURIER})
CARRYING_KINDS = frozenset({JourneyKind.DEPOSIT, JourneyKind.HAUL})
"""Internal journeys that carry goods to one of their own stores."""
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
    CAUGHT = "caught"
    """Spies or a courier found out and taken prisoner."""


class StopReason(StrEnum):
    FOREIGN_LAND = "foreign_land"
    PROVISIONS = "provisions"
    NO_STONEWORKER = "no_stoneworker"
    MATERIALS = "materials"
    TOLL = "toll"
    RIVER = "river"
    """A deep river with no bridge lies across the route (saves from before the crossing
    rule only; new routes are refused at dispatch)."""


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
    """Person-days already spent toward `work_grade` on the crew's current tile, or toward
    bridging the river ahead once the tile is done (`work_grade` None)."""
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
    encamped: bool = False
    """A war party holding the end of its route, besieging or occupying, until it leaves."""
    wreck_roads: bool = False
    """A war party that stops a day on each enemy road tile to pull it down one grade."""
    wrecked: tuple[HexCoord, ...] = ()
    """Road tiles this party has already wrecked; each is wrecked once."""
    captive_ids: tuple[EntityId, ...] = ()
    """Enemy prisoners marching with a war party to be held at its home."""
    waiting: bool = False
    """Petitioners at the other civilization's settlement, waiting for its answer."""
    watch_days: int = Field(default=0, ge=0, le=MAX_WATCH_DAYS)
    """How many days spies mean to watch the settlement at the end of their route."""
    watched: int = Field(default=0, ge=0)
    """Days the spies have watched so far."""
    watching: bool = False
    """Spies at the end of their route, watching, until their days are done."""
    findings: Estimate | None = None
    """What the spies, or their courier, carry home."""
    work_days: int = Field(default=0, ge=0, le=MAX_WORK_DAYS)
    """How many days an extraction party means to work its deposit or quarry."""
    days_worked: int = Field(default=0, ge=0)
    working: bool = False
    """An extraction party at its site, working, until it leaves."""

    @model_serializer(mode="wrap")
    def _omit_unused(self, handler: SerializerFunctionWrapHandler) -> object:
        # Journeys from before extraction parties, and every other kind, dump as before.
        dumped = handler(self)
        if isinstance(dumped, dict):
            for key, default in (("work_days", 0), ("days_worked", 0), ("working", False)):
                if dumped.get(key) == default:
                    dumped.pop(key, None)
        return dumped

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
        if self.encamped and (
            self.objective not in {WarObjective.BESIEGE, WarObjective.OCCUPY}
            or self.phase is not JourneyPhase.OUTBOUND
            or self.route_index != len(self.route) - 1
        ):
            raise ValueError("only besiegers and occupiers hold the end of their route")
        if not campaign and (self.wreck_roads or self.wrecked):
            raise ValueError("only a war party wrecks roads")
        if not campaign and self.captive_ids:
            raise ValueError("only a war party takes prisoners along")
        if self.waiting and (
            self.kind is not JourneyKind.PETITION
            or self.phase is not JourneyPhase.OUTBOUND
            or self.route_index != len(self.route) - 1
        ):
            raise ValueError("only petitioners wait, at the end of their route")
        spy = self.kind is JourneyKind.SPY
        if spy != (self.watch_days > 0):
            raise ValueError("only spies, and all spies, set out to watch for some days")
        if self.watching and (
            not spy
            or self.phase is not JourneyPhase.OUTBOUND
            or self.route_index != len(self.route) - 1
        ):
            raise ValueError("only spies watch, at the end of their route")
        if self.watched > self.watch_days:
            raise ValueError("spies watch no longer than they meant to")
        extraction = self.kind is JourneyKind.EXTRACTION
        if extraction != (self.work_days > 0):
            raise ValueError("only extraction parties, and all of them, set out to work")
        if self.working and (
            not extraction
            or self.phase is not JourneyPhase.OUTBOUND
            or self.route_index != len(self.route) - 1
        ):
            raise ValueError("only extraction parties work, at the end of their route")
        if self.days_worked > self.work_days:
            raise ValueError("a party works no longer than it meant to")
        if self.kind not in SPYING_KINDS and self.findings is not None:
            raise ValueError("only spies and their couriers carry findings")
        if self.kind is JourneyKind.COURIER and self.findings is None:
            raise ValueError("a courier carries findings")
        if self.kind in SPYING_KINDS and (self.cargo or self.carrying_cargo):
            raise ValueError("spies carry nothing but their provisions")
        if self.kind is JourneyKind.PETITION and (self.cargo or self.carrying_cargo):
            raise ValueError("petitioners carry nothing but their provisions")
        if self.captive_ids != tuple(sorted(set(self.captive_ids))):
            raise ValueError("captives are unique and sorted")
        if not campaign and (self.plunder or self.battles):
            raise ValueError("only a war party carries plunder or fights battles")
        if campaign and (set(self.cargo) - WAR_GEAR or self.carrying_cargo):
            raise ValueError("a war party carries only its kits and engines")
        if (
            internal
            and self.kind not in CARRYING_KINDS
            and self.kind not in LOADING_KINDS
            and (self.cargo or self.carrying_cargo)
        ):
            raise ValueError("internal journeys carry no trade cargo")
        if self.kind in CARRYING_KINDS and not self.cargo:
            raise ValueError("a deposit or a haul carries goods")
        roadwork = self.kind is JourneyKind.ROADWORK
        if roadwork != (self.road_grade is not None):
            raise ValueError("only a road crew, and every road crew, has a target grade")
        if not roadwork and (self.materials or self.work_done or self.work_grade):
            raise ValueError("only a road crew carries materials or does road work")
        if any(quantity <= 0 for quantity in self.materials.values()):
            raise ValueError("material quantities must be positive")
        if self.traveller_ids != tuple(sorted(set(self.traveller_ids))):
            raise ValueError("journey travellers must be unique and sorted")
        if not self.traveller_ids or (not campaign and len(self.traveller_ids) > MAX_TRAVELLERS):
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
    bridges: Bridges = NO_BRIDGES,
) -> int:
    """Days on the road without delays: out and back for goods, one way for migrants.

    Heavy siege engines make every day of it half as long again.
    """
    days = travel_days(world_map, route[1:], roads, start=route[0], bridges=bridges)
    if kind in ROUND_TRIP_KINDS:
        days += travel_days(
            world_map, tuple(reversed(route))[1:], roads, start=route[-1], bridges=bridges
        )
    return slowed(days) if heavy else days


def roadwork_days(
    world_map: WorldMap,
    route: tuple[HexCoord, ...],
    target: RoadGrade,
    roads: Roads,
    crew: int,
    *,
    bridges: Bridges = NO_BRIDGES,
) -> int:
    """Days for a crew to walk out, raise every route tile to the target grade, bridge the
    rivers it must, and walk home over its own bridges."""
    work = bridge_labour_days(world_map, route, target, bridges, crew)
    spanned = spans_planned(world_map, route, target, bridges)
    for tile in dict.fromkeys(route):
        base = ENTRY_COST[world_map.tile(tile).terrain]
        if base is None:
            continue
        labour = sum(step_labour(base, grade) for grade in steps_to(roads.get(tile), target))
        work += ceil(labour / max(crew, 1))
    home = tuple(reversed(route))[1:]
    out = travel_days(world_map, route[1:], roads, start=route[0], bridges=spanned)
    return out + work + travel_days(world_map, home, roads, start=route[-1], bridges=spanned)


def provisions_needed(days: int, travellers: int, extra: int = 0) -> int:
    """Food packed at dispatch: the days on the road plus a margin for delays."""
    margin = max(2, ceil(days / 4))
    return travellers * (days + margin) + extra


def forage_chance_bp(world_map: WorldMap, coord: HexCoord, *, cover: bool = False) -> int:
    """Chance, in basis points, that one traveller finds a day's food on this tile.

    With `cover` (rules version 2), woods and wetland add to it: a tile all wood, a tenth."""
    tile = world_map.tile(coord)
    chance = FORAGE_TERRAIN_BP[tile.terrain] + tile.soil * 3 + (1_500 if tile.river else 0)
    if cover and tile.cover:
        chance += forage_bp(tile.cover) // FORAGE_COVER_DIVISOR
    return chance


FORAGE_COVER_DIVISOR = 10
FORAGE_HAZARD_CAUSE = "forage hazard"
"""Reserved for wild animals met while foraging; nothing causes it yet."""


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
    GOODS_HAULED = "goods_hauled"


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
class BridgeBuilt:
    journey_id: EntityId
    civilization_id: EntityId
    a: HexCoord
    b: HexCoord
    depth: Depth


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
    bridges_built: tuple[BridgeBuilt, ...] = ()
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
    bridges: Bridges = NO_BRIDGES,
    forage_bonus: bool = False,
) -> JourneyDayResult:
    """Move each active party one route tile, resolving deaths, hazards, and delays.

    Arrival only marks the journey; the engine performs receipt and allegiance transfer.
    A party arriving under a treaty that is no longer in force is turned away.
    Every party still on the road then eats from its pack or forages; a party whose
    journey ends today hands its leftover pack to whichever storehouse it reached.

    A road crew works instead of walking while its tile is below the target grade, then,
    raising a graded road or better, bridges the river ahead before crossing it; it turns
    home early when the tile ahead is foreign, when its pack holds only enough for
    the walk home, or when stone must be laid and no stoneworker is left alive.

    A foreign party pays each staffed toll post on its way out once. A party whose pack
    would not then last the rest of its trip cannot pay: it takes the shortest way round
    over land its civilization knows, if its pack covers the longer road, and otherwise
    turns back with its goods. A road crew that cannot pay stops.
    """
    people: dict[EntityId, dict[EntityId, Person]] = {
        civilization_id: CopyOnRead(population)
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
    spans = set(bridges)
    roads_built: list[RoadBuilt] = []
    bridges_built: list[BridgeBuilt] = []
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
                        "encamped": False,
                        "working": False,
                    }
                )
            )
            continue
        if journey.encamped or journey.waiting or journey.watching or journey.working:
            # A camp, or petitioners at a gate, stay where they are; they only eat and forage.
            updated.append(journey)
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
            worked = _roadwork_day(journey, living, world_map, grades, spans, stop)
            if isinstance(worked, RoadBuilt | BridgeBuilt | None):
                if isinstance(worked, RoadBuilt):
                    grades[worked.tile] = worked.grade
                    roads_built.append(worked)
                elif isinstance(worked, BridgeBuilt):
                    spans.add((worked.a, worked.b))
                    bridges_built.append(worked)
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
            spans,
        )
        if not reached:
            updated.append(moved)
            continue
        route_index = moved.route_index
        if journey.phase is JourneyPhase.OUTBOUND and route_index == len(journey.route) - 1:
            if journey.kind is JourneyKind.PETITION:
                # Petitioners wait at the gate for the other civilization's council.
                moved = moved.model_copy(update={"arrived_day": day, "waiting": True})
                arrived.append(moved)
                updated.append(moved)
                continue
            if journey.kind is JourneyKind.SPY:
                # Spies settle in to watch; the engine keeps their count of days.
                moved = moved.model_copy(update={"arrived_day": day, "watching": True})
                arrived.append(moved)
                updated.append(moved)
                continue
            if journey.kind is JourneyKind.EXTRACTION:
                # Workers set to; the engine counts their days and what they take.
                moved = moved.model_copy(update={"arrived_day": day, "working": True})
                arrived.append(moved)
                updated.append(moved)
                continue
            if journey.kind is JourneyKind.COURIER:
                moved = moved.model_copy(
                    update={
                        "arrived_day": day,
                        "outcome": JourneyOutcome.DELIVERED,
                        "phase": JourneyPhase.COMPLETE,
                        "completed_day": day,
                    }
                )
                arrived.append(moved)
                updated.append(moved)
                continue
            if journey.kind is JourneyKind.CAMPAIGN:
                # The engine fights for the objective and turns the party home today.
                moved = moved.model_copy(update={"arrived_day": day})
                arrived.append(moved)
                updated.append(moved)
                continue
            if journey.kind in CARRYING_KINDS or journey.kind is JourneyKind.SALVAGE:
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
            chance = forage_chance_bp(
                world_map, journey.route[journey.route_index], cover=forage_bonus
            )
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
        people_by_civilization={
            civilization_id: dict(population) for civilization_id, population in people.items()
        },
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
        bridges_built=tuple(bridges_built),
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
    spans: Bridges = NO_BRIDGES,
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
        cost = entry_cost(
            world_map, journey.route[ahead], grades, origin=journey.route[index], bridges=spans
        )
        if cost is None:
            cost = _legacy_crossing(world_map, journey.route[index], journey.route[ahead], grades)
            if outbound:
                # Only a party dispatched before deep rivers blocked travel meets one here:
                # it cannot cross, so it turns for home (a crew stops) without a crash.
                halted = journey.model_copy(
                    update={"route_index": index, "travel_progress": min(progress, DAY - 1)}
                )
                if crew:
                    return stop(halted, StopReason.RIVER), False
                return halted.model_copy(
                    update={
                        "phase": JourneyPhase.RETURNING,
                        "outcome": JourneyOutcome.TURNED_BACK,
                    }
                ), False
        if journey.kind is JourneyKind.CAMPAIGN and slows(journey.cargo):
            cost = slowed(cost)
        if progress < cost:
            break
        if outbound:
            passage = _pass_toll(
                journey, index, living, world_map, grades, tolls, encounters, spans
            )
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
        if halts is not None and index != end and halts(journey, journey.route[index]):
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
            tile = journey.route[index]
            if (
                index == end
                or rank(grades.get(tile)) < rank(journey.road_grade)
                or (
                    can_bridge(journey.road_grade)
                    and span_needed(world_map, tile, journey.route[index + 1], spans) is not None
                )
            ):
                # Work to do here: a tile to raise, or a river ahead to bridge from this bank.
                return here, False
    reached = index == end and not crew
    if reached:
        progress = min(progress, DAY - 1)
    moved = journey.model_copy(update={"route_index": index, "travel_progress": progress})
    return moved, reached


def _legacy_crossing(
    world_map: WorldMap, here: HexCoord, ahead: HexCoord, grades: dict[HexCoord, RoadGrade]
) -> int:
    """The cost of fording a deep river a party already crossed before the crossing rule.

    Saves made before deep rivers blocked travel can hold parties on the far bank; they wade
    home the way they came, as if the river were an ordinary one, rather than being stranded.
    """
    cost = entry_cost(world_map, ahead, grades)
    if cost is None or world_map.river_between(here, ahead) is None:
        raise ValueError("a journey route cannot enter impassable terrain")
    river = CROSSING_COST["river"]
    assert river is not None
    return cost + river


def _pass_toll(
    journey: Journey,
    index: int,
    living: list[Person],
    world_map: WorldMap,
    grades: dict[HexCoord, RoadGrade],
    tolls: TollRules,
    encounters: list[TollEncounter],
    spans: Bridges = NO_BRIDGES,
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
    if journey.kind is JourneyKind.ROADWORK:
        spans = spans_planned(world_map, journey.route, journey.road_grade, spans)
    left = _days_left(journey.route, index, journey.kind, world_map, grades, spans)
    if food + len(living) * left <= (journey.provisions):
        encounters.append(_encounter(journey, gate, ahead, {Resource.FOOD: food} if food else {}))
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
        # A way round is planned over the bridges the payer's civilization knows of.
        known_spans = tolls.known_bridges.get(payer, NO_BRIDGES)
        rerouted = detour(
            world_map,
            tolls.known_tiles.get(payer, frozenset()),
            journey.route,
            index,
            avoid,
            bridges=known_spans,
        )
        if rerouted is not None:
            days = _days_left(rerouted, index, journey.kind, world_map, grades, known_spans)
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
    bridges: Bridges = NO_BRIDGES,
) -> int:
    """Days of walking still ahead from route[index], and back again for a round trip."""
    days = travel_days(world_map, route[index + 1 :], grades, start=route[index], bridges=bridges)
    if kind in ROUND_TRIP_KINDS or kind is JourneyKind.ROADWORK:
        days += travel_days(
            world_map, tuple(reversed(route))[1:], grades, start=route[-1], bridges=bridges
        )
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
    spans: Bridges,
    stop: Callable[[Journey, StopReason], Journey],
) -> RoadBuilt | BridgeBuilt | Journey | None:
    """One day of a road crew on its current tile.

    Returns the grade or bridge finished today, None for a day of unfinished work, or the
    journey to move today: onward when the tile and any bridge ahead are done, homeward
    when the crew stops or is done.
    """
    assert journey.road_grade is not None
    here = journey.route[journey.route_index]
    homeward = tuple(reversed(journey.route[: journey.route_index + 1]))[1:]
    if journey.provisions <= provisions_needed(
        travel_days(world_map, homeward, grades, start=here, bridges=spans), len(living)
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
        ahead = journey.route[journey.route_index + 1]
        depth = span_needed(world_map, here, ahead, spans)
        if depth is not None and can_bridge(journey.road_grade):
            return _bridge_day(journey, living, here, ahead, depth, stop)
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


def _bridge_day(
    journey: Journey,
    living: list[Person],
    here: HexCoord,
    ahead: HexCoord,
    depth: Depth,
    stop: Callable[[Journey, StopReason], Journey],
) -> BridgeBuilt | Journey | None:
    """A day bridging the river between here and the tile ahead, from this bank."""
    if depth in MASONRY and not any(person.skills.get(STONEWORKING, 0) > 0 for person in living):
        return stop(journey, StopReason.NO_STONEWORKER)
    if any(
        journey.materials.get(resource, 0) < quantity
        for resource, quantity in BRIDGE_MATERIALS[depth].items()
    ):
        return stop(journey, StopReason.MATERIALS)
    if _labour(journey, None) + len(living) < BRIDGE_LABOUR[depth]:
        return None
    a, b = edge_key(here, ahead)
    return BridgeBuilt(
        journey_id=journey.journey_id,
        civilization_id=journey.sender_civilization_id,
        a=a,
        b=b,
        depth=depth,
    )


def _labour(journey: Journey, grade: RoadGrade | None) -> int:
    """Labour this crew has already put toward the grade; none if that grade changed."""
    return journey.work_done if journey.work_grade == grade else 0


def _worked_journey(
    journey: Journey,
    living: list[Person],
    world_map: WorldMap,
    grades: dict[HexCoord, RoadGrade],
    built: RoadBuilt | BridgeBuilt | None,
) -> Journey:
    """The crew after a day of work: labour counted, and materials used for a finished grade
    or bridge."""
    if isinstance(built, BridgeBuilt):
        left = dict(journey.materials)
        for resource, quantity in BRIDGE_MATERIALS[built.depth].items():
            left[resource] -= quantity
        return journey.model_copy(
            update={
                "work_done": 0,
                "work_grade": None,
                "materials": {resource: amount for resource, amount in left.items() if amount},
            }
        )
    if built is None:
        upcoming = next_grade(grades.get(journey.route[journey.route_index]))
        assert journey.road_grade is not None
        if upcoming is None or rank(upcoming) > rank(journey.road_grade):
            # The tile is done: today's labour went into the bridge ahead.
            return journey.model_copy(
                update={"work_done": _labour(journey, None) + len(living), "work_grade": None}
            )
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
