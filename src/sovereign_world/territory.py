"""Territory derived from settlements, terrain, and supply rather than painted claims."""

from __future__ import annotations

import heapq
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from math import isqrt

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sovereign_world.hexmap import HexCoord, WorldMap
from sovereign_world.ids import EntityId
from sovereign_world.travel import Roads, entry_cost

SETTLEMENT_BASE_STRENGTH = 40
SETTLEMENT_STRENGTH_PER_ROOT = 8
TAKE_THRESHOLD = 40
LOSE_THRESHOLD = 25
CHALLENGE_MARGIN = 15
CHALLENGE_DAYS = 7
DAILY_GAIN = 3
DAILY_LOSS = 2
SETTLEMENT_SIGHT = 1
GARRISON_STRENGTH = 25
SETTLEMENT_SPACING = 3


class Settlement(BaseModel):
    """A place where a civilization's people live and from which it governs."""

    model_config = ConfigDict(frozen=True)

    settlement_id: EntityId
    civilization_id: EntityId
    tile: HexCoord
    founded_day: int = Field(ge=0)
    capital: bool = False


class Garrison(BaseModel):
    """People stationed on a tile to hold it; they project influence but never anchor it."""

    model_config = ConfigDict(frozen=True)

    garrison_id: EntityId
    civilization_id: EntityId
    tile: HexCoord
    member_ids: tuple[EntityId, ...]
    since_day: int = Field(ge=0)

    @model_validator(mode="after")
    def valid_members(self) -> Garrison:
        if not self.member_ids or self.member_ids != tuple(sorted(set(self.member_ids))):
            raise ValueError("garrison members must be unique, sorted, and not empty")
        return self


class Claim(BaseModel):
    """A border a sovereign announced. History only: control never reads it."""

    model_config = ConfigDict(frozen=True)

    claim_id: str
    civilization_id: EntityId
    claimed_day: int = Field(ge=0)
    tiles: tuple[HexCoord, ...]

    @model_validator(mode="after")
    def valid_tiles(self) -> Claim:
        if not self.tiles or self.tiles != tuple(sorted(set(self.tiles))):
            raise ValueError("claimed tiles must be unique, sorted, and not empty")
        return self


class HeldControl(BaseModel):
    """One civilization's slowly drifting hold on one tile."""

    model_config = ConfigDict(frozen=True)

    tile: HexCoord
    civilization_id: EntityId
    value: int = Field(gt=0)


class TileOwner(BaseModel):
    model_config = ConfigDict(frozen=True)

    tile: HexCoord
    civilization_id: EntityId
    since_day: int = Field(ge=0)


class Challenge(BaseModel):
    """A rival that has out-held a tile's owner by the margin for consecutive days."""

    model_config = ConfigDict(frozen=True)

    tile: HexCoord
    civilization_id: EntityId
    days: int = Field(ge=1)


class Territory(BaseModel):
    """Authoritative control, kept sparse and canonically sorted."""

    model_config = ConfigDict(frozen=True)

    held: tuple[HeldControl, ...] = ()
    owners: tuple[TileOwner, ...] = ()
    challenges: tuple[Challenge, ...] = ()
    cut_off: tuple[EntityId, ...] = ()

    @model_validator(mode="after")
    def canonical(self) -> Territory:
        held_keys = [(item.tile, item.civilization_id) for item in self.held]
        if held_keys != sorted(set(held_keys)):
            raise ValueError("held control must be unique and sorted")
        owned = [item.tile for item in self.owners]
        if owned != sorted(set(owned)):
            raise ValueError("each tile has at most one owner, sorted")
        challenged = [item.tile for item in self.challenges]
        if challenged != sorted(set(challenged)):
            raise ValueError("each tile has at most one challenge, sorted")
        if self.cut_off != tuple(sorted(set(self.cut_off))):
            raise ValueError("cut-off sources must be unique and sorted")
        return self

    def owner_of(self) -> dict[HexCoord, EntityId]:
        return {item.tile: item.civilization_id for item in self.owners}

    def held_by_tile(self) -> dict[HexCoord, dict[EntityId, int]]:
        table: dict[HexCoord, dict[EntityId, int]] = {}
        for item in self.held:
            table.setdefault(item.tile, {})[item.civilization_id] = item.value
        return table

    def contested(self) -> tuple[HexCoord, ...]:
        """Tiles where two or more civilizations hold at least the losing threshold."""
        return tuple(
            tile
            for tile, holders in sorted(self.held_by_tile().items())
            if sum(value >= LOSE_THRESHOLD for value in holders.values()) >= 2
        )


