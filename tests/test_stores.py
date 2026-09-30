from logistics_helpers import (
    OneShotSovereign,
    clear_journey_id,
    envelope,
    linked_world,
    treaty_world,
)

from sovereign_world.capabilities import CapabilityId, CapabilityRecord
from sovereign_world.commands import (
    DirectOrder,
    DirectOrderKind,
    ProjectKind,
    build_council_report,
    validate_envelope,
)
from sovereign_world.engine import TransitionResult, advance_day
from sovereign_world.ids import EntityId
from sovereign_world.logistics import JourneyOutcome, NoticeKind
from sovereign_world.resources import Inventory, Resource
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState, validate_world
from sovereign_world.stores import (
    BASE_CAPACITY,
    STOREHOUSE_GRADES,
    StorehouseGrade,
    founding_capacity,
    holdings,
    store,
)
from sovereign_world.territory import Settlement
from sovereign_world.war import WarObjective

GRANARY = STOREHOUSE_GRADES[StorehouseGrade.GRANARY].capacity


def _run(
    state: WorldState, days: int, sovereigns=None
) -> tuple[WorldState, list[TransitionResult]]:
    rng = StableRng(state.config.seed)
    results: list[TransitionResult] = []
    for _ in range(days):
        result = advance_day(state, rng, sovereigns=sovereigns)
        state = result.state
        results.append(result)
    return state, results


def _events(results: list[TransitionResult], kind: str):
    return [event for result in results for event in result.events.events if event.kind == kind]


def _colony(state: WorldState, civilization_id: EntityId, tile, *, people: int, food: int):
    """Found a second settlement on a tile, move some people there and stock its store."""
    civilization = state.civilizations[civilization_id]
    colony = Settlement(
        settlement_id=EntityId(f"settlement:{civilization_id.rsplit(':', 1)[-1]}-0002"),
        civilization_id=civilization_id,
        tile=tile,
        founded_day=0,
    )
    civilization.settlements = (*civilization.settlements, colony)
    movers = civilization.population.living_ids[-people:] if people else ()
    for person_id in movers:
        civilization.population.people[person_id].location = tile
    civilization.stores = {
        colony.settlement_id: Inventory(capacity=100_000, quantities={Resource.FOOD: food})
    }
    return colony


def _codes(state: WorldState, civilization_id: EntityId, *orders: DirectOrder) -> list[str]:
    errors = validate_envelope(envelope(state, civilization_id, *orders), state).errors
    return [error.code for error in errors]


def _haul(state, home, route, cargo, *, people=slice(0, 2), journey="journey:haul"):
    return DirectOrder(
        command_id=f"order:{journey}",
        kind=DirectOrderKind.HAUL_GOODS,
        journey_id=EntityId(journey),
        traveller_ids=state.civilizations[home].population.living_ids[people],
        route=route,
        cargo=cargo,
    )


def test_one_settlement_keeps_everything_in_the_capital_store() -> None:
    state, home, _, _ = treaty_world()
    civilization = state.civilizations[home]
    [capital] = civilization.settlements
    report = build_council_report(state, home)
    assert report.stores == {capital.settlement_id: report.inventory}
    assert report.holdings == {key: value for key, value in report.inventory.items() if value}
    assert store(civilization, capital.settlement_id) is civilization.inventory


def test_each_settlement_feeds_its_own_residents_from_its_own_store() -> None:
    _, state, home, _, route = linked_world(distance=8)
    colony = _colony(state, home, route[4], people=4, food=6)
    capital_food = state.civilizations[home].inventory.quantities[Resource.FOOD]

    state, results = _run(state, 3)

    civilization = state.civilizations[home]
    assert civilization.stores[colony.settlement_id].quantities.get(Resource.FOOD, 0) == 0
    short = _events(results, "food_shortage")[0]
    assert short.subject_id == str(colony.settlement_id)
    assert short.payload["people"] == 2, "6 food feeds 4 people for a day and a half"
    residents = len(civilization.population.living_ids) - 4
    eaten = capital_food - civilization.inventory.quantities[Resource.FOOD]
    assert eaten == 3 * residents, "the capital's store fed only the capital"
    hungry = [
        civilization.population.people[person_id]
        for person_id in civilization.population.living_ids
        if civilization.population.people[person_id].location == route[4]
    ]
    assert all(person.nutrition_debt > 0 for person in hungry)
    validate_world(state)


