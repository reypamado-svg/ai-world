"""The ends of civilizations: homelessness, elimination, the ruins they leave, and the
endings of the world itself."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from sovereign_world.hexmap import HexCoord
from sovereign_world.ids import EntityId
from sovereign_world.resources import Inventory
from sovereign_world.stores import Storehouse
from sovereign_world.walls import Walls

BREAKUP_GRACE_DAYS = 90
"""How long a civilization may go without a working settlement before it breaks up."""
BREAKUP_SHARE = 4
"""Each council, one in this many of a broken-up civilization's free people leaves."""


class Ruin(BaseModel):
    """What is left of an eliminated civilization's settlement: open to salvage or resettling."""

    model_config = ConfigDict(frozen=True)

    ruin_id: EntityId
    tile: HexCoord
    former_settlement_id: EntityId
    former_civilization_id: EntityId
    since_day: int = Field(ge=0)
    store: Inventory
    storehouses: tuple[Storehouse, ...] = ()
    walls: Walls | None = None


class EndingKind(StrEnum):
    LAST_CIVILIZATION = "last_civilization"
    NO_CIVILIZATION = "no_civilization"


class Ending(BaseModel):
    """The day only one civilization, or none, still had living members."""

    model_config = ConfigDict(frozen=True)

    kind: EndingKind
    day: int = Field(ge=0)
    survivor_id: EntityId | None = None
    population: int = Field(default=0, ge=0)
