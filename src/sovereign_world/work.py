"""Daily work allocation and construction projects."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from sovereign_world.hexmap import HexCoord
from sovereign_world.ids import EntityId
from sovereign_world.people import Person
from sovereign_world.resources import InsufficientResource, Inventory, Recipe, Resource


class WorkKind(StrEnum):
    GATHER = "gather"
    CRAFT = "craft"
    HAUL = "haul"
    FARM = "farm"
    CARE = "care"
    CONSTRUCT = "construct"


class ProjectStatus(StrEnum):
    PLANNED = "planned"
    ACTIVE = "active"
    COMPLETE = "complete"
    CANCELLED = "cancelled"


class WorkOrder(BaseModel):
    model_config = ConfigDict(frozen=True)

    order_id: EntityId
    kind: WorkKind
    worker_ids: tuple[EntityId, ...]
    recipe: Recipe | None = None
    project_id: EntityId | None = None


class ConstructionProject(BaseModel):
    project_id: EntityId
    location: HexCoord
    required_materials: dict[Resource, int] = Field(default_factory=dict)
    delivered_materials: dict[Resource, int] = Field(default_factory=dict)
    required_labor_minutes: int = Field(ge=0)
    completed_labor_minutes: int = Field(default=0, ge=0)
    status: ProjectStatus = ProjectStatus.ACTIVE
    adds_capacity: int = Field(default=0, ge=0)
    """Storage a finished storehouse adds to the store of the settlement it stands in."""

    def materials_ready(self) -> bool:
        return all(
            self.delivered_materials.get(resource, 0) >= quantity
            for resource, quantity in self.required_materials.items()
        )


class WorkDayResult(BaseModel):
    inventory: Inventory
    projects: dict[EntityId, ConstructionProject]
    completed_order_ids: tuple[EntityId, ...]
    blocked_order_ids: tuple[EntityId, ...]
    completed_project_ids: tuple[EntityId, ...]


def _living_workers(order: WorkOrder, people: dict[EntityId, Person]) -> tuple[Person, ...]:
    return tuple(
        people[worker_id]
        for worker_id in sorted(order.worker_ids)
        if worker_id in people and people[worker_id].alive
    )


def execute_work_day(
    work_orders: tuple[WorkOrder, ...],
    people: dict[EntityId, Person],
    inventory: Inventory,
    projects: dict[EntityId, ConstructionProject],
) -> WorkDayResult:
    current_inventory = inventory
    current_projects = {
        project_id: project.model_copy(deep=True) for project_id, project in projects.items()
    }
    completed_orders: list[EntityId] = []
    blocked_orders: list[EntityId] = []
    completed_projects: list[EntityId] = []
    reserved_tools: dict[Resource, int] = {}

    def order_key(order: WorkOrder) -> tuple[str, str]:
        first_worker = min(order.worker_ids, default=EntityId("~"))
        return str(first_worker), str(order.order_id)

    for order in sorted(work_orders, key=order_key):
        workers = _living_workers(order, people)
        if not workers:
            blocked_orders.append(order.order_id)
            continue
        if order.kind is WorkKind.CRAFT and order.recipe is not None:
            tools_available = all(
                current_inventory.quantities.get(resource, 0) - reserved_tools.get(resource, 0)
                >= quantity
                for resource, quantity in order.recipe.tools.items()
            )
            if not tools_available:
                blocked_orders.append(order.order_id)
                continue
            try:
                next_inventory = current_inventory.apply(order.recipe)
            except (InsufficientResource, ValueError):
                blocked_orders.append(order.order_id)
                continue
            for resource, quantity in order.recipe.tools.items():
                reserved_tools[resource] = reserved_tools.get(resource, 0) + quantity
            current_inventory = next_inventory
            completed_orders.append(order.order_id)
            continue
        if order.kind is WorkKind.CONSTRUCT and order.project_id in current_projects:
            project = current_projects[order.project_id]
            if project.status is ProjectStatus.COMPLETE or not project.materials_ready():
                blocked_orders.append(order.order_id)
                continue
            project.completed_labor_minutes += 480 * len(workers)
            if project.completed_labor_minutes >= project.required_labor_minutes:
                project.completed_labor_minutes = project.required_labor_minutes
                project.status = ProjectStatus.COMPLETE
                completed_projects.append(project.project_id)
            completed_orders.append(order.order_id)
            continue
        blocked_orders.append(order.order_id)

    return WorkDayResult(
        inventory=current_inventory,
        projects=current_projects,
        completed_order_ids=tuple(completed_orders),
        blocked_order_ids=tuple(blocked_orders),
        completed_project_ids=tuple(completed_projects),
    )
