"""Toll posts on roads: what a passing party owes, and the way round when it cannot pay."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sovereign_world.hexmap import HexCoord, WorldMap
from sovereign_world.ids import EntityId
from sovereign_world.resources import Resource
from sovereign_world.travel import entry_cost

MAX_CARGO_RATE_BP = 2_000
MAX_FOOD_PER_HEAD = 2
MIN_DEPOSIT_DAYS = 10
MAX_DEPOSIT_DAYS = 90
DEFAULT_DEPOSIT_DAYS = 30
BASIS_POINTS = 10_000


class TollPost(BaseModel):
    """A staffed toll on one of a civilization's road tiles, with a chest of takings."""

    model_config = ConfigDict(frozen=True)

    post_id: EntityId
    civilization_id: EntityId
    tile: HexCoord
    cargo_rate_bp: int = Field(ge=0, le=MAX_CARGO_RATE_BP)
    food_per_head: int = Field(ge=0, le=MAX_FOOD_PER_HEAD)
    deposit_every_days: int = Field(
        default=DEFAULT_DEPOSIT_DAYS, ge=MIN_DEPOSIT_DAYS, le=MAX_DEPOSIT_DAYS
    )
    deposit_route: tuple[HexCoord, ...]
    """From the post to the settlement whose storehouse receives the chest."""
    set_day: int = Field(ge=0)
    last_deposit_day: int = Field(ge=0)
    collecting: bool = True
    """False once the toll is lifted, or has lapsed for want of collectors or land."""
    lifted: bool = False
    """True once the owner lifts the toll; a lifted post only waits to empty its chest."""
    chest: dict[Resource, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_shape(self) -> TollPost:
        if not self.deposit_route or self.deposit_route[0] != self.tile:
            raise ValueError("a deposit route starts at its toll post")
        if any(quantity <= 0 for quantity in self.chest.values()):
            raise ValueError("a toll chest holds only positive quantities")
        if self.lifted and self.collecting:
            raise ValueError("a lifted toll collects nothing")
        return self

    @property
    def at_storehouse(self) -> bool:
        """A gate on the storehouse's own tile stores its takings at once."""
        return len(self.deposit_route) == 1


class TollView(BaseModel):
    """A toll a civilization knows of, at the rates it last saw."""

    model_config = ConfigDict(frozen=True)

    tile: HexCoord
    owner: EntityId
    cargo_rate_bp: int = Field(ge=0, le=MAX_CARGO_RATE_BP)
    food_per_head: int = Field(ge=0, le=MAX_FOOD_PER_HEAD)
    as_of_day: int = Field(ge=0)


@dataclass(frozen=True, slots=True)
class TollGate:
    """A toll collecting today: its owner and its rates."""

    owner: EntityId
    cargo_rate_bp: int
    food_per_head: int


@dataclass(frozen=True, slots=True)
class TollRules:
    """Everything travel needs to charge tolls and find ways round them today."""

    gates: Mapping[HexCoord, TollGate] = field(default_factory=dict)
    free_passage: frozenset[frozenset[EntityId]] = frozenset()
    """Pairs of civilizations whose parties pass each other's tolls free."""
    known_gates: Mapping[EntityId, Mapping[HexCoord, TollGate]] = field(default_factory=dict)
    known_tiles: Mapping[EntityId, frozenset[HexCoord]] = field(default_factory=dict)

    def exempt(self, payer: EntityId, owner: EntityId) -> bool:
        return payer == owner or frozenset({payer, owner}) in self.free_passage


def cargo_charge(cargo: Mapping[Resource, int], rate_bp: int) -> dict[Resource, int]:
    """The share of each cargo resource a shipment pays, never all of any one of them."""
    if not rate_bp:
        return {}
    charge: dict[Resource, int] = {}
    for resource, quantity in sorted(cargo.items()):
        owed = max(quantity * rate_bp // BASIS_POINTS, 1 if quantity >= 2 else 0)
        if owed:
            charge[resource] = owed
    return charge


def food_charge(gate: TollGate, travellers: int) -> int:
    return gate.food_per_head * travellers


def detour(
    world_map: WorldMap,
    known: frozenset[HexCoord],
    route: tuple[HexCoord, ...],
    index: int,
    avoid: frozenset[HexCoord],
) -> tuple[HexCoord, ...] | None:
    """The shortest way over known land from route[index] back onto a later route tile.

    Returns the rewritten route, or None when no way round is known. Ties between
    equally short ways go to neighbours in a fixed order.
    """
    rejoin = {tile: position for position, tile in enumerate(route) if position > index + 1}
    rejoin = {tile: position for tile, position in rejoin.items() if tile not in avoid}
    start = route[index]
    previous: dict[HexCoord, HexCoord | None] = {start: None}
    frontier = deque([start])
    while frontier:
        tile = frontier.popleft()
        if tile in rejoin and tile != start:
            path = [tile]
            while (step := previous[path[-1]]) is not None:
                path.append(step)
            path.reverse()
            return (*route[:index], *path, *route[rejoin[tile] + 1 :])
        for neighbor in sorted(tile.neighbors()):
            if (
                neighbor in previous
                or neighbor in avoid
                or neighbor not in known
                or not world_map.contains(neighbor)
                or entry_cost(world_map, neighbor, origin=tile) is None
            ):
                continue
            previous[neighbor] = tile
            frontier.append(neighbor)
    return None
