import pytest

from sovereign_world.resources import (
    CapacityExceeded,
    InsufficientResource,
    Inventory,
    InventoryDelta,
    Recipe,
    Resource,
)


def test_recipe_with_missing_input_is_atomic() -> None:
    inventory = Inventory(capacity=1_000, quantities={Resource.TIMBER: 4})
    before = inventory.model_copy(deep=True)
    recipe = Recipe(inputs={Resource.TIMBER: 5}, outputs={Resource.PLANK: 2})

    with pytest.raises(InsufficientResource):
        inventory.apply(recipe)

    assert inventory == before


def test_successful_recipe_returns_replacement_inventory() -> None:
    inventory = Inventory(capacity=1_000, quantities={Resource.TIMBER: 5})

    result = inventory.apply(Recipe(inputs={Resource.TIMBER: 5}, outputs={Resource.PLANK: 2}))

    assert result.quantities == {Resource.PLANK: 2}
    assert inventory.quantities == {Resource.TIMBER: 5}


def test_delta_cannot_exceed_storage_capacity() -> None:
    inventory = Inventory(capacity=5, quantities={Resource.FOOD: 5})

    with pytest.raises(CapacityExceeded):
        inventory.apply_delta(InventoryDelta(changes={Resource.WATER: 1}))


def test_overflow_can_be_collected_as_explicit_waste() -> None:
    inventory = Inventory(capacity=5, quantities={Resource.FOOD: 4})

    result, waste = inventory.store_with_waste({Resource.WATER: 3})

    assert result.total_units == 5
    assert waste == {Resource.WATER: 2}
