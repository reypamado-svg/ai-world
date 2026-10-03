"""Every settlement keeps its own store; the capital's is the civilization's `inventory`."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from sovereign_world.capabilities import CapabilityId
from sovereign_world.hexmap import HexCoord
from sovereign_world.ids import EntityId
from sovereign_world.resources import Inventory, InventoryDelta, Resource

if TYPE_CHECKING:
    from sovereign_world.people_store import PeopleTable, Person
    from sovereign_world.state import CivilizationState, WorldState
    from sovereign_world.territory import Settlement

BASE_CAPACITY = 2_000
"""What a settlement can keep with no storehouse: baskets, jars and corners."""


class StorehouseGrade(StrEnum):
    PIT = "storage_pit"
    GRANARY = "granary"
    STOREHOUSE = "storehouse"
    WAREHOUSE = "warehouse"
    DEPOT = "depot"


@dataclass(frozen=True, slots=True)
class GradeSpec:
    capacity: int
    """What a storehouse of this grade adds to its settlement's store."""
    materials: dict[Resource, int]
    """What the step up to this grade costs."""
    person_days: int
    capability: CapabilityId | None


GRADES: tuple[StorehouseGrade, ...] = tuple(StorehouseGrade)
STOREHOUSE_GRADES: dict[StorehouseGrade, GradeSpec] = {
    StorehouseGrade.PIT: GradeSpec(2_000, {}, 5, None),
    StorehouseGrade.GRANARY: GradeSpec(5_000, {Resource.STONE: 30}, 10, None),
    StorehouseGrade.STOREHOUSE: GradeSpec(
        10_000, {Resource.STONE: 40, Resource.TIMBER: 20}, 20, CapabilityId.TIMBERCRAFT
    ),
    StorehouseGrade.WAREHOUSE: GradeSpec(
        20_000, {Resource.STONE: 80, Resource.PLANK: 20}, 40, CapabilityId.STONEWORKING
    ),
    StorehouseGrade.DEPOT: GradeSpec(
        40_000, {Resource.STONE: 150, Resource.TOOL: 10}, 60, CapabilityId.STONEWORKING
    ),
}
FOUNDING_GRADE = StorehouseGrade.GRANARY
FOUNDING_STOREHOUSES = 5
"""Granaries every capital starts with."""


def rank(grade: StorehouseGrade | None) -> int:
    return 0 if grade is None else GRADES.index(grade) + 1


def grade_below(grade: StorehouseGrade) -> StorehouseGrade | None:
    """The grade a burned storehouse falls to; a burned storage pit is gone."""
    level = rank(grade)
    return GRADES[level - 2] if level > 1 else None


def steps(current: StorehouseGrade | None, target: StorehouseGrade) -> tuple[StorehouseGrade, ...]:
    """The grades built one after another to raise a storehouse from `current` to `target`."""
    return GRADES[rank(current) : rank(target)]


def step_materials(current: StorehouseGrade | None, target: StorehouseGrade) -> dict[Resource, int]:
    materials: dict[Resource, int] = {}
    for grade in steps(current, target):
        for resource, quantity in STOREHOUSE_GRADES[grade].materials.items():
            materials[resource] = materials.get(resource, 0) + quantity
    return dict(sorted(materials.items()))


def step_person_days(current: StorehouseGrade | None, target: StorehouseGrade) -> int:
    return sum(STOREHOUSE_GRADES[grade].person_days for grade in steps(current, target))


