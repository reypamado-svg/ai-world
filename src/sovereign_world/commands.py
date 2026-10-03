"""Versioned sovereign reports, commands, and semantic validation."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum
from itertools import pairwise
from typing import Annotated, Any, Literal

import numpy as np
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
)

from sovereign_world.armoury import (
    GOODS_RECIPES,
    MAX_CRAFT_QUANTITY,
    RECIPES,
    WAR_GEAR,
    CraftJob,
    cargo_load,
    craft_materials,
    crew_needed,
    engines_in,
    personal_kits,
    slows,
)
from sovereign_world.bridges import (
    Bridge,
    bridge_materials,
    bridged_edges,
    deep_spans,
    spans_planned,
)
from sovereign_world.capabilities import CapabilityId
from sovereign_world.cover import WET_FIELD
from sovereign_world.diplomacy import (
    ActiveTreaty,
    Contact,
    DiplomaticMessage,
    MissionStatus,
    PeaceTerms,
    TreatyKind,
)
from sovereign_world.endings import Ending, EndingKind, RuinView
from sovereign_world.espionage import MAX_SPIES, MAX_WATCH_DAYS, CaughtSpy, SpyReport
from sovereign_world.events import DomainEvent
from sovereign_world.exploration import ExpeditionStatus
from sovereign_world.hexmap import COVER_CLASSES, CoverClass, HexCoord, Terrain, WorldMap
from sovereign_world.housing import (
    MAX_HOUSES_PER_ORDER,
    HouseGrade,
    HouseJob,
    best_grade,
    house_materials,
    known_grades,
    residents_by_settlement,
)
from sovereign_world.ids import EntityId
from sovereign_world.institutions import (
    ARMOURY_GEAR,
    CIVIC_KINDS,
    INSTITUTIONS,
    MAX_STAFF,
    SCHOOL_APPRENTICES,
    SEAT,
    Institution,
    InstitutionKind,
    serving_tiles,
    staff_of,
)
from sovereign_world.land import (
    fields_by_settlement,
    food_capacity,
    irrigated_fields,
    stone_capacity,
    timber_capacity,
    water_near,
)
from sovereign_world.languages import FLUENT
from sovereign_world.logistics import (
    CARGO_UNITS_PER_CARRIER,
    INTERNAL_KINDS,
    MAX_TRAVELLERS,
    SPYING_KINDS,
    TREATY_KINDS,
    Journey,
    JourneyKind,
    JourneyOutcome,
    JourneyPhase,
    LogisticsNotice,
    journey_days,
    provisions_needed,
    roadwork_days,
)
from sovereign_world.people import FERTILE_HEALTH_BP
from sovereign_world.people_store import PeopleTable, Sex, place_code
from sovereign_world.ranks import (
    INSTITUTION_RANK,
    INSTITUTION_SLOTS,
    STOREHOUSE_RANK,
    TOLL_RANK,
    TRIBUTE_RANK,
    WAR_PARTY_LIMIT,
    WRITING_RANK,
    RealmRank,
    SettlementRank,
    at_least,
    realm_at_least,
    settlement_rank,
)
from sovereign_world.research import (
    CIVIL_TOPICS,
    LOGISTICS_CARRY,
    MAX_RESEARCH_DAYS,
    ResearchAssignment,
    knows,
    research_error,
)
from sovereign_world.resources import Resource
from sovereign_world.roads import (
    STONE_LAYING,
    STONEWORKING,
    RoadGrade,
    RoadView,
    materials_for,
)
from sovereign_world.rules import rules_for
from sovereign_world.sites import FIND_KINDS, MAX_WORK_DAYS, WORKED_KINDS, SiteKind
from sovereign_world.state import CivilizationState, WorldState
from sovereign_world.stores import (
    STOREHOUSE_GRADES,
    Storehouse,
    StorehouseGrade,
    StorehouseJob,
    all_stores,
    holdings,
    rank,
    settlement_at,
    step_materials,
    steps,
    store_at,
    store_id_at,
)
from sovereign_world.territory import SETTLEMENT_SPACING, Garrison, Settlement, visible_tiles
from sovereign_world.tolls import (
    DEFAULT_DEPOSIT_DAYS,
    MAX_CARGO_RATE_BP,
    MAX_DEPOSIT_DAYS,
    MAX_FOOD_PER_HEAD,
    MIN_DEPOSIT_DAYS,
    TollPost,
    TollView,
)
from sovereign_world.travel import (
    NO_BRIDGES,
    Bridges,
    crossing,
    entry_cost,
    passable,
    river_depth,
)
from sovereign_world.walls import (
    WALL_GRADES,
    WallGrade,
    WallJob,
    Walls,
    repair_materials,
    tower_materials,
    tower_spec,
)
from sovereign_world.walls import rank as wall_rank
from sovereign_world.walls import step_materials as wall_step_materials
from sovereign_world.walls import steps as wall_steps
from sovereign_world.war import (
    BattleReport,
    Drill,
    Occupation,
    Siege,
    War,
    WarObjective,
    able_to_fight,
)


class DecreeKind(StrEnum):
    FOOD_RESERVE_TARGET = "food_reserve_target"
    LABOR_PRIORITY = "labor_priority"
    POPULATION_GROWTH_POLICY = "population_growth_policy"
    HOUSING_POLICY = "housing_policy"
    """Rules version 2: the spare room, in percent, each settlement keeps building toward."""
    MATERIALS_RESERVE_TARGET = "materials_reserve_target"
    """Rules version 2: the timber each settlement gathers toward (and half as much stone)."""


class DirectOrderKind(StrEnum):
    ASSIGN_WORK = "assign_work"
    START_PROJECT = "start_project"
    CANCEL_PROJECT = "cancel_project"
    RELOCATE_GROUP = "relocate_group"
    START_TEACHING = "start_teaching"
    START_EXPEDITION = "start_expedition"
    SEND_MESSAGE = "send_message"
    OFFER_TREATY = "offer_treaty"
    ACCEPT_TREATY = "accept_treaty"
    CANCEL_TREATY = "cancel_treaty"
    REPUDIATE_TREATY = "repudiate_treaty"
    DISPATCH_SHIPMENT = "dispatch_shipment"
    DISPATCH_MIGRATION = "dispatch_migration"
    CLAIM_BORDER = "claim_border"
    FOUND_SETTLEMENT = "found_settlement"
    STATION_GARRISON = "station_garrison"
    BUILD_ROAD = "build_road"
    SET_TOLL = "set_toll"
    DECLARE_WAR = "declare_war"
    SEND_WAR_PARTY = "send_war_party"
    DRILL = "drill"
    CRAFT_EQUIPMENT = "craft_equipment"
    RESEARCH = "research"
    HAUL_GOODS = "haul_goods"
    BUILD_STOREHOUSE = "build_storehouse"
    BUILD_WALLS = "build_walls"
    BUILD_TOWERS = "build_towers"
    REPAIR_WALLS = "repair_walls"
    LIFT_SIEGE = "lift_siege"
    STORM_SETTLEMENT = "storm_settlement"
    BURN_STOREHOUSE = "burn_storehouse"
    RELEASE_PRISONERS = "release_prisoners"
    RELEASE_PEOPLE = "release_people"
    ANSWER_PETITION = "answer_petition"
    SALVAGE = "salvage"
    SEND_SPY = "send_spy"
    SEND_COURIER = "send_courier"
    FOUND_INSTITUTION = "found_institution"
    STAFF_INSTITUTION = "staff_institution"
    EXTRACT = "extract"
    """Rules version 2: send workers to a deposit or quarry to work it for some days."""


MESSAGE_ORDERS = frozenset(
    {
        DirectOrderKind.SEND_MESSAGE,
        DirectOrderKind.OFFER_TREATY,
        DirectOrderKind.ACCEPT_TREATY,
        DirectOrderKind.CANCEL_TREATY,
        DirectOrderKind.DECLARE_WAR,
    }
)
TREATY_END_ORDERS = frozenset({DirectOrderKind.CANCEL_TREATY, DirectOrderKind.REPUDIATE_TREATY})
JOURNEY_ORDERS: dict[DirectOrderKind, JourneyKind] = {
    DirectOrderKind.DISPATCH_SHIPMENT: JourneyKind.SHIPMENT,
    DirectOrderKind.DISPATCH_MIGRATION: JourneyKind.MIGRATION,
    DirectOrderKind.FOUND_SETTLEMENT: JourneyKind.SETTLEMENT,
    DirectOrderKind.STATION_GARRISON: JourneyKind.GARRISON,
    DirectOrderKind.RELOCATE_GROUP: JourneyKind.RELOCATION,
    DirectOrderKind.BUILD_ROAD: JourneyKind.ROADWORK,
    DirectOrderKind.SEND_WAR_PARTY: JourneyKind.CAMPAIGN,
    DirectOrderKind.HAUL_GOODS: JourneyKind.HAUL,
    DirectOrderKind.RELEASE_PEOPLE: JourneyKind.PETITION,
    DirectOrderKind.SALVAGE: JourneyKind.SALVAGE,
    DirectOrderKind.EXTRACT: JourneyKind.EXTRACTION,
    DirectOrderKind.SEND_SPY: JourneyKind.SPY,
}
CAMP_ORDERS = frozenset(
    {
        DirectOrderKind.LIFT_SIEGE,
        DirectOrderKind.STORM_SETTLEMENT,
        DirectOrderKind.BURN_STOREHOUSE,
    }
)
"""Orders to a war party holding the end of its route: besiegers or occupiers."""
WALL_ORDERS = frozenset(
    {DirectOrderKind.BUILD_WALLS, DirectOrderKind.BUILD_TOWERS, DirectOrderKind.REPAIR_WALLS}
)
REQUIRED_TREATY: dict[JourneyKind, TreatyKind] = {
    JourneyKind.SHIPMENT: TreatyKind.TRADE,
    JourneyKind.MIGRATION: TreatyKind.MIGRATION,
}


MAX_CLAIMED_TILES = 256
MAX_WORKER_COUNT = 100
"""The most workers one counted order sets to a duty."""


class ControlView(BaseModel):
    """Who a civilization believes owns a tile, and as of which day."""

    model_config = ConfigDict(frozen=True)

    tile: HexCoord
    owner: EntityId | None
    as_of_day: int = Field(ge=0)


class ProjectKind(StrEnum):
    SHELTER = "shelter"
    STORAGE = "storage"


class Decree(BaseModel):
    model_config = ConfigDict(frozen=True)

    command_id: str
    kind: DecreeKind
    value: int
    priority: int = Field(default=50, ge=0, le=100)
    duration_days: int = Field(default=30, ge=1)


class DirectOrder(BaseModel):
    model_config = ConfigDict(frozen=True)

    command_id: str
    kind: DirectOrderKind
    worker_ids: tuple[EntityId, ...] = ()
    project_id: EntityId | None = None
    project_kind: ProjectKind | None = None
    assignment_id: EntityId | None = None
    teacher_id: EntityId | None = None
    apprentice_id: EntityId | None = None
    capability: CapabilityId | None = None
    expedition_id: EntityId | None = None
    explorer_ids: tuple[EntityId, ...] = ()
    route: tuple[HexCoord, ...] = ()
    message_id: EntityId | None = None
    ambassador_id: EntityId | None = None
    recipient_civilization_id: EntityId | None = None
    message_text: str = Field(default="", max_length=1_000)
    treaty_id: EntityId | None = None
    treaty_kind: TreatyKind | None = None
    peace_terms: PeaceTerms | None = None
    """The truce, prisoners and tribute a peace offer proposes."""
    journey_id: EntityId | None = None
    traveller_ids: tuple[EntityId, ...] = ()
    cargo: dict[Resource, int] = Field(default_factory=dict)
    claimed_tiles: tuple[HexCoord, ...] = Field(default=(), max_length=MAX_CLAIMED_TILES)
    road_grade: RoadGrade | None = None
    toll_rate_bp: int | None = Field(default=None, ge=0, le=MAX_CARGO_RATE_BP)
    toll_food_per_head: int | None = Field(default=None, ge=0, le=MAX_FOOD_PER_HEAD)
    deposit_interval_days: int = Field(
        default=DEFAULT_DEPOSIT_DAYS, ge=MIN_DEPOSIT_DAYS, le=MAX_DEPOSIT_DAYS
    )
    war_objective: WarObjective | None = None
    wreck_roads: bool = False
    captive_ids: tuple[EntityId, ...] = ()
    """Prisoners this civilization holds at its settlements, to be let go."""
    admit: bool = False
    watch_days: int = Field(default=0, ge=0, le=MAX_WATCH_DAYS)
    institution_kind: InstitutionKind | None = None
    """The institution its founders raise and then keep."""
    institution_id: EntityId | None = None
    """The institution whose staff an order replaces."""
    """How long spies watch the settlement at the end of their route."""
    """The answer to a petition: take the petitioners in, or send them home."""
    """A war party stops on each enemy road tile it passes and pulls it down a grade."""
    drill_days: int = Field(default=30, ge=1, le=180)
    craft_item: Resource | None = None
    storehouse_id: EntityId | None = None
    """The storehouse to upgrade; none builds a new one."""
    storehouse_grade: StorehouseGrade | None = None
    """The grade the storehouse is raised to, one step at a time."""
    wall_grade: WallGrade | None = None
    """The grade a settlement's walls are raised to, one step at a time."""
    tower_count: int = Field(default=0, ge=0, le=6)
    """Towers to add to a settlement's walls."""
    craft_quantity: int = Field(default=1, ge=1, le=MAX_CRAFT_QUANTITY)
    research_topic: CapabilityId | None = None
    research_days: int = Field(default=30, ge=1, le=MAX_RESEARCH_DAYS)
    extra_provisions: int = Field(default=0, ge=0, le=CARGO_UNITS_PER_CARRIER * MAX_TRAVELLERS)
    priority: int = Field(default=50, ge=0, le=100)
    house_count: int = Field(default=1, ge=1, le=MAX_HOUSES_PER_ORDER)
    """Rules version 2: how many houses a shelter project raises, one after another."""
    work_days: int = Field(default=0, ge=0, le=MAX_WORK_DAYS)
    """Rules version 2: days an extraction party works its deposit or quarry."""
    house_grade: HouseGrade | None = None
    """Rules version 2: the kind of house to raise; none means the best this people knows."""
    worker_count: int | None = Field(default=None, ge=1, le=MAX_WORKER_COUNT)
    """Rules version 2: for work at home, how many idle grown-ups at `settlement_id` to set
    to it, instead of naming them in `worker_ids`."""
    settlement_id: EntityId | None = None
    """Rules version 2: the settlement a counted order's workers are taken from."""

    @model_serializer(mode="wrap")
    def _omit_unset_counts(self, handler: SerializerFunctionWrapHandler) -> object:
        # Orders that name their workers dump exactly as before counts existed.
        dumped = handler(self)
        if isinstance(dumped, dict):
            for key in ("worker_count", "settlement_id"):
                if key in dumped and dumped[key] is None:
                    dumped.pop(key)
        return dumped


Command = Annotated[Decree | DirectOrder, Field(union_mode="left_to_right")]


class CommandEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int = Field(ge=1, le=2)
    """1, or 2 for envelopes that may count workers instead of naming them."""
    civilization_id: EntityId
    council_day: int = Field(ge=0)
    correlation_id: str
    commands: tuple[Command, ...] = Field(default=(), max_length=8)
    rationale: str = Field(default="", max_length=4_000)


class CommandError(BaseModel):
    model_config = ConfigDict(frozen=True)

    command_id: str | None
    code: str
    message: str


class CommandValidation(BaseModel):
    model_config = ConfigDict(frozen=True)

    accepted: tuple[Command, ...]
    errors: tuple[CommandError, ...]


class BridgeView(BaseModel):
    """A bridge a civilization knows of: only where it stands, never who built it or when."""

    model_config = ConfigDict(frozen=True)

    a: HexCoord
    b: HexCoord


