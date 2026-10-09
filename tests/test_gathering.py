"""Gathering timber and stone at home (rules version 2), and building the house the store can
pay for."""

from logistics_helpers import OneShotSovereign, envelope

from sovereign_world.capabilities import CapabilityId, CapabilityRecord
from sovereign_world.commands import (
    Decree,
    DecreeKind,
    DirectOrder,
    DirectOrderKind,
    ProjectKind,
    validate_envelope,
)
from sovereign_world.config import RunManifest, WorldConfig
from sovereign_world.engine import TransitionResult, _gather, advance_day
from sovereign_world.housing import HouseGrade, affordable_grade
from sovereign_world.ids import EntityId
from sovereign_world.land import fields_by_settlement, stone_capacity, timber_capacity
from sovereign_world.resources import Inventory, Resource
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState, build_initial_state, validate_world

CONFIG = WorldConfig(seed=9, width=24, height=24)


def _state(rules_version: int = 2) -> WorldState:
    return build_initial_state(RunManifest.new(CONFIG, "0.1.0", rules_version=rules_version))


def _run(
    state: WorldState, days: int, sovereigns=None
) -> tuple[WorldState, list[TransitionResult]]:
    rng = StableRng(state.config.seed)
    results = []
    for _ in range(days):
        result = advance_day(state, rng, sovereigns=sovereigns)
        state = result.state
        results.append(result)
    return state, results


def _events(results, kind: str):
    return [event for result in results for event in result.events.events if event.kind == kind]


def _decrees(state: WorldState, home, *, materials: int = 300, labour: int = 70) -> None:
    far = 10_000
    state.active_decrees = {
        home: {
            "labor_priority": labour,
            "labor_priority_expires": far,
            "materials_reserve_target": materials,
            "materials_reserve_target_expires": far,
        }
    }


def _stock(state: WorldState, home, **goods: int) -> None:
    civilization = state.civilizations[home]
    quantities = {**civilization.inventory.quantities}
    for name, quantity in goods.items():
        quantities[Resource(name)] = quantity
    civilization.inventory = civilization.inventory.model_copy(update={"quantities": quantities})


def test_spare_hands_gather_up_to_the_target_as_fast_as_the_land_allows() -> None:
    state = _state()
    home = sorted(state.civilizations)[0]
    _decrees(state, home)
    _stock(state, home, timber=250, stone=100)
    civilization = state.civilizations[home]
    [capital] = civilization.settlements
    fields = fields_by_settlement(civilization)[capital.settlement_id]
    per_day = timber_capacity(state.world_map, fields)
    assert per_day > 0

    state, results = _run(state, 1)
    [timber] = _events(results, "timber_gathered")
    assert timber.payload["units"] == min(per_day, 50, 32), "32 founders, all free hands"
    state, results = _run(state, 30)
    assert state.civilizations[home].inventory.quantities[Resource.TIMBER] <= 300
    assert state.civilizations[home].inventory.quantities[Resource.TIMBER] >= 300 - 40, (
        "refilled to the target, less what the housing builders used"
    )
    validate_world(state)


def test_gathering_needs_rules_two_a_target_and_labour() -> None:
    for state, setup in (
        (_state(1), dict()),
        (_state(2), dict(materials=0)),
        (_state(2), dict(labour=0)),
    ):
        home = sorted(state.civilizations)[0]
        _decrees(state, home, **setup)
        _stock(state, home, timber=100)
        state, results = _run(state, 2)
        assert _events(results, "timber_gathered") == []
        assert _events(results, "stone_gathered") == []


def test_tools_double_a_gatherer_and_stone_comes_at_half_the_target() -> None:
    world = _state().world_map
    tiles = tuple(tile.coord for tile in world.tiles if tile.cover)
    larder = Inventory(capacity=100_000, quantities={Resource.TIMBER: 0, Resource.STONE: 0})
    big = timber_capacity(world, tiles)
    assert big > 50 and stone_capacity(world, tiles) > 10
    _, plain = _gather(world, tiles, larder, 10, 300)
    assert plain == {Resource.TIMBER: 10}
    tooled = larder.model_copy(update={"quantities": {Resource.TOOL: 4}})
    _, with_tools = _gather(world, tiles, tooled, 10, 300)
    assert with_tools == {Resource.TIMBER: 14}
    full = larder.model_copy(update={"quantities": {Resource.TIMBER: 300, Resource.STONE: 140}})
    _, stone = _gather(world, tiles, full, 30, 300)
    assert stone == {Resource.STONE: 10}, "stone tops up to half the timber target"
    _, nothing = _gather(world, tiles, larder, 0, 300)
    assert nothing == {}


