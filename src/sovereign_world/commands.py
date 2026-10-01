"""Versioned sovereign reports, commands, and semantic validation."""

from __future__ import annotations

from enum import StrEnum
from itertools import pairwise
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from sovereign_world.capabilities import CapabilityId
from sovereign_world.diplomacy import Contact, DiplomaticMessage, MissionStatus, TreatyKind
from sovereign_world.events import DomainEvent
from sovereign_world.exploration import ExpeditionStatus
from sovereign_world.hexmap import HexCoord, Terrain
from sovereign_world.ids import EntityId
from sovereign_world.logistics import (
    CARGO_UNITS_PER_CARRIER,
    INTERNAL_KINDS,
    MAX_TRAVELLERS,
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
from sovereign_world.resources import Resource
from sovereign_world.roads import (
    STONE_LAYING,
    STONEWORKING,
    RoadGrade,
    RoadView,
    materials_for,
)
from sovereign_world.state import WorldState
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
from sovereign_world.travel import passable
from sovereign_world.war import (
    BattleReport,
    Drill,
    War,
    WarObjective,
    able_to_fight,
)


class DecreeKind(StrEnum):
    FOOD_RESERVE_TARGET = "food_reserve_target"
    LABOR_PRIORITY = "labor_priority"
    POPULATION_GROWTH_POLICY = "population_growth_policy"


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
}
REQUIRED_TREATY: dict[JourneyKind, TreatyKind] = {
    JourneyKind.SHIPMENT: TreatyKind.TRADE,
    JourneyKind.MIGRATION: TreatyKind.MIGRATION,
}


MAX_CLAIMED_TILES = 256


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
    drill_days: int = Field(default=30, ge=1, le=180)
    extra_provisions: int = Field(default=0, ge=0, le=CARGO_UNITS_PER_CARRIER * MAX_TRAVELLERS)
    priority: int = Field(default=50, ge=0, le=100)


Command = Annotated[Decree | DirectOrder, Field(union_mode="left_to_right")]


class CommandEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int = Field(ge=1, le=1)
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


class CouncilReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    report_id: str
    civilization_id: EntityId
    day: int
    person_ids: tuple[EntityId, ...]
    start_center: HexCoord
    known_tiles: tuple[HexCoord, ...]
    known_terrain: tuple[tuple[HexCoord, Terrain], ...] = ()
    inventory: dict[Resource, int]
    project_ids: tuple[EntityId, ...]
    active_decrees: dict[str, int]
    contacts: tuple[Contact, ...] = ()
    received_messages: tuple[DiplomaticMessage, ...] = ()
    logistics_notices: tuple[LogisticsNotice, ...] = ()
    controlled_tiles: tuple[HexCoord, ...] = ()
    observed_control: tuple[ControlView, ...] = ()
    settlements: tuple[Settlement, ...] = ()
    garrisons: tuple[Garrison, ...] = ()
    known_roads: tuple[RoadView, ...] = ()
    toll_posts: tuple[TollPost, ...] = ()
    known_tolls: tuple[TollView, ...] = ()
    wars: tuple[War, ...] = ()
    war_reports: tuple[BattleReport, ...] = ()
    drills: tuple[Drill, ...] = ()
    recent_events: tuple[DomainEvent, ...] = ()


def build_council_report(
    state: WorldState,
    civilization_id: EntityId,
    recent_events: tuple[DomainEvent, ...] = (),
) -> CouncilReport:
    civilization = state.civilizations[civilization_id]
    latest_migration: dict[EntityId, Journey] = {}
    for journey in sorted(
        state.journeys, key=lambda item: (item.departed_day, item.journey_id)
    ):
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
    visible_events = tuple(
        event
        for event in recent_events
        if event.actor_id == str(civilization_id) or event.subject_id == str(civilization_id)
    )
    return CouncilReport(
        report_id=f"report:{state.day}:{civilization_id}",
        civilization_id=civilization_id,
        day=state.day,
        person_ids=tuple(
            sorted(
                person_id
                for person_id in civilization.population.people
                if person_id not in emigrants
            )
        ),
        start_center=civilization.start_center,
        known_tiles=tuple(sorted(civilization.known_tiles)),
        known_terrain=tuple(
            (tile, state.world_map.tile(tile).terrain) for tile in sorted(civilization.known_tiles)
        ),
        inventory=dict(civilization.inventory.quantities),
        project_ids=tuple(sorted(civilization.projects)),
        active_decrees=dict(state.active_decrees.get(civilization_id, {})),
        contacts=civilization.contacts,
        received_messages=civilization.received_messages,
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
        toll_posts=civilization.toll_posts,
        known_tolls=known_tolls(state, civilization_id),
        wars=tuple(
            war
            for war in state.wars
            if civilization_id in {war.aggressor_id, war.defender_id}
            and war.known_to(civilization_id)
        ),
        war_reports=civilization.war_reports,
        drills=civilization.drills,
        recent_events=visible_events,
    )