def settlement_strength(residents: int) -> int:
    """A settlement projects 40 plus 8 per square root of its residents; empty ones nothing."""
    if residents <= 0:
        return 0
    return SETTLEMENT_BASE_STRENGTH + SETTLEMENT_STRENGTH_PER_ROOT * isqrt(residents)


def influence_field(
    world_map: WorldMap,
    sources: Iterable[tuple[HexCoord, int]],
    roads: Roads | None = None,
) -> dict[HexCoord, int]:
    """Best (strength - travel cost) from any source, over passable land, above zero.

    Roads cut the travel cost, so influence reaches further along them.
    """
    best: dict[HexCoord, int] = {}
    frontier: list[tuple[int, int, int]] = []
    for tile, strength in sources:
        if strength > best.get(tile, 0):
            best[tile] = strength
            heapq.heappush(frontier, (-strength, tile.q, tile.r))
    while frontier:
        negative, q, r = heapq.heappop(frontier)
        tile = HexCoord(q, r)
        value = -negative
        if value != best.get(tile):
            continue
        for neighbor in world_map.neighbors(tile):
            cost = entry_cost(world_map, neighbor, roads)
            if cost is None:
                continue
            reached = value - cost
            if reached > best.get(neighbor, 0):
                best[neighbor] = reached
                heapq.heappush(frontier, (-reached, neighbor.q, neighbor.r))
    return best


def supply_connected(
    world_map: WorldMap,
    start: HexCoord,
    capital: HexCoord,
    civilization_id: EntityId,
    owners: Mapping[HexCoord, EntityId],
) -> bool:
    """Whether a source reaches its capital over land its civilization owns or nobody owns."""
    if start == capital:
        return True
    seen = {start}
    frontier = [start]
    while frontier:
        tile = frontier.pop()
        for neighbor in world_map.neighbors(tile):
            if neighbor in seen or entry_cost(world_map, neighbor) is None:
                continue
            if owners.get(neighbor, civilization_id) != civilization_id:
                continue
            if neighbor == capital:
                return True
            seen.add(neighbor)
            frontier.append(neighbor)
    return False


@dataclass(frozen=True, slots=True)
class ControlChange:
    tile: HexCoord
    civilization_id: EntityId
    gained: bool
    previous_owner: EntityId | None


@dataclass(frozen=True, slots=True)
class TerritoryDayResult:
    territory: Territory
    changes: tuple[ControlChange, ...]
    severed: tuple[EntityId, ...]
    restored: tuple[EntityId, ...]


def _drift(held: int, target: int) -> int:
    if target > held:
        return held + min(DAILY_GAIN, target - held)
    return held - min(DAILY_LOSS, held - target)


def _strongest(holders: Mapping[EntityId, int], threshold: int) -> EntityId | None:
    """The single civilization holding the most, at or above the threshold; ties give none."""
    eligible = sorted(
        (
            (value, civilization_id)
            for civilization_id, value in holders.items()
            if value >= threshold
        ),
        reverse=True,
    )
    if not eligible or (len(eligible) > 1 and eligible[0][0] == eligible[1][0]):
        return None
    return eligible[0][1]