def test_the_materials_decree_is_checked() -> None:
    def decree(value: int) -> Decree:
        return Decree(command_id=f"m{value}", kind=DecreeKind.MATERIALS_RESERVE_TARGET, value=value)

    new, old = _state(2), _state(1)
    home = sorted(new.civilizations)[0]

    def codes(state, order) -> list[str]:
        return [e.code for e in validate_envelope(envelope(state, home, order), state).errors]

    assert codes(new, decree(300)) == []
    assert codes(new, decree(5_001)) == ["invalid_decree"]
    assert codes(old, decree(300)) == ["invalid_decree"]


def _with(state: WorldState, home, *capabilities: CapabilityId) -> None:
    civilization = state.civilizations[home]
    keep = [
        item
        for item in civilization.capabilities
        if item.capability not in {CapabilityId.TIMBERCRAFT, CapabilityId.STONEWORKING}
    ]
    civilization.capabilities = tuple(
        sorted(
            (
                *keep,
                *(
                    CapabilityRecord(capability=item, practitioner_ids=(), discovered_day=0)
                    for item in capabilities
                ),
            ),
            key=lambda record: record.capability.value,
        )
    )


def test_a_shelter_order_may_name_a_house_it_knows() -> None:
    state = _state()
    home = sorted(state.civilizations)[0]
    _with(state, home, CapabilityId.TIMBERCRAFT)
    workers = state.civilizations[home].population.living_ids[:2]

    def order(grade: HouseGrade | None) -> DirectOrder:
        return DirectOrder(
            command_id="houses",
            kind=DirectOrderKind.START_PROJECT,
            project_id=EntityId("project:houses"),
            project_kind=ProjectKind.SHELTER,
            worker_ids=workers,
            house_count=2,
            house_grade=grade,
        )

    def codes(item: DirectOrder) -> list[str]:
        return [e.code for e in validate_envelope(envelope(state, home, item), state).errors]

    assert codes(order(HouseGrade.HUT)) == []
    assert codes(order(HouseGrade.STONE_HOUSE)) == ["invalid_project"]
    state, results = _run(state, 1, {home: OneShotSovereign(order(HouseGrade.HUT))})
    [started] = _events(results, "house_work_started")
    assert started.payload["grade"] == "hut"


def test_short_of_stone_the_housing_policy_raises_huts() -> None:
    goods = {Resource.TIMBER: 100, Resource.STONE: 5}
    state = _state()
    home = sorted(state.civilizations)[0]
    _with(state, home, CapabilityId.TIMBERCRAFT)
    capabilities = state.civilizations[home].capabilities
    assert affordable_grade(capabilities, goods) is HouseGrade.HUT
    assert affordable_grade(capabilities, {**goods, Resource.STONE: 10}) is HouseGrade.HOUSE
    assert affordable_grade(capabilities, {Resource.TIMBER: 5}) is None

    _stock(state, home, stone=5)
    decree = Decree(command_id="d", kind=DecreeKind.HOUSING_POLICY, value=10, duration_days=60)
    state, results = _run(state, 1, {home: OneShotSovereign(decree)})
    [started] = _events(results, "house_work_started")
    assert started.payload["grade"] == "hut"


def test_food_comes_first() -> None:
    state = _state()
    home = sorted(state.civilizations)[0]
    _decrees(state, home)
    state.active_decrees[home]["food_reserve_target"] = 10_000
    state.active_decrees[home]["food_reserve_target_expires"] = 10_000
    _stock(state, home, timber=0, food=0)
    civilization = state.civilizations[home]
    [capital] = civilization.settlements
    fields = fields_by_settlement(civilization)[capital.settlement_id]
    state, results = _run(state, 1)
    [farmed] = _events(results, "food_produced")
    gathered = _events(results, "timber_gathered")
    assert farmed.payload["units"] + sum(e.payload["units"] for e in gathered) <= 32
    assert fields