def _in_sight(state: WorldState, civilization_id: EntityId) -> frozenset[HexCoord]:
    """Tiles seen today from this civilization's inhabited settlements."""
    civilization = state.civilizations[civilization_id]
    return visible_tiles(
        state.world_map,
        (
            settlement.tile
            for settlement in civilization.settlements
            if any(
                person.alive and person.location == settlement.tile
                for person in civilization.population.people.values()
            )
        ),
    )


def trade_partners(state: WorldState, civilization_id: EntityId) -> frozenset[EntityId]:
    """Civilizations bound to this one by a trade treaty in force, with its road terms."""
    return frozenset(
        treaty.counterparty(civilization_id)
        for treaty in state.active_treaties
        if treaty.in_force
        and treaty.kind is TreatyKind.TRADE
        and civilization_id
        in {treaty.proposer_civilization_id, treaty.recipient_civilization_id}
    )


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
    for tile in _in_sight(state, civilization_id):
        views.pop(tile, None)
        if tile in roads:
            views[tile] = RoadView(tile=tile, grade=roads[tile], as_of_day=state.day)
    return tuple(views[tile] for tile in sorted(views))


def known_tolls(state: WorldState, civilization_id: EntityId) -> tuple[TollView, ...]:
    """Its own tolls, tolls in sight of its settlements today, and tolls met or mapped."""
    civilization = state.civilizations[civilization_id]
    views = {view.tile: view for view in civilization.toll_intel}
    in_sight = _in_sight(state, civilization_id)
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
    in_sight = _in_sight(state, civilization_id)
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


def at_war(state: WorldState, first: EntityId, second: EntityId) -> War | None:
    return next((war for war in state.wars if war.active and war.involves(first, second)), None)


def _drilling_people(state: WorldState, civilization_id: EntityId) -> set[EntityId]:
    return {
        person_id
        for drill in state.civilizations[civilization_id].drills
        if drill.active
        for person_id in drill.person_ids
    }


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
    crew = len(command.traveller_ids)
    taken = dict(command.cargo)
    free = trade_partners(state, civilization_id) | {civilization_id}
    toll_food = (
        0
        if kind in {JourneyKind.SHIPMENT, JourneyKind.CAMPAIGN}
        else sum(
            view.food_per_head * crew
            for view in known_tolls(state, civilization_id)
            if view.owner not in free and view.tile in set(command.route[1:])
        )
    )
    if kind is JourneyKind.ROADWORK and command.road_grade is not None:
        days = roadwork_days(state.world_map, command.route, command.road_grade, roads, crew)
        # A crew packs what it can bear and turns home when only the walk back is left.
        provisions = min(
            provisions_needed(days, crew, command.extra_provisions + toll_food),
            CARGO_UNITS_PER_CARRIER * crew,
        )
        taken = materials_for(command.route, command.road_grade, roads)
    else:
        provisions = provisions_needed(
            journey_days(kind, state.world_map, command.route, roads),
            crew,
            command.extra_provisions + toll_food,
        )
    taken[Resource.FOOD] = taken.get(Resource.FOOD, 0) + provisions
    return provisions, taken