def founding_storehouses(goods: int) -> int:
    """Granaries a capital starts with: five, or as many as its founders' goods need."""
    room = STOREHOUSE_GRADES[FOUNDING_GRADE].capacity
    return max(FOUNDING_STOREHOUSES, -(-(goods - BASE_CAPACITY) // room))


def founding_capacity(goods: int) -> int:
    return BASE_CAPACITY + STOREHOUSE_GRADES[FOUNDING_GRADE].capacity * founding_storehouses(goods)


class Storehouse(BaseModel):
    """A storage building in a settlement; it is upgraded one grade at a time."""

    model_config = ConfigDict(frozen=True)

    storehouse_id: EntityId
    settlement_id: EntityId
    grade: StorehouseGrade
    built_day: int = Field(ge=0)


class StorehouseJob(BaseModel):
    """Builders raising a storehouse, step by step, to a target grade.

    The materials for every step were taken from the settlement's store when the job began.
    """

    model_config = ConfigDict(frozen=True)

    job_id: EntityId
    storehouse_id: EntityId
    settlement_id: EntityId
    tile: HexCoord
    worker_ids: tuple[EntityId, ...]
    start_grade: StorehouseGrade | None
    target: StorehouseGrade
    started_day: int = Field(ge=0)
    person_days_done: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def rises(self) -> StorehouseJob:
        if rank(self.target) <= rank(self.start_grade):
            raise ValueError("a storehouse job raises the building's grade")
        return self

    def built(self) -> StorehouseGrade | None:
        """The grade the work done so far has reached."""
        grade = self.start_grade
        spent = 0
        for step in steps(self.start_grade, self.target):
            spent += STOREHOUSE_GRADES[step].person_days
            if self.person_days_done < spent:
                break
            grade = step
        return grade


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


def rows_by_store(
    civilization: CivilizationState, table: PeopleTable, rows: np.ndarray
) -> dict[EntityId, np.ndarray]:
    """These rows grouped by the store supplying where each stands, each group keeping the
    rows' order, and the stores in the order their first person appears."""
    from sovereign_world.people_store import place_of

    if not len(rows):
        return {}
    codes, first, where = np.unique(table.loc_code[rows], return_index=True, return_inverse=True)
    stores: list[EntityId] = []
    for code in codes.tolist():
        store_id = store_id_at(civilization, place_of(code))
        assert store_id is not None
        stores.append(store_id)
    order = sorted(
        set(stores),
        key=lambda item: min(
            int(first[index]) for index, other in enumerate(stores) if other == item
        ),
    )
    return {
        store_id: rows[
            np.isin(where, [index for index, other in enumerate(stores) if other == store_id])
        ]
        for store_id in order
    }


def held_captives(
    state: WorldState, civilization_id: EntityId, settlements: set[EntityId]
) -> dict[EntityId, Person]:
    """Other civilizations' living people held prisoner at these settlements, by id: by
    civilization, then in each one's row order."""
    held: dict[EntityId, Person] = {}
    for other_id, other in sorted(state.civilizations.items()):
        if other_id == civilization_id:
            continue
        table = other.population.people.table
        for row in np.flatnonzero(table.mask(alive=True, captive=True)).tolist():
            person = table.person(row)
            if person.captive_of == civilization_id and person.held_at in settlements:
                held[table.ids[row]] = person
    return held


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


def shrink(civilization: CivilizationState, tile: HexCoord, units: int) -> dict[Resource, int]:
    """Lower the capacity of the store supplying a tile; return the goods lost with it.

    What no longer fits is lost, food first, then the other goods in their fixed order.
    """
    settlement_id = store_id_at(civilization, tile)
    inventory = store(civilization, settlement_id)
    capacity = max(inventory.capacity - units, 0)
    excess = max(inventory.total_units - capacity, 0)
    quantities = dict(inventory.quantities)
    lost: dict[Resource, int] = {}
    for resource in (Resource.FOOD, *(item for item in Resource if item is not Resource.FOOD)):
        if not excess:
            break
        gone = min(quantities.get(resource, 0), excess)
        if gone:
            quantities[resource] -= gone
            lost[resource] = gone
            excess -= gone
    set_store(
        civilization,
        settlement_id,
        Inventory(
            capacity=capacity,
            quantities={resource: count for resource, count in quantities.items() if count},
        ),
    )
    return lost


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
