"""What a settlement's land yields, from its cover (rules version 2).

A settlement farms, gathers and forages on the tiles it supplies. Fields are the open
ground and half the scrub, as fertile as the soil; water close at hand and the wild food of
woods and wetland add to them; irrigation and fishing add more. Timber comes from the woods
and stone from the rock. Tiles without land cover (maps made before it) yield as before.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from sovereign_world.cover import ROCK, WOOD, forage_bp, graze_bp, watered
from sovereign_world.hexmap import HexCoord, WorldMap
from sovereign_world.ids import EntityId
from sovereign_world.stores import store_id_at

if TYPE_CHECKING:
    from sovereign_world.state import CivilizationState

FIELD_SCALE = 7_500
"""A grassland tile's fields yield what they did before land cover: soil // 200 a day."""
FORAGE_SCALE = 5_000
"""Wild food: a tile all wood or wetland gives two days' food a day."""
WATER_FOOD = 2
"""Food a day from a watered tile (a lake, a river, or a tenth wetland)."""
FISHING_FOOD = 1
"""Food a day from each tile of open water or river, once fishing is known."""
IRRIGATED_NUM, IRRIGATED_DEN = 3, 2
"""Irrigated fields (watered, with irrigation known) yield half again."""
TIMBER_SCALE = 10_000
STONE_SCALE = 20_000


def food_capacity(
    world_map: WorldMap, tiles: Iterable[HexCoord], *, irrigation: bool, fishing: bool
) -> int:
    """The most food a day the people on these fields can bring in."""
    fields = forage = legacy = flat = 0
    for coord in tiles:
        tile = world_map.tile(coord)
        if tile.has_water and fishing:
            flat += FISHING_FOOD
        if not tile.cover:
            legacy += tile.soil // 200 + (2 if tile.has_water else 0)
            continue
        factor = IRRIGATED_NUM if irrigation and watered(tile) else IRRIGATED_DEN
        fields += (tile.soil // 200) * graze_bp(tile.cover) * factor
        forage += forage_bp(tile.cover)
        if watered(tile):
            flat += WATER_FOOD
    return fields // (FIELD_SCALE * IRRIGATED_DEN) + forage // FORAGE_SCALE + flat + legacy


def irrigated_fields(world_map: WorldMap, tiles: Iterable[HexCoord]) -> int:
    return sum(
        bool(world_map.tile(coord).cover) and watered(world_map.tile(coord)) for coord in tiles
    )


def timber_capacity(world_map: WorldMap, tiles: Iterable[HexCoord]) -> int:
    """The most timber a day the woods on these tiles give; they grow back."""
    total = 0
    for coord in tiles:
        tile = world_map.tile(coord)
        if tile.cover:
            total += tile.cover[WOOD] * (tile.timber // 100)
    return total // TIMBER_SCALE


def stone_capacity(world_map: WorldMap, tiles: Iterable[HexCoord]) -> int:
    """The most stone a day loose rock on these tiles gives; a quarry gives far more."""
    total = 0
    for coord in tiles:
        tile = world_map.tile(coord)
        if tile.cover:
            total += tile.cover[ROCK] * (tile.stone // 100)
    return total // STONE_SCALE


def water_near(world_map: WorldMap, coord: HexCoord) -> bool:
    """Water on the tile, or open water or a river on a tile beside it."""
    if watered(world_map.tile(coord)):
        return True
    return any(
        world_map.contains(other) and world_map.tile(other).has_water for other in coord.neighbors()
    )


def fields_by_settlement(civilization: CivilizationState) -> dict[EntityId, list[HexCoord]]:
    """The known tiles each settlement supplies, as the engine farms them."""
    fields: dict[EntityId, list[HexCoord]] = {}
    if not civilization.settlements:
        return fields
    for coord in civilization.known_tiles:
        store_id = store_id_at(civilization, coord)
        assert store_id is not None
        fields.setdefault(store_id, []).append(coord)
    return fields
