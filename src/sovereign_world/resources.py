"""Atomic fixed-unit inventories and production recipes."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Resource(StrEnum):
    FOOD = "food"
    WATER = "water"
    TIMBER = "timber"
    PLANK = "plank"
    STONE = "stone"
    ORE = "ore"
    METAL = "metal"
    AXE = "axe"
    TOOL = "tool"


class InsufficientResource(ValueError):
    pass


class CapacityExceeded(ValueError):
    pass


class InventoryDelta(BaseModel):
    model_config = ConfigDict(frozen=True)

    changes: dict[Resource, int] = Field(default_factory=dict)


class Recipe(BaseModel):
    model_config = ConfigDict(frozen=True)

    inputs: dict[Resource, int] = Field(default_factory=dict)
    outputs: dict[Resource, int] = Field(default_factory=dict)
    tools: dict[Resource, int] = Field(default_factory=dict)
    required_skills: dict[str, int] = Field(default_factory=dict)
    labor_minutes: int = Field(default=480, ge=0)
    tool_wear: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def quantities_are_positive(self) -> Recipe:
        for collection in (self.inputs, self.outputs, self.tools):
            if any(quantity <= 0 for quantity in collection.values()):
                raise ValueError("recipe quantities must be positive")
        return self


class Inventory(BaseModel):
    model_config = ConfigDict(frozen=True)

    capacity: int = Field(ge=0)
    quantities: dict[Resource, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_quantities(self) -> Inventory:
        if any(quantity < 0 for quantity in self.quantities.values()):
            raise ValueError("inventory quantities cannot be negative")
        if self.total_units > self.capacity:
            raise CapacityExceeded("inventory exceeds storage capacity")
        return self

    @property
    def total_units(self) -> int:
        return sum(self.quantities.values())

    def plan_transaction(self, recipe: Recipe) -> InventoryDelta:
        for resource, required in recipe.inputs.items():
            if self.quantities.get(resource, 0) < required:
                raise InsufficientResource(f"requires {required} {resource}")
        for resource, required in recipe.tools.items():
            if self.quantities.get(resource, 0) < required:
                raise InsufficientResource(f"requires tool {resource}")
        changes: dict[Resource, int] = {}
        for resource, quantity in recipe.inputs.items():
            changes[resource] = changes.get(resource, 0) - quantity
        for resource, quantity in recipe.outputs.items():
            changes[resource] = changes.get(resource, 0) + quantity
        return InventoryDelta(changes=changes)

    def apply(self, recipe: Recipe) -> Inventory:
        return self.apply_delta(self.plan_transaction(recipe))

    def apply_delta(self, delta: InventoryDelta) -> Inventory:
        quantities = dict(self.quantities)
        for resource, change in delta.changes.items():
            new_quantity = quantities.get(resource, 0) + change
            if new_quantity < 0:
                raise InsufficientResource(f"delta would make {resource} negative")
            if new_quantity:
                quantities[resource] = new_quantity
            else:
                quantities.pop(resource, None)
        if sum(quantities.values()) > self.capacity:
            raise CapacityExceeded("inventory exceeds storage capacity")
        return Inventory(capacity=self.capacity, quantities=quantities)

    def store_with_waste(
        self,
        additions: dict[Resource, int],
    ) -> tuple[Inventory, dict[Resource, int]]:
        quantities = dict(self.quantities)
        remaining = self.capacity - self.total_units
        waste: dict[Resource, int] = {}
        for resource in sorted(additions, key=str):
            requested = additions[resource]
            accepted = min(requested, remaining)
            if accepted:
                quantities[resource] = quantities.get(resource, 0) + accepted
                remaining -= accepted
            if requested > accepted:
                waste[resource] = requested - accepted
        return Inventory(capacity=self.capacity, quantities=quantities), waste
