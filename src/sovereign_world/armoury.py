"""Kits and siege engines: what they are made of, who can make them, and what they do."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from sovereign_world.capabilities import CapabilityId
from sovereign_world.hexmap import HexCoord
from sovereign_world.ids import EntityId
from sovereign_world.resources import Resource

BASIS = 10_000


class UnitType(StrEnum):
    LEVY = "levy"
    SLINGER = "slinger"
    SPEARMAN = "spearman"
    AXEMAN = "axeman"
    ARCHER = "archer"
    HEAVY = "heavy_infantry"


@dataclass(frozen=True, slots=True)
class Kit:
    """A fighter's equipment and the unit type it makes of them."""

    resource: Resource
    unit: UnitType
    strength_bp: int
    """Melee strength, as a multiple of an unarmed fighter's."""
    attack_bp: int = BASIS
    """Extra strength when attacking."""
    defence_bp: int = BASIS
    """Extra strength when defending."""
    volley_bp: int = 0
    """Chance, in basis points, that the fighter lands a hit in the opening volley."""
    morale_bp: int = 0
    """Added to the side's morale threshold when the whole side carries this kit."""


KITS: dict[Resource, Kit] = {
    Resource.SLING: Kit(Resource.SLING, UnitType.SLINGER, 9_000, volley_bp=500),
    Resource.SPEAR: Kit(Resource.SPEAR, UnitType.SPEARMAN, 14_000, defence_bp=12_500),
    Resource.AXE: Kit(Resource.AXE, UnitType.AXEMAN, 15_000, attack_bp=11_000),
    Resource.BOW: Kit(Resource.BOW, UnitType.ARCHER, 9_000, volley_bp=1_000),
    Resource.BRONZE_ARMS: Kit(Resource.BRONZE_ARMS, UnitType.HEAVY, 18_000, morale_bp=500),
}
LEVY = Kit(Resource.FOOD, UnitType.LEVY, BASIS)
"""A fighter with no kit; the resource is unused."""

KIT_PRIORITY: tuple[Resource, ...] = (
    Resource.BRONZE_ARMS,
    Resource.AXE,
    Resource.SPEAR,
    Resource.BOW,
    Resource.SLING,
)
"""The best kit goes to the first fighter in id order, and so on down."""


@dataclass(frozen=True, slots=True)
class Engine:
    resource: Resource
    crew: int
    load: int
    """Carrying units the engine takes up in the party's packs."""
    heavy: bool
    """Heavy engines slow the whole party by half again on every tile."""


ENGINES: dict[Resource, Engine] = {
    Resource.LADDER: Engine(Resource.LADDER, crew=2, load=4, heavy=False),
    Resource.RAM: Engine(Resource.RAM, crew=4, load=10, heavy=True),
    Resource.CATAPULT: Engine(Resource.CATAPULT, crew=6, load=20, heavy=True),
}
ENGINE_PRIORITY: tuple[Resource, ...] = (Resource.CATAPULT, Resource.RAM, Resource.LADDER)
CREW_STRENGTH_BP = 5_000
"""Fighters working an engine fight at half strength."""
CATAPULT_HITS_BP = 3_000
"""Each catapult's chance of a hit on the defenders before every round."""


@dataclass(frozen=True, slots=True)
class Recipe:
    materials: dict[Resource, int]
    person_days: int
    capability: CapabilityId | None


RECIPES: dict[Resource, Recipe] = {
    Resource.SLING: Recipe({Resource.TIMBER: 1}, 1, None),
    Resource.SPEAR: Recipe({Resource.TIMBER: 2}, 2, CapabilityId.TIMBERCRAFT),
    Resource.BOW: Recipe({Resource.TIMBER: 3}, 3, CapabilityId.ARCHERY),
    Resource.BRONZE_ARMS: Recipe({Resource.METAL: 3}, 4, CapabilityId.BRONZE_WORKING),
    Resource.LADDER: Recipe({Resource.TIMBER: 4}, 2, None),
    Resource.RAM: Recipe({Resource.TIMBER: 10, Resource.PLANK: 4}, 6, CapabilityId.TIMBERCRAFT),
    Resource.CATAPULT: Recipe(
        {Resource.TIMBER: 15, Resource.STONE: 10}, 10, CapabilityId.SIEGECRAFT
    ),
    Resource.METAL: Recipe({Resource.ORE: 2}, 1, CapabilityId.METALLURGY_AWARENESS),
    Resource.TOOL: Recipe({Resource.METAL: 1, Resource.TIMBER: 1}, 1, None),
    Resource.PLANK: Recipe({Resource.TIMBER: 2}, 1, CapabilityId.TIMBERCRAFT),
}
"""Everything the armoury can make, one item at a time."""
GOODS_RECIPES: frozenset[Resource] = frozenset({Resource.METAL, Resource.TOOL, Resource.PLANK})
"""Goods rather than kits or engines: made only under rules version 2."""