class RiverView(BaseModel):
    """A river a civilization knows of: it runs along the border between a known tile and
    its neighbour across, and is a stream, a river, or too deep to wade."""

    model_config = ConfigDict(frozen=True)

    tile: HexCoord
    across: HexCoord
    depth: Literal["stream", "river", "deep"]


class SiteView(BaseModel):
    """An ore deposit, quarry, ancient ruin or trove on a tile this civilization has seen,
    as it was when last seen."""

    model_config = ConfigDict(frozen=True)

    site_id: EntityId
    tile: HexCoord
    kind: SiteKind
    richness: int
    remaining: int
    as_of_day: int


class LandView(BaseModel):
    """What a settlement's fields, woods and rock yield at most each day (rules version 2)."""

    model_config = ConfigDict(frozen=True)

    food_per_day: int
    timber_per_day: int
    stone_per_day: int
    watered: bool
    """Water on or beside the settlement."""
    irrigable_fields: int
    """Its fields with a river or wetland, which irrigation makes yield half again."""


class HousingView(BaseModel):
    """A settlement's houses, the room they give, and who it has to house."""

    model_config = ConfigDict(frozen=True)

    houses: dict[HouseGrade, int]
    slots: int
    residents: int
    """Its people at home and on its fields, and the captives held there."""
    buildable: HouseGrade
    """The best house this civilization knows how to build."""


GROWN_DAYS = 16 * 365
"""The age at which a person can be set to work at home."""
FIGHTING_YEARS = 60
"""The oldest a person drills or fights."""
ELDER_YEARS = 65
"""The age from which a person counts as an elder: when old age starts to tell."""
NOTABLE_PEOPLE = 40
"""How many of its people a council report names, with what they are doing."""


