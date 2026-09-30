"""Settlement walls, raised one grade at a time, and the towers that stand on them."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sovereign_world.capabilities import CapabilityId
from sovereign_world.hexmap import HexCoord
from sovereign_world.ids import EntityId
from sovereign_world.resources import Resource

BASIS = 10_000
TOWER_CREW = 2
"""Home defenders needed to man each tower; they shoot from it and still fight."""
TOWER_HITS_BP = 1_500
"""Each manned tower's chance of a hit on the attackers in the opening volley and every round."""


class WallGrade(StrEnum):
    EARTHWORK = "earthwork"
    PALISADE = "palisade"
    DRYSTONE = "drystone_wall"
    MORTARED = "mortared_wall"
    FORTRESS = "fortress_wall"


@dataclass(frozen=True, slots=True)
class WallSpec:
    defence_bp: int
    """Multiplies the defence of the settlement's own defenders."""
    strength: int
    """Damage the walls can take before they fall to the grade below (W2c)."""
    towers: int
    """Towers the walls can carry."""
    materials: dict[Resource, int]
    """What the step up to this grade costs."""
    person_days: int
    capability: CapabilityId | None


GRADES: tuple[WallGrade, ...] = tuple(WallGrade)
WALL_GRADES: dict[WallGrade, WallSpec] = {
    WallGrade.EARTHWORK: WallSpec(11_000, 10, 0, {}, 30, None),
    WallGrade.PALISADE: WallSpec(
        12_500, 25, 2, {Resource.TIMBER: 40}, 60, CapabilityId.TIMBERCRAFT
    ),
    WallGrade.DRYSTONE: WallSpec(
        14_000, 45, 3, {Resource.STONE: 80}, 120, CapabilityId.STONEWORKING
    ),
    WallGrade.MORTARED: WallSpec(
        16_000,
        70,
        4,
        {Resource.STONE: 150, Resource.TOOL: 10},
        200,
        CapabilityId.FORTIFICATION,
    ),
    WallGrade.FORTRESS: WallSpec(
        18_000,
        100,
        6,
        {Resource.STONE: 250, Resource.TOOL: 20},
        300,
        CapabilityId.FORTIFICATION,
    ),
}
HIGH_WALLS = frozenset({WallGrade.MORTARED, WallGrade.FORTRESS})
"""Walls too high for ladders, and too thick for a ram to do more than halve their worth."""


@dataclass(frozen=True, slots=True)
class TowerSpec:
    materials: dict[Resource, int]
    person_days: int
    capability: CapabilityId


WOODEN_TOWER = TowerSpec({Resource.TIMBER: 10}, 10, CapabilityId.TIMBERCRAFT)
STONE_TOWER = TowerSpec({Resource.STONE: 20}, 20, CapabilityId.STONEWORKING)


def tower_spec(grade: WallGrade) -> TowerSpec:
    return WOODEN_TOWER if grade is WallGrade.PALISADE else STONE_TOWER


def rank(grade: WallGrade | None) -> int:
    return 0 if grade is None else GRADES.index(grade) + 1


def steps(current: WallGrade | None, target: WallGrade) -> tuple[WallGrade, ...]:
    return GRADES[rank(current) : rank(target)]


def step_materials(current: WallGrade | None, target: WallGrade) -> dict[Resource, int]:
    materials: dict[Resource, int] = {}
    for grade in steps(current, target):
        for resource, quantity in WALL_GRADES[grade].materials.items():
            materials[resource] = materials.get(resource, 0) + quantity
    return dict(sorted(materials.items()))


def tower_materials(grade: WallGrade, count: int) -> dict[Resource, int]:
    return {
        resource: quantity * count
        for resource, quantity in sorted(tower_spec(grade).materials.items())
    }


class Walls(BaseModel):
    """A settlement's walls: their grade, how much damage they can still take, their towers."""

    model_config = ConfigDict(frozen=True)

    settlement_id: EntityId
    grade: WallGrade
    strength: int = Field(ge=0)
    towers: int = Field(default=0, ge=0)
    built_day: int = Field(ge=0)

    @model_validator(mode="after")
    def within_grade(self) -> Walls:
        spec = WALL_GRADES[self.grade]
        if self.strength > spec.strength:
            raise ValueError("walls cannot be stronger than their grade")
        if self.towers > spec.towers:
            raise ValueError("walls carry no more towers than their grade allows")
        return self