def advance_territory(
    territory: Territory,
    world_map: WorldMap,
    settlements: Iterable[Settlement],
    residents: Mapping[EntityId, int],
    day: int,
    garrisons: Iterable[Garrison] = (),
    garrisoned: Mapping[EntityId, int] | None = None,
    roads: Roads | None = None,
) -> TerritoryDayResult:
    """Drift each civilization's hold toward its influence, then settle ownership.

    `residents` counts living people at each settlement, and `garrisoned` living
    members standing at each garrison. Unowned tiles are taken at 40;
    owners lose a tile below 25; a rival takes an owned tile only after beating the owner
    by 15 for 7 consecutive days; ties never change hands. An inhabited settlement's own
    tile always belongs to its civilization.
    """
    settlement_list = sorted(settlements, key=lambda item: item.settlement_id)
    owners = territory.owner_of()
    capitals = {item.civilization_id: item.tile for item in settlement_list if item.capital}
    sources: dict[EntityId, list[tuple[HexCoord, int]]] = {}
    cut_off: list[EntityId] = []
    garrisoned = garrisoned or {}
    projecting: list[tuple[EntityId, EntityId, HexCoord, int]] = [
        (
            settlement.settlement_id,
            settlement.civilization_id,
            settlement.tile,
            settlement_strength(residents.get(settlement.settlement_id, 0)),
        )
        for settlement in settlement_list
    ]
    projecting.extend(
        (
            garrison.garrison_id,
            garrison.civilization_id,
            garrison.tile,
            GARRISON_STRENGTH if garrisoned.get(garrison.garrison_id, 0) else 0,
        )
        for garrison in sorted(garrisons, key=lambda item: item.garrison_id)
    )
    for source_id, civilization_id, tile, strength in projecting:
        capital = capitals.get(civilization_id)
        if strength and (
            capital is None
            or not supply_connected(world_map, tile, capital, civilization_id, owners)
        ):
            strength //= 2
            cut_off.append(source_id)
        sources.setdefault(civilization_id, []).append((tile, strength))

    previous = territory.held_by_tile()
    fields = {
        civilization_id: influence_field(world_map, civilization_sources, roads)
        for civilization_id, civilization_sources in sorted(sources.items())
    }
    held: dict[HexCoord, dict[EntityId, int]] = {}
    tiles = set(previous)
    for field in fields.values():
        tiles.update(field)
    for tile in sorted(tiles):
        before = previous.get(tile, {})
        for civilization_id in sorted(set(before) | set(fields)):
            value = _drift(
                before.get(civilization_id, 0),
                fields.get(civilization_id, {}).get(tile, 0),
            )
            if value:
                held.setdefault(tile, {})[civilization_id] = value

    anchors = {
        item.tile: item.civilization_id
        for item in settlement_list
        if residents.get(item.settlement_id, 0) > 0
    }
    challenges = {item.tile: item for item in territory.challenges}
    since = {item.tile: item.since_day for item in territory.owners}
    new_owners: dict[HexCoord, EntityId] = {}
    new_challenges: list[Challenge] = []
    changes: list[ControlChange] = []
    for tile in sorted(set(held) | set(owners) | set(anchors)):
        holders = held.get(tile, {})
        owner = owners.get(tile)
        if tile in anchors:
            new_owner: EntityId | None = anchors[tile]
        elif owner is not None and holders.get(owner, 0) >= LOSE_THRESHOLD:
            new_owner = owner
            rival = _strongest({key: value for key, value in holders.items() if key != owner}, 0)
            lead = holders.get(rival, 0) - holders[owner] if rival is not None else 0
            if rival is not None and lead >= CHALLENGE_MARGIN:
                previous_challenge = challenges.get(tile)
                days = (
                    previous_challenge.days + 1
                    if previous_challenge is not None
                    and previous_challenge.civilization_id == rival
                    else 1
                )
                if days >= CHALLENGE_DAYS:
                    new_owner = rival
                else:
                    new_challenges.append(Challenge(tile=tile, civilization_id=rival, days=days))
        else:
            new_owner = _strongest(holders, TAKE_THRESHOLD)
        if new_owner is not None:
            new_owners[tile] = new_owner
        if new_owner != owner:
            if owner is not None:
                changes.append(ControlChange(tile, owner, gained=False, previous_owner=owner))
            if new_owner is not None:
                changes.append(ControlChange(tile, new_owner, gained=True, previous_owner=owner))

    return TerritoryDayResult(
        territory=Territory(
            held=tuple(
                HeldControl(tile=tile, civilization_id=civilization_id, value=value)
                for tile in sorted(held)
                for civilization_id, value in sorted(held[tile].items())
            ),
            owners=tuple(
                TileOwner(
                    tile=tile,
                    civilization_id=civilization_id,
                    since_day=(
                        since[tile]
                        if owners.get(tile) == civilization_id and tile in since
                        else day
                    ),
                )
                for tile, civilization_id in sorted(new_owners.items())
            ),
            challenges=tuple(sorted(new_challenges, key=lambda item: item.tile)),
            cut_off=tuple(sorted(cut_off)),
        ),
        changes=tuple(changes),
        severed=tuple(sorted(set(cut_off) - set(territory.cut_off))),
        restored=tuple(sorted(set(territory.cut_off) - set(cut_off))),
    )


def visible_tiles(world_map: WorldMap, centers: Iterable[HexCoord]) -> frozenset[HexCoord]:
    """Tiles within sight of a settlement."""
    seen: set[HexCoord] = set()
    for center in centers:
        ring = {center}
        for _ in range(SETTLEMENT_SIGHT):
            ring |= {neighbor for tile in ring for neighbor in world_map.neighbors(tile)}
        seen |= ring
    return frozenset(seen)