def idle_at(
    table: PeopleTable, tile: HexCoord, busy: set[EntityId], count: int, *, fit: bool = False
) -> list[EntityId]:
    """Up to `count` living, free, grown people standing on the tile and not busy, lowest
    ids first; with `fit`, only those able to fight."""
    living = table.living_rows()
    ages = table.nums["age_days"][living]
    wanted = (
        (table.loc_code[living] == place_code(tile)) & ~table.captive[living] & (ages >= GROWN_DAYS)
    )
    if fit:
        wanted &= (ages // 365 <= FIGHTING_YEARS) & (table.nums["health_bp"][living] > 0)
    here = living[wanted]
    idle: list[EntityId] = []
    for row in here.tolist():
        person_id = table.ids[row]
        if person_id not in busy:
            idle.append(person_id)
            if len(idle) == count:
                break
    return idle


class PopulationSummary(BaseModel):
    """A civilization's people in numbers: what a council needs, at any size."""

    model_config = ConfigDict(frozen=True)

    living: int
    """Its living people it knows of: not emigrants, not its people held by others."""
    children: int
    """Of those, under 16."""
    grown: int
    """From 16 to 64."""
    elders: int
    """65 and over."""
    women_able_to_conceive: int
    """Women of 18 to 42 in good enough health to bear children."""
    hungry: int
    """Short of food lately."""
    ailing: int
    """In poor health."""
    newcomers: int
    """People of other cultures still becoming its own."""
    dead_this_year: int
    """Its people who died in the last 365 days."""
    residents: dict[EntityId, int]
    """Each settlement's people at home and on its fields, and the captives held there."""
    idle_workers: dict[EntityId, int]
    """Each settlement's grown-ups standing there with no duty: who an order can set to
    work there."""
    speakers: dict[EntityId, int]
    """How many of its free people speak each language."""


class PersonView(BaseModel):
    """One of a civilization's people, as its council knows them."""

    model_config = ConfigDict(frozen=True)

    person_id: EntityId
    sex: Sex
    age_years: int
    health_bp: int
    hungry: bool
    settlement_id: EntityId | None
    """The settlement whose ground they stand on, if any."""
    skills: dict[str, int]
    duty: str | None
    """What they are doing, as `kind:id`; none when idle."""


_REPORT_ADDITIONS: tuple[tuple[str, object], ...] = (
    ("population", None),
    ("notable_people", []),
    ("known_sites", []),
    ("rules_version", 1),
    ("housing", {}),
    ("house_jobs", []),
    ("ranks", {}),
    ("realm_rank", None),
    ("land", {}),
    ("extractions", []),
)
"""Report fields added since council-3, and the value at which each is left out, so reports
from older worlds read, and so prompt, exactly as before."""


class CouncilReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    report_id: str
    civilization_id: EntityId
    day: int
    person_ids: tuple[EntityId, ...]
    start_center: HexCoord
    known_tiles: tuple[HexCoord, ...]
    known_terrain: tuple[tuple[HexCoord, Terrain], ...] = ()
    known_rivers: tuple[RiverView, ...] = ()
    known_sites: tuple[SiteView, ...] = ()
    """Sites on tiles this civilization has seen; left out of the report while there are none."""
    """Rivers along the borders of known tiles."""
    inventory: dict[Resource, int]
    """The capital's store."""
    stores: dict[EntityId, dict[Resource, int]] = Field(default_factory=dict)
    """Every settlement's store, the capital's included."""
    store_capacity: dict[EntityId, int] = Field(default_factory=dict)
    """How much each settlement's store can hold; storehouses raise it."""
    storehouses: tuple[Storehouse, ...] = ()
    storehouse_jobs: tuple[StorehouseJob, ...] = ()
    walls: tuple[Walls, ...] = ()
    wall_jobs: tuple[WallJob, ...] = ()
    holdings: dict[Resource, int] = Field(default_factory=dict)
    """All the goods in all the civilization's stores."""
    project_ids: tuple[EntityId, ...]
    active_decrees: dict[str, int]
    contacts: tuple[Contact, ...] = ()
    received_messages: tuple[DiplomaticMessage, ...] = ()
    treaties: tuple[ActiveTreaty, ...] = ()
    """Treaties this civilization is party to, in force or ended."""
    logistics_notices: tuple[LogisticsNotice, ...] = ()
    controlled_tiles: tuple[HexCoord, ...] = ()
    observed_control: tuple[ControlView, ...] = ()
    settlements: tuple[Settlement, ...] = ()
    garrisons: tuple[Garrison, ...] = ()
    known_roads: tuple[RoadView, ...] = ()
    known_bridges: tuple[BridgeView, ...] = ()
    """Bridges this civilization built or can see."""
    toll_posts: tuple[TollPost, ...] = ()
    known_tolls: tuple[TollView, ...] = ()
    wars: tuple[War, ...] = ()
    sieges: tuple[Siege, ...] = ()
    """Sieges this civilization is laying or suffering."""
    captives: tuple[EntityId, ...] = ()
    """Foreign prisoners this civilization holds, at home or marching with its war parties."""
    petitions: tuple[Journey, ...] = ()
    """People of other civilizations waiting at this one's gates to be taken in."""
    ruins: tuple[RuinView, ...] = ()
    """Ruins on land this civilization knows."""
    institutions: tuple[Institution, ...] = ()
    """This civilization's institutions, built or going up, and their staff."""
    cultures: dict[EntityId, int] = Field(default_factory=dict)
    """How many of its living free people live by each culture: how blended it is."""
    ancestries: dict[EntityId, int] = Field(default_factory=dict)
    """How many of its living free people have forebears from each culture."""
    assimilating: int = 0
    """Newcomers still becoming its people."""
    spy_missions: tuple[Journey, ...] = ()
    """This civilization's spies and couriers still out."""
    spy_reports: tuple[SpyReport, ...] = ()
    """Findings its spies and couriers have brought home."""
    caught_spies: tuple[CaughtSpy, ...] = ()
    """Foreign spies and couriers it has caught, and who sent them."""
    crisis: tuple[str, ...] = ()
    """What struck yesterday, if anything: a war learned of, a first contact, a treaty offer
    received, a siege or occupation seen."""
    speakers: dict[EntityId, tuple[EntityId, ...]] = Field(default_factory=dict)
    """For each language, this civilization's free people who speak it, natively or fluently."""
    endings: tuple[Ending, ...] = ()
    """The endings of the world so far: the last civilization standing, or none."""
    held_captive: tuple[EntityId, ...] = ()
    """Its own people it has been told are held prisoner by others."""
    occupations: tuple[Occupation, ...] = ()
    """Settlements this civilization holds, or has lost to occupiers."""
    war_reports: tuple[BattleReport, ...] = ()
    drills: tuple[Drill, ...] = ()
    craft_jobs: tuple[CraftJob, ...] = ()
    research: tuple[ResearchAssignment, ...] = ()
    research_points: dict[CapabilityId, int] = Field(default_factory=dict)
    recent_events: tuple[DomainEvent, ...] = ()
    rules_version: int = 1
    """The rules this world runs under; version 2 adds houses, ranks and civil research."""
    housing: dict[EntityId, HousingView] = Field(default_factory=dict)
    """Each settlement's houses (rules version 2)."""
    house_jobs: tuple[HouseJob, ...] = ()
    """Houses going up."""
    ranks: dict[EntityId, SettlementRank] = Field(default_factory=dict)
    """Each settlement's rank (rules version 2); a settlement not listed is a village."""
    realm_rank: RealmRank | None = None
    """The civilization's rank: chiefdom, kingdom or empire (rules version 2)."""
    land: dict[EntityId, LandView] = Field(default_factory=dict)
    """What each settlement's land yields at most each day (rules version 2)."""
    extractions: tuple[Journey, ...] = ()
    """This civilization's parties out at deposits and quarries."""
    population: PopulationSummary | None = None
    """Its people in numbers (from council-5)."""
    notable_people: tuple[PersonView, ...] = ()
    """Up to 40 of its people: those on a duty, then idle grown-ups at each settlement."""

    @model_serializer(mode="wrap")
    def _omit_empty_additions(self, handler: SerializerFunctionWrapHandler) -> object:
        dumped = handler(self)
        if isinstance(dumped, dict):
            for key, empty in _REPORT_ADDITIONS:
                if key in dumped and dumped[key] in (empty, None, ()):
                    dumped.pop(key)
        return dumped


def _land_views(state: WorldState, civilization_id: EntityId) -> dict[EntityId, LandView]:
    if not rules_for(state.rules_version).cover_mechanics:
        return {}
    civilization = state.civilizations[civilization_id]
    fields = fields_by_settlement(civilization)
    irrigation = knows(civilization.capabilities, CapabilityId.IRRIGATION)
    fishing = knows(civilization.capabilities, CapabilityId.FISHING)
    world_map = state.world_map
    return {
        settlement.settlement_id: LandView(
            food_per_day=food_capacity(
                world_map,
                fields.get(settlement.settlement_id, ()),
                irrigation=irrigation,
                fishing=fishing,
            ),
            timber_per_day=timber_capacity(world_map, fields.get(settlement.settlement_id, ())),
            stone_per_day=stone_capacity(world_map, fields.get(settlement.settlement_id, ())),
            watered=water_near(world_map, settlement.tile),
            irrigable_fields=irrigated_fields(world_map, fields.get(settlement.settlement_id, ())),
        )
        for settlement in civilization.settlements
    }


def _housing_views(
    state: WorldState,
    civilization_id: EntityId,
    residents: Mapping[EntityId, Sequence[EntityId]] | None = None,
) -> dict[EntityId, HousingView]:
    if not rules_for(state.rules_version).houses:
        return {}
    civilization = state.civilizations[civilization_id]
    if residents is None:
        residents = residents_by_settlement(state, civilization_id)
    buildable = best_grade(civilization.capabilities)
    views: dict[EntityId, HousingView] = {}
    for settlement in civilization.settlements:
        housing = civilization.housing.get(settlement.settlement_id)
        views[settlement.settlement_id] = HousingView(
            houses={} if housing is None else dict(housing.houses),
            slots=0 if housing is None else housing.slots,
            residents=len(residents.get(settlement.settlement_id, ())),
            buildable=buildable,
        )
    return views


CRISIS_GAP_DAYS = 7
"""A civilization holds at most one crisis council in this many days."""


def crisis_reasons(state: WorldState, civilization_id: EntityId) -> tuple[str, ...]:
    """What this civilization learned yesterday that calls for its council at once."""
    yesterday = state.day - 1
    civilization = state.civilizations[civilization_id]
    found = {
        "war": any(
            war.defender_id == civilization_id and war.defender_learned_day == yesterday
            for war in state.wars
        ),
        "first_contact": any(
            contact.first_contact_day == yesterday for contact in civilization.contacts
        ),
        "treaty_offer": any(
            message.treaty_offer is not None and message.delivered_day == yesterday
            for message in civilization.received_messages
        ),
        "siege": any(
            siege.defender_id == civilization_id and siege.defender_learned_day == yesterday
            for siege in state.sieges
        ),
        "occupation": any(
            occupation.owner_id == civilization_id and occupation.owner_learned_day == yesterday
            for occupation in state.occupations
        ),
    }
    return tuple(reason for reason, struck in found.items() if struck)


def crisis_council_due(state: WorldState, civilization_id: EntityId) -> bool:
    """A council outside the monthly round, called by yesterday's news."""
    if state.day == 0 or state.day % state.config.council_interval_days == 0:
        return False
    last = state.civilizations[civilization_id].last_crisis_council
    if last is not None and state.day - last < CRISIS_GAP_DAYS:
        return False
    return bool(crisis_reasons(state, civilization_id))


def _busy_for_orders(state: WorldState, civilization_id: EntityId) -> set[EntityId]:
    """People an order cannot set to work at home today: travelling, garrisoned, on a
    duty at home, teaching or learning, or on a work crew. (The engine's housing policy
    keeps its own, older rule.)"""
    civilization = state.civilizations[civilization_id]
    return (
        _travelling_people(state, civilization_id)
        | _garrisoned_people(state, civilization_id)
        | _drilling_people(state, civilization_id)
        | {
            person_id
            for assignment in civilization.teaching_assignments
            for person_id in (assignment.teacher_id, assignment.apprentice_id)
        }
        | {person_id for order in civilization.work_orders for person_id in order.worker_ids}
    )


def _duties(state: WorldState, civilization_id: EntityId) -> dict[EntityId, str]:
    """What each busy person is doing, as `kind:id`; the first duty found counts."""
    civilization = state.civilizations[civilization_id]
    found: dict[EntityId, str] = {}

    def note(people: Iterable[EntityId], label: str) -> None:
        for person_id in people:
            found.setdefault(person_id, label)

    for institution in civilization.institutions:
        note(institution.staff_ids, f"staff:{institution.institution_id}")
    for assignment in civilization.research:
        if assignment.active:
            note(assignment.scholar_ids, f"scholar:{assignment.assignment_id}")
    for drill in civilization.drills:
        if drill.active:
            note(drill.person_ids, f"drill:{drill.drill_id}")
    for craft in civilization.craft_jobs:
        if not craft.done:
            note(craft.worker_ids, f"craft:{craft.job_id}")
    for jobs in (civilization.storehouse_jobs, civilization.wall_jobs, civilization.house_jobs):
        for job in jobs:
            note(job.worker_ids, f"builder:{job.job_id}")
    for teaching in civilization.teaching_assignments:
        note((teaching.teacher_id,), f"teacher:{teaching.assignment_id}")
        note((teaching.apprentice_id,), f"apprentice:{teaching.assignment_id}")
    for order in civilization.work_orders:
        note(order.worker_ids, f"work:{order.order_id}")
    for garrison in civilization.garrisons:
        note(garrison.member_ids, f"garrison:{garrison.garrison_id}")
    for expedition in civilization.expeditions:
        if expedition.status is ExpeditionStatus.ACTIVE:
            note(expedition.explorer_ids, f"expedition:{expedition.expedition_id}")
    for journey in state.journeys:
        if journey.active and journey.sender_civilization_id == civilization_id:
            note(journey.traveller_ids, f"journey:{journey.journey_id}")
    for message in state.diplomatic_missions:
        if (
            message.status is MissionStatus.IN_TRANSIT
            and message.sender_civilization_id == civilization_id
        ):
            note((message.ambassador_id,), f"envoy:{message.message_id}")
    return found


def _people_part(
    state: WorldState,
    civilization_id: EntityId,
    emigrants: set[EntityId],
    residents: Mapping[EntityId, Sequence[EntityId]],
) -> dict[str, Any]:
    """Everything a council report says about the people, read from the columns in one
    pass: no person is copied or handed out, so nothing is marked as changed."""
    civilization = state.civilizations[civilization_id]
    table = civilization.population.people.table
    ids, objs, nums = table.ids, table.objs, table.nums
    known_captives = set(civilization.known_captives)
    hidden = emigrants | known_captives
    person_ids = tuple(
        person_id
        for person_id in map(ids.__getitem__, table.ordered(table.rows()).tolist())
        if person_id not in hidden
    )
    living = table.living_rows().tolist()
    own_tongue, homeland = objs["native_language"], objs["civilization_id"]
    known_languages, cultures_of, ancestry_of = objs["languages"], objs["culture"], objs["ancestry"]
    speakers: dict[EntityId, list[EntityId]] = {}
    cultures: dict[EntityId, int] = {}
    ancestries: dict[EntityId, int] = {}
    assimilating = 0
    for row in living:
        person_id = ids[row]
        if person_id in known_captives:
            continue
        own = own_tongue[row] or homeland[row]
        known = known_languages[row]
        tongues = (
            {own} | {language for language, level in known.items() if level >= FLUENT}
            if known
            else (own,)
        )
        for language in tongues:
            speakers.setdefault(language, []).append(person_id)
        lives_by = cultures_of[row] or homeland[row]
        cultures[lives_by] = cultures.get(lives_by, 0) + 1
        for origin in ancestry_of[row] or (own,):
            ancestries[origin] = ancestries.get(origin, 0) + 1
        assimilating += cultures_of[row] is not None
    captives = sorted(
        other.ids[row]
        for civilization_other in state.civilizations.values()
        for other in (civilization_other.population.people.table,)
        for row in np.flatnonzero(other.mask(alive=True, captive=True)).tolist()
        if other.objs["captive_of"][row] == civilization_id
    )

    free = np.array([row for row in living if ids[row] not in hidden], dtype=np.int64)
    ages = nums["age_days"][free]
    health = nums["health_bp"][free]
    female = np.fromiter(
        (objs["sex"][row] is Sex.FEMALE for row in free.tolist()), dtype=bool, count=len(free)
    )
    elder = ages // 365 >= ELDER_YEARS
    dead = np.flatnonzero(table.mask(alive=False))
    died = table.column("death_day", dead)
    busy = _busy_for_orders(state, civilization_id)
    idle = free[(ages >= GROWN_DAYS) & ~table.captive[free]]
    busy_rows = table.rows_of(busy)
    if len(busy_rows):
        idle = idle[~np.isin(idle, busy_rows)]
    idle_codes = table.loc_code[idle]
    settlements = sorted(
        civilization.settlements, key=lambda item: (not item.capital, item.settlement_id)
    )
    population = PopulationSummary(
        living=len(free),
        children=int(np.count_nonzero(ages < GROWN_DAYS)),
        grown=int(np.count_nonzero((ages >= GROWN_DAYS) & ~elder)),
        elders=int(np.count_nonzero(elder)),
        women_able_to_conceive=int(
            np.count_nonzero(
                female & (ages >= 18 * 365) & (ages <= 42 * 365) & (health >= FERTILE_HEALTH_BP)
            )
        ),
        hungry=int(np.count_nonzero(nums["nutrition_debt"][free] > 0)),
        ailing=int(np.count_nonzero(health < FERTILE_HEALTH_BP)),
        newcomers=sum(cultures_of[row] is not None for row in free.tolist()),
        dead_this_year=sum(day is not None and day > state.day - 365 for day in died),
        residents={
            settlement.settlement_id: len(residents.get(settlement.settlement_id, ()))
            for settlement in civilization.settlements
        },
        idle_workers={
            settlement.settlement_id: int(
                np.count_nonzero(idle_codes == place_code(settlement.tile))
            )
            for settlement in civilization.settlements
        },
        speakers={language: len(speakers[language]) for language in sorted(speakers)},
    )
    # Those on a duty first, then idle grown-ups taken in turn from each settlement.
    duties = _duties(state, civilization_id)
    notable = [row for row in free.tolist() if ids[row] in duties][:NOTABLE_PEOPLE]
    queues = [
        idle[idle_codes == place_code(settlement.tile)].tolist()[:NOTABLE_PEOPLE]
        for settlement in settlements
    ]
    turn = 0
    while len(notable) < NOTABLE_PEOPLE and any(turn < len(queue) for queue in queues):
        notable.extend(queue[turn] for queue in queues if turn < len(queue))
        turn += 1
    notable = notable[:NOTABLE_PEOPLE]
    settlement_at = {}
    for settlement in reversed(settlements):
        settlement_at[place_code(settlement.tile)] = settlement.settlement_id
    notable_people = tuple(
        PersonView(
            person_id=ids[row],
            sex=objs["sex"][row],
            age_years=int(nums["age_days"][row]) // 365,
            health_bp=int(nums["health_bp"][row]),
            hungry=int(nums["nutrition_debt"][row]) > 0,
            settlement_id=settlement_at.get(int(table.loc_code[row])),
            skills=dict(objs["skills"][row]),
            duty=duties.get(ids[row]),
        )
        for row in notable
    )
    return {
        "person_ids": person_ids,
        "speakers": {language: tuple(speakers[language]) for language in sorted(speakers)},
        "cultures": dict(sorted(cultures.items())),
        "ancestries": dict(sorted(ancestries.items())),
        "assimilating": assimilating,
        "captives": tuple(captives),
        "population": population,
        "notable_people": notable_people,
    }


def build_council_report(
    state: WorldState,
    civilization_id: EntityId,
    recent_events: tuple[DomainEvent, ...] = (),
) -> CouncilReport:
    civilization = state.civilizations[civilization_id]
    latest_migration: dict[EntityId, Journey] = {}
    for journey in sorted(state.journeys, key=lambda item: (item.departed_day, item.journey_id)):
        if journey.kind is JourneyKind.MIGRATION:
            for person_id in journey.traveller_ids:
                latest_migration[person_id] = journey
    # A civilization loses sight of its emigrants at departure, whatever becomes of them,
    # unless the party walks back home after a failed or refused delivery.
    emigrants = {
        person_id
        for person_id, journey in latest_migration.items()
        if journey.sender_civilization_id == civilization_id
        and not (
            journey.phase is JourneyPhase.COMPLETE
            and journey.outcome in {JourneyOutcome.FAILED, JourneyOutcome.REFUSED}
        )
    }
    residents = residents_by_settlement(state, civilization_id)
    people = _people_part(state, civilization_id, emigrants, residents)
    visible_events = tuple(
        event
        for event in recent_events
        if event.actor_id == str(civilization_id) or event.subject_id == str(civilization_id)
    )
    return CouncilReport(
        report_id=f"report:{state.day}:{civilization_id}",
        civilization_id=civilization_id,
        day=state.day,
        person_ids=people["person_ids"],
        ruins=known_ruins(state, civilization_id),
        speakers=people["speakers"],
        crisis=crisis_reasons(state, civilization_id),
        spy_missions=tuple(
            journey
            for journey in state.journeys
            if journey.active
            and journey.kind in SPYING_KINDS
            and journey.sender_civilization_id == civilization_id
        ),
        spy_reports=civilization.spy_reports,
        institutions=civilization.institutions,
        cultures=people["cultures"],
        ancestries=people["ancestries"],
        assimilating=people["assimilating"],
        caught_spies=civilization.caught_spies,
        endings=_known_endings(state, civilization_id),
        petitions=tuple(
            journey
            for journey in state.journeys
            if journey.waiting and journey.recipient_civilization_id == civilization_id
        ),
        captives=people["captives"],
        held_captive=civilization.known_captives,
        start_center=civilization.start_center,
        known_tiles=tuple(sorted(civilization.known_tiles)),
        known_terrain=tuple(
            (tile, state.world_map.tile(tile).terrain) for tile in sorted(civilization.known_tiles)
        ),
        known_rivers=known_rivers(state.world_map, frozenset(civilization.known_tiles)),
        known_sites=known_sites(state, civilization),
        rules_version=state.rules_version,
        housing=_housing_views(state, civilization_id, residents),
        land=_land_views(state, civilization_id),
        extractions=tuple(
            journey
            for journey in state.journeys
            if journey.active
            and journey.kind is JourneyKind.EXTRACTION
            and journey.sender_civilization_id == civilization_id
        ),
        house_jobs=civilization.house_jobs,
        ranks=dict(civilization.ranks_reached),
        realm_rank=(
            civilization.realm_rank_reached if rules_for(state.rules_version).ranks else None
        ),
        inventory=dict(civilization.inventory.quantities),
        stores={
            settlement_id: dict(sorted(inventory.quantities.items()))
            for settlement_id, inventory in all_stores(civilization).items()
        },
        holdings=holdings(civilization),
        storehouses=civilization.storehouses,
        storehouse_jobs=civilization.storehouse_jobs,
        walls=civilization.walls,
        wall_jobs=civilization.wall_jobs,
        store_capacity={
            settlement_id: inventory.capacity
            for settlement_id, inventory in all_stores(civilization).items()
        },
        project_ids=tuple(sorted(civilization.projects)),
        active_decrees=dict(state.active_decrees.get(civilization_id, {})),
        contacts=civilization.contacts,
        received_messages=civilization.received_messages,
        treaties=tuple(
            treaty.as_known_to(civilization_id)
            for treaty in state.active_treaties
            if civilization_id
            in {treaty.proposer_civilization_id, treaty.recipient_civilization_id}
        ),
        logistics_notices=civilization.logistics_notices,
        controlled_tiles=tuple(
            sorted(
                owner.tile
                for owner in state.territory.owners
                if owner.civilization_id == civilization_id
            )
        ),
        observed_control=_observed_control(state, civilization_id),
        settlements=civilization.settlements,
        garrisons=civilization.garrisons,
        known_roads=known_roads(state, civilization_id),
        known_bridges=tuple(
            BridgeView(a=bridge.a, b=bridge.b) for bridge in known_bridges(state, civilization_id)
        ),
        toll_posts=civilization.toll_posts,
        known_tolls=known_tolls(state, civilization_id),
        occupations=tuple(
            occupation
            for occupation in state.occupations
            if civilization_id == occupation.occupier_id
            or (civilization_id == occupation.owner_id and occupation.owner_learned_day is not None)
        ),
        sieges=tuple(
            siege
            for siege in state.sieges
            if civilization_id == siege.besieger_id
            or (civilization_id == siege.defender_id and siege.defender_learned_day is not None)
        ),
        wars=tuple(
            war
            for war in state.wars
            if civilization_id in {war.aggressor_id, war.defender_id}
            and war.known_to(civilization_id)
        ),
        war_reports=civilization.war_reports,
        drills=civilization.drills,
        craft_jobs=civilization.craft_jobs,
        research=civilization.research,
        research_points=dict(civilization.research_points),
        recent_events=visible_events,
        population=people["population"],
        notable_people=people["notable_people"],
    )


def sight_of(state: WorldState, civilization_id: EntityId) -> frozenset[HexCoord]:
    """Tiles seen today from this civilization's inhabited settlements."""
    civilization = state.civilizations[civilization_id]
    table = civilization.population.people.table
    occupied = set(np.unique(table.loc_code[np.flatnonzero(table.mask(alive=True))]).tolist())
    return visible_tiles(
        state.world_map,
        (
            settlement.tile
            for settlement in civilization.settlements
            if place_code(settlement.tile) in occupied
        ),
    )


def trade_partners(state: WorldState, civilization_id: EntityId) -> frozenset[EntityId]:
    """Civilizations bound to this one by a trade treaty in force, with its road terms."""
    return frozenset(
        treaty.counterparty(civilization_id)
        for treaty in state.active_treaties
        if treaty.in_force
        and treaty.kind is TreatyKind.TRADE
        and civilization_id in {treaty.proposer_civilization_id, treaty.recipient_civilization_id}
    )


def known_sites(state: WorldState, civilization: CivilizationState) -> tuple[SiteView, ...]:
    """The sites on tiles a civilization knows, as of the day it last saw each tile."""
    if not state.sites:
        return ()
    known = set(civilization.known_tiles)
    seen: dict[HexCoord, int] = {}
    for observation in civilization.observations:
        if observation.tile in known:
            seen[observation.tile] = max(seen.get(observation.tile, 0), observation.observed_day)
    return tuple(
        SiteView(
            site_id=site.site_id,
            tile=site.tile,
            kind=site.kind,
            richness=site.richness,
            remaining=site.remaining,
            as_of_day=seen.get(site.tile, 0),
        )
        for site in state.sites
        if site.tile in known
    )


def known_rivers(world_map: WorldMap, known: frozenset[HexCoord]) -> tuple[RiverView, ...]:
    """Every river along a border of a known tile, seen from that tile."""
    views: list[RiverView] = []
    for edge in world_map.rivers:
        if edge.a in known or edge.b in known:
            tile, across = (edge.a, edge.b) if edge.a in known else (edge.b, edge.a)
            views.append(RiverView(tile=tile, across=across, depth=river_depth(edge.flow)))
    return tuple(sorted(views, key=lambda view: (view.tile, view.across)))


def known_bridges(state: WorldState, civilization_id: EntityId) -> tuple[Bridge, ...]:
    """Bridges this civilization built, and any standing where its people can see today."""
    in_sight = sight_of(state, civilization_id)
    return tuple(
        bridge
        for bridge in state.bridges
        if bridge.civilization_id == civilization_id or bridge.a in in_sight or bridge.b in in_sight
    )


def known_spans(state: WorldState, civilization_id: EntityId) -> Bridges:
    """The river borders this civilization knows are bridged."""
    return bridged_edges(known_bridges(state, civilization_id))


def _route_spans(command: DirectOrder, state: WorldState, civilization_id: EntityId) -> Bridges:
    """Known bridges, plus those a road crew on this route will build before crossing."""
    known = known_spans(state, civilization_id)
    if JOURNEY_ORDERS.get(command.kind) is JourneyKind.ROADWORK:
        return spans_planned(state.world_map, command.route, command.road_grade, known)
    return known


def known_roads(state: WorldState, civilization_id: EntityId) -> tuple[RoadView, ...]:
    """Roads seen from settlements today, or at the grade last seen or shown on a map."""
    civilization = state.civilizations[civilization_id]
    views = {view.tile: view for view in civilization.road_intel}
    for observation in civilization.observations:
        if observation.observed_road is None:
            continue
        seen = views.get(observation.tile)
        if seen is None or seen.as_of_day <= observation.observed_day:
            views[observation.tile] = RoadView(
                tile=observation.tile,
                grade=observation.observed_road,
                as_of_day=observation.observed_day,
            )
    roads = {road.tile: road.grade for road in state.roads}
    for tile in sight_of(state, civilization_id):
        views.pop(tile, None)
        if tile in roads:
            views[tile] = RoadView(tile=tile, grade=roads[tile], as_of_day=state.day)
    return tuple(views[tile] for tile in sorted(views))


def known_ruins(state: WorldState, civilization_id: EntityId) -> tuple[RuinView, ...]:
    """Ruins its people have seen, and ruins in sight of its settlements today."""
    civilization = state.civilizations[civilization_id]
    views = {view.ruin.tile: view for view in civilization.ruin_intel}
    in_sight = sight_of(state, civilization_id)
    for tile in in_sight:
        views.pop(tile, None)
    for ruin in state.ruins:
        if ruin.tile in in_sight:
            views[ruin.tile] = RuinView(ruin=ruin, as_of_day=state.day)
    return tuple(views[tile] for tile in sorted(views))


def _known_endings(state: WorldState, civilization_id: EntityId) -> tuple[Ending, ...]:
    """The last civilization knows it is the last once it knows every other has fallen."""
    civilization = state.civilizations[civilization_id]
    others = set(state.civilizations) - {civilization_id}
    if not others or not others <= set(civilization.fallen):
        return ()
    return (
        Ending(
            kind=EndingKind.LAST_CIVILIZATION,
            day=max(civilization.fallen[other] for other in others),
            survivor_id=civilization_id,
            population=len(civilization.population.living_ids),
        ),
    )


def known_tolls(state: WorldState, civilization_id: EntityId) -> tuple[TollView, ...]:
    """Its own tolls, tolls in sight of its settlements today, and tolls met or mapped."""
    civilization = state.civilizations[civilization_id]
    views = {view.tile: view for view in civilization.toll_intel}
    in_sight = sight_of(state, civilization_id)
    for tile in in_sight:
        views.pop(tile, None)
    for other in state.civilizations.values():
        for post in other.toll_posts:
            if post.collecting and (
                post.civilization_id == civilization_id or post.tile in in_sight
            ):
                views[post.tile] = TollView(
                    tile=post.tile,
                    owner=post.civilization_id,
                    cargo_rate_bp=post.cargo_rate_bp,
                    food_per_head=post.food_per_head,
                    as_of_day=state.day,
                )
    for post in civilization.toll_posts:
        if not post.collecting:
            views.pop(post.tile, None)
    return tuple(views[tile] for tile in sorted(views))


def _observed_control(state: WorldState, civilization_id: EntityId) -> tuple[ControlView, ...]:
    """Ownership seen from settlements today, or recorded by explorers when they passed."""
    civilization = state.civilizations[civilization_id]
    owners = state.territory.owner_of()
    in_sight = sight_of(state, civilization_id)
    views = {
        observation.tile: ControlView(
            tile=observation.tile,
            owner=observation.observed_owner,
            as_of_day=observation.observed_day,
        )
        for observation in civilization.observations
    }
    for tile in in_sight:
        views[tile] = ControlView(tile=tile, owner=owners.get(tile), as_of_day=state.day)
    return tuple(views[tile] for tile in sorted(views))


def _person_owner(state: WorldState, person_id: EntityId) -> EntityId | None:
    for civilization_id in sorted(state.civilizations):
        if person_id in state.civilizations[civilization_id].population.people:
            return civilization_id
    return None


def _travelling_people(state: WorldState, civilization_id: EntityId) -> set[EntityId]:
    """People already committed to a journey, embassy, or expedition."""
    busy = {
        person_id
        for journey in state.journeys
        if journey.active and journey.sender_civilization_id == civilization_id
        for person_id in journey.traveller_ids
    }
    busy.update(
        message.ambassador_id
        for message in state.diplomatic_missions
        if message.status is MissionStatus.IN_TRANSIT
        and message.sender_civilization_id == civilization_id
    )
    busy.update(
        person_id
        for expedition in state.civilizations[civilization_id].expeditions
        if expedition.status is ExpeditionStatus.ACTIVE
        for person_id in expedition.explorer_ids
    )
    return busy


def pays_tribute(treaty: ActiveTreaty, civilization_id: EntityId) -> bool:
    """Tribute is shipped under the peace treaty that owes it, by its payer."""
    return (
        treaty.kind is TreatyKind.PEACE
        and treaty.terms is not None
        and treaty.terms.tribute_payer == civilization_id
    )


def in_truce(state: WorldState, first: EntityId, second: EntityId) -> ActiveTreaty | None:
    """A peace treaty in force whose truce has not yet run out."""
    return next(
        (
            treaty
            for treaty in state.active_treaties
            if treaty.in_force
            and treaty.truce_until is not None
            and state.day < treaty.truce_until
            and {treaty.proposer_civilization_id, treaty.recipient_civilization_id}
            == {first, second}
        ),
        None,
    )


def at_war(state: WorldState, first: EntityId, second: EntityId) -> War | None:
    return next((war for war in state.wars if war.active and war.involves(first, second)), None)


def _drilling_people(state: WorldState, civilization_id: EntityId) -> set[EntityId]:
    """People bound to home duties that keep them from travel: drill and the armoury."""
    civilization = state.civilizations[civilization_id]
    return (
        {
            person_id
            for drill in civilization.drills
            if drill.active
            for person_id in drill.person_ids
        }
        | {
            person_id
            for job in civilization.craft_jobs
            if not job.done
            for person_id in job.worker_ids
        }
        | {person_id for job in civilization.storehouse_jobs for person_id in job.worker_ids}
        | {person_id for job in civilization.wall_jobs for person_id in job.worker_ids}
        | {person_id for job in civilization.house_jobs for person_id in job.worker_ids}
        | {
            person_id
            for assignment in civilization.research
            if assignment.active
            for person_id in assignment.scholar_ids
        }
        | staff_of(civilization)
    )


def _garrisoned_people(state: WorldState, civilization_id: EntityId) -> set[EntityId]:
    return {
        person_id
        for garrison in state.civilizations[civilization_id].garrisons
        for person_id in garrison.member_ids
    }


def journey_supplies(
    command: DirectOrder, state: WorldState, civilization_id: EntityId
) -> tuple[int, dict[Resource, int]]:
    """Provisions to pack, and everything the order takes from the sender's storehouse.

    Both are planned from the roads this civilization knows of, never from unseen ones.
    """
    kind = JOURNEY_ORDERS[command.kind]
    roads = {view.tile: view.grade for view in known_roads(state, civilization_id)}
    spans = known_spans(state, civilization_id)
    crew = len(command.traveller_ids)
    taken = dict(command.cargo)
    free = trade_partners(state, civilization_id) | {civilization_id}
    toll_food = (
        0
        if kind in {JourneyKind.SHIPMENT, JourneyKind.CAMPAIGN, JourneyKind.HAUL}
        else sum(
            view.food_per_head * crew
            for view in known_tolls(state, civilization_id)
            if view.owner not in free and view.tile in set(command.route[1:])
        )
    )
    if kind is JourneyKind.ROADWORK and command.road_grade is not None:
        days = roadwork_days(
            state.world_map, command.route, command.road_grade, roads, crew, bridges=spans
        )
        # A crew packs what it can bear and turns home when only the walk back is left.
        provisions = min(
            provisions_needed(days, crew, command.extra_provisions + toll_food),
            CARGO_UNITS_PER_CARRIER * crew,
        )
        taken = materials_for(command.route, command.road_grade, roads)
        for resource, quantity in bridge_materials(
            state.world_map, command.route, command.road_grade, spans
        ).items():
            taken[resource] = taken.get(resource, 0) + quantity
        taken = dict(sorted(taken.items()))
    else:
        provisions = provisions_needed(
            journey_days(
                kind,
                state.world_map,
                command.route,
                roads,
                heavy=kind is JourneyKind.CAMPAIGN and slows(command.cargo),
                bridges=spans,
            ),
            crew,
            command.extra_provisions
            + toll_food
            + (crew * command.watch_days if kind is JourneyKind.SPY else 0)
            + (crew * command.work_days if kind is JourneyKind.EXTRACTION else 0),
        )
        if kind is JourneyKind.SPY:
            # Spies pack what they can bear and forage through a long watch.
            provisions = min(provisions, CARGO_UNITS_PER_CARRIER * crew)
    taken[Resource.FOOD] = taken.get(Resource.FOOD, 0) + provisions
    return provisions, taken


def war_party_carry(civilization: CivilizationState) -> int:
    """What each fighter can bear: more once military logistics is known."""
    if knows(civilization.capabilities, CapabilityId.MILITARY_LOGISTICS):
        return LOGISTICS_CARRY
    return CARGO_UNITS_PER_CARRIER


Reserved = dict[tuple[EntityId | None, Resource], int]
"""Goods already promised by earlier orders in the council, by store and resource."""


def _short(
    civilization: CivilizationState,
    tile: HexCoord,
    reserved: Reserved,
    resource: Resource,
    quantity: int,
) -> bool:
    """Whether the store supplying a tile lacks this much beyond what is promised."""
    promised = reserved.get((store_id_at(civilization, tile), resource), 0)
    return promised + quantity > store_at(civilization, tile).quantities.get(resource, 0)


def _reserve(
    civilization: CivilizationState,
    tile: HexCoord,
    reserved: Reserved,
    goods: dict[Resource, int],
) -> None:
    store_id = store_id_at(civilization, tile)
    for resource, quantity in goods.items():
        reserved[(store_id, resource)] = reserved.get((store_id, resource), 0) + quantity


def _known_route_passable(
    world_map: WorldMap,
    route: tuple[HexCoord, ...],
    known: frozenset[HexCoord],
    bridges: Bridges = NO_BRIDGES,
) -> bool:
    """Whether a route avoids every obstacle its civilization knows of.

    Known tiles must be enterable, and a river along a border seen from a known tile must be
    wadeable; what lies in unknown country is not checked, so refusing never reveals it.
    """
    for origin, tile in pairwise(route):
        if tile in known:
            if entry_cost(world_map, tile, origin=origin, bridges=bridges) is None:
                return False
        elif origin in known and crossing(world_map, origin, tile, bridges) is None:
            return False
    return True


def _campaign_error(
    command: DirectOrder,
    civilization_id: EntityId,
    state: WorldState,
    reserved_cargo: Reserved,
) -> CommandError | None:
    """Validate a war party against what this civilization knows and holds."""
    civilization = state.civilizations[civilization_id]

    def error(code: str, message: str) -> CommandError:
        return CommandError(command_id=command.command_id, code=code, message=message)

    target = command.recipient_civilization_id
    if command.war_objective is None or target is None or target == civilization_id:
        return error("invalid_war_party", "a war party needs a foreign target and an objective")
    if not any(contact.civilization_id == target for contact in civilization.contacts):
        return error("unknown_contact", "a war party marches only on a known civilization")
    if (
        in_truce(state, civilization_id, target) is not None
        and at_war(state, civilization_id, target) is None
    ):
        return error("truce", "a truce forbids war parties against the other side")
    route = command.route
    if (
        len(route) < 2
        or route[0] not in {settlement.tile for settlement in civilization.settlements}
        or any(tile not in civilization.known_tiles for tile in route)
        or any(not state.world_map.contains(tile) for tile in route)
        or any(first.distance(second) != 1 for first, second in pairwise(route))
        or not passable(
            state.world_map,
            route[1:],
            start=route[0],
            bridges=known_spans(state, civilization_id),
        )
    ):
        return error(
            "invalid_route", "a war party leaves one of its own settlements over known land"
        )
    if command.war_objective is WarObjective.OCCUPY and route[-1] not in {
        contact.settlement for contact in civilization.contacts if contact.civilization_id == target
    }:
        return error("invalid_route", "occupiers march on a settlement of the target they know")
    if command.war_objective is WarObjective.BESIEGE:
        known = {
            contact.settlement
            for contact in civilization.contacts
            if contact.civilization_id == target
        }
        if route[-1] in known or not any(route[-1].distance(tile) == 1 for tile in known):
            return error(
                "invalid_route", "a siege camp stands next to a settlement of the target, not in it"
            )
    people = civilization.population.people
    if any(people[person_id].location != route[0] for person_id in command.traveller_ids):
        return error("traveller_not_home", "the war party must set out together")
    if not all(able_to_fight(people[person_id]) for person_id in command.traveller_ids):
        return error("unfit_fighter", "fighters must be between 13 and 60 and not dying")
    expectant = {birth.parent_ids[0] for birth in civilization.population.scheduled_births}
    if expectant & set(command.traveller_ids):
        return error("expectant_traveller", "a mother with a birth due cannot march")
    fighters = len(command.traveller_ids)
    if rules_for(state.rules_version).ranks:
        limit = WAR_PARTY_LIMIT[civilization.realm_rank_reached]
        if fighters > limit:
            return error(
                "party_too_large",
                f"a {civilization.realm_rank_reached.value} sends at most {limit} fighters"
                " in one war party",
            )
    if (
        set(command.cargo) - WAR_GEAR
        or any(count <= 0 for count in command.cargo.values())
        or sum(personal_kits(command.cargo).values()) > fighters
    ):
        return error(
            "invalid_cargo", "a war party carries kits, at most one per fighter, and engines"
        )
    if crew_needed(engines_in(command.cargo)) > fighters:
        return error("invalid_cargo", "every engine needs its crew from among the fighters")
    provisions, taken = journey_supplies(command, state, civilization_id)
    if provisions + cargo_load(command.cargo) > war_party_carry(civilization) * fighters:
        return error("cargo_over_capacity", "each fighter can bear only so much food and gear")
    for resource, quantity in taken.items():
        if _short(civilization, command.route[0], reserved_cargo, resource, quantity):
            if resource is Resource.FOOD:
                return error("insufficient_provisions", "not enough food for the war party")
            return error("insufficient_goods", f"not enough {resource} to arm the war party")
    return None


def _internal_journey_error(
    command: DirectOrder,
    civilization_id: EntityId,
    state: WorldState,
    reserved_cargo: Reserved,
) -> CommandError | None:
    """Validate founding, garrisoning, relocating or hauling against what is known."""
    kind = JOURNEY_ORDERS[command.kind]
    civilization = state.civilizations[civilization_id]

    def error(code: str, message: str) -> CommandError:
        return CommandError(command_id=command.command_id, code=code, message=message)

    route = command.route
    own_settlements = {settlement.tile for settlement in civilization.settlements}
    own_garrisons = {garrison.tile for garrison in civilization.garrisons}
    origins = own_settlements | (own_garrisons if kind is JourneyKind.RELOCATION else set())
    if (
        len(route) < 2
        or route[0] not in origins
        or any(tile not in civilization.known_tiles for tile in route)
        or any(not state.world_map.contains(tile) for tile in route)
        or any(first.distance(second) != 1 for first, second in pairwise(route))
        or not passable(
            state.world_map,
            route[1:],
            start=route[0],
            bridges=_route_spans(command, state, civilization_id),
        )
    ):
        return error(
            "invalid_route",
            "the route must leave one of this civilization's own places over known land",
        )
    people = civilization.population.people
    if any(people[person_id].location != route[0] for person_id in command.traveller_ids):
        return error("traveller_not_home", "the party must set out together from its origin")
    expectant = {birth.parent_ids[0] for birth in civilization.population.scheduled_births}
    if expectant & set(command.traveller_ids):
        return error("expectant_traveller", "a mother with a birth due cannot leave on a journey")
    hauling = kind is JourneyKind.HAUL
    if command.cargo and not hauling:
        return error("invalid_cargo", "internal journeys carry no trade cargo")
    if hauling and (not command.cargo or any(count <= 0 for count in command.cargo.values())):
        return error("invalid_cargo", "a haul carries a positive quantity of goods")
    destination = route[-1]
    known_settlements = own_settlements | {contact.settlement for contact in civilization.contacts}
    believed_owner = {view.tile: view.owner for view in _observed_control(state, civilization_id)}
    foreign = believed_owner.get(destination) not in {None, civilization_id}
    if kind is JourneyKind.RELOCATION and destination not in own_settlements - {route[0]}:
        return error("invalid_destination", "people relocate to another of their own settlements")
    if hauling and destination not in own_settlements - {route[0]}:
        return error("invalid_destination", "goods are hauled to another of their own settlements")
    if kind is JourneyKind.SALVAGE and destination not in {ruin.tile for ruin in state.ruins}:
        if not rules_for(state.rules_version).sites:
            return error("invalid_destination", "salvagers go to a ruin")
        if not any(
            view.tile == destination and view.kind in FIND_KINDS and view.remaining > 0
            for view in known_sites(state, civilization)
        ):
            return error("invalid_destination", "salvagers go to a ruin or a trove")
    if kind is JourneyKind.EXTRACTION:
        if not rules_for(state.rules_version).sites:
            return error("invalid_journey", "this world's rules have no worked sites")
        if not any(
            view.tile == destination and view.kind in WORKED_KINDS and view.remaining > 0
            for view in known_sites(state, civilization)
        ):
            return error("invalid_destination", "extractors go to a deposit or quarry they know of")
        if not 1 <= command.work_days <= MAX_WORK_DAYS:
            return error("invalid_stay", f"a party works 1 to {MAX_WORK_DAYS} days")
    elif command.work_days:
        return error("invalid_stay", "only an extraction party sets out to work")
    if kind is JourneyKind.SETTLEMENT and (
        foreign
        or any(tile.distance(destination) < SETTLEMENT_SPACING for tile in known_settlements)
    ):
        return error(
            "invalid_destination",
            "a new settlement needs land not seen as foreign, three tiles from any settlement",
        )
    if (
        kind is JourneyKind.SETTLEMENT
        and rules_for(state.rules_version).cover_mechanics
        and not water_near(state.world_map, destination)
    ):
        return error("invalid_destination", "a new settlement needs water on its tile or beside it")
    if kind is JourneyKind.GARRISON and (foreign or destination in known_settlements):
        return error(
            "invalid_destination", "a garrison holds land that is not a settlement or foreign"
        )
    if kind is JourneyKind.ROADWORK:
        if command.road_grade is None:
            return error("invalid_road", "a road crew needs a target grade")
        allowed = {None, civilization_id} | trade_partners(state, civilization_id)
        if any(believed_owner.get(tile) not in allowed for tile in route):
            return error(
                "foreign_land", "a road cannot be built on foreign land without a trade treaty"
            )
        masonry = command.road_grade in STONE_LAYING or deep_spans(
            state.world_map, route, command.road_grade, known_spans(state, civilization_id)
        )
        if masonry and not any(
            people[person_id].skills.get(STONEWORKING, 0) > 0 for person_id in command.traveller_ids
        ):
            return error(
                "no_stoneworker",
                "laying stone or bridging a deep river needs a stoneworker in the crew",
            )
    provisions, taken = journey_supplies(command, state, civilization_id)
    if provisions + sum(command.cargo.values()) > CARGO_UNITS_PER_CARRIER * len(
        command.traveller_ids
    ):
        return error(
            "cargo_over_capacity", "each traveller can bear 50 units of goods and provisions"
        )
    for resource, quantity in taken.items():
        if _short(civilization, command.route[0], reserved_cargo, resource, quantity):
            promised = reserved_cargo.get((store_id_at(civilization, route[0]), resource), 0)
            available = store_at(civilization, route[0]).quantities.get(resource, 0)
            if resource is Resource.FOOD and promised + command.cargo.get(resource, 0) <= available:
                return error("insufficient_provisions", "not enough food to provision the party")
            if hauling:
                return error("insufficient_goods", f"not enough {resource} to haul")
            return error("insufficient_materials", f"not enough {resource} for the road")
    return None


def _petition_error(
    command: DirectOrder,
    civilization_id: EntityId,
    state: WorldState,
    reserved_cargo: Reserved,
) -> CommandError | None:
    """Released people walk from one of their settlements to a known foreign settlement."""
    civilization = state.civilizations[civilization_id]

    def error(code: str, message: str) -> CommandError:
        return CommandError(command_id=command.command_id, code=code, message=message)

    target = command.recipient_civilization_id
    known = {
        contact.settlement for contact in civilization.contacts if contact.civilization_id == target
    }
    route = command.route
    if target is None or target == civilization_id or not known:
        return error("unknown_contact", "people are released to a civilization this one knows")
    if (
        len(route) < 2
        or route[0] not in {settlement.tile for settlement in civilization.settlements}
        or route[-1] not in known
        or any(tile not in civilization.known_tiles for tile in route)
        or any(first.distance(second) != 1 for first, second in pairwise(route))
        or not passable(
            state.world_map,
            route[1:],
            start=route[0],
            bridges=known_spans(state, civilization_id),
        )
    ):
        return error(
            "invalid_route", "the released walk from a settlement of theirs to one of the other's"
        )
    if command.cargo:
        return error("invalid_cargo", "the released carry only their provisions")
    people = civilization.population.people
    if any(people[person_id].location != route[0] for person_id in command.traveller_ids):
        return error("traveller_not_home", "the released set out together from their settlement")
    expectant = {birth.parent_ids[0] for birth in civilization.population.scheduled_births}
    if expectant & set(command.traveller_ids):
        return error("expectant_traveller", "a mother with a birth due cannot leave on a journey")
    provisions, taken = journey_supplies(command, state, civilization_id)
    if provisions > CARGO_UNITS_PER_CARRIER * len(command.traveller_ids):
        return error("cargo_over_capacity", "each traveller can bear 50 units of provisions")
    for resource, quantity in taken.items():
        if _short(civilization, route[0], reserved_cargo, resource, quantity):
            return error("insufficient_provisions", "not enough food for the journey")
    return None


def _spy_error(
    command: DirectOrder,
    civilization_id: EntityId,
    state: WorldState,
    reserved_cargo: Reserved,
) -> CommandError | None:
    """Spies walk from one of their settlements to watch a known foreign settlement."""
    if not 1 <= command.watch_days <= MAX_WATCH_DAYS:
        return CommandError(
            command_id=command.command_id,
            code="invalid_watch",
            message=f"spies watch for between 1 and {MAX_WATCH_DAYS} days",
        )
    if len(command.traveller_ids) > MAX_SPIES:
        return CommandError(
            command_id=command.command_id,
            code="invalid_journey",
            message=f"at most {MAX_SPIES} go spying together",
        )
    return _petition_error(command, civilization_id, state, reserved_cargo)


def _shelter_error(
    command: DirectOrder,
    civilization_id: EntityId,
    state: WorldState,
    reserved: Reserved,
    starting: set[EntityId],
) -> CommandError | None:
    """Rules version 2: builders raise houses, of the best kind their people know, where they
    stand, from their settlement's store."""
    civilization = state.civilizations[civilization_id]

    def error(code: str, message: str) -> CommandError:
        return CommandError(command_id=command.command_id, code=code, message=message)

    if not command.worker_ids or len(set(command.worker_ids)) != len(command.worker_ids):
        return error("invalid_project", "a shelter project names one or more distinct builders")
    assert command.project_id is not None
    if (
        command.project_id in starting
        or command.project_id in civilization.projects
        or any(job.job_id == command.project_id for job in civilization.house_jobs)
    ):
        return error("invalid_project", "that project is already under way")
    people = civilization.population.people
    places = {people[person_id].location for person_id in command.worker_ids}
    site = settlement_at(civilization, next(iter(places))) if len(places) == 1 else None
    if site is None:
        return error(
            "invalid_project", "builders raise houses together at one of their settlements"
        )
    grade = command.house_grade or best_grade(civilization.capabilities)
    if grade not in known_grades(civilization.capabilities):
        return error("invalid_project", f"this people does not know how to build a {grade.value}")
    materials = house_materials(grade, command.house_count)
    for resource, quantity in materials.items():
        if _short(civilization, site.tile, reserved, resource, quantity):
            return error(
                "insufficient_materials",
                f"not enough {resource} for {command.house_count} {grade.value}(s)",
            )
    starting.add(command.project_id)
    _reserve(civilization, site.tile, reserved, materials)
    return None


def _civil_research_error(
    command: DirectOrder, state: WorldState, civilization_id: EntityId
) -> CommandError | None:
    """Rules version 2: what a civil topic needs of the land and the settlements."""
    civilization = state.civilizations[civilization_id]

    def error(code: str, message: str) -> CommandError:
        return CommandError(command_id=command.command_id, code=code, message=message)

    topic = command.research_topic
    if topic is CapabilityId.WRITING and not any(
        at_least(settlement_rank(civilization, item.settlement_id), WRITING_RANK)
        for item in civilization.settlements
    ):
        return error(
            "rank_required",
            f"writing is worked out in a {WRITING_RANK.value.replace('_', ' ')} or above",
        )
    if topic is CapabilityId.IRRIGATION and not any(
        (tile := state.world_map.tile(coord)).river
        or (tile.cover and tile.cover[WETLAND_INDEX] >= IRRIGATION_WETLAND)
        for coord in civilization.known_tiles
    ):
        return error("invalid_research", "irrigation needs a river or wetland among its fields")
    if topic is CapabilityId.FISHING and not any(
        state.world_map.tile(item.tile).has_water
        or any(
            state.world_map.contains(other) and state.world_map.tile(other).terrain is Terrain.WATER
            for other in item.tile.neighbors()
        )
        for item in civilization.settlements
    ):
        return error("invalid_research", "fishing needs water at or beside a settlement")
    return None


WETLAND_INDEX = COVER_CLASSES.index(CoverClass.WETLAND)
MAX_MATERIALS_TARGET = 5_000
IRRIGATION_WETLAND = WET_FIELD
"""A field with this much wetland (basis points), or a river, can be irrigated."""


def _decree_error(command: Decree, state: WorldState) -> CommandError | None:
    if command.kind is DecreeKind.MATERIALS_RESERVE_TARGET:
        if not rules_for(state.rules_version).cover_mechanics:
            return CommandError(
                command_id=command.command_id,
                code="invalid_decree",
                message="this world's rules have no gathering, so no materials target",
            )
        if not 0 <= command.value <= MAX_MATERIALS_TARGET:
            return CommandError(
                command_id=command.command_id,
                code="invalid_decree",
                message=f"a materials target is 0 to {MAX_MATERIALS_TARGET} timber",
            )
        return None
    if command.kind is not DecreeKind.HOUSING_POLICY:
        return None
    if not rules_for(state.rules_version).houses:
        return CommandError(
            command_id=command.command_id,
            code="invalid_decree",
            message="this world's rules have no houses, so no housing policy",
        )
    if not 0 <= command.value <= 100:
        return CommandError(
            command_id=command.command_id,
            code="invalid_decree",
            message="a housing policy is the spare room to keep, from 0 to 100 percent",
        )
    return None


def _found_institution_error(
    command: DirectOrder,
    civilization_id: EntityId,
    state: WorldState,
    reserved: Reserved,
    founding: set[tuple[EntityId, InstitutionKind]],
) -> CommandError | None:
    """Founders raise an institution where they stand, then keep it."""
    civilization = state.civilizations[civilization_id]

    def error(code: str, message: str) -> CommandError:
        return CommandError(command_id=command.command_id, code=code, message=message)

    kind = command.institution_kind
    if kind is None or not 1 <= len(command.worker_ids) <= MAX_STAFF:
        return error(
            "invalid_institution", f"an institution names its kind and 1 to {MAX_STAFF} founders"
        )
    if len(set(command.worker_ids)) != len(command.worker_ids):
        return error("invalid_institution", "each founder is named once")
    people = civilization.population.people
    places = {people[person_id].location for person_id in command.worker_ids}
    site = settlement_at(civilization, next(iter(places))) if len(places) == 1 else None
    if site is None:
        return error("invalid_institution", "founders work together at one of their settlements")
    if kind in CIVIC_KINDS and not rules_for(state.rules_version).ranks:
        return error("invalid_institution", f"this world's rules have no {kind.value}")
    spec = INSTITUTIONS[kind]
    known = {record.capability for record in civilization.capabilities}
    if spec.needs and not spec.needs & known:
        needs = " or ".join(sorted(item.value for item in spec.needs))
        return error("missing_capability", f"a {kind.value} needs {needs}")
    if (site.settlement_id, kind) in founding or any(
        item.kind is kind and item.settlement_id == site.settlement_id
        for item in civilization.institutions
    ):
        return error("invalid_institution", f"the settlement already has a {kind.value}")
    if rules_for(state.rules_version).ranks:
        held = settlement_rank(civilization, site.settlement_id)
        floor = INSTITUTION_RANK.get(kind)
        if floor is not None and not at_least(held, floor):
            return error(
                "rank_required", f"a {kind.value} needs a {floor.value.replace('_', ' ')} or above"
            )
        slots = INSTITUTION_SLOTS[held]
        if kind is not SEAT and slots is not None:
            kept = sum(
                item.settlement_id == site.settlement_id and item.kind is not SEAT
                for item in civilization.institutions
            ) + sum(
                settlement_id == site.settlement_id and other is not SEAT
                for settlement_id, other in founding
            )
            if kept >= slots:
                return error(
                    "rank_required",
                    f"a {held.value.replace('_', ' ')} keeps at most {slots} institution(s)"
                    " besides its hall",
                )
    for resource, quantity in spec.materials.items():
        if _short(civilization, site.tile, reserved, resource, quantity):
            return error("insufficient_materials", f"not enough {resource} for the {kind.value}")
    founding.add((site.settlement_id, kind))
    _reserve(civilization, site.tile, reserved, spec.materials)
    return None


def _staff_institution_error(
    command: DirectOrder, civilization_id: EntityId, state: WorldState, restaffed: set[EntityId]
) -> CommandError | None:
    """New staff for an institution: up to four people standing at it, or none."""
    civilization = state.civilizations[civilization_id]

    def error(code: str, message: str) -> CommandError:
        return CommandError(command_id=command.command_id, code=code, message=message)

    institution = next(
        (
            item
            for item in civilization.institutions
            if item.institution_id == command.institution_id
        ),
        None,
    )
    if institution is None or institution.institution_id in restaffed:
        return error("invalid_institution", "staff are named for one of its institutions, once")
    if len(command.worker_ids) > MAX_STAFF or len(set(command.worker_ids)) != len(
        command.worker_ids
    ):
        return error("invalid_institution", f"an institution keeps up to {MAX_STAFF} staff")
    people = civilization.population.people
    if any(people[person_id].location != institution.tile for person_id in command.worker_ids):
        return error("invalid_institution", "staff stand at the institution they keep")
    restaffed.add(institution.institution_id)
    return None


def _courier_error(
    command: DirectOrder, civilization_id: EntityId, state: WorldState, sent: set[EntityId]
) -> CommandError | None:
    """One of a watching party goes home ahead with the findings so far."""

    def error(code: str, message: str) -> CommandError:
        return CommandError(command_id=command.command_id, code=code, message=message)

    civilization = state.civilizations[civilization_id]
    party = next(
        (
            journey
            for journey in state.journeys
            if journey.journey_id == command.journey_id
            and journey.sender_civilization_id == civilization_id
            and journey.watching
        ),
        None,
    )
    if party is None or party.journey_id in sent:
        return error(
            "invalid_courier", "a courier leaves spies of this civilization on watch, once"
        )
    if party.findings is None:
        return error("invalid_courier", "the spies have nothing to send yet")
    if (
        len(command.traveller_ids) != 1
        or command.traveller_ids[0] not in party.traveller_ids
        or len(party.traveller_ids) < 2
    ):
        return error("invalid_courier", "one of the spies goes, and at least one stays")
    route = command.route
    if (
        len(route) < 2
        or route[0] != party.route[-1]
        or route[-1] not in {settlement.tile for settlement in civilization.settlements}
        or any(tile not in civilization.known_tiles for tile in route)
        or any(first.distance(second) != 1 for first, second in pairwise(route))
        or not passable(
            state.world_map,
            route[1:],
            start=route[0],
            bridges=known_spans(state, civilization_id),
        )
    ):
        return error("invalid_route", "a courier walks from the spies to one of its settlements")
    return None


def _journey_error(
    command: DirectOrder,
    civilization_id: EntityId,
    state: WorldState,
    reserved_cargo: Reserved,
) -> CommandError | None:
    """Validate an already-identified shipment or migration against treaty, route, and goods."""
    kind = JOURNEY_ORDERS[command.kind]
    civilization = state.civilizations[civilization_id]

    def error(code: str, message: str) -> CommandError:
        return CommandError(command_id=command.command_id, code=code, message=message)

    if kind in INTERNAL_KINDS:
        return _internal_journey_error(command, civilization_id, state, reserved_cargo)
    if kind is JourneyKind.CAMPAIGN:
        return _campaign_error(command, civilization_id, state, reserved_cargo)
    if kind is JourneyKind.PETITION:
        return _petition_error(command, civilization_id, state, reserved_cargo)
    if kind is JourneyKind.SPY:
        return _spy_error(command, civilization_id, state, reserved_cargo)
    treaty = next(
        (item for item in state.active_treaties if item.treaty_id == command.treaty_id),
        None,
    )
    if (
        treaty is None
        or not treaty.in_force
        or not (
            treaty.kind is REQUIRED_TREATY[kind]
            or (kind is JourneyKind.SHIPMENT and pays_tribute(treaty, civilization_id))
        )
        or {treaty.proposer_civilization_id, treaty.recipient_civilization_id}
        != {civilization_id, command.recipient_civilization_id}
    ):
        return error("no_active_treaty", f"{kind.value} requires a matching active treaty")
    contact = next(
        (
            item
            for item in civilization.contacts
            if item.civilization_id == command.recipient_civilization_id
        ),
        None,
    )
    if contact is None:
        return error("unknown_contact", "journeys require a discovered foreign settlement")
    route = command.route
    if (
        len(route) < 2
        or route[0] != civilization.start_center
        or route[-1] != contact.settlement
        or any(tile not in civilization.known_tiles for tile in route)
        or any(not state.world_map.contains(tile) for tile in route)
        or any(first.distance(second) != 1 for first, second in pairwise(route))
        or not passable(
            state.world_map,
            route[1:],
            start=route[0],
            bridges=known_spans(state, civilization_id),
        )
    ):
        return error(
            "invalid_route",
            "journey route must leave home over known adjacent tiles to the settlement",
        )
    people = civilization.population.people
    home = civilization.start_center
    if any(people[person_id].location != home for person_id in command.traveller_ids):
        return error("traveller_not_home", "travellers must depart from the home settlement")
    expectant = {birth.parent_ids[0] for birth in civilization.population.scheduled_births}
    if expectant & set(command.traveller_ids):
        return error("expectant_traveller", "a mother with a birth due cannot leave on a journey")
    if kind is JourneyKind.MIGRATION and command.cargo:
        return error("invalid_cargo", "migration journeys carry no trade cargo")
    if kind is JourneyKind.SHIPMENT and (
        not command.cargo or any(quantity <= 0 for quantity in command.cargo.values())
    ):
        return error("invalid_cargo", "a shipment requires positive cargo")
    provisions, taken = journey_supplies(command, state, civilization_id)
    if sum(command.cargo.values()) + provisions > CARGO_UNITS_PER_CARRIER * len(
        command.traveller_ids
    ):
        return error(
            "cargo_over_capacity", "each traveller can bear 50 units of cargo and provisions"
        )
    for resource, quantity in taken.items():
        available = store_at(civilization, command.route[0]).quantities.get(resource, 0)
        promised = reserved_cargo.get((store_id_at(civilization, command.route[0]), resource), 0)
        if promised + quantity > available:
            cargo_only = promised + command.cargo.get(resource, 0)
            if resource is Resource.FOOD and cargo_only <= available:
                return error("insufficient_provisions", "not enough food to provision the party")
            return error("insufficient_goods", f"not enough {resource} to ship")
    return None


def _craft_error(
    command: DirectOrder,
    civilization_id: EntityId,
    state: WorldState,
    reserved: Reserved,
) -> CommandError | None:
    """Validate an armoury order: something makeable, by people who know how, from stock."""
    civilization = state.civilizations[civilization_id]

    def error(code: str, message: str) -> CommandError:
        return CommandError(command_id=command.command_id, code=code, message=message)

    item = command.craft_item
    if (
        item is None
        or item not in RECIPES
        or not command.worker_ids
        or (item in GOODS_RECIPES and not rules_for(state.rules_version).sites)
    ):
        return error("invalid_craft", "the armoury makes a known kit or engine, with workers")
    if len(set(command.worker_ids)) != len(command.worker_ids):
        return error("invalid_craft", "each worker is named once")
    people = civilization.population.people
    homes = {settlement.tile for settlement in civilization.settlements}
    places = {people[person_id].location for person_id in command.worker_ids}
    if len(places) != 1 or not places <= homes:
        return error("invalid_craft", "workers make equipment together at one of their settlements")
    [workshop] = places
    needed = RECIPES[item].capability
    if needed is not None and not any(
        people[person_id].skills.get(needed.value, 0) > 0 for person_id in command.worker_ids
    ):
        return error("unqualified_worker", f"making {item} needs someone who knows {needed}")
    if rules_for(state.rules_version).ranks and item in ARMOURY_GEAR:
        away = _travelling_people(state, civilization_id) | _garrisoned_people(
            state, civilization_id
        )
        if workshop not in serving_tiles(civilization, InstitutionKind.ARMOURY, away):
            return error("building_required", f"{item} is made only at an open armoury")
    for resource, quantity in craft_materials(item, command.craft_quantity).items():
        if _short(civilization, workshop, reserved, resource, quantity):
            return error("insufficient_materials", f"not enough {resource} to make {item}")
    return None


def _storehouse_grade(
    civilization: CivilizationState, storehouse_id: EntityId | None
) -> StorehouseGrade | None:
    house = next(
        (item for item in civilization.storehouses if item.storehouse_id == storehouse_id), None
    )
    return None if house is None else house.grade


def _storehouse_error(
    command: DirectOrder,
    civilization_id: EntityId,
    state: WorldState,
    reserved: Reserved,
    upgrading: set[EntityId],
) -> CommandError | None:
    """Validate building or upgrading a storehouse where its builders stand."""
    civilization = state.civilizations[civilization_id]

    def error(code: str, message: str) -> CommandError:
        return CommandError(command_id=command.command_id, code=code, message=message)

    target = command.storehouse_grade
    if target is None or not command.worker_ids:
        return error("invalid_storehouse", "a storehouse order names a grade and builders")
    if len(set(command.worker_ids)) != len(command.worker_ids):
        return error("invalid_storehouse", "each builder is named once")
    people = civilization.population.people
    places = {people[person_id].location for person_id in command.worker_ids}
    site = settlement_at(civilization, next(iter(places))) if len(places) == 1 else None
    if site is None:
        return error("invalid_storehouse", "builders work together at one of their settlements")
    current: StorehouseGrade | None = None
    if command.storehouse_id is not None:
        house = next(
            (
                item
                for item in civilization.storehouses
                if item.storehouse_id == command.storehouse_id
            ),
            None,
        )
        if house is None or house.settlement_id != site.settlement_id:
            return error("invalid_storehouse", "builders upgrade a storehouse where they stand")
        busy = {job.storehouse_id for job in civilization.storehouse_jobs}
        if house.storehouse_id in busy | upgrading:
            return error("invalid_storehouse", "that storehouse is already being worked on")
        current = house.grade
    if rank(target) <= rank(current):
        return error("invalid_storehouse", "an upgrade raises the storehouse's grade")
    if rules_for(state.rules_version).ranks:
        held = settlement_rank(civilization, site.settlement_id)
        for grade in steps(current, target):
            floor = STOREHOUSE_RANK.get(grade)
            if floor is not None and not at_least(held, floor):
                return error(
                    "rank_required",
                    f"a {grade.value} needs a {floor.value.replace('_', ' ')} or above",
                )
    for grade in steps(current, target):
        needed = STOREHOUSE_GRADES[grade].capability
        if needed is not None and not any(
            people[person_id].skills.get(needed.value, 0) > 0 for person_id in command.worker_ids
        ):
            return error(
                "unqualified_worker", f"building a {grade} needs someone who knows {needed}"
            )
    for resource, quantity in step_materials(current, target).items():
        if _short(civilization, site.tile, reserved, resource, quantity):
            return error("insufficient_materials", f"not enough {resource} for the storehouse")
    return None


def _walls_of(civilization: CivilizationState, tile: HexCoord) -> Walls | None:
    site = settlement_at(civilization, tile)
    if site is None:
        return None
    return next(
        (item for item in civilization.walls if item.settlement_id == site.settlement_id), None
    )


def _wall_materials(civilization: CivilizationState, command: DirectOrder) -> dict[Resource, int]:
    """What a validated wall or tower order takes from its settlement's store."""
    tile = civilization.population.people[command.worker_ids[0]].location
    walls = _walls_of(civilization, tile)
    current = walls.grade if walls is not None else None
    if command.kind is DirectOrderKind.BUILD_TOWERS:
        assert current is not None
        return tower_materials(current, command.tower_count)
    if command.kind is DirectOrderKind.REPAIR_WALLS:
        assert current is not None
        return repair_materials(current)
    assert command.wall_grade is not None
    return wall_step_materials(current, command.wall_grade)


def _walls_error(
    command: DirectOrder,
    civilization_id: EntityId,
    state: WorldState,
    reserved: Reserved,
    walling: set[HexCoord],
) -> CommandError | None:
    """Validate raising a settlement's walls, or adding towers, where the builders stand."""
    civilization = state.civilizations[civilization_id]

    def error(code: str, message: str) -> CommandError:
        return CommandError(command_id=command.command_id, code=code, message=message)

    if not command.worker_ids or len(set(command.worker_ids)) != len(command.worker_ids):
        return error("invalid_walls", "builders are named once each")
    people = civilization.population.people
    if any(person_id not in people for person_id in command.worker_ids):
        return error("invalid_walls", "builders belong to this civilization")
    places = {people[person_id].location for person_id in command.worker_ids}
    site = settlement_at(civilization, next(iter(places))) if len(places) == 1 else None
    if site is None:
        return error("invalid_walls", "builders work together at one of their settlements")
    busy = {job.settlement_id for job in civilization.wall_jobs}
    if site.settlement_id in busy or site.tile in walling:
        return error("invalid_walls", "that settlement's walls are already being worked on")
    walls = _walls_of(civilization, site.tile)
    current = walls.grade if walls is not None else None
    skills = [people[person_id].skills for person_id in command.worker_ids]
    needed: list[CapabilityId]
    if command.kind is DirectOrderKind.REPAIR_WALLS:
        if walls is None or walls.strength >= WALL_GRADES[walls.grade].strength:
            return error("invalid_walls", "only damaged walls are repaired")
        grade_craft = WALL_GRADES[walls.grade].capability
        needed = [grade_craft] if grade_craft is not None else []
    elif command.kind is DirectOrderKind.BUILD_TOWERS:
        if current is None or command.tower_count < 1:
            return error("invalid_walls", "towers are added, one or more, to standing walls")
        assert walls is not None
        if walls.towers + command.tower_count > WALL_GRADES[current].towers:
            return error(
                "invalid_walls", f"a {current} carries at most {WALL_GRADES[current].towers} towers"
            )
        needed = [tower_spec(current).capability]
    else:
        target = command.wall_grade
        if target is None or command.tower_count:
            return error("invalid_walls", "a wall order names the grade to raise the walls to")
        if wall_rank(target) <= wall_rank(current):
            return error("invalid_walls", "the walls are raised to a higher grade")
        needed = [
            capability
            for grade in wall_steps(current, target)
            if (capability := WALL_GRADES[grade].capability) is not None
        ]
    for capability in needed:
        if not any(item.get(capability.value, 0) > 0 for item in skills):
            return error("unqualified_worker", f"this work needs someone who knows {capability}")
    for resource, quantity in _wall_materials(civilization, command).items():
        if _short(civilization, site.tile, reserved, resource, quantity):
            return error("insufficient_materials", f"not enough {resource} for the walls")
    return None


def besieged(state: WorldState, civilization_id: EntityId) -> dict[HexCoord, set[HexCoord]]:
    """This civilization's settlements under siege, with the camps around each."""
    camps: dict[HexCoord, set[HexCoord]] = {}
    for siege in state.sieges:
        if siege.active and siege.defender_id == civilization_id:
            camps.setdefault(siege.settlement_tile, set()).add(siege.camp)
    return camps


def _blockade_error(
    command: DirectOrder, civilization_id: EntityId, state: WorldState
) -> CommandError | None:
    """Nothing leaves or reaches a besieged settlement, except a sally against its camp."""
    camps = besieged(state, civilization_id)
    held = {
        occupation.tile
        for occupation in state.occupations
        if occupation.active and occupation.owner_id == civilization_id
    }
    route = command.route
    if not (camps or held) or not route:
        return None
    sally = command.kind is DirectOrderKind.SEND_WAR_PARTY and route[-1] in camps.get(
        route[0], set()
    )
    if (route[0] in camps and not sally) or route[-1] in camps:
        return CommandError(
            command_id=command.command_id,
            code="blockaded",
            message="a besieged settlement can send out only a sally against the camp",
        )
    if route[0] in held or route[-1] in held:
        return CommandError(
            command_id=command.command_id,
            code="occupied",
            message="an occupied settlement's people cannot be sent anywhere, nor sent to",
        )
    return None


def _siege_order_error(
    command: DirectOrder, civilization_id: EntityId, state: WorldState, ordered: set[EntityId]
) -> CommandError | None:
    """Lifting or storming needs one of this civilization's own camps, ordered once."""
    camp = next(
        (
            journey
            for journey in state.journeys
            if journey.journey_id == command.journey_id
            and journey.sender_civilization_id == civilization_id
            and journey.encamped
        ),
        None,
    )
    if camp is None or camp.journey_id in ordered:
        return CommandError(
            command_id=command.command_id,
            code="invalid_siege_order",
            message="the order names one of this civilization's standing camps, once",
        )
    if command.kind is DirectOrderKind.STORM_SETTLEMENT and camp.objective is not (
        WarObjective.BESIEGE
    ):
        return CommandError(
            command_id=command.command_id,
            code="invalid_siege_order",
            message="only a siege camp storms; occupiers already hold the settlement",
        )
    if command.kind is DirectOrderKind.BURN_STOREHOUSE:
        occupation = next(
            (
                item
                for item in state.occupations
                if item.active and item.journey_id == camp.journey_id
            ),
            None,
        )
        if occupation is None or not any(
            house.storehouse_id == command.storehouse_id
            and house.settlement_id == occupation.settlement_id
            for house in state.civilizations[occupation.owner_id].storehouses
        ):
            return CommandError(
                command_id=command.command_id,
                code="invalid_siege_order",
                message="occupiers burn a storehouse in the settlement they hold",
            )
    if command.kind is DirectOrderKind.STORM_SETTLEMENT and command.war_objective not in {
        None,
        WarObjective.RAID,
        WarObjective.ATTACK,
    }:
        return CommandError(
            command_id=command.command_id,
            code="invalid_siege_order",
            message="a storm either raids the settlement or only beats its defenders",
        )
    return None


def _toll_error(
    command: DirectOrder,
    civilization_id: EntityId,
    state: WorldState,
    tolled: set[HexCoord],
) -> CommandError | None:
    """Validate setting or lifting a toll at a staffed road tile this civilization holds."""
    civilization = state.civilizations[civilization_id]

    def error(code: str, message: str) -> CommandError:
        return CommandError(command_id=command.command_id, code=code, message=message)

    route = command.route
    if not route or command.toll_rate_bp is None or command.toll_food_per_head is None:
        return error("invalid_toll", "a toll names its post, its rates, and a deposit route")
    tile = route[0]
    if tile in tolled:
        return error("duplicate_toll", "the same post is tolled twice in one council")
    existing = next((post for post in civilization.toll_posts if post.tile == tile), None)
    if not command.toll_rate_bp and not command.toll_food_per_head:
        if existing is None or existing.lifted:
            return error("invalid_toll", "there is no toll here to lift")
        return None
    if state.territory.owner_of().get(tile) != civilization_id:
        return error("invalid_toll", "a toll can only be set on land this civilization holds")
    if tile not in {view.tile for view in known_roads(state, civilization_id)}:
        return error("invalid_toll", "a toll can only be set on a road")
    staffed = {settlement.tile for settlement in civilization.settlements} | {
        garrison.tile for garrison in civilization.garrisons
    }
    if tile not in staffed:
        return error("invalid_toll", "a toll needs a settlement or garrison to collect it")
    own_settlements = {settlement.tile for settlement in civilization.settlements}
    if (
        route[-1] not in own_settlements
        or (len(route) == 1 and tile != civilization.start_center)
        or any(step not in civilization.known_tiles for step in route)
        or any(first.distance(second) != 1 for first, second in pairwise(route))
        or not passable(
            state.world_map,
            route[1:],
            start=route[0],
            bridges=known_spans(state, civilization_id),
        )
    ):
        return error(
            "invalid_route",
            "a deposit route leads over known land to one of this civilization's settlements",
        )
    if rules_for(state.rules_version).ranks:
        deposit = settlement_at(civilization, route[-1])
        assert deposit is not None
        if not at_least(settlement_rank(civilization, deposit.settlement_id), TOLL_RANK):
            return error(
                "rank_required",
                f"a toll's takings go to a {TOLL_RANK.value.replace('_', ' ')} or above",
            )
    return None


def _treaty_end_error(
    command: DirectOrder,
    civilization_id: EntityId,
    state: WorldState,
    ending: set[EntityId],
) -> CommandError | None:
    """Validate a cancellation or repudiation of a treaty this civilization is party to."""

    def error(code: str, message: str) -> CommandError:
        return CommandError(command_id=command.command_id, code=code, message=message)

    treaty = next(
        (item for item in state.active_treaties if item.treaty_id == command.treaty_id),
        None,
    )
    if (
        treaty is None
        or not treaty.in_force
        or civilization_id
        not in {treaty.proposer_civilization_id, treaty.recipient_civilization_id}
    ):
        return error("unknown_treaty", "only a treaty in force with this civilization can end")
    if treaty.treaty_id in ending:
        return error("duplicate_treaty_act", "the treaty is already being ended in this council")
    if command.kind is DirectOrderKind.REPUDIATE_TREATY:
        return None
    if command.recipient_civilization_id != treaty.counterparty(civilization_id):
        return error("invalid_treaty_party", "a cancellation must go to the other party")
    if any(
        message.cancellation_of == treaty.treaty_id
        and message.sender_civilization_id == civilization_id
        and message.status is MissionStatus.IN_TRANSIT
        for message in state.diplomatic_missions
    ):
        return error("cancellation_pending", "a cancellation notice is already travelling")
    return None


COUNTED_ORDERS = frozenset(
    {
        DirectOrderKind.START_PROJECT,
        DirectOrderKind.FOUND_INSTITUTION,
        DirectOrderKind.STAFF_INSTITUTION,
        DirectOrderKind.BUILD_STOREHOUSE,
        DirectOrderKind.BUILD_WALLS,
        DirectOrderKind.BUILD_TOWERS,
        DirectOrderKind.REPAIR_WALLS,
        DirectOrderKind.CRAFT_EQUIPMENT,
        DirectOrderKind.RESEARCH,
        DirectOrderKind.DRILL,
    }
)
"""Work at home whose workers an order may count instead of naming (rules version 2)."""


def _counted_workers(
    command: DirectOrder, civilization_id: EntityId, state: WorldState, busy: set[EntityId]
) -> tuple[DirectOrder, CommandError | None]:
    """A counted order with the people it takes named: the lowest-numbered idle grown-ups
    at its settlement, none already busy or set to another order in this council."""

    def refused(code: str, message: str) -> tuple[DirectOrder, CommandError | None]:
        return command, CommandError(command_id=command.command_id, code=code, message=message)

    if not rules_for(state.rules_version).worker_counts:
        return refused("invalid_workers", "this world's rules take named workers only")
    if command.kind not in COUNTED_ORDERS:
        return refused("invalid_workers", "a worker count goes with work at home")
    if command.worker_ids:
        return refused("invalid_workers", "name the workers or count them, not both")
    if command.worker_count is None or command.settlement_id is None:
        return refused("invalid_workers", "a worker count names its settlement")
    civilization = state.civilizations[civilization_id]
    settlement = next(
        (item for item in civilization.settlements if item.settlement_id == command.settlement_id),
        None,
    )
    if settlement is None:
        return refused("unknown_settlement", "no such settlement of this civilization")
    chosen = idle_at(
        civilization.population.people.table,
        settlement.tile,
        busy,
        command.worker_count,
        fit=command.kind is DirectOrderKind.DRILL,
    )
    if len(chosen) < command.worker_count:
        return refused(
            "too_few_idle_workers",
            f"{len(chosen)} idle at {settlement.settlement_id}, {command.worker_count} asked",
        )
    return command.model_copy(update={"worker_ids": tuple(chosen)}), None


def validate_envelope(envelope: CommandEnvelope, state: WorldState) -> CommandValidation:
    if envelope.civilization_id not in state.civilizations:
        return CommandValidation(
            accepted=(),
            errors=(CommandError(command_id=None, code="unknown_civilization", message="unknown"),),
        )
    if envelope.council_day != state.day:
        return CommandValidation(
            accepted=(),
            errors=(CommandError(command_id=None, code="wrong_day", message="stale council day"),),
        )

    accepted: list[Command] = []
    errors: list[CommandError] = []
    seen: set[str] = set()
    seen_assignments: set[EntityId] = set()
    seen_expeditions: set[EntityId] = set()
    seen_messages: set[EntityId] = set()
    seen_treaties: set[EntityId] = set()
    seen_journeys: set[EntityId] = set()
    couriered: set[EntityId] = set()
    founding: set[tuple[EntityId, InstitutionKind]] = set()
    restaffed: set[EntityId] = set()
    starting_houses: set[EntityId] = set()
    rules = rules_for(state.rules_version)
    teaching_load: dict[EntityId, int] = {}
    for assignment in state.civilizations[envelope.civilization_id].teaching_assignments:
        teaching_load[assignment.teacher_id] = teaching_load.get(assignment.teacher_id, 0) + 1
    ending_treaties: set[EntityId] = set()
    committed_travellers: set[EntityId] = set()
    committed_at_home: set[EntityId] = set()
    tolled: set[HexCoord] = set()
    researching: set[CapabilityId] = set()
    teaching_people = {
        person_id
        for assignment in state.civilizations[envelope.civilization_id].teaching_assignments
        for person_id in (assignment.teacher_id, assignment.apprentice_id)
    }
    already_travelling = _travelling_people(state, envelope.civilization_id)
    garrisoned = _garrisoned_people(state, envelope.civilization_id)
    drilling = _drilling_people(state, envelope.civilization_id)
    working = {
        person_id
        for order in state.civilizations[envelope.civilization_id].work_orders
        for person_id in order.worker_ids
    }
    reserved_cargo: Reserved = {}
    upgrading: set[EntityId] = set()
    walling: set[HexCoord] = set()
    ordered_camps: set[EntityId] = set()
    answered: set[EntityId] = set()
    for command in envelope.commands:
        if command.command_id in seen:
            errors.append(
                CommandError(
                    command_id=command.command_id,
                    code="duplicate_command",
                    message="command ID is repeated",
                )
            )
            continue
        seen.add(command.command_id)
        if isinstance(command, DirectOrder):
            command_error: CommandError | None = None
            if command.worker_count is not None or command.settlement_id is not None:
                # Counted workers become named ones; every check below then sees them.
                command, counted_error = _counted_workers(
                    command,
                    envelope.civilization_id,
                    state,
                    already_travelling
                    | garrisoned
                    | drilling
                    | teaching_people
                    | working
                    | committed_travellers
                    | committed_at_home,
                )
                if counted_error is not None:
                    errors.append(counted_error)
                    continue
            if command.kind is DirectOrderKind.START_PROJECT and (
                command.project_id is None or command.project_kind is None
            ):
                command_error = CommandError(
                    command_id=command.command_id,
                    code="invalid_project",
                    message="start-project order requires project ID and kind",
                )
            if command.kind is DirectOrderKind.START_TEACHING:
                required = (
                    command.assignment_id,
                    command.teacher_id,
                    command.apprentice_id,
                    command.capability,
                )
                if any(value is None for value in required):
                    command_error = CommandError(
                        command_id=command.command_id,
                        code="invalid_teaching",
                        message=(
                            "start-teaching order requires assignment, teacher, apprentice, "
                            "and capability"
                        ),
                    )
                else:
                    assert command.assignment_id is not None
                    existing_assignments = state.civilizations[
                        envelope.civilization_id
                    ].teaching_assignments
                    is_duplicate = command.assignment_id in seen_assignments or any(
                        assignment.assignment_id == command.assignment_id
                        for assignment in existing_assignments
                    )
                    if is_duplicate:
                        command_error = CommandError(
                            command_id=command.command_id,
                            code="duplicate_assignment",
                            message="teaching assignment ID is repeated",
                        )
                    else:
                        seen_assignments.add(command.assignment_id)
            if command.kind is DirectOrderKind.START_EXPEDITION:
                if command.expedition_id is None or not command.explorer_ids or not command.route:
                    command_error = CommandError(
                        command_id=command.command_id,
                        code="invalid_expedition",
                        message="start-expedition order requires an ID, explorers, and route",
                    )
                elif command.expedition_id in seen_expeditions or any(
                    expedition.expedition_id == command.expedition_id
                    for expedition in state.civilizations[envelope.civilization_id].expeditions
                ):
                    command_error = CommandError(
                        command_id=command.command_id,
                        code="duplicate_expedition",
                        message="expedition ID is repeated",
                    )
                else:
                    seen_expeditions.add(command.expedition_id)
            if command.kind in MESSAGE_ORDERS:
                message_required = (
                    command.message_id,
                    command.ambassador_id,
                    command.recipient_civilization_id,
                )
                if (
                    any(value is None for value in message_required)
                    or not command.message_text
                    or not command.route
                ):
                    command_error = CommandError(
                        command_id=command.command_id,
                        code="invalid_message",
                        message=("message requires an ID, ambassador, recipient, text, and route"),
                    )
                else:
                    assert command.message_id is not None
                    duplicate = command.message_id in seen_messages or any(
                        message.message_id == command.message_id
                        for message in state.diplomatic_missions
                    )
                    if duplicate:
                        command_error = CommandError(
                            command_id=command.command_id,
                            code="duplicate_message",
                            message="message ID is repeated",
                        )
                    else:
                        seen_messages.add(command.message_id)
            if command.kind is DirectOrderKind.OFFER_TREATY:
                if command.treaty_id is None or command.treaty_kind is None:
                    command_error = CommandError(
                        command_id=command.command_id,
                        code="invalid_treaty",
                        message="treaty offer requires an ID and kind",
                    )
                elif command.peace_terms is not None and (
                    command.treaty_kind is not TreatyKind.PEACE
                    or command.peace_terms.tribute_payer
                    not in {None, envelope.civilization_id, command.recipient_civilization_id}
                ):
                    command_error = CommandError(
                        command_id=command.command_id,
                        code="invalid_treaty",
                        message="peace terms go with a peace offer, and a party pays tribute",
                    )
                elif (
                    command.peace_terms is not None
                    and rules.ranks
                    and command.peace_terms.tribute_payer is not None
                    and command.peace_terms.tribute_payer == command.recipient_civilization_id
                    and not realm_at_least(
                        state.civilizations[envelope.civilization_id].realm_rank_reached,
                        TRIBUTE_RANK,
                    )
                ):
                    command_error = CommandError(
                        command_id=command.command_id,
                        code="rank_required",
                        message=f"only a {TRIBUTE_RANK.value} or an empire can demand tribute",
                    )
                elif (
                    command.peace_terms is not None
                    and command.peace_terms.ceded_settlement is not None
                    and not any(
                        settlement.settlement_id == command.peace_terms.ceded_settlement
                        and not settlement.capital
                        for side in (envelope.civilization_id, command.recipient_civilization_id)
                        if side in state.civilizations
                        for settlement in state.civilizations[side].settlements
                    )
                ):
                    command_error = CommandError(
                        command_id=command.command_id,
                        code="invalid_treaty",
                        message="a ceded settlement belongs to one side and is not its capital",
                    )
                elif command.treaty_id in seen_treaties or any(
                    offer.offer_id == command.treaty_id for offer in state.treaty_offers
                ):
                    command_error = CommandError(
                        command_id=command.command_id,
                        code="duplicate_treaty",
                        message="treaty ID is repeated",
                    )
                else:
                    seen_treaties.add(command.treaty_id)
            if command.kind is DirectOrderKind.ACCEPT_TREATY and command.treaty_id is None:
                command_error = CommandError(
                    command_id=command.command_id,
                    code="invalid_treaty",
                    message="treaty acceptance requires the offered treaty ID",
                )
            if command.kind is DirectOrderKind.CLAIM_BORDER and (
                not command.claimed_tiles
                or len(set(command.claimed_tiles)) != len(command.claimed_tiles)
                or any(not state.world_map.contains(tile) for tile in command.claimed_tiles)
            ):
                command_error = CommandError(
                    command_id=command.command_id,
                    code="invalid_claim",
                    message="a claim names one or more distinct tiles on the map",
                )
            if command.kind in TREATY_END_ORDERS and command.treaty_id is None:
                command_error = CommandError(
                    command_id=command.command_id,
                    code="invalid_treaty",
                    message="ending a treaty requires its ID",
                )
            if command.kind in JOURNEY_ORDERS:
                traveller_ids = command.traveller_ids
                kind = JOURNEY_ORDERS[command.kind]
                foreign = kind not in INTERNAL_KINDS
                if (
                    command.journey_id is None
                    or (kind in TREATY_KINDS and command.treaty_id is None)
                    or (foreign and command.recipient_civilization_id is None)
                    or not traveller_ids
                    or (kind is not JourneyKind.CAMPAIGN and len(traveller_ids) > MAX_TRAVELLERS)
                    or len(set(traveller_ids)) != len(traveller_ids)
                    or not command.route
                ):
                    command_error = CommandError(
                        command_id=command.command_id,
                        code="invalid_journey",
                        message=(
                            "journey requires an ID, treaty, recipient, route, and one to "
                            "sixteen distinct travellers"
                        ),
                    )
                elif command.journey_id in seen_journeys or any(
                    journey.journey_id == command.journey_id for journey in state.journeys
                ):
                    command_error = CommandError(
                        command_id=command.command_id,
                        code="duplicate_journey",
                        message="journey ID is repeated",
                    )
                else:
                    seen_journeys.add(command.journey_id)
            person_ids = command.worker_ids
            if command.kind is DirectOrderKind.START_TEACHING and command_error is None:
                assert command.teacher_id is not None
                assert command.apprentice_id is not None
                person_ids += (command.teacher_id, command.apprentice_id)
            if command.kind is DirectOrderKind.START_EXPEDITION and command_error is None:
                person_ids += command.explorer_ids
            if command.kind in MESSAGE_ORDERS and command_error is None:
                assert command.ambassador_id is not None
                person_ids += (command.ambassador_id,)
            if command.kind in JOURNEY_ORDERS and command_error is None:
                person_ids += command.traveller_ids
            for person_id in person_ids:
                if command_error is not None:
                    break
                owner = _person_owner(state, person_id)
                if owner is None:
                    command_error = CommandError(
                        command_id=command.command_id,
                        code="unknown_person",
                        message=f"unknown person {person_id}",
                    )
                    break
                if owner != envelope.civilization_id:
                    command_error = CommandError(
                        command_id=command.command_id,
                        code="foreign_person",
                        message=f"person {person_id} belongs to another civilization",
                    )
                    break
                if not state.civilizations[owner].population.people[person_id].alive:
                    command_error = CommandError(
                        command_id=command.command_id,
                        code="dead_person",
                        message=f"person {person_id} is dead",
                    )
                    break
                if state.civilizations[owner].population.people[person_id].captive_of is not None:
                    command_error = CommandError(
                        command_id=command.command_id,
                        code="held_captive",
                        message=f"person {person_id} is held captive and cannot be ordered",
                    )
                    break
            travellers: tuple[EntityId, ...] = ()
            if command.kind is DirectOrderKind.START_EXPEDITION:
                travellers = command.explorer_ids
            elif command.kind in JOURNEY_ORDERS:
                travellers = command.traveller_ids
            elif command.kind in MESSAGE_ORDERS and command.ambassador_id is not None:
                travellers = (command.ambassador_id,)
            home_duty: tuple[EntityId, ...] = ()
            if command.kind in {
                DirectOrderKind.START_PROJECT,
                DirectOrderKind.DRILL,
                DirectOrderKind.CRAFT_EQUIPMENT,
                DirectOrderKind.RESEARCH,
                DirectOrderKind.BUILD_STOREHOUSE,
                DirectOrderKind.BUILD_WALLS,
                DirectOrderKind.BUILD_TOWERS,
                DirectOrderKind.REPAIR_WALLS,
                DirectOrderKind.FOUND_INSTITUTION,
                DirectOrderKind.STAFF_INSTITUTION,
            }:
                home_duty = command.worker_ids
            elif command.kind is DirectOrderKind.START_TEACHING and command_error is None:
                assert command.teacher_id is not None
                assert command.apprentice_id is not None
                home_duty = (command.teacher_id, command.apprentice_id)
            if travellers and command_error is None:
                committed = (
                    committed_travellers
                    | committed_at_home
                    | teaching_people
                    | already_travelling
                    | drilling
                )
                if command.kind is not DirectOrderKind.RELOCATE_GROUP:
                    committed |= garrisoned
                if any(person_id in committed for person_id in travellers):
                    command_error = CommandError(
                        command_id=command.command_id,
                        code="traveller_unavailable",
                        message="a traveller is already committed to another duty",
                    )
            if home_duty and command_error is None:
                away = committed_travellers | already_travelling | garrisoned | drilling
                if command.kind in {
                    DirectOrderKind.DRILL,
                    DirectOrderKind.CRAFT_EQUIPMENT,
                    DirectOrderKind.RESEARCH,
                    DirectOrderKind.BUILD_STOREHOUSE,
                    DirectOrderKind.BUILD_WALLS,
                    DirectOrderKind.BUILD_TOWERS,
                    DirectOrderKind.REPAIR_WALLS,
                    DirectOrderKind.FOUND_INSTITUTION,
                    DirectOrderKind.STAFF_INSTITUTION,
                }:
                    away |= committed_at_home | teaching_people
                if command.kind is DirectOrderKind.STAFF_INSTITUTION:
                    # The institution's own staff may be kept on.
                    away -= {
                        person_id
                        for institution in state.civilizations[
                            envelope.civilization_id
                        ].institutions
                        if institution.institution_id == command.institution_id
                        for person_id in institution.staff_ids
                    }
                if any(person_id in away for person_id in home_duty):
                    command_error = CommandError(
                        command_id=command.command_id,
                        code="person_travelling",
                        message="a person away on a journey cannot work or teach at home",
                    )
            if (
                command.kind in JOURNEY_ORDERS
                and command_error is None
                and command.treaty_id in ending_treaties
            ):
                command_error = CommandError(
                    command_id=command.command_id,
                    code="no_active_treaty",
                    message="this council already ended that treaty",
                )
            if command.kind in JOURNEY_ORDERS and command_error is None:
                command_error = _journey_error(
                    command, envelope.civilization_id, state, reserved_cargo
                )
            if command.kind in JOURNEY_ORDERS and command_error is None:
                command_error = _blockade_error(command, envelope.civilization_id, state)
            if command.kind is DirectOrderKind.RELEASE_PRISONERS and command_error is None:
                held = {
                    person_id
                    for other in state.civilizations.values()
                    for person_id, person in other.population.people.items()
                    if person.alive
                    and person.captive_of == envelope.civilization_id
                    and person.held_at is not None
                }
                if not command.captive_ids or not set(command.captive_ids) <= held:
                    command_error = CommandError(
                        command_id=command.command_id,
                        code="invalid_release",
                        message="only prisoners held at this civilization's settlements are let go",
                    )
            if (
                command_error is None
                and command.recipient_civilization_id in state.civilizations
                and state.civilizations[command.recipient_civilization_id].eliminated_day
                is not None
            ):
                command_error = CommandError(
                    command_id=command.command_id,
                    code="eliminated",
                    message="that civilization is no more",
                )
            if command.kind is DirectOrderKind.ANSWER_PETITION and command_error is None:
                waiting = next(
                    (
                        journey
                        for journey in state.journeys
                        if journey.journey_id == command.journey_id
                        and journey.waiting
                        and journey.recipient_civilization_id == envelope.civilization_id
                    ),
                    None,
                )
                if waiting is None or waiting.journey_id in answered:
                    command_error = CommandError(
                        command_id=command.command_id,
                        code="invalid_petition",
                        message="an answer names petitioners waiting at this civilization, once",
                    )
                else:
                    answered.add(waiting.journey_id)
            if command.kind is DirectOrderKind.SEND_COURIER and command_error is None:
                command_error = _courier_error(command, envelope.civilization_id, state, couriered)
                if command_error is None:
                    assert command.journey_id is not None
                    couriered.add(command.journey_id)
            if command.kind in CAMP_ORDERS and command_error is None:
                command_error = _siege_order_error(
                    command, envelope.civilization_id, state, ordered_camps
                )
                if command_error is None:
                    assert command.journey_id is not None
                    ordered_camps.add(command.journey_id)
            if command.kind is DirectOrderKind.DRILL and command_error is None:
                civilization = state.civilizations[envelope.civilization_id]
                homes = {settlement.tile for settlement in civilization.settlements}
                people = civilization.population.people
                if not command.worker_ids or len(set(command.worker_ids)) != len(
                    command.worker_ids
                ):
                    command_error = CommandError(
                        command_id=command.command_id,
                        code="invalid_drill",
                        message="a drill names one or more distinct people",
                    )
                elif not all(
                    able_to_fight(people[person_id]) and people[person_id].location in homes
                    for person_id in command.worker_ids
                ):
                    command_error = CommandError(
                        command_id=command.command_id,
                        code="invalid_drill",
                        message="only people fit to fight, at one of their settlements, drill",
                    )
            if command.kind is DirectOrderKind.RESEARCH and command_error is None:
                civilization = state.civilizations[envelope.civilization_id]
                homes = {settlement.tile for settlement in civilization.settlements}
                people = civilization.population.people
                reason = (
                    research_error(
                        command.research_topic,
                        civilization.capabilities,
                        civil=rules.civil_research,
                    )
                    if command.research_topic is not None
                    else "names no topic"
                )
                if reason is None and command.research_topic in researching:
                    reason = "is already being researched in this council"
                if reason is not None:
                    command_error = CommandError(
                        command_id=command.command_id,
                        code="invalid_research",
                        message=f"this research {reason}",
                    )
                elif command.research_topic in CIVIL_TOPICS and (
                    civil_error := _civil_research_error(command, state, envelope.civilization_id)
                ):
                    command_error = civil_error
                elif not command.worker_ids or len(set(command.worker_ids)) != len(
                    command.worker_ids
                ):
                    command_error = CommandError(
                        command_id=command.command_id,
                        code="invalid_research",
                        message="research names one or more distinct scholars",
                    )
                elif any(
                    people[person_id].location not in homes for person_id in command.worker_ids
                ):
                    command_error = CommandError(
                        command_id=command.command_id,
                        code="invalid_research",
                        message="scholars work at one of their settlements",
                    )
            if command.kind is DirectOrderKind.CRAFT_EQUIPMENT and command_error is None:
                command_error = _craft_error(
                    command, envelope.civilization_id, state, reserved_cargo
                )
            if command.kind in WALL_ORDERS and command_error is None:
                command_error = _walls_error(
                    command, envelope.civilization_id, state, reserved_cargo, walling
                )
            if (
                command.kind is DirectOrderKind.START_PROJECT
                and command.project_kind is ProjectKind.SHELTER
                and rules.houses
                and command_error is None
            ):
                command_error = _shelter_error(
                    command, envelope.civilization_id, state, reserved_cargo, starting_houses
                )
            if command.kind is DirectOrderKind.FOUND_INSTITUTION and command_error is None:
                command_error = _found_institution_error(
                    command, envelope.civilization_id, state, reserved_cargo, founding
                )
            if command.kind is DirectOrderKind.STAFF_INSTITUTION and command_error is None:
                command_error = _staff_institution_error(
                    command, envelope.civilization_id, state, restaffed
                )
            if command.kind is DirectOrderKind.BUILD_STOREHOUSE and command_error is None:
                command_error = _storehouse_error(
                    command, envelope.civilization_id, state, reserved_cargo, upgrading
                )
            if (
                command.kind is DirectOrderKind.DECLARE_WAR
                and command_error is None
                and command.recipient_civilization_id is not None
                and at_war(state, envelope.civilization_id, command.recipient_civilization_id)
            ):
                command_error = CommandError(
                    command_id=command.command_id,
                    code="already_at_war",
                    message="this civilization is already at war with the recipient",
                )
            if command.kind is DirectOrderKind.SET_TOLL and command_error is None:
                command_error = _toll_error(command, envelope.civilization_id, state, tolled)
            if command.kind is DirectOrderKind.START_TEACHING and command_error is None:
                assert command.teacher_id is not None
                assert command.capability is not None
                teacher = state.civilizations[envelope.civilization_id].population.people[
                    command.teacher_id
                ]
                if teacher.skills.get(command.capability.value, 0) <= 0:
                    command_error = CommandError(
                        command_id=command.command_id,
                        code="unqualified_teacher",
                        message="teacher does not possess the requested capability",
                    )
                schools = serving_tiles(
                    state.civilizations[envelope.civilization_id],
                    InstitutionKind.SCHOOL,
                    already_travelling,
                )
                limit = SCHOOL_APPRENTICES if teacher.location in schools else 1
                if command_error is None and teaching_load.get(command.teacher_id, 0) >= limit:
                    command_error = CommandError(
                        command_id=command.command_id,
                        code="teacher_busy",
                        message="a teacher takes one apprentice at a time, or two at a school",
                    )
                if command_error is None:
                    teaching_load[command.teacher_id] = teaching_load.get(command.teacher_id, 0) + 1
            if command.kind is DirectOrderKind.START_EXPEDITION and command_error is None:
                locations = {
                    state.civilizations[envelope.civilization_id]
                    .population.people[person_id]
                    .location
                    for person_id in command.explorer_ids
                }
                route = command.route
                if (
                    len(locations) != 1
                    or route[0] not in state.civilizations[envelope.civilization_id].known_tiles
                    or route[0] not in locations
                    or any(not state.world_map.contains(tile) for tile in route)
                    or any(first.distance(second) != 1 for first, second in pairwise(route))
                    or not _known_route_passable(
                        state.world_map,
                        route,
                        frozenset(state.civilizations[envelope.civilization_id].known_tiles),
                        known_spans(state, envelope.civilization_id),
                    )
                ):
                    command_error = CommandError(
                        command_id=command.command_id,
                        code="invalid_route",
                        message=(
                            "expedition route must start locally and use adjacent in-bounds tiles"
                        ),
                    )
            if command.kind in MESSAGE_ORDERS and command_error is None:
                assert command.ambassador_id is not None
                assert command.recipient_civilization_id is not None
                civilization = state.civilizations[envelope.civilization_id]
                contact = next(
                    (
                        item
                        for item in civilization.contacts
                        if item.civilization_id == command.recipient_civilization_id
                    ),
                    None,
                )
                ambassador = civilization.population.people[command.ambassador_id]
                in_transit = any(
                    message.ambassador_id == command.ambassador_id
                    and message.status.value == "in_transit"
                    for message in state.diplomatic_missions
                )
                if command.recipient_civilization_id not in state.civilizations:
                    command_error = CommandError(
                        command_id=command.command_id,
                        code="unknown_recipient",
                        message="recipient civilization is unknown",
                    )
                elif contact is None:
                    command_error = CommandError(
                        command_id=command.command_id,
                        code="unknown_contact",
                        message="messages require a physically discovered foreign settlement",
                    )
                elif in_transit:
                    command_error = CommandError(
                        command_id=command.command_id,
                        code="ambassador_unavailable",
                        message="ambassador already carries a message",
                    )
                elif (
                    command.route[0] != ambassador.location
                    or command.route[-1] != contact.settlement
                    or any(tile not in civilization.known_tiles for tile in command.route)
                    or any(not state.world_map.contains(tile) for tile in command.route)
                    or any(first.distance(second) != 1 for first, second in pairwise(command.route))
                    or not passable(
                        state.world_map,
                        command.route[1:],
                        start=command.route[0],
                        bridges=known_spans(state, envelope.civilization_id),
                    )
                ):
                    command_error = CommandError(
                        command_id=command.command_id,
                        code="invalid_route",
                        message=(
                            "message route must be known, adjacent, and end at the "
                            "discovered settlement"
                        ),
                    )
            if command.kind is DirectOrderKind.ACCEPT_TREATY and command_error is None:
                assert command.treaty_id is not None
                assert command.recipient_civilization_id is not None
                civilization = state.civilizations[envelope.civilization_id]
                offered = next(
                    (
                        message.treaty_offer
                        for message in civilization.received_messages
                        if message.treaty_offer is not None
                        and message.treaty_offer.offer_id == command.treaty_id
                    ),
                    None,
                )
                if offered is None:
                    command_error = CommandError(
                        command_id=command.command_id,
                        code="unknown_treaty",
                        message="only a delivered treaty offer may be accepted",
                    )
                elif (
                    offered.proposer_civilization_id != command.recipient_civilization_id
                    or offered.recipient_civilization_id != envelope.civilization_id
                ):
                    command_error = CommandError(
                        command_id=command.command_id,
                        code="invalid_treaty_party",
                        message="acceptance must return to the treaty proposer",
                    )
            if command.kind in TREATY_END_ORDERS and command_error is None:
                command_error = _treaty_end_error(
                    command, envelope.civilization_id, state, ending_treaties
                )
            if command_error is not None:
                errors.append(command_error)
                continue
            if command.kind in TREATY_END_ORDERS:
                assert command.treaty_id is not None
                ending_treaties.add(command.treaty_id)
            committed_travellers.update(travellers)
            committed_at_home.update(home_duty)
            if command.kind is DirectOrderKind.SET_TOLL:
                tolled.add(command.route[0])
            if command.kind is DirectOrderKind.RESEARCH and command.research_topic is not None:
                researching.add(command.research_topic)
            civilization = state.civilizations[envelope.civilization_id]
            if command.kind is DirectOrderKind.CRAFT_EQUIPMENT and command.craft_item:
                workshop = civilization.population.people[command.worker_ids[0]].location
                _reserve(
                    civilization,
                    workshop,
                    reserved_cargo,
                    craft_materials(command.craft_item, command.craft_quantity),
                )
            if command.kind in JOURNEY_ORDERS:
                _, taken = journey_supplies(command, state, envelope.civilization_id)
                _reserve(civilization, command.route[0], reserved_cargo, taken)
            if command.kind is DirectOrderKind.BUILD_STOREHOUSE:
                assert command.storehouse_grade is not None
                site = civilization.population.people[command.worker_ids[0]].location
                current = _storehouse_grade(civilization, command.storehouse_id)
                _reserve(
                    civilization,
                    site,
                    reserved_cargo,
                    step_materials(current, command.storehouse_grade),
                )
                if command.storehouse_id is not None:
                    upgrading.add(command.storehouse_id)
            if command.kind in WALL_ORDERS:
                site_tile = civilization.population.people[command.worker_ids[0]].location
                _reserve(
                    civilization, site_tile, reserved_cargo, _wall_materials(civilization, command)
                )
                walling.add(site_tile)
        elif (decree_error := _decree_error(command, state)) is not None:
            errors.append(decree_error)
            continue
        accepted.append(command)
    return CommandValidation(accepted=tuple(accepted), errors=tuple(errors))