class WallJob(BaseModel):
    """Builders raising a settlement's walls to a target grade, or adding towers to them.

    The materials for every step or tower were taken from the settlement's store when the
    job began.
    """

    model_config = ConfigDict(frozen=True)

    job_id: EntityId
    settlement_id: EntityId
    tile: HexCoord
    worker_ids: tuple[EntityId, ...]
    start_grade: WallGrade | None
    target: WallGrade | None = None
    """The grade to raise the walls to; none for a job that only adds towers."""
    towers: int = Field(default=0, ge=0)
    """Towers to add to walls of `start_grade`."""
    repair: bool = False
    """Restore walls of `start_grade` to full strength."""
    started_day: int = Field(ge=0)
    person_days_done: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def one_task(self) -> WallJob:
        if sum((self.target is not None, self.towers > 0, self.repair)) != 1:
            raise ValueError("a wall job raises the walls, adds towers, or repairs them")
        if self.repair and self.start_grade is None:
            raise ValueError("only standing walls are repaired")
        if self.target is not None and rank(self.target) <= rank(self.start_grade):
            raise ValueError("a wall job raises the walls' grade")
        if self.towers and self.start_grade is None:
            raise ValueError("towers stand on walls")
        return self

    def built(self) -> WallGrade | None:
        """The grade the work done so far has reached."""
        grade = self.start_grade
        if self.target is None:
            return grade
        spent = 0
        for step in steps(self.start_grade, self.target):
            spent += WALL_GRADES[step].person_days
            if self.person_days_done < spent:
                break
            grade = step
        return grade

    def towers_built(self) -> int:
        if not self.towers or self.start_grade is None:
            return 0
        return min(self.towers, self.person_days_done // tower_spec(self.start_grade).person_days)

    def repaired(self) -> bool:
        return (
            self.repair
            and self.start_grade is not None
            and self.person_days_done >= repair_person_days(self.start_grade)
        )

    @property
    def done(self) -> bool:
        if self.target is not None:
            return self.built() is self.target
        if self.repair:
            return self.repaired()
        return self.towers_built() == self.towers


def wall_bonus_after_engines(grade: WallGrade | None, engines: dict[Resource, int]) -> int:
    """Ladders halve low walls' worth and cannot scale high ones; a ram breaches low walls
    and halves the worth of high ones."""
    if grade is None:
        return BASIS
    extra = WALL_GRADES[grade].defence_bp - BASIS
    high = grade in HIGH_WALLS
    if engines.get(Resource.RAM):
        extra = extra // 2 if high else 0
    elif engines.get(Resource.LADDER) and not high:
        extra //= 2
    return BASIS + extra


def manned_towers(towers: int, defenders: int) -> int:
    return min(towers, defenders // TOWER_CREW)


WALL_HIT = 5
"""Strength a catapult hit knocks off a settlement's walls."""


def battered(walls: Walls, points: int) -> tuple[Walls | None, bool]:
    """Walls after taking damage, and whether they fell a grade.

    Walls whose strength reaches zero fall to the grade below at its full strength, and the
    towers beyond its limit fall with them; earthwork that falls leaves no walls.
    """
    strength = walls.strength - points
    if strength > 0:
        return walls.model_copy(update={"strength": strength}), False
    below = rank(walls.grade) - 1
    if below == 0:
        return None, True
    grade = GRADES[below - 1]
    spec = WALL_GRADES[grade]
    return (
        walls.model_copy(
            update={
                "grade": grade,
                "strength": spec.strength,
                "towers": min(walls.towers, spec.towers),
            }
        ),
        True,
    )


def repair_materials(grade: WallGrade) -> dict[Resource, int]:
    """A quarter of the grade's own materials, rounded up."""
    return {
        resource: -(-quantity // 4)
        for resource, quantity in sorted(WALL_GRADES[grade].materials.items())
    }


def repair_person_days(grade: WallGrade) -> int:
    return -(-WALL_GRADES[grade].person_days // 4)