MAX_CRAFT_QUANTITY = 100


class CraftJob(BaseModel):
    """Workers at home making equipment; materials were taken when the job began."""

    model_config = ConfigDict(frozen=True)

    job_id: EntityId
    item: Resource
    quantity: int = Field(ge=1, le=MAX_CRAFT_QUANTITY)
    worker_ids: tuple[EntityId, ...]
    workshop: HexCoord
    """The settlement whose store gave the materials and receives the items."""
    started_day: int = Field(ge=0)
    person_days_needed: int = Field(ge=1)
    person_days_done: int = Field(default=0, ge=0)

    @property
    def done(self) -> bool:
        return self.person_days_done >= self.person_days_needed


def craft_materials(item: Resource, quantity: int) -> dict[Resource, int]:
    return {
        resource: amount * quantity for resource, amount in sorted(RECIPES[item].materials.items())
    }


FORMATION_SPEAR = Kit(Resource.SPEAR, UnitType.SPEARMAN, 14_000, defence_bp=13_750)
"""A spearman trained in formations holds a line better still."""


def kit_assignment(
    fighter_ids: list[EntityId],
    kits: dict[Resource, int],
    *,
    formations: bool = False,
) -> dict[EntityId, Kit]:
    """Hand out kits, best first, to fighters in id order; the rest fight as levies."""
    issued: dict[EntityId, Kit] = {}
    queue = [resource for resource in KIT_PRIORITY for _ in range(kits.get(resource, 0))]
    for person_id, resource in zip(sorted(fighter_ids), queue, strict=False):
        kit = KITS[resource]
        issued[person_id] = FORMATION_SPEAR if formations and resource is Resource.SPEAR else kit
    return issued


def crewed_engines(fighter_count: int, engines: dict[Resource, int]) -> dict[Resource, int]:
    """Engines the party has hands to work, heaviest first; the rest stay idle."""
    working: dict[Resource, int] = {}
    hands = fighter_count
    for resource in ENGINE_PRIORITY:
        engine = ENGINES[resource]
        for _ in range(engines.get(resource, 0)):
            if hands < engine.crew:
                break
            working[resource] = working.get(resource, 0) + 1
            hands -= engine.crew
    return working


def crew_needed(engines: dict[Resource, int]) -> int:
    return sum(ENGINES[resource].crew * count for resource, count in engines.items())


def settlement_bonus_after_engines(bonus_bp: int, engines: dict[Resource, int]) -> int:
    """Ladders halve a settlement's defence bonus; a ram removes it."""
    extra = bonus_bp - BASIS
    if engines.get(Resource.RAM):
        extra = 0
    elif engines.get(Resource.LADDER):
        extra //= 2
    return BASIS + extra


WAR_GEAR = frozenset({*KITS, *ENGINES})
"""Everything a war party may carry besides food and plunder."""


def cargo_load(cargo: dict[Resource, int]) -> int:
    """Carrying units: one per kit, more for an engine."""
    return sum(
        (ENGINES[resource].load if resource in ENGINES else 1) * count
        for resource, count in cargo.items()
    )


def slows(cargo: dict[Resource, int]) -> bool:
    return any(
        ENGINES[resource].heavy
        for resource, count in cargo.items()
        if resource in ENGINES and count
    )


def slowed(cost: int) -> int:
    """Heavy engines add half again to every tile's cost."""
    return -(-cost * 3 // 2)


def personal_kits(cargo: dict[Resource, int]) -> dict[Resource, int]:
    return {resource: count for resource, count in cargo.items() if resource in KITS}


def engines_in(cargo: dict[Resource, int]) -> dict[Resource, int]:
    return {resource: count for resource, count in cargo.items() if resource in ENGINES}
