"""Aggregate authoritative world state and canonical hashing."""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

import numpy as np
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializationInfo,
    SerializerFunctionWrapHandler,
    model_serializer,
)

from sovereign_world.armoury import CraftJob
from sovereign_world.bridges import Bridge
from sovereign_world.capabilities import (
    CapabilityId,
    CapabilityRecord,
    TeachingAssignment,
    regional_capability,
)
from sovereign_world.config import CURRENT_RULES, RunManifest, WorldConfig
from sovereign_world.diplomacy import (
    ActiveTreaty,
    Contact,
    DiplomaticMessage,
    MissionStatus,
    TreatyOffer,
)
from sovereign_world.endings import Ending, Ruin, RuinView
from sovereign_world.espionage import CaughtSpy, SpyReport
from sovereign_world.exploration import Expedition, ExpeditionStatus, Observation
from sovereign_world.hexmap import HexCoord, Terrain, WorldMap
from sovereign_world.housing import HouseJob, Housing, founding_housing
from sovereign_world.ids import EntityId, IdAllocator
from sovereign_world.institutions import Institution
from sovereign_world.logistics import (
    Journey,
    JourneyKind,
    JourneyOutcome,
    LogisticsNotice,
)
from sovereign_world.people import Population, create_founders
from sovereign_world.people_store import people_hash
from sovereign_world.ranks import RealmRank, SettlementRank
from sovereign_world.research import ResearchAssignment
from sovereign_world.resources import Inventory, Resource
from sovereign_world.rings import WallRing, gate_sections, tower_cap
from sovereign_world.rng import StableRng
from sovereign_world.roads import Road, RoadView
from sovereign_world.sites import Site
from sovereign_world.stores import (
    FOUNDING_GRADE,
    Storehouse,
    StorehouseJob,
    founding_capacity,
    founding_storehouses,
)
from sovereign_world.territory import Claim, Garrison, Settlement, Territory
from sovereign_world.tolls import TollPost, TollView
from sovereign_world.townplan import DEFAULT_PLAN, TownPlan, default_plan, site_error
from sovereign_world.walls import WallJob, Walls
from sovereign_world.war import Battle, BattleReport, Drill, Occupation, Siege, War
from sovereign_world.work import ConstructionProject, WorkOrder
from sovereign_world.worldgen import generate_world


class CivilizationState(BaseModel):
    civilization_id: EntityId
    start_center: HexCoord
    population: Population
    inventory: Inventory
    """The capital's store."""
    stores: dict[EntityId, Inventory] = Field(default_factory=dict)
    """Every other settlement's store, by settlement id."""
    storehouses: tuple[Storehouse, ...] = ()
    storehouse_jobs: tuple[StorehouseJob, ...] = ()
    walls: tuple[Walls, ...] = ()
    """Each settlement's walls, by settlement id."""
    homeless_since: int | None = None
    """The day this civilization was left without a working settlement."""
    eliminated_day: int | None = None
    last_crisis_council: int | None = None
    """The day of its last crisis council; crisis councils come at most once a week."""
    """The day its last member died or left; it then keeps only its history."""
    wall_jobs: tuple[WallJob, ...] = ()
    work_orders: tuple[WorkOrder, ...] = ()
    projects: dict[EntityId, ConstructionProject] = Field(default_factory=dict)
    known_tiles: tuple[HexCoord, ...] = ()
    observations: tuple[Observation, ...] = ()
    expeditions: tuple[Expedition, ...] = ()
    capabilities: tuple[CapabilityRecord, ...] = ()
    teaching_assignments: tuple[TeachingAssignment, ...] = ()
    contacts: tuple[Contact, ...] = ()
    received_messages: tuple[DiplomaticMessage, ...] = ()
    logistics_notices: tuple[LogisticsNotice, ...] = ()
    spy_reports: tuple[SpyReport, ...] = ()
    """Findings its spies and couriers have brought home."""
    caught_spies: tuple[CaughtSpy, ...] = ()
    institutions: tuple[Institution, ...] = ()
    ruin_intel: tuple[RuinView, ...] = ()
    """Ruins its people have seen, as they last saw them; by tile."""
    fallen: dict[EntityId, int] = Field(default_factory=dict)
    """Civilizations it knows have died out, and the day it learned each."""
    known_captives: tuple[EntityId, ...] = ()
    """Its own people it knows are held prisoner: told by a battle report, until they are
    home and free again."""
    """Archives, schools, healers' houses, workshops and diplomatic services."""
    """Foreign spies and couriers it has caught, and who sent them."""
    settlements: tuple[Settlement, ...] = ()
    garrisons: tuple[Garrison, ...] = ()
    claims: tuple[Claim, ...] = ()
    toll_posts: tuple[TollPost, ...] = ()
    road_intel: tuple[RoadView, ...] = ()
    """Roads learned from a trade partner's map, at the grade and day it showed."""
    toll_intel: tuple[TollView, ...] = ()
    """Tolls this civilization's people have met, or learned from a partner's map."""
    drills: tuple[Drill, ...] = ()
    craft_jobs: tuple[CraftJob, ...] = ()
    research: tuple[ResearchAssignment, ...] = ()
    research_points: dict[CapabilityId, int] = Field(default_factory=dict)
    """Progress toward each topic not yet discovered."""
    war_reports: tuple[BattleReport, ...] = ()
    """Battles as this civilization's own survivors told them."""
    housing: dict[EntityId, Housing] = Field(default_factory=dict)
    """Each settlement's houses, by settlement id (rules version 2)."""
    house_jobs: tuple[HouseJob, ...] = ()
    """Houses going up, by job id."""
    ranks_reached: dict[EntityId, SettlementRank] = Field(default_factory=dict)
    """Each settlement's rank above village, by settlement id (rules version 2)."""
    realm_rank_reached: RealmRank = RealmRank.CHIEFDOM
    town_plans: dict[EntityId, TownPlan] = Field(default_factory=dict)
    """Each settlement's design, by settlement id (rules version 3)."""
    wall_rings: dict[EntityId, WallRing] = Field(default_factory=dict)
    """Each settlement's walls along its planned ring, by settlement id (rules version 3)."""