def _campaign_error(
    command: DirectOrder,
    civilization_id: EntityId,
    state: WorldState,
    reserved_cargo: dict[Resource, int],
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
    route = command.route
    if (
        len(route) < 2
        or route[0] not in {settlement.tile for settlement in civilization.settlements}
        or any(tile not in civilization.known_tiles for tile in route)
        or any(not state.world_map.contains(tile) for tile in route)
        or any(first.distance(second) != 1 for first, second in pairwise(route))
        or not passable(state.world_map, route[1:])
    ):
        return error(
            "invalid_route", "a war party leaves one of its own settlements over known land"
        )
    people = civilization.population.people
    if any(people[person_id].location != route[0] for person_id in command.traveller_ids):
        return error("traveller_not_home", "the war party must set out together")
    if not all(able_to_fight(people[person_id]) for person_id in command.traveller_ids):
        return error("unfit_fighter", "fighters must be between 13 and 60 and not dying")
    expectant = {birth.parent_ids[0] for birth in civilization.population.scheduled_births}
    if expectant & set(command.traveller_ids):
        return error("expectant_traveller", "a mother with a birth due cannot march")
    if set(command.cargo) - {Resource.AXE} or command.cargo.get(Resource.AXE, 0) > len(
        command.traveller_ids
    ):
        return error("invalid_cargo", "a war party carries at most one axe per fighter")
    provisions, taken = journey_supplies(command, state, civilization_id)
    if provisions + sum(command.cargo.values()) > CARGO_UNITS_PER_CARRIER * len(
        command.traveller_ids
    ):
        return error("cargo_over_capacity", "each fighter can bear 50 units of food and arms")
    for resource, quantity in taken.items():
        if reserved_cargo.get(resource, 0) + quantity > civilization.inventory.quantities.get(
            resource, 0
        ):
            if resource is Resource.FOOD:
                return error("insufficient_provisions", "not enough food for the war party")
            return error("insufficient_goods", f"not enough {resource} to arm the war party")
    return None


def _internal_journey_error(
    command: DirectOrder,
    civilization_id: EntityId,
    state: WorldState,
    reserved_cargo: dict[Resource, int],
) -> CommandError | None:
    """Validate founding, garrisoning, or relocating against what this civilization knows."""
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
        or not passable(state.world_map, route[1:])
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
    if command.cargo:
        return error("invalid_cargo", "internal journeys carry no trade cargo")
    destination = route[-1]
    known_settlements = own_settlements | {contact.settlement for contact in civilization.contacts}
    believed_owner = {
        view.tile: view.owner for view in _observed_control(state, civilization_id)
    }
    foreign = believed_owner.get(destination) not in {None, civilization_id}
    if kind is JourneyKind.RELOCATION and destination not in own_settlements - {route[0]}:
        return error("invalid_destination", "people relocate to another of their own settlements")
    if kind is JourneyKind.SETTLEMENT and (
        foreign
        or any(tile.distance(destination) < SETTLEMENT_SPACING for tile in known_settlements)
    ):
        return error(
            "invalid_destination",
            "a new settlement needs land not seen as foreign, three tiles from any settlement",
        )
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
        if command.road_grade in STONE_LAYING and not any(
            people[person_id].skills.get(STONEWORKING, 0) > 0
            for person_id in command.traveller_ids
        ):
            return error("no_stoneworker", "laying stone needs a stoneworker in the crew")
    provisions, taken = journey_supplies(command, state, civilization_id)
    if provisions > CARGO_UNITS_PER_CARRIER * len(command.traveller_ids):
        return error("cargo_over_capacity", "each traveller can bear 50 units of provisions")
    for resource, quantity in taken.items():
        available = civilization.inventory.quantities.get(resource, 0)
        if reserved_cargo.get(resource, 0) + quantity > available:
            if resource is Resource.FOOD:
                return error("insufficient_provisions", "not enough food to provision the party")
            return error("insufficient_materials", f"not enough {resource} for the road")
    return None


def _journey_error(
    command: DirectOrder,
    civilization_id: EntityId,
    state: WorldState,
    reserved_cargo: dict[Resource, int],
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
    treaty = next(
        (item for item in state.active_treaties if item.treaty_id == command.treaty_id),
        None,
    )
    if (
        treaty is None
        or not treaty.in_force
        or treaty.kind is not REQUIRED_TREATY[kind]
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
        or not passable(state.world_map, route[1:])
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
        available = civilization.inventory.quantities.get(resource, 0)
        if reserved_cargo.get(resource, 0) + quantity > available:
            cargo_only = reserved_cargo.get(resource, 0) + command.cargo.get(resource, 0)
            if resource is Resource.FOOD and cargo_only <= available:
                return error("insufficient_provisions", "not enough food to provision the party")
            return error("insufficient_goods", f"not enough {resource} to ship")
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
        or not passable(state.world_map, route[1:])
    ):
        return error(
            "invalid_route",
            "a deposit route leads over known land to one of this civilization's settlements",
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
    ending_treaties: set[EntityId] = set()
    committed_travellers: set[EntityId] = set()
    committed_at_home: set[EntityId] = set()
    tolled: set[HexCoord] = set()
    teaching_people = {
        person_id
        for assignment in state.civilizations[envelope.civilization_id].teaching_assignments
        for person_id in (assignment.teacher_id, assignment.apprentice_id)
    }
    already_travelling = _travelling_people(state, envelope.civilization_id)
    garrisoned = _garrisoned_people(state, envelope.civilization_id)
    drilling = _drilling_people(state, envelope.civilization_id)
    reserved_cargo: dict[Resource, int] = {}
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
                        message=(
                            "message requires an ID, ambassador, recipient, text, and route"
                        ),
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
            travellers: tuple[EntityId, ...] = ()
            if command.kind is DirectOrderKind.START_EXPEDITION:
                travellers = command.explorer_ids
            elif command.kind in JOURNEY_ORDERS:
                travellers = command.traveller_ids
            elif command.kind in MESSAGE_ORDERS and command.ambassador_id is not None:
                travellers = (command.ambassador_id,)
            home_duty: tuple[EntityId, ...] = ()
            if command.kind in {DirectOrderKind.START_PROJECT, DirectOrderKind.DRILL}:
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
                if command.kind is DirectOrderKind.DRILL:
                    away |= committed_at_home | teaching_people
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
                    or not passable(
                        state.world_map,
                        (
                            tile
                            for tile in route[1:]
                            if tile in state.civilizations[envelope.civilization_id].known_tiles
                        ),
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
                    or not passable(state.world_map, command.route[1:])
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
            if command.kind in JOURNEY_ORDERS:
                _, taken = journey_supplies(command, state, envelope.civilization_id)
                for resource, quantity in taken.items():
                    reserved_cargo[resource] = reserved_cargo.get(resource, 0) + quantity
        accepted.append(command)
    return CommandValidation(accepted=tuple(accepted), errors=tuple(errors))
