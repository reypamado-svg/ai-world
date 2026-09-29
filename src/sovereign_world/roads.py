"""Road grades: what each costs to build and how much quicker it makes a tile to cross."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sovereign_world.hexmap import HexCoord, Terrain
from sovereign_world.ids import EntityId
from sovereign_world.resources import Resource


class RoadGrade(StrEnum):
    FOOTPATH = "footpath"
    TRACK = "track"
    GRADED = "graded"
    GRAVEL = "gravel"
    PAVED = "paved"
    HIGHWAY = "highway"


GRADES: tuple[RoadGrade, ...] = tuple(RoadGrade)
"""Every grade, lowest first; a tile passes through each in turn."""

ROAD_COST: dict[Terrain, tuple[int, ...]] = {
    Terrain.GRASSLAND: (9, 8, 7, 6, 5, 4),
    Terrain.FOREST: (13, 11, 9, 8, 6, 5),
    Terrain.DESERT: (13, 11, 9, 8, 6, 5),
    Terrain.TUNDRA: (13, 11, 9, 8, 6, 5),
    Terrain.MOUNTAIN: (26, 22, 18, 15, 12, 10),
}
"""Entry cost of a road tile, in tenths of a day, for each grade in order."""

LABOUR_MULTIPLE: dict[RoadGrade, int] = {
    RoadGrade.FOOTPATH: 1,
    RoadGrade.TRACK: 2,
    RoadGrade.GRADED: 3,
    RoadGrade.GRAVEL: 4,
    RoadGrade.PAVED: 6,
    RoadGrade.HIGHWAY: 8,
}
"""Person-days to reach a grade, as a multiple of the tile's roadless entry cost."""

STEP_MATERIALS: dict[RoadGrade, dict[Resource, int]] = {
    RoadGrade.FOOTPATH: {},
    RoadGrade.TRACK: {},
    RoadGrade.GRADED: {Resource.TIMBER: 3},
    RoadGrade.GRAVEL: {Resource.STONE: 5},
    RoadGrade.PAVED: {Resource.STONE: 15},
    RoadGrade.HIGHWAY: {Resource.STONE: 20, Resource.TOOL: 2},
}
"""Materials consumed on one tile to reach each grade."""

STONE_LAYING = frozenset({RoadGrade.PAVED, RoadGrade.HIGHWAY})
"""Grades whose work needs a living stoneworker in the crew."""

STONEWORKING = "stoneworking"


def rank(grade: RoadGrade | None) -> int:
    """0 for no road, then 1 (footpath) to 6 (highway)."""
    return 0 if grade is None else GRADES.index(grade) + 1


def next_grade(grade: RoadGrade | None) -> RoadGrade | None:
    """The grade the next piece of work would reach, or None at the top."""
    level = rank(grade)
    return GRADES[level] if level < len(GRADES) else None


def steps_to(current: RoadGrade | None, target: RoadGrade) -> tuple[RoadGrade, ...]:
    """Every grade a tile must still pass through to reach the target."""
    return GRADES[rank(current) : rank(target)]


def road_cost(terrain: Terrain, grade: RoadGrade) -> int:
    return ROAD_COST[terrain][rank(grade) - 1]


def step_labour(base_cost: int, grade: RoadGrade) -> int:
    """Person-days to raise one tile to this grade from the grade below."""
    return base_cost * LABOUR_MULTIPLE[grade]


def materials_for(
    tiles: Iterable[HexCoord],
    target: RoadGrade,
    grades: Mapping[HexCoord, RoadGrade],
) -> dict[Resource, int]:
    """Everything needed to raise each distinct tile to the target grade."""
    total: dict[Resource, int] = {}
    for tile in dict.fromkeys(tiles):
        for grade in steps_to(grades.get(tile), target):
            for resource, quantity in STEP_MATERIALS[grade].items():
                total[resource] = total.get(resource, 0) + quantity
    return dict(sorted(total.items()))


class Road(BaseModel):
    """A built road tile; any traveller of any civilization moves faster on it."""

    model_config = ConfigDict(frozen=True)

    tile: HexCoord
    grade: RoadGrade
    civilization_id: EntityId
    """The civilization whose crew last raised the grade."""
    built_day: int = Field(ge=0)
    graded_day: int = Field(ge=0)

    @model_validator(mode="after")
    def graded_after_built(self) -> Road:
        if self.graded_day < self.built_day:
            raise ValueError("a road cannot change grade before it was built")
        return self


class RoadView(BaseModel):
    """A road a civilization knows of, at the grade it last saw."""

    model_config = ConfigDict(frozen=True)

    tile: HexCoord
    grade: RoadGrade
    as_of_day: int = Field(ge=0)


def grades_of(roads: Iterable[Road]) -> dict[HexCoord, RoadGrade]:
    return {road.tile: road.grade for road in roads}