_CIVILIZATION_ADDITIONS: tuple[tuple[str, object], ...] = (
    ("housing", {}),
    ("house_jobs", []),
    ("ranks_reached", {}),
    ("realm_rank_reached", "chiefdom"),
    ("town_plans", {}),
    ("wall_rings", {}),
)
"""Civilization fields added by rules versions 2 and 3, and the value at which each is left
out."""


class WorldState(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    run_id: UUID
    manifest_hash: str
    config: WorldConfig
    day: int = Field(default=0, ge=0)
    world_map: WorldMap
    civilizations: dict[EntityId, CivilizationState]
    active_decrees: dict[EntityId, dict[str, int]] = Field(default_factory=dict)
    diplomatic_missions: tuple[DiplomaticMessage, ...] = ()
    treaty_offers: tuple[TreatyOffer, ...] = ()
    active_treaties: tuple[ActiveTreaty, ...] = ()
    journeys: tuple[Journey, ...] = ()
    territory: Territory = Field(default_factory=Territory)
    roads: tuple[Road, ...] = ()
    bridges: tuple[Bridge, ...] = ()
    """River borders a road crew has bridged; any traveller crosses them at plain cost."""
    wars: tuple[War, ...] = ()
    battles: tuple[Battle, ...] = ()
    """Every battle as it really happened; civilizations see only their own reports."""
    sieges: tuple[Siege, ...] = ()
    """Every siege, standing or ended; each side sees the ones it is part of."""
    occupations: tuple[Occupation, ...] = ()
    """Every occupation, standing or ended; each side sees the ones it is part of."""
    ruins: tuple[Ruin, ...] = ()
    """Settlements left by eliminated civilizations, by tile."""
    endings: tuple[Ending, ...] = ()
    """The last-civilization and no-civilization endings, each recorded once."""
    joined_roads: tuple[EntityId, ...] = ()
    """Trade treaties whose partners' settlements a continuous road now links."""
    sites: tuple[Site, ...] = ()
    """Ore deposits, quarries, ancient ruins and troves placed when the world was made, by tile;
    worlds from before them have none."""
    rules_version: int = Field(default=1, ge=1, le=CURRENT_RULES)
    """The rules this world runs under, copied from its manifest; see ``rules.rules_for``."""

    @model_serializer(mode="wrap")
    def _omit_empty_additions(
        self, handler: SerializerFunctionWrapHandler, info: SerializationInfo
    ) -> object:
        # Worlds from before rivers had courses, or before any bridge stood, have none;
        # leaving those keys out keeps their saves' hashes.
        dumped = handler(self)
        if isinstance(dumped, dict):
            world_map = dumped.get("world_map")
            if isinstance(world_map, dict):
                if not world_map.get("rivers"):
                    world_map.pop("rivers", None)
                # Tiles without land cover (water, and every tile of older maps).
                for tile in world_map.get("tiles", ()):
                    if isinstance(tile, dict) and not tile.get("cover"):
                        tile.pop("cover", None)
            for key in ("bridges", "sites"):
                if not dumped.get(key):
                    dumped.pop(key, None)
            if dumped.get("rules_version") == 1:
                dumped.pop("rules_version")
            # Rules-2 additions to each civilization, left out while they hold nothing.
            for civilization in (dumped.get("civilizations") or {}).values():
                if isinstance(civilization, dict):
                    for key, empty in _CIVILIZATION_ADDITIONS:
                        if key in civilization and civilization[key] == empty:
                            civilization.pop(key)
        return dumped


def _canonical_payload(state: WorldState) -> str:
    return json.dumps(
        state.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )


def state_hash(state: WorldState) -> str:
    return hashlib.sha256(_canonical_payload(state).encode()).hexdigest()


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


OutsidePeople = tuple[dict[str, Any], dict[str, dict[str, Any]]]
"""A world as JSON without its map and people: the world's fields, and each
civilization's."""


def outside_people(state: WorldState) -> OutsidePeople:
    """The world dumped without its map and people, in one pass: what hash v2 hashes and
    what a format-2 journal compares from day to day."""
    dumped = state.model_dump(
        mode="json",
        exclude={
            "world_map": True,
            "civilizations": {"__all__": {"population": {"people"}}},
        },
    )
    civilizations: dict[str, dict[str, Any]] = dumped.pop("civilizations")
    return dumped, civilizations


def hash_parts(
    state: WorldState, *, fresh: bool = False, parts: OutsidePeople | None = None
) -> dict[str, object]:
    """What hash v2 is made of: the map's own hash, everything outside the people, and each
    civilization's fields and people, hashed apart. `parts` is `outside_people(state)`
    when the caller has it already."""
    dumped, civilizations = outside_people(state) if parts is None else parts
    return {
        "version": 2,
        "map": state.world_map.content_hash(),
        "world": _digest(dumped),
        "civilizations": {
            civilization_id: {
                "fields": _digest(civilizations[civilization_id]),
                "people": people_hash(civilization.population.people.table, fresh=fresh),
            }
            for civilization_id, civilization in sorted(state.civilizations.items())
        },
    }


def state_hash_v2(
    state: WorldState, *, fresh: bool = False, parts: OutsidePeople | None = None
) -> str:
    """Hash version 2 of the whole world: the same for the same state, as hash v1 is, but
    built from parts so that most of it is not worked out again each day. `fresh` works
    every part out from scratch, as verification does."""
    return _digest(hash_parts(state, fresh=fresh, parts=parts))


def build_initial_state(manifest: RunManifest) -> WorldState:
    stable_rng = StableRng(manifest.config.seed)
    generated = generate_world(
        manifest.config, stable_rng, generator_version=manifest.generator_version
    )
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
        founding_goods = {
            Resource.FOOD: manifest.config.founders_per_civilization * 730,
            Resource.WATER: manifest.config.founders_per_civilization * 30,
            Resource.TIMBER: 500,
            Resource.STONE: 300,
            Resource.AXE: 8,
        }
        inventory = Inventory(
            capacity=founding_capacity(sum(founding_goods.values())),
            quantities=founding_goods,
        )
        capital_id = EntityId(f"settlement:{civilization_id.rsplit(':', 1)[-1]}-0001")
        civilizations[civilization_id] = CivilizationState(
            civilization_id=civilization_id,
            start_center=start.center,
            population=population,
            inventory=inventory,
            known_tiles=known_tiles,
            observations=observations,
            storehouses=tuple(
                Storehouse(
                    storehouse_id=EntityId(f"storehouse:{capital_id}:{number:02d}"),
                    settlement_id=capital_id,
                    grade=FOUNDING_GRADE,
                    built_day=0,
                )
                for number in range(1, founding_storehouses(sum(founding_goods.values())) + 1)
            ),
            settlements=(
                Settlement(
                    settlement_id=capital_id,
                    civilization_id=civilization_id,
                    tile=start.center,
                    founded_day=0,
                    capital=True,
                ),
            ),
            capabilities=(
                CapabilityRecord(
                    capability=capability,
                    practitioner_ids=tuple(sorted(practitioner_ids)),
                    discovered_day=0,
                ),
            ),
            housing=(
                {capital_id: founding_housing(manifest.config.founders_per_civilization)}
                if manifest.rules_version >= 2
                else {}
            ),
            town_plans=(
                {capital_id: default_plan(capital_id, 0)} if manifest.rules_version >= 3 else {}
            ),
        )
    return WorldState(
        run_id=manifest.run_id,
        manifest_hash=manifest.content_hash(),
        config=manifest.config,
        world_map=generated.world_map,
        civilizations=civilizations,
        sites=generated.sites,
        rules_version=manifest.rules_version,
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
            if expedition.status is not ExpeditionStatus.ACTIVE:
                continue
            for person_id in expedition.explorer_ids:
                if person_id not in civilization.population.people:
                    raise ValueError("expedition explorer must be local")
        capabilities = tuple(record.capability for record in civilization.capabilities)
        sorted_capabilities = tuple(
            sorted(set(capabilities), key=lambda capability: capability.value)
        )
        if capabilities != sorted_capabilities:
            raise ValueError("civilization capabilities must be unique and sorted")
        tiles = [view.ruin.tile for view in civilization.ruin_intel]
        if tiles != sorted(set(tiles)):
            raise ValueError("ruin intel is one view per tile, by tile")
        if set(civilization.fallen) - (set(state.civilizations) - {civilization_id}):
            raise ValueError("a civilization learns only of other civilizations falling")
        if civilization.known_captives != tuple(sorted(set(civilization.known_captives))):
            raise ValueError("known captives are unique and sorted")
        if any(
            person_id not in civilization.population.people
            for person_id in civilization.known_captives
        ):
            raise ValueError("a civilization knows only of its own people held captive")
        kinds_at = [(item.settlement_id, item.kind) for item in civilization.institutions]
        if len(kinds_at) != len(set(kinds_at)):
            raise ValueError("a settlement keeps at most one institution of each kind")
        kept_by: dict[EntityId, EntityId] = {}
        for institution in civilization.institutions:
            for person_id in institution.staff_ids:
                if person_id not in civilization.population.people:
                    raise ValueError("an institution's staff belong to its civilization")
                if kept_by.setdefault(person_id, institution.institution_id) != (
                    institution.institution_id
                ):
                    raise ValueError("a person keeps one institution")
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
        contacts = civilization.contacts
        if contacts != tuple(sorted(contacts, key=lambda contact: contact.civilization_id)):
            raise ValueError("contacts must be sorted")
        if len({contact.civilization_id for contact in contacts}) != len(contacts):
            raise ValueError("contacts must be unique")
        for contact in contacts:
            foreign = state.civilizations.get(contact.civilization_id)
            if foreign is None or contact.civilization_id == civilization_id:
                raise ValueError("contact must identify a foreign civilization")
            if contact.settlement != foreign.start_center:
                raise ValueError("contact settlement must match the known foreign start")
        received = civilization.received_messages
        if received != tuple(sorted(received, key=lambda message: message.message_id)):
            raise ValueError("received messages must be sorted")
        if len({message.message_id for message in received}) != len(received):
            raise ValueError("received messages must be unique")
        if any(
            message.recipient_civilization_id != civilization_id
            or message.status is not MissionStatus.DELIVERED
            for message in received
        ):
            raise ValueError("received messages must be delivered to this civilization")
        settlements = civilization.settlements
        if settlements != tuple(sorted(settlements, key=lambda item: item.settlement_id)):
            raise ValueError("settlements must be sorted")
        if len({item.settlement_id for item in settlements}) != len(settlements):
            raise ValueError("settlements must be unique")
        if any(item.civilization_id != civilization_id for item in settlements):
            raise ValueError("a settlement must belong to its civilization")
        others = {item.settlement_id for item in settlements if not item.capital}
        if not set(civilization.stores) <= others:
            raise ValueError("stores belong to the civilization's non-capital settlements")
        houses = civilization.storehouses
        if houses != tuple(sorted(houses, key=lambda item: item.storehouse_id)):
            raise ValueError("storehouses must be sorted")
        if len({item.storehouse_id for item in houses}) != len(houses):
            raise ValueError("storehouses must be unique")
        settlement_ids = {item.settlement_id for item in settlements}
        if any(item.settlement_id not in settlement_ids for item in houses):
            raise ValueError("a storehouse stands in one of its civilization's settlements")
        if not set(civilization.housing) <= settlement_ids:
            raise ValueError("houses stand in their civilization's own settlements")
        house_jobs = [job.job_id for job in civilization.house_jobs]
        if house_jobs != sorted(set(house_jobs)):
            raise ValueError("house jobs are unique and sorted")
        if any(job.settlement_id not in settlement_ids for job in civilization.house_jobs):
            raise ValueError("houses go up in their civilization's own settlements")
        if not set(civilization.ranks_reached) <= settlement_ids or any(
            rank is SettlementRank.VILLAGE for rank in civilization.ranks_reached.values()
        ):
            raise ValueError("ranks above village belong to the civilization's own settlements")
        plans = list(civilization.town_plans)
        if plans != sorted(plans) or not set(plans) <= settlement_ids:
            raise ValueError("town plans belong to the civilization's own settlements, sorted")
        settled_at = {item.settlement_id: item.tile for item in settlements}
        for settlement_id, plan in civilization.town_plans.items():
            if plan.settlement_id != settlement_id:
                raise ValueError("town plan key does not match its settlement")
            if site_error(state.world_map, settled_at[settlement_id], plan) is not None:
                raise ValueError("a town plan must suit its settlement's land")
        builders = [person_id for job in civilization.house_jobs for person_id in job.worker_ids]
        if len(builders) != len(set(builders)):
            raise ValueError("a builder works on one house job at a time")
        rings = list(civilization.wall_rings)
        if rings != sorted(rings) or not set(rings) <= settlement_ids:
            raise ValueError("wall rings belong to the civilization's own settlements, sorted")
        if rings and civilization.walls:
            raise ValueError("a civilization builds walls by ring or whole, not both")
        for settlement_id, ring in civilization.wall_rings.items():
            design = civilization.town_plans.get(settlement_id) or DEFAULT_PLAN
            if ring.settlement_id != settlement_id:
                raise ValueError("wall ring key does not match its settlement")
            if ring.ring != design.wall_ring or ring.gates() != gate_sections(
                ring.ring, design.gates
            ):
                raise ValueError("a wall ring runs along its settlement's planned line")
            if ring.towers > tower_cap(ring):
                raise ValueError("a ring carries no more towers than its walls allow")
        for job in civilization.wall_jobs:
            if job.sections:
                ring_of_job = civilization.wall_rings.get(job.settlement_id)
                if ring_of_job is None or max(job.sections) >= len(ring_of_job.sections):
                    raise ValueError("a ring job works on its ring's own sections")
        walled = [item.settlement_id for item in civilization.walls]
        if walled != sorted(set(walled)) or not set(walled) <= settlement_ids:
            raise ValueError("each settlement has at most one set of walls, sorted")
        walling = [job.settlement_id for job in civilization.wall_jobs]
        if len(walling) != len(set(walling)):
            raise ValueError("one wall job at a time works on a settlement's walls")
        building = [job.storehouse_id for job in civilization.storehouse_jobs]
        if len(building) != len(set(building)):
            raise ValueError("one job at a time works on a storehouse")
        capitals = [item for item in settlements if item.capital]
        if civilization.eliminated_day is not None:
            if settlements or civilization.population.living_ids:
                raise ValueError("an eliminated civilization has no settlements and no one living")
        elif len(capitals) != 1 or capitals[0].tile != civilization.start_center:
            raise ValueError("a civilization has exactly one capital, at its start")
        garrisons = civilization.garrisons
        if garrisons != tuple(sorted(garrisons, key=lambda item: item.garrison_id)):
            raise ValueError("garrisons must be sorted")
        if len({item.garrison_id for item in garrisons}) != len(garrisons):
            raise ValueError("garrisons must be unique")
        stationed = [person_id for item in garrisons for person_id in item.member_ids]
        if len(stationed) != len(set(stationed)):
            raise ValueError("a person serves in at most one garrison")
        if any(
            item.civilization_id != civilization_id
            or any(person_id not in civilization.population.people for person_id in item.member_ids)
            for item in garrisons
        ):
            raise ValueError("a garrison belongs to its civilization and its people")
        posts = civilization.toll_posts
        if posts != tuple(sorted(posts, key=lambda item: item.tile)):
            raise ValueError("toll posts must be sorted by tile")
        if len({item.tile for item in posts}) != len(posts):
            raise ValueError("a tile has at most one toll post")
        own_settlements = {item.tile for item in settlements}
        for post in posts:
            if post.civilization_id != civilization_id:
                raise ValueError("a toll post belongs to its civilization")
            if post.deposit_route[-1] not in own_settlements:
                raise ValueError("a toll post deposits at one of its own settlements")
            if any(
                first.distance(second) != 1
                for first, second in zip(post.deposit_route, post.deposit_route[1:], strict=False)
            ):
                raise ValueError("a deposit route steps between neighbouring tiles")
        for intel in (civilization.road_intel, civilization.toll_intel):
            tiles = [item.tile for item in intel]
            if tiles != sorted(set(tiles)):
                raise ValueError("learned roads and tolls must be unique and sorted")
        if civilization.claims != tuple(
            sorted(civilization.claims, key=lambda item: item.claim_id)
        ):
            raise ValueError("claims must be sorted")
        notices = civilization.logistics_notices
        if notices != tuple(sorted(notices, key=lambda item: item.notice_id)):
            raise ValueError("logistics notices must be sorted")
        if len({item.notice_id for item in notices}) != len(notices):
            raise ValueError("logistics notices must be unique")
    person_owners: set[EntityId] = set()
    for civilization_id, civilization in state.civilizations.items():
        table = civilization.population.people.table
        rows = table.rows()
        ids = list(map(table.ids.__getitem__, rows.tolist()))
        records = table.column("person_id", rows)
        owners = table.column("civilization_id", rows)
        if (
            records == ids
            and set(owners) <= {civilization_id}
            and len(set(ids)) == len(ids)
            and person_owners.isdisjoint(ids)
        ):
            person_owners.update(ids)
            continue
        # Something is wrong: find the first person at fault, as each is checked in turn.
        for person_id, record_id, owner in zip(ids, records, owners, strict=True):
            if person_id in person_owners:
                raise ValueError("person IDs must be globally unique")
            if record_id != person_id or owner != civilization_id:
                raise ValueError("person record must match its civilization")
            person_owners.add(person_id)
    missions = state.diplomatic_missions
    if missions != tuple(sorted(missions, key=lambda message: message.message_id)):
        raise ValueError("diplomatic missions must be sorted")
    if len({message.message_id for message in missions}) != len(missions):
        raise ValueError("diplomatic missions must be unique")
    for message in missions:
        sender = state.civilizations.get(message.sender_civilization_id)
        if sender is None or message.recipient_civilization_id not in state.civilizations:
            raise ValueError("diplomatic mission must name existing civilizations")
        if (
            message.status is MissionStatus.IN_TRANSIT
            and message.ambassador_id not in sender.population.people
        ):
            raise ValueError("diplomatic ambassador must belong to sender")
    offers = state.treaty_offers
    if offers != tuple(sorted(offers, key=lambda offer: offer.offer_id)):
        raise ValueError("treaty offers must be sorted")
    if len({offer.offer_id for offer in offers}) != len(offers):
        raise ValueError("treaty offers must be unique")
    for offer in offers:
        if (
            offer.proposer_civilization_id not in state.civilizations
            or offer.recipient_civilization_id not in state.civilizations
            or offer.proposer_civilization_id == offer.recipient_civilization_id
        ):
            raise ValueError("treaty offer parties must be distinct existing civilizations")
    treaties = state.active_treaties
    if treaties != tuple(sorted(treaties, key=lambda treaty: treaty.treaty_id)):
        raise ValueError("active treaties must be sorted")
    if len({treaty.treaty_id for treaty in treaties}) != len(treaties):
        raise ValueError("active treaties must be unique")
    if any(treaty.treaty_id not in {offer.offer_id for offer in offers} for treaty in treaties):
        raise ValueError("active treaty requires a recorded offer")

    journeys = state.journeys
    if journeys != tuple(sorted(journeys, key=lambda journey: journey.journey_id)):
        raise ValueError("journeys must be sorted")
    if len({journey.journey_id for journey in journeys}) != len(journeys):
        raise ValueError("journeys must be unique")
    treaties_by_id = {treaty.treaty_id: treaty for treaty in treaties}
    required_kind = {JourneyKind.SHIPMENT: "trade", JourneyKind.MIGRATION: "migration"}
    busy: set[EntityId] = set()
    for journey in journeys:
        sender = state.civilizations.get(journey.sender_civilization_id)
        if sender is None or journey.recipient_civilization_id not in state.civilizations:
            raise ValueError("journey must name existing civilizations")
        treaty = treaties_by_id.get(journey.treaty_id) if journey.treaty_id else None
        tribute = (
            treaty is not None
            and journey.kind.value == "shipment"
            and treaty.terms is not None
            and treaty.terms.tribute_payer == journey.sender_civilization_id
        )
        if journey.kind in required_kind and (
            treaty is None
            or (treaty.kind.value != required_kind[journey.kind] and not tribute)
            or {treaty.proposer_civilization_id, treaty.recipient_civilization_id}
            != {journey.sender_civilization_id, journey.recipient_civilization_id}
        ):
            raise ValueError("journey requires a matching active treaty")
        if not journey.active:
            continue
        for person_id in journey.traveller_ids:
            if person_id not in sender.population.people:
                raise ValueError("travelling party must belong to its sender")
            if person_id in busy:
                raise ValueError("a person cannot travel on two journeys at once")
            busy.add(person_id)
            person = sender.population.people[person_id]
            if person.alive and person.location != journey.route[journey.route_index]:
                raise ValueError("living travellers must stand on their route position")
        # Salvagers and extractors have done their errand and carry its goods home.
        if (
            journey.carrying_cargo
            and journey.kind.value not in {"salvage", "extraction"}
            and journey.outcome
            not in {
                JourneyOutcome.PENDING,
                JourneyOutcome.FAILED,
                JourneyOutcome.REFUSED,
                JourneyOutcome.TURNED_BACK,
            }
        ):
            raise ValueError("only undelivered cargo can still be carried")
    for owner in state.territory.owners:
        if owner.civilization_id not in state.civilizations:
            raise ValueError("territory must belong to an existing civilization")
        if state.world_map.tile(owner.tile).terrain.value == "water":
            raise ValueError("water cannot be controlled")
    wars = state.wars
    if wars != tuple(sorted(wars, key=lambda war: war.war_id)):
        raise ValueError("wars must be sorted")
    for war in wars:
        if {war.aggressor_id, war.defender_id} - set(state.civilizations):
            raise ValueError("a war is fought between existing civilizations")
    active_pairs = [frozenset({war.aggressor_id, war.defender_id}) for war in wars if war.active]
    if len(active_pairs) != len(set(active_pairs)):
        raise ValueError("two civilizations fight at most one war at a time")
    if state.battles != tuple(sorted(state.battles, key=lambda battle: battle.battle_id)):
        raise ValueError("battles must be sorted")
    sieges = state.sieges
    if sieges != tuple(sorted(sieges, key=lambda siege: siege.siege_id)):
        raise ValueError("sieges must be sorted")
    camps = {journey.journey_id: journey for journey in state.journeys}
    standing = [siege.journey_id for siege in sieges if siege.active]
    if len(standing) != len(set(standing)):
        raise ValueError("a camp lays at most one siege")
    for siege in sieges:
        if {siege.besieger_id, siege.defender_id} - set(state.civilizations):
            raise ValueError("a siege is laid between existing civilizations")
        camp = camps.get(siege.journey_id)
        if siege.active and (camp is None or not camp.encamped):
            raise ValueError("a standing siege has its camp")
    marching = {
        person_id: journey.sender_civilization_id
        for journey in state.journeys
        if journey.active
        for person_id in journey.captive_ids
    }
    for civilization_id, civilization in state.civilizations.items():
        table = civilization.population.people.table
        # Only captives, and anyone wrongly held somewhere, have anything to check.
        for row in np.union1d(
            table.rows_where_set("captive_of"), table.rows_where_set("held_at")
        ).tolist():
            person_id = table.ids[row]
            person = table.person(row)
            if person.captive_of is None:
                if person.held_at is not None:
                    raise ValueError("only captives are held at a settlement")
                continue
            if person.captive_of == civilization_id or person.captive_of not in state.civilizations:
                raise ValueError("a captive is held by another existing civilization")
            if not person.alive:
                continue
            captor = state.civilizations[person.captive_of]
            if person.held_at is not None:
                if person.held_at not in {item.settlement_id for item in captor.settlements}:
                    raise ValueError("a captive is held at one of the captor's settlements")
            elif marching.get(person_id) != person.captive_of:
                raise ValueError("a captive not held at a settlement marches with the captor")
    ruins = state.ruins
    if ruins != tuple(sorted(ruins, key=lambda item: item.tile)):
        raise ValueError("ruins must be sorted by tile")
    if len({ruin.tile for ruin in ruins}) != len(ruins):
        raise ValueError("a tile holds at most one ruin")
    settled = {
        settlement.tile
        for civilization in state.civilizations.values()
        for settlement in civilization.settlements
    }
    if any(ruin.tile in settled for ruin in ruins):
        raise ValueError("a ruin is not a living settlement")
    sites = state.sites
    if sites != tuple(sorted(sites, key=lambda item: item.tile)):
        raise ValueError("sites must be sorted by tile")
    if len({site.tile for site in sites}) != len(sites):
        raise ValueError("a tile holds at most one site")
    if len({site.site_id for site in sites}) != len(sites):
        raise ValueError("site ids must be unique")
    for site in sites:
        if state.world_map.tile(site.tile).terrain is Terrain.WATER:
            raise ValueError("a site lies on land")
        if site.remaining > site.richness:
            raise ValueError("a site cannot hold more than it started with")
    occupations = state.occupations
    if occupations != tuple(sorted(occupations, key=lambda item: item.occupation_id)):
        raise ValueError("occupations must be sorted")
    holding = [item.journey_id for item in occupations if item.active]
    if len(holding) != len(set(holding)) or set(holding) & set(standing):
        raise ValueError("a war party holds at most one siege or occupation")
    held_settlements = [item.settlement_id for item in occupations if item.active]
    if len(held_settlements) != len(set(held_settlements)):
        raise ValueError("a settlement has at most one occupier")
    for occupation in occupations:
        if {occupation.occupier_id, occupation.owner_id} - set(state.civilizations):
            raise ValueError("an occupation is between existing civilizations")
        camp = camps.get(occupation.journey_id)
        if occupation.active and (camp is None or not camp.encamped):
            raise ValueError("a standing occupation has its occupiers")
    if any(
        journey.encamped and journey.journey_id not in {*standing, *holding}
        for journey in state.journeys
    ):
        raise ValueError("every camp lays a standing siege or holds an occupation")
    roads = state.roads
    if roads != tuple(sorted(roads, key=lambda road: road.tile)):
        raise ValueError("roads must be sorted by tile")
    if len({road.tile for road in roads}) != len(roads):
        raise ValueError("a tile has at most one road")
    for road in roads:
        if road.civilization_id not in state.civilizations:
            raise ValueError("a road is built by an existing civilization")
        if not state.world_map.contains(road.tile):
            raise ValueError("a road lies on the map")
        if state.world_map.tile(road.tile).terrain.value == "water":
            raise ValueError("no road can be built on water")
    bridges = state.bridges
    if bridges != tuple(sorted(bridges, key=lambda bridge: (bridge.a, bridge.b))):
        raise ValueError("bridges must be sorted by border")
    if len({(bridge.a, bridge.b) for bridge in bridges}) != len(bridges):
        raise ValueError("a border has at most one bridge")
    for bridge in bridges:
        if bridge.civilization_id not in state.civilizations:
            raise ValueError("a bridge is built by an existing civilization")
        if state.world_map.river_between(bridge.a, bridge.b) is None:
            raise ValueError("a bridge spans a river")
    for journey in journeys:
        if journey.kind is JourneyKind.ROADWORK and journey.route[0] not in {
            settlement.tile
            for settlement in state.civilizations[journey.sender_civilization_id].settlements
        }:
            raise ValueError("a road crew sets out from one of its own settlements")
