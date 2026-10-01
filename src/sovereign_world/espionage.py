"""Espionage: spies watch a foreign settlement and bring home what they saw.

What a spy sees is an estimate. It is rounder and more often wrong when the spy does not
speak the language around them. Each day on watch a spy may be caught, and a courier
carrying findings home may be caught on the other civilization's land.
"""

from __future__ import annotations

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from sovereign_world.hexmap import HexCoord
from sovereign_world.ids import EntityId
from sovereign_world.languages import speaks
from sovereign_world.people import Person
from sovereign_world.walls import WallGrade

SPYCRAFT = "spycraft"
"""A skill spies gain from each watch they come home from; it keeps them hidden."""
MAX_WATCH_DAYS = 90
MAX_SPIES = 4
WATCH_CAUGHT_BP = 100
"""Chance, in basis points, that a party on watch is found out on any one day."""
COURIER_CAUGHT_BP = 50
"""Chance that a courier is stopped on a day spent on the watched civilization's land."""
FLUENT_CUT_BP = 50
"""A spy who speaks the language passes for a local, and is found out less."""
SKILL_CUT_BP = 5
"""Each point of spycraft takes this much off, up to `MAX_SKILL_CUT_BP`."""
MAX_SKILL_CUT_BP = 50
MIN_CAUGHT_BP = 10
FLUENT_ERROR = 10
"""A spy who speaks the language miscounts by up to this percent."""
STRANGER_ERROR = 30
"""A spy who does not miscounts by up to this percent."""
STORE_ROUNDING = 50


class Estimate(BaseModel):
    """What a spy made of a settlement on their last day of watching it."""

    model_config = ConfigDict(frozen=True)

    settlement_id: EntityId
    civilization_id: EntityId
    tile: HexCoord
    day: int = Field(ge=0)
    residents: int = Field(ge=0)
    fighters: int = Field(ge=0)
    store_units: int = Field(ge=0)
    wall_grade: WallGrade | None = None
    towers: int = Field(default=0, ge=0)
    works: int = Field(default=0, ge=0)
    """Buildings, storehouses and walls going up there."""


class SpyReport(BaseModel):
    """Findings that reached home, by the spies themselves or by a courier."""

    model_config = ConfigDict(frozen=True)

    report_id: str
    journey_id: EntityId
    delivered_day: int = Field(ge=0)
    by_courier: bool = False
    estimate: Estimate


class CaughtSpy(BaseModel):
    """A foreign spy or courier this civilization caught, and who sent them."""

    model_config = ConfigDict(frozen=True)

    day: int = Field(ge=0)
    person_id: EntityId
    sender_civilization_id: EntityId
    settlement_id: EntityId


def caught_chance_bp(people: list[Person], language: EntityId, base_bp: int) -> int:
    """A party is as hidden as its least careful member: fluency and spycraft help."""
    cuts = [
        min(MAX_SKILL_CUT_BP, SKILL_CUT_BP * person.skills.get(SPYCRAFT, 0))
        + (FLUENT_CUT_BP if speaks(person, language) else 0)
        for person in people
    ]
    return max(MIN_CAUGHT_BP, base_bp - min(cuts, default=0))


def _misjudge(value: int, spread: int, roll: np.random.Generator) -> int:
    factor = 100 + int(roll.integers(-spread, spread + 1))
    return max(0, round(value * factor / 100))


def observe(
    *,
    settlement_id: EntityId,
    civilization_id: EntityId,
    tile: HexCoord,
    day: int,
    residents: int,
    fighters: int,
    store_units: int,
    wall_grade: WallGrade | None,
    towers: int,
    works: int,
    spies: list[Person],
    roll: np.random.Generator,
) -> Estimate:
    """What the spies make of the truth: walls are plain to see, numbers are guessed."""
    fluent = any(speaks(person, civilization_id) for person in spies)
    spread = FLUENT_ERROR if fluent else STRANGER_ERROR
    seen_residents = _misjudge(residents, spread, roll)
    seen_fighters = min(seen_residents, _misjudge(fighters, spread, roll))
    store = _misjudge(store_units, spread, roll)
    return Estimate(
        settlement_id=settlement_id,
        civilization_id=civilization_id,
        tile=tile,
        day=day,
        residents=seen_residents,
        fighters=seen_fighters,
        store_units=round(store / STORE_ROUNDING) * STORE_ROUNDING,
        wall_grade=wall_grade,
        towers=towers,
        works=works,
    )
