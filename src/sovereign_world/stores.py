"""Every settlement keeps its own store; the capital's is the civilization's `inventory`."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING

from sovereign_world.hexmap import HexCoord
from sovereign_world.ids import EntityId
from sovereign_world.resources import Inventory, InventoryDelta, Resource

if TYPE_CHECKING:
    from sovereign_world.state import CivilizationState
    from sovereign_world.territory import Settlement

BASE_CAPACITY = 2_000
"""What a settlement can keep with no storehouse: pits, baskets and corners."""
STOREHOUSE_CAPACITY = 5_000
"""What each storehouse adds to its settlement's store."""
FOUNDING_STOREHOUSES = 5
"""Storehouses every capital starts with."""
STOREHOUSE_MATERIALS = {Resource.STONE: 30}
STOREHOUSE_PERSON_DAYS = 10


def founding_capacity(goods: int) -> int:
    """The capital's first store: its founding storehouses, or as many as its goods need."""
    needed = max(0, -(-(goods - BASE_CAPACITY) // STOREHOUSE_CAPACITY))
    return BASE_CAPACITY + STOREHOUSE_CAPACITY * max(FOUNDING_STOREHOUSES, needed)


def storehouses(inventory: Inventory) -> int:
    return (inventory.capacity - BASE_CAPACITY) // STOREHOUSE_CAPACITY


def settlement_at(civilization: CivilizationState, tile: HexCoord) -> Settlement | None:
    return next((item for item in civilization.settlements if item.tile == tile), None)


def supplying(civilization: CivilizationState, tile: HexCoord) -> Settlement | None:
    """The settlement that feeds and stocks people on a tile: its own, else the nearest.

    Ties go to the settlement with the lowest id, which is always the capital first.
    """
    if not civilization.settlements:
        return None
    return min(
        civilization.settlements,
        key=lambda item: (item.tile.distance(tile), item.settlement_id),
    )


def _is_capital(civilization: CivilizationState, settlement_id: EntityId | None) -> bool:
    return settlement_id is None or any(
        item.capital and item.settlement_id == settlement_id for item in civilization.settlements
    )


def store(civilization: CivilizationState, settlement_id: EntityId | None) -> Inventory:
    if _is_capital(civilization, settlement_id):
        return civilization.inventory
    assert settlement_id is not None
    return civilization.stores.get(settlement_id) or Inventory(capacity=BASE_CAPACITY)


def set_store(
    civilization: CivilizationState, settlement_id: EntityId | None, inventory: Inventory
) -> None:
    if _is_capital(civilization, settlement_id):
        civilization.inventory = inventory
        return
    assert settlement_id is not None
    civilization.stores = {**civilization.stores, settlement_id: inventory}


def store_id_at(civilization: CivilizationState, tile: HexCoord) -> EntityId | None:
    settlement = supplying(civilization, tile)
    return None if settlement is None else settlement.settlement_id


def store_at(civilization: CivilizationState, tile: HexCoord) -> Inventory:
    return store(civilization, store_id_at(civilization, tile))


def take(civilization: CivilizationState, tile: HexCoord, goods: Mapping[Resource, int]) -> None:
    """Remove goods from the store supplying a tile; the caller checked they are there."""
    settlement_id = store_id_at(civilization, tile)
    changes = {resource: -quantity for resource, quantity in goods.items() if quantity}
    if changes:
        set_store(
            civilization,
            settlement_id,
            store(civilization, settlement_id).apply_delta(InventoryDelta(changes=changes)),
        )


def put(
    civilization: CivilizationState, tile: HexCoord, goods: Mapping[Resource, int]
) -> dict[Resource, int]:
    """Store goods at the settlement supplying a tile; return what did not fit."""
    settlement_id = store_id_at(civilization, tile)
    inventory, waste = store(civilization, settlement_id).store_with_waste(dict(goods))
    set_store(civilization, settlement_id, inventory)
    return waste


def enlarge(civilization: CivilizationState, tile: HexCoord, units: int) -> int:
    """Raise the capacity of the store supplying a tile; return its new capacity."""
    settlement_id = store_id_at(civilization, tile)
    inventory = store(civilization, settlement_id)
    enlarged = inventory.model_copy(update={"capacity": inventory.capacity + units})
    set_store(civilization, settlement_id, enlarged)
    return enlarged.capacity


def has(civilization: CivilizationState, tile: HexCoord, goods: Mapping[Resource, int]) -> bool:
    quantities = store_at(civilization, tile).quantities
    return all(quantities.get(resource, 0) >= quantity for resource, quantity in goods.items())


def all_stores(civilization: CivilizationState) -> dict[EntityId, Inventory]:
    """Every settlement's store, by settlement id."""
    return {
        item.settlement_id: store(civilization, item.settlement_id)
        for item in civilization.settlements
    }


def total(stores: Iterable[Inventory]) -> dict[Resource, int]:
    goods: dict[Resource, int] = {}
    for inventory in stores:
        for resource, quantity in inventory.quantities.items():
            if quantity:
                goods[resource] = goods.get(resource, 0) + quantity
    return dict(sorted(goods.items()))


def holdings(civilization: CivilizationState) -> dict[Resource, int]:
    """All the goods in all of a civilization's stores."""
    return total(all_stores(civilization).values())