def test_settlers_stock_their_new_settlement_with_what_they_did_not_eat() -> None:
    _, state, home, _, route = linked_world(distance=6)
    found = DirectOrder(
        command_id="found",
        kind=DirectOrderKind.FOUND_SETTLEMENT,
        journey_id=EntityId(clear_journey_id("found")),
        traveller_ids=state.civilizations[home].population.living_ids[-4:],
        route=route[:4],
        extra_provisions=40,
    )
    state, _ = _run(state, 4, {home: OneShotSovereign(found)})

    colony = next(item for item in state.civilizations[home].settlements if not item.capital)
    [arrived] = [
        item
        for item in state.civilizations[home].logistics_notices
        if item.kind is NoticeKind.PARTY_ARRIVED
    ]
    stocked = arrived.cargo[Resource.FOOD]
    assert stocked >= 40
    eaten_since = 4 * (4 - arrived.day)
    assert (
        store(state.civilizations[home], colony.settlement_id).quantities[Resource.FOOD]
        == stocked - eaten_since
    ), "the settlers eat from their own new store once they arrive"


def test_goods_are_hauled_from_one_settlement_store_to_another() -> None:
    _, state, home, _, route = linked_world(distance=8)
    colony = _colony(state, home, route[4], people=2, food=0)
    haul = _haul(
        state,
        home,
        route[:5],
        {Resource.FOOD: 40, Resource.TIMBER: 20},
        journey=clear_journey_id("haul", days=12),
    )
    before = holdings(state.civilizations[home])
    timber = state.civilizations[home].inventory.quantities[Resource.TIMBER]

    state, results = _run(state, 12, {home: OneShotSovereign(haul)})

    civilization = state.civilizations[home]
    [dispatched] = _events(results, "haul_dispatched")
    [delivered] = _events(results, "goods_hauled")
    [returned] = _events(results, "haulers_returned")
    assert dispatched.day < delivered.day < returned.day
    assert civilization.inventory.quantities[Resource.TIMBER] == timber - 20
    colony_store = civilization.stores[colony.settlement_id].quantities
    assert colony_store[Resource.TIMBER] == 20
    assert colony_store.get(Resource.FOOD, 0) < 40, "the colony has eaten from its delivery"
    assert holdings(civilization)[Resource.TIMBER] == before[Resource.TIMBER]
    [journey] = [item for item in state.journeys if item.kind.value == "haul"]
    assert journey.outcome is JourneyOutcome.DELIVERED
    assert any(item.kind is NoticeKind.GOODS_HAULED for item in civilization.logistics_notices)
    validate_world(state)


def test_a_haul_goes_between_own_settlements_with_goods_the_store_holds() -> None:
    _, state, home, _, route = linked_world(distance=8)
    _colony(state, home, route[4], people=2, food=30)
    colony_people = slice(-2, None)
    back = tuple(reversed(route[:5]))

    assert _codes(state, home, _haul(state, home, route[:5], {})) == ["invalid_cargo"]
    assert _codes(state, home, _haul(state, home, route[:4], {Resource.TIMBER: 5})) == [
        "invalid_destination"
    ]
    assert _codes(state, home, _haul(state, home, route[:5], {Resource.TIMBER: 99})) == [
        "cargo_over_capacity"
    ]
    assert _codes(
        state, home, _haul(state, home, back, {Resource.TIMBER: 5}, people=colony_people)
    ) == ["insufficient_goods"], "the colony's store holds no timber"
    assert (
        _codes(state, home, _haul(state, home, back, {Resource.FOOD: 10}, people=colony_people))
        == []
    ), "the colony hauls from its own store"


