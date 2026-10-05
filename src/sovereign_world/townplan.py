"""Town plans: how a council lays out its settlement (rules version 3).

A council designs each settlement like a kingdom: a style, where the keep (the settlement's
hall) and the market, shrine and craft quarter stand, how far out the wall ring runs, and
where its gates open. The engine records the plan, so a replay shows the same town, and the
plan changes the game: a wall ring is built section by section and shelters only the houses
inside it; a keep at the centre, a hill fort, a craft quarter by the water and a market by
the store each change one existing number. A shrine is drawn but changes nothing yet.

Places inside a settlement are in blocks of 64 m around its centre; the observer lays the
streets out from the same plan. A new settlement starts with `DEFAULT_PLAN`, under which
every number is what it was before plans existed.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from sovereign_world.hexmap import HexCoord, Terrain, WorldMap
from sovereign_world.ids import EntityId
from sovereign_world.land import water_near

MAX_RING = 5
"""The widest wall ring, in 64 m blocks from the centre."""
GATE_SECTORS = 6
"""Gates open towards one of the six hex directions, numbered 0-5 as the map's neighbours."""
MAX_GATES = 3

SHELTERED_HOUSES: dict[int, int] = {1: 128, 2: 384, 3: 768, 4: 1280, 5: 1920}
"""How many houses a wall ring of each radius encloses (16 to a block, the core left open)."""


def sections_of(ring: int) -> int:
    """Wall sections in a ring: a square of side 2r+1 blocks, two block-sides a section."""
    return 2 * (2 * ring + 1)


STANDARD_RING = 2
"""The ring whose walls cost what walls cost before plans: ten sections."""


class PlanStyle(StrEnum):
    OPEN = "open"
    """Wards spread as they grow; no particular order."""
    RINGED = "ringed"
    """Wards in rings around the keep."""
    GRID = "grid"
    """Straight streets in a grid."""
    RIVER_TOWN = "river_town"
    """Strung along the water; needs water on or beside the tile."""
    HILL_FORT = "hill_fort"
    """Built up a hill around a high keep; needs hills or mountains."""


class Place(StrEnum):
    CENTRE = "centre"
    BY_STORE = "by_store"
    BY_GATE = "by_gate"
    BY_WATER = "by_water"
    """Needs water on or beside the tile."""
    EDGE = "edge"


class TownPlanSpec(BaseModel):
    """What a council writes: a settlement's design."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    style: PlanStyle
    keep: Place
    """Where the keep (the settlement's hall) stands."""
    market: Place | None = None
    shrine: Place | None = None
    craft_quarter: Place | None = None
    wall_ring: int = Field(ge=1, le=MAX_RING)
    """How far out the walls run, in 64 m blocks from the centre."""
    gates: tuple[int, ...] = Field(min_length=1, max_length=MAX_GATES)
    """The hex directions (0-5) the gates face."""

    @field_validator("gates")
    @classmethod
    def _distinct_gates(cls, gates: tuple[int, ...]) -> tuple[int, ...]:
        if any(not 0 <= gate < GATE_SECTORS for gate in gates):
            raise ValueError("gates face directions 0-5")
        if len(set(gates)) != len(gates):
            raise ValueError("each gate faces a different direction")
        return tuple(sorted(gates))

    def needs_water(self) -> bool:
        return self.style is PlanStyle.RIVER_TOWN or Place.BY_WATER in (
            self.keep,
            self.market,
            self.shrine,
            self.craft_quarter,
        )

    def needs_heights(self) -> bool:
        return self.style is PlanStyle.HILL_FORT


class TownPlan(TownPlanSpec):
    """A settlement's recorded design, and the day it was drawn up."""

    settlement_id: EntityId
    planned_day: int = Field(ge=0)

    def spec(self) -> TownPlanSpec:
        return TownPlanSpec.model_validate(
            self.model_dump(exclude={"settlement_id", "planned_day"})
        )


DEFAULT_PLAN = TownPlanSpec(
    style=PlanStyle.OPEN, keep=Place.EDGE, wall_ring=STANDARD_RING, gates=(0,)
)
"""The plan a settlement starts with: every number as it was before plans existed."""


def default_plan(settlement_id: EntityId, day: int) -> TownPlan:
    return TownPlan(**DEFAULT_PLAN.model_dump(), settlement_id=settlement_id, planned_day=day)


def is_default(plan: TownPlan) -> bool:
    return plan.spec() == DEFAULT_PLAN


def site_error(world_map: WorldMap, tile: HexCoord, spec: TownPlanSpec) -> str | None:
    """Why the land at `tile` cannot carry this design, or None when it can."""
    if spec.needs_heights() and world_map.tile(tile).terrain not in (
        Terrain.HILLS,
        Terrain.MOUNTAIN,
    ):
        return "a hill fort needs hills or mountains"
    if spec.needs_water() and not water_near(world_map, tile):
        return "a river town or a place by the water needs water on or beside the tile"
    return None
