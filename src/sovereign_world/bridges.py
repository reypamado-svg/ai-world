"""Bridges: river borders a road crew has spanned, which any traveller then crosses freely.

A crew raising a road to a graded track or better bridges every river border its route
crosses, before crossing it. A stream or river takes timber and labour; a deep river takes
more of both, stone, and a living stoneworker in the crew.
"""

from __future__ import annotations

from collections.abc import Iterable
from itertools import pairwise
from math import ceil

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sovereign_world.hexmap import HexCoord, WorldMap, edge_key
from sovereign_world.ids import EntityId
from sovereign_world.resources import Resource
from sovereign_world.roads import RoadGrade, rank
from sovereign_world.travel import Bridges, Depth, river_depth

BRIDGING_GRADE = RoadGrade.GRADED
"""Crews raising a road to this grade or better bridge the rivers on their way."""

BRIDGE_LABOUR: dict[Depth, int] = {"stream": 60, "river": 60, "deep": 150}
"""Person-days to bridge a river of each depth."""

BRIDGE_MATERIALS: dict[Depth, dict[Resource, int]] = {
    "stream": {Resource.TIMBER: 20},
    "river": {Resource.TIMBER: 20},
    "deep": {Resource.TIMBER: 40, Resource.STONE: 20},
}
"""Materials one bridge of each depth uses up."""

MASONRY: frozenset[Depth] = frozenset({"deep"})
"""Depths whose bridge needs a living stoneworker in the crew."""


class Bridge(BaseModel):
    """A bridged river border between tiles a and b (in edge order)."""

    model_config = ConfigDict(frozen=True)

    a: HexCoord
    b: HexCoord
    civilization_id: EntityId
    """The civilization whose crew built it."""
    built_day: int = Field(ge=0)

    @model_validator(mode="after")
    def spans_one_border(self) -> Bridge:
        if not self.a < self.b or self.b not in self.a.neighbors():
            raise ValueError("a bridge spans one ordered border")
        return self


def can_bridge(grade: RoadGrade | None) -> bool:
    """Whether a crew raising a road to this grade bridges the rivers on its way."""
    return rank(grade) >= rank(BRIDGING_GRADE)


def bridged_edges(bridges: Iterable[Bridge]) -> frozenset[tuple[HexCoord, HexCoord]]:
    return frozenset((bridge.a, bridge.b) for bridge in bridges)


def span_needed(
    world_map: WorldMap, here: HexCoord, ahead: HexCoord, bridges: Bridges
) -> Depth | None:
    """How deep the unbridged river on this border is, or None where none runs or one is
    already bridged."""
    river = world_map.river_between(here, ahead)
    if river is None or edge_key(here, ahead) in bridges:
        return None
    return river_depth(river.flow)


def _spans(
    world_map: WorldMap, route: tuple[HexCoord, ...], grade: RoadGrade | None, bridges: Bridges
) -> list[tuple[tuple[HexCoord, HexCoord], Depth]]:
    if not can_bridge(grade):
        return []
    planned: dict[tuple[HexCoord, HexCoord], Depth] = {}
    for here, ahead in pairwise(route):
        depth = span_needed(world_map, here, ahead, bridges)
        if depth is not None:
            planned.setdefault(edge_key(here, ahead), depth)
    return sorted(planned.items())


def spans_planned(
    world_map: WorldMap, route: tuple[HexCoord, ...], grade: RoadGrade | None, bridges: Bridges
) -> frozenset[tuple[HexCoord, HexCoord]]:
    """The bridges standing plus every river border on the route a crew of this grade will
    bridge."""
    return frozenset(bridges) | {edge for edge, _depth in _spans(world_map, route, grade, bridges)}


def deep_spans(
    world_map: WorldMap, route: tuple[HexCoord, ...], grade: RoadGrade | None, bridges: Bridges
) -> bool:
    """Whether a crew of this grade would bridge a deep river on the route."""
    return any(depth in MASONRY for _edge, depth in _spans(world_map, route, grade, bridges))


def bridge_materials(
    world_map: WorldMap, route: tuple[HexCoord, ...], grade: RoadGrade | None, bridges: Bridges
) -> dict[Resource, int]:
    """Everything the bridges on a crew's route will use up."""
    total: dict[Resource, int] = {}
    for _edge, depth in _spans(world_map, route, grade, bridges):
        for resource, quantity in BRIDGE_MATERIALS[depth].items():
            total[resource] = total.get(resource, 0) + quantity
    return dict(sorted(total.items()))


def bridge_labour_days(
    world_map: WorldMap,
    route: tuple[HexCoord, ...],
    grade: RoadGrade | None,
    bridges: Bridges,
    crew: int,
) -> int:
    """Days a crew of this size spends building the bridges on its route."""
    return sum(
        ceil(BRIDGE_LABOUR[depth] / max(crew, 1))
        for _edge, depth in _spans(world_map, route, grade, bridges)
    )