def test_orders_in_one_council_share_each_store_separately() -> None:
    _, state, home, _, route = linked_world(distance=8)
    _colony(state, home, route[4], people=4, food=40)
    back = tuple(reversed(route[:5]))
    # Each haul packs 20 food for the walk there and back.
    first = _haul(state, home, back, {Resource.FOOD: 10}, people=slice(-2, None))
    second = _haul(state, home, back, {Resource.FOOD: 10}, people=slice(-4, -2), journey="j:2")
    assert _codes(state, home, first) == []
    assert _codes(state, home, first, second) == ["insufficient_provisions"], (
        "the colony's 40 food cannot fill two hauls and their provisions"
    )
    from_capital = _haul(state, home, route[:5], {Resource.FOOD: 10}, journey="j:3")
    assert _codes(state, home, first, from_capital) == [], "the capital's store is separate"


def test_raiders_empty_only_the_store_of_the_settlement_they_beat() -> None:
    state, home, rival, route = treaty_world(distance=8)
    target = _colony(state, rival, route[4], people=0, food=30)
    rival_capital_food = state.civilizations[rival].inventory.quantities[Resource.FOOD]
    raid = DirectOrder(
        command_id="raid",
        kind=DirectOrderKind.SEND_WAR_PARTY,
        journey_id=EntityId(clear_journey_id("raid", days=12)),
        recipient_civilization_id=rival,
        traveller_ids=state.civilizations[home].population.living_ids[:6],
        route=route[:5],
        war_objective=WarObjective.RAID,
    )
    state, results = _run(state, 6, {home: OneShotSovereign(raid)})

    [raided] = _events(results, "settlement_raided")
    assert raided.payload["units"] == 30
    rival_state = state.civilizations[rival]
    assert rival_state.stores[target.settlement_id].quantities.get(Resource.FOOD, 0) == 0
    capital_residents = len(rival_state.population.living_ids)
    assert (
        rival_state.inventory.quantities[Resource.FOOD]
        == rival_capital_food - 6 * capital_residents
    ), "the capital's store only fed its own people"


def test_armourers_use_and_fill_their_own_settlement_store() -> None:
    _, state, home, _, route = linked_world(distance=8)
    colony = _colony(state, home, route[4], people=2, food=30)
    workers = state.civilizations[home].population.living_ids[-2:]
    craft = DirectOrder(
        command_id="slings",
        kind=DirectOrderKind.CRAFT_EQUIPMENT,
        worker_ids=workers,
        craft_item=Resource.SLING,
        craft_quantity=2,
    )
    assert _codes(state, home, craft) == ["insufficient_materials"], "no timber at the colony"
    civilization = state.civilizations[home]
    civilization.stores = {
        colony.settlement_id: Inventory(
            capacity=100_000, quantities={Resource.FOOD: 30, Resource.TIMBER: 2}
        )
    }
    timber = civilization.inventory.quantities[Resource.TIMBER]
    state, results = _run(state, 2, {home: OneShotSovereign(craft)})

    assert _events(results, "equipment_crafted")
    civilization = state.civilizations[home]
    colony_store = civilization.stores[colony.settlement_id].quantities
    assert colony_store.get(Resource.TIMBER, 0) == 0
    assert colony_store[Resource.SLING] == 2
    assert civilization.inventory.quantities[Resource.TIMBER] == timber
    assert Resource.SLING not in civilization.inventory.quantities


def test_a_capital_starts_with_five_storehouses_and_a_new_settlement_with_none() -> None:
    _, state, home, _, route = linked_world(distance=8)
    civilization = state.civilizations[home]
    assert civilization.inventory.capacity == BASE_CAPACITY + 5 * GRANARY == 27_000
    assert civilization.inventory.total_units <= civilization.inventory.capacity
    assert founding_capacity(40_000) == BASE_CAPACITY + 8 * GRANARY, (
        "a large founding gets the storehouses its goods need"
    )
    colony = _colony(state, home, route[4], people=0, food=0)
    civilization.stores = {}
    report = build_council_report(state, home)
    assert report.store_capacity == {
        civilization.settlements[0].settlement_id: 27_000,
        colony.settlement_id: BASE_CAPACITY,
    }


