from sovereign_world.hexmap import HexCoord
from sovereign_world.ids import EntityId
from sovereign_world.people import Person, Sex
from sovereign_world.resources import Inventory, Recipe, Resource
from sovereign_world.work import (
    ConstructionProject,
    ProjectStatus,
    WorkKind,
    WorkOrder,
    execute_work_day,
)


def _person(sequence: int, *, alive: bool = True) -> Person:
    return Person(
        person_id=EntityId(f"person:{sequence:010d}"),
        civilization_id=EntityId("civilization:0000000001"),
        sex=Sex.FEMALE if sequence % 2 else Sex.MALE,
        birth_day=-9_000,
        age_days=9_000,
        location=HexCoord(1, 1),
        skills={"craft": 500, "build": 500},
        alive=alive,
        death_day=None if alive else 4,
    )


def test_workers_competing_for_one_tool_resolve_by_person_id() -> None:
    people = {person.person_id: person for person in (_person(2), _person(1))}
    recipe = Recipe(
        inputs={Resource.TIMBER: 1},
        outputs={Resource.PLANK: 1},
        tools={Resource.AXE: 1},
        labor_minutes=480,
    )
    orders = (
        WorkOrder(
            order_id=EntityId("order:0000000002"),
            kind=WorkKind.CRAFT,
            worker_ids=(EntityId("person:0000000002"),),
            recipe=recipe,
        ),
        WorkOrder(
            order_id=EntityId("order:0000000001"),
            kind=WorkKind.CRAFT,
            worker_ids=(EntityId("person:0000000001"),),
            recipe=recipe,
        ),
    )
    inventory = Inventory(capacity=100, quantities={Resource.TIMBER: 2, Resource.AXE: 1})

    result = execute_work_day(orders, people, inventory, {})

    assert result.completed_order_ids == (EntityId("order:0000000001"),)
    assert result.blocked_order_ids == (EntityId("order:0000000002"),)
    assert result.inventory.quantities[Resource.AXE] == 1
    assert result.inventory.quantities[Resource.PLANK] == 1


def test_dead_worker_cannot_produce() -> None:
    worker = _person(1, alive=False)
    order = WorkOrder(
        order_id=EntityId("order:0000000001"),
        kind=WorkKind.CRAFT,
        worker_ids=(worker.person_id,),
        recipe=Recipe(inputs={Resource.TIMBER: 1}, outputs={Resource.PLANK: 1}),
    )
    inventory = Inventory(capacity=100, quantities={Resource.TIMBER: 1})

    result = execute_work_day((order,), {worker.person_id: worker}, inventory, {})

    assert result.completed_order_ids == ()
    assert result.blocked_order_ids == (order.order_id,)
    assert result.inventory == inventory


def test_project_completes_once_on_exact_labor_boundary() -> None:
    worker = _person(1)
    project = ConstructionProject(
        project_id=EntityId("project:0000000001"),
        location=HexCoord(1, 1),
        required_materials={Resource.PLANK: 2},
        delivered_materials={Resource.PLANK: 2},
        required_labor_minutes=480,
    )
    order = WorkOrder(
        order_id=EntityId("order:0000000001"),
        kind=WorkKind.CONSTRUCT,
        worker_ids=(worker.person_id,),
        project_id=project.project_id,
    )

    first = execute_work_day(
        (order,),
        {worker.person_id: worker},
        Inventory(capacity=100),
        {project.project_id: project},
    )
    second = execute_work_day(
        (order,),
        {worker.person_id: worker},
        first.inventory,
        first.projects,
    )

    assert first.projects[project.project_id].status is ProjectStatus.COMPLETE
    assert first.completed_project_ids == (project.project_id,)
    assert second.completed_project_ids == ()
