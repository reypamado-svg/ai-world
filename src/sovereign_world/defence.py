"""Standing defence orders (rules version 3): how a settlement fights when it is attacked.

A council sets, for each of its settlements, who stands in the line (everyone, only those
trained to arms, or everyone but its craftsmen), what share of the line waits as a reserve
for a second wave, who mans the towers, and who gets the best arms first. Until it does,
every able person fights, kits go out in id order, and any two defenders man a tower: the
same battle as before orders existed.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from sovereign_world.armoury import RECIPES
from sovereign_world.ids import EntityId
from sovereign_world.walls import STONE_TOWER, WALL_GRADES, WOODEN_TOWER

BASIS = 10_000
FIGHTER_ARMS = 10
"""Arms skill that makes someone one of the settlement's fighters."""
MIN_LINE = 4
"""Fewer fighters than this and everyone fights after all."""
MAX_RESERVE_BP = 5_000
RESERVE_JOINS_ROUND = 3
"""The round a reserve joins the fight, unless the line would break sooner."""
VETERAN_TOWER_HITS_BP = 2_000
"""A tower manned by two veterans hits more often than one manned by anyone (1,500)."""
SALLY_TOWER_HITS_BP = 750
"""Each manned tower facing a camp covers a sally against it at half a tower's usual chance."""
SALLY_PURSUIT_BP = 500
"""A sally routed under a complete ring is chased half as far as usual (1,000)."""
COUNTER_BATTERY_DRAWS = 3
"""Draws a town's catapult makes each siege day, all always made: whether it hits, whom,
how hard."""
CRAFT_SKILLS: frozenset[str] = frozenset(
    capability.value
    for capability in (
        *(spec.capability for spec in WALL_GRADES.values()),
        WOODEN_TOWER.capability,
        STONE_TOWER.capability,
        *(recipe.capability for recipe in RECIPES.values()),
    )
    if capability is not None
)
"""The skills of the people `craftsmen_back` keeps out of the fight: building and making."""


class Posture(StrEnum):
    EVERYONE = "everyone"
    """Every able person fights (the default)."""
    FIGHTERS = "fighters"
    """Only those trained to arms fight; everyone if too few are."""
    CRAFTSMEN_BACK = "craftsmen_back"
    """Everyone fights but the builders and makers, who are kept safe."""


class Crews(StrEnum):
    ANY = "any"
    """Any two defenders man a tower (the default)."""
    DRILLED = "drilled"
    """The best fighters man the towers."""


class Arms(StrEnum):
    ANY = "any"
    """The best kits go out in id order (the default)."""
    VETERANS = "veterans"
    """The best kits go to the most practised fighters first."""


class DefenceOrderSpec(BaseModel):
    """What a council writes: how one of its settlements defends itself."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    posture: Posture = Posture.EVERYONE
    reserve_bp: int = Field(default=0, ge=0, le=MAX_RESERVE_BP)
    """The share of the line, in basis points, held back for a second wave."""
    tower_crews: Crews = Crews.ANY
    arms_priority: Arms = Arms.ANY


class DefenceOrder(DefenceOrderSpec):
    """A settlement's standing defence order, and the day it was given."""

    settlement_id: EntityId
    set_day: int = Field(ge=0)

    def spec(self) -> DefenceOrderSpec:
        return DefenceOrderSpec.model_validate(
            self.model_dump(exclude={"settlement_id", "set_day"})
        )


DEFAULT_DEFENCE = DefenceOrderSpec()


def is_craftsman(skills: dict[str, int]) -> bool:
    return any(skills.get(skill, 0) > 0 for skill in CRAFT_SKILLS)