def test_a_storehouse_enlarges_the_store_of_the_settlement_that_builds_it() -> None:
    _, state, home, _, route = linked_world(distance=8)
    colony = _colony(state, home, route[4], people=2, food=100)
    civilization = state.civilizations[home]
    civilization.stores = {
        colony.settlement_id: Inventory(
            capacity=BASE_CAPACITY, quantities={Resource.FOOD: 100, Resource.STONE: 30}
        )
    }
    stone = civilization.inventory.quantities[Resource.STONE]
    build = DirectOrder(
        command_id="build",
        kind=DirectOrderKind.START_PROJECT,
        worker_ids=civilization.population.living_ids[-2:],
        project_id=EntityId("project:colony-store"),
        project_kind=ProjectKind.STORAGE,
    )
    state, results = _run(state, 6, {home: OneShotSovereign(build)})

    [built] = _events(results, "storehouse_built")
    assert built.payload["settlement"] == str(colony.settlement_id)
    assert built.day == 4, "two builders put in the 10 person-days over five days"
    civilization = state.civilizations[home]
    colony_store = civilization.stores[colony.settlement_id]
    assert colony_store.capacity == BASE_CAPACITY + GRANARY
    assert colony_store.quantities.get(Resource.STONE, 0) == 0
    assert civilization.inventory.capacity == 27_000
    assert civilization.inventory.quantities[Resource.STONE] == stone
    validate_world(state)


def test_goods_beyond_a_small_store_are_wasted() -> None:
    _, state, home, _, route = linked_world(distance=8)
    colony = _colony(state, home, route[4], people=2, food=0)
    civilization = state.civilizations[home]
    civilization.stores = {
        colony.settlement_id: Inventory(
            capacity=BASE_CAPACITY, quantities={Resource.FOOD: BASE_CAPACITY - 10}
        )
    }
    haul = _haul(
        state, home, route[:5], {Resource.TIMBER: 30}, journey=clear_journey_id("haul", days=12)
    )
    state, results = _run(state, 8, {home: OneShotSovereign(haul)})

    [delivered] = _events(results, "goods_hauled")
    eaten = 2 * delivered.day
    assert delivered.payload["units"] == 10 + eaten
    assert delivered.payload["wasted"] == 30 - 10 - eaten


def _grant(state: WorldState, civilization_id: EntityId, person_id, capability) -> None:
    civilization = state.civilizations[civilization_id]
    person = civilization.population.people[person_id]
    person.skills = {**person.skills, capability.value: 100}
    others = [item for item in civilization.capabilities if item.capability is not capability]
    civilization.capabilities = tuple(
        sorted(
            (
                *others,
                CapabilityRecord(
                    capability=capability, practitioner_ids=(person_id,), discovered_day=0
                ),
            ),
            key=lambda item: item.capability.value,
        )
    )


def _build(state, home, builders, grade, *, storehouse_id=None, command_id="build"):
    return DirectOrder(
        command_id=command_id,
        kind=DirectOrderKind.BUILD_STOREHOUSE,
        worker_ids=builders,
        storehouse_grade=grade,
        storehouse_id=storehouse_id,
    )


def test_the_capital_starts_with_five_granaries() -> None:
    state, home, _, _ = treaty_world()
    civilization = state.civilizations[home]
    assert [item.grade for item in civilization.storehouses] == [StorehouseGrade.GRANARY] * 5
    assert {item.settlement_id for item in civilization.storehouses} == {
        civilization.settlements[0].settlement_id
    }


def test_a_new_storehouse_rises_grade_by_grade_and_adds_room_at_each() -> None:
    state, home, _, _ = treaty_world()
    civilization = state.civilizations[home]
    builders = civilization.population.living_ids[:5]
    _grant(state, home, builders[0], CapabilityId.TIMBERCRAFT)
    stone = civilization.inventory.quantities[Resource.STONE]
    timber = civilization.inventory.quantities[Resource.TIMBER]
    order = _build(state, home, builders, StorehouseGrade.STOREHOUSE)
    assert _codes(state, home, order) == []

    state, results = _run(state, 8, {home: OneShotSovereign(order)})

    civilization = state.civilizations[home]
    assert civilization.inventory.quantities[Resource.STONE] == stone - 70
    assert civilization.inventory.quantities[Resource.TIMBER] == timber - 20
    [built] = _events(results, "storehouse_built")
    upgraded = _events(results, "storehouse_upgraded")
    assert built.payload["grade"] == "storage_pit" and built.day == 0, "5 builders: 5 days of work"
    assert [event.payload["grade"] for event in upgraded] == ["granary", "storehouse"]
    assert [event.day for event in upgraded] == [2, 6], "10 more, then 20 more person-days"
    assert civilization.inventory.capacity == 27_000 + 10_000
    [house] = [item for item in civilization.storehouses if item.storehouse_id == built.subject_id]
    assert house.grade is StorehouseGrade.STOREHOUSE
    assert civilization.storehouse_jobs == ()
    validate_world(state)


def test_upgrades_need_the_craft_the_materials_and_a_higher_grade() -> None:
    state, home, _, _ = treaty_world()
    civilization = state.civilizations[home]
    builders = civilization.population.living_ids[:4]
    granary = civilization.storehouses[0].storehouse_id
    warehouse = _build(state, home, builders, StorehouseGrade.WAREHOUSE, storehouse_id=granary)
    _grant(state, home, builders[0], CapabilityId.TIMBERCRAFT)
    assert _codes(state, home, warehouse) == ["unqualified_worker"], "a warehouse is stonework"
    _grant(state, home, builders[1], CapabilityId.STONEWORKING)
    assert _codes(state, home, warehouse) == ["insufficient_materials"], "no planks in store"
    civilization.inventory = civilization.inventory.model_copy(
        update={"quantities": {**civilization.inventory.quantities, Resource.PLANK: 20}}
    )
    assert _codes(state, home, warehouse) == []
    assert _codes(
        state, home, _build(state, home, builders, StorehouseGrade.PIT, storehouse_id=granary)
    ) == ["invalid_storehouse"], "a granary is already above a pit"
    twice = _build(
        state,
        home,
        civilization.population.living_ids[4:6],
        StorehouseGrade.STOREHOUSE,
        storehouse_id=granary,
        command_id="again",
    )
    assert _codes(state, home, warehouse, twice) == ["invalid_storehouse"]

    state, results = _run(state, 20, {home: OneShotSovereign(warehouse)})
    upgraded = _events(results, "storehouse_upgraded")
    assert [event.payload["grade"] for event in upgraded] == ["storehouse", "warehouse"]
    assert state.civilizations[home].inventory.capacity == 27_000 - 5_000 + 20_000


def test_builders_stay_home_and_unused_materials_return_if_they_all_die() -> None:
    state, home, _, _ = treaty_world()
    civilization = state.civilizations[home]
    builders = civilization.population.living_ids[:2]
    order = _build(state, home, builders, StorehouseGrade.GRANARY)
    stone = civilization.inventory.quantities[Resource.STONE]
    state, results = _run(state, 3, {home: OneShotSovereign(order)})
    assert _events(results, "storehouse_built"), "the pit is dug in 5 person-days"
    assert state.civilizations[home].inventory.quantities[Resource.STONE] == stone - 30
    for person_id in builders:
        person = state.civilizations[home].population.people[person_id]
        person.alive = False
        person.death_day = state.day

    state, results = _run(state, 1)

    [stopped] = _events(results, "storehouse_work_stopped")
    assert stopped.payload["grade"] == "storage_pit"
    civilization = state.civilizations[home]
    assert civilization.inventory.quantities[Resource.STONE] == stone, "the granary's stone is back"
    assert civilization.storehouse_jobs == ()
