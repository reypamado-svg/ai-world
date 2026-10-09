"""Building houses (rules version 2): shelter orders, the housing policy, decrees that run out,
and houses lost to war and neglect."""

from logistics_helpers import OneShotSovereign, envelope, treaty_world

from sovereign_world.capabilities import CapabilityId, CapabilityRecord
from sovereign_world.commands import (
    Decree,
    DecreeKind,
    DirectOrder,
    DirectOrderKind,
    ProjectKind,
    build_council_report,
    validate_envelope,
)
from sovereign_world.config import RunManifest, WorldConfig
from sovereign_world.engine import TransitionResult, _lose_houses, advance_day
from sovereign_world.housing import HouseGrade, Housing
from sovereign_world.ids import EntityId
from sovereign_world.resources import Inventory, Resource
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState, build_initial_state, validate_world
from sovereign_world.territory import Settlement

CONFIG = WorldConfig(seed=9, width=24, height=24)


def _state(rules_version: int = 2) -> WorldState:
    """A world whose peoples know no building craft yet, so they raise huts."""
    state = build_initial_state(RunManifest.new(CONFIG, "0.1.0", rules_version=rules_version))
    for civilization in state.civilizations.values():
        civilization.capabilities = tuple(
            record
            for record in civilization.capabilities
            if record.capability not in {CapabilityId.TIMBERCRAFT, CapabilityId.STONEWORKING}
        )
    return state


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


def _shelter(state: WorldState, civilization_id, workers, count: int, project: str = "houses"):
    return DirectOrder(
        command_id=f"order:{project}",
        kind=DirectOrderKind.START_PROJECT,
        project_id=EntityId(f"project:{project}"),
        project_kind=ProjectKind.SHELTER,
        worker_ids=workers,
        house_count=count,
    )


def _codes(state: WorldState, civilization_id, *commands) -> list[str]:
    errors = validate_envelope(envelope(state, civilization_id, *commands), state).errors
    return [error.code for error in errors]


def test_shelter_orders_are_checked_under_rules_two() -> None:
    state = _state()
    home = sorted(state.civilizations)[0]
    civilization = state.civilizations[home]
    workers = civilization.population.living_ids[:2]
    assert _codes(state, home, _shelter(state, home, workers, 3)) == []

    afield = civilization.population.living_ids[2]
    civilization.population.people[afield].location = civilization.known_tiles[0]
    assert civilization.known_tiles[0] != civilization.start_center
    split = (workers[0], afield)
    assert _codes(state, home, _shelter(state, home, split, 1)) == ["invalid_project"]
    assert _codes(state, home, _shelter(state, home, (), 1)) == ["invalid_project"]

    # 500 timber builds 50 huts, so two orders for 20 and a third for 20 overrun it.
    many = [_shelter(state, home, workers, 20, f"p{index}") for index in range(3)]
    many = [
        order.model_copy(update={"worker_ids": (person_id,)})
        for order, person_id in zip(many, civilization.population.living_ids[3:6], strict=True)
    ]
    assert _codes(state, home, *many) == ["insufficient_materials"]
    twice = (
        _shelter(state, home, workers[:1], 1, "same"),
        _shelter(state, home, workers[1:], 1, "same"),
    )
    assert _codes(
        state,
        home,
        *(item.model_copy(update={"command_id": f"c{i}"}) for i, item in enumerate(twice)),
    ) == ["invalid_project"]

    old = _state(1)
    old_home = sorted(old.civilizations)[0]
    # Rules 1 keeps its own checks: a shelter project is not a house order there.
    assert _codes(old, old_home, _shelter(old, old_home, (), 1)) == []


def test_the_housing_policy_belongs_to_rules_two_and_is_a_percentage() -> None:
    def policy(value: int) -> Decree:
        return Decree(command_id=f"d{value}", kind=DecreeKind.HOUSING_POLICY, value=value)

    new, old = _state(2), _state(1)
    new_home, old_home = sorted(new.civilizations)[0], sorted(old.civilizations)[0]
    assert _codes(new, new_home, policy(10)) == []
    assert _codes(new, new_home, policy(101)) == ["invalid_decree"]
    assert _codes(new, new_home, policy(-1)) == ["invalid_decree"]
    assert _codes(old, old_home, policy(10)) == ["invalid_decree"]


def test_builders_raise_houses_one_after_another() -> None:
    state = _state()
    home = sorted(state.civilizations)[0]
    civilization = state.civilizations[home]
    [capital] = civilization.settlements
    workers = civilization.population.living_ids[:2]
    timber = civilization.inventory.quantities[Resource.TIMBER]
    order = _shelter(state, home, workers, 3)

    state, results = _run(state, 7, {home: OneShotSovereign(order)})
    [started] = _events(results, "house_work_started")
    assert started.payload["grade"] == "hut" and started.payload["count"] == 3
    civilization = state.civilizations[home]
    assert civilization.inventory.quantities[Resource.TIMBER] == timber - 30
    # Two builders put in 14 person-days in seven days: two huts of five days each.
    assert civilization.housing[capital.settlement_id].houses == {HouseGrade.HUT: 9}
    [job] = civilization.house_jobs
    assert job.built() == 2
    assert build_council_report(state, home).house_jobs == (job,)

    state, results = _run(state, 1)
    civilization = state.civilizations[home]
    assert civilization.housing[capital.settlement_id].houses == {HouseGrade.HUT: 10}
    assert civilization.housing[capital.settlement_id].slots == 50
    assert civilization.house_jobs == ()
    [built] = _events(results, "house_built")
    assert built.payload["slots"] == 50
    validate_world(state)


def test_a_people_that_works_stone_builds_stone_houses() -> None:
    state = _state()
    home = sorted(state.civilizations)[0]
    civilization = state.civilizations[home]
    civilization.capabilities = (
        *civilization.capabilities,
        CapabilityRecord(
            capability=CapabilityId.STONEWORKING, practitioner_ids=(), discovered_day=0
        ),
    )
    workers = civilization.population.living_ids[:2]
    state, results = _run(state, 1, {home: OneShotSovereign(_shelter(state, home, workers, 2))})
    [started] = _events(results, "house_work_started")
    assert started.payload["grade"] == "stone_house"
    assert state.civilizations[home].inventory.quantities[Resource.STONE] == 300 - 60


def test_unused_materials_go_back_when_the_builders_die() -> None:
    state = _state()
    home = sorted(state.civilizations)[0]
    workers = state.civilizations[home].population.living_ids[:2]
    timber = state.civilizations[home].inventory.quantities[Resource.TIMBER]
    state, _ = _run(state, 3, {home: OneShotSovereign(_shelter(state, home, workers, 4))})
    civilization = state.civilizations[home]
    # Six person-days: one hut built, three to go.
    assert civilization.house_jobs[0].built() == 1
    for person_id in workers:
        civilization.population.people[person_id].alive = False
        civilization.population.people[person_id].death_day = state.day
    state, results = _run(state, 1)
    civilization = state.civilizations[home]
    [stopped] = _events(results, "house_work_stopped")
    assert stopped.payload["built"] == 1
    assert civilization.house_jobs == ()
    assert civilization.inventory.quantities[Resource.TIMBER] == timber - 10


def test_a_housing_policy_keeps_building_while_room_is_short() -> None:
    state = _state()
    home = sorted(state.civilizations)[0]
    decree = Decree(command_id="d", kind=DecreeKind.HOUSING_POLICY, value=10, duration_days=60)
    state, results = _run(state, 1, {home: OneShotSovereign(decree)})
    [started] = _events(results, "house_work_started")
    assert started.payload["source"] == "housing_policy"
    [job] = state.civilizations[home].house_jobs
    assert job.count == 1 and len(job.worker_ids) == 2

    # 35 places for 32 is under 10% spare; 40 places is over it, so building stops there.
    state, results = _run(state, 5)
    [capital] = state.civilizations[home].settlements
    assert state.civilizations[home].housing[capital.settlement_id].count == 8
    state, results = _run(state, 10)
    assert _events(results, "house_work_started") == []

    # Without timber, there is nothing to build with.
    poor = _state()
    poor_home = sorted(poor.civilizations)[0]
    inventory = poor.civilizations[poor_home].inventory
    poor.civilizations[poor_home].inventory = inventory.model_copy(
        update={"quantities": {**inventory.quantities, Resource.TIMBER: 5}}
    )
    poor, results = _run(poor, 2, {poor_home: OneShotSovereign(decree)})
    assert _events(results, "house_work_started") == []


def test_decrees_run_out_under_rules_two_and_not_before() -> None:
    def grow(state: WorldState):
        home = sorted(state.civilizations)[0]
        decree = Decree(
            command_id="grow", kind=DecreeKind.POPULATION_GROWTH_POLICY, value=1, duration_days=30
        )
        return home, {home: OneShotSovereign(decree)}

    new = _state(2)
    home, sovereigns = grow(new)
    new, _ = _run(new, 30, sovereigns)
    assert new.active_decrees[home]["population_growth_policy"] == 1, "still in force on day 29"
    new, results = _run(new, 1, sovereigns)
    assert "population_growth_policy" not in new.active_decrees[home]
    [expired] = _events(results, "decree_expired")
    assert expired.payload == {"decree": "population_growth_policy"}

    old = _state(1)
    home, sovereigns = grow(old)
    old, results = _run(old, 31, sovereigns)
    assert old.active_decrees[home]["population_growth_policy"] == 1
    assert _events(results, "decree_expired") == []


def test_stormed_settlements_lose_a_quarter_of_their_houses_the_meanest_first() -> None:
    state = _state()
    home = sorted(state.civilizations)[0]
    [capital] = state.civilizations[home].settlements
    state.civilizations[home].housing = {
        capital.settlement_id: Housing(houses={HouseGrade.HUT: 3, HouseGrade.HOUSE: 5})
    }
    [event] = _lose_houses(state, home, capital.settlement_id, "stormed")
    assert event.payload == {"cause": "stormed", "count": 2}
    assert state.civilizations[home].housing[capital.settlement_id].houses == {
        HouseGrade.HUT: 1,
        HouseGrade.HOUSE: 5,
    }
    old = _state(1)
    old_home = sorted(old.civilizations)[0]
    assert _lose_houses(old, old_home, capital.settlement_id, "stormed") == []


def test_an_empty_settlement_loses_a_house_a_month_after_a_year() -> None:
    state, home, _, route = treaty_world(rules_version=2, distance=6)
    civilization = state.civilizations[home]
    colony = Settlement(
        settlement_id=EntityId(f"settlement:{home.rsplit(':', 1)[-1]}-0002"),
        civilization_id=home,
        tile=route[3],
        founded_day=0,
    )
    civilization.settlements = (*civilization.settlements, colony)
    civilization.stores = {colony.settlement_id: Inventory(capacity=10_000)}
    civilization.housing = {
        **civilization.housing,
        colony.settlement_id: Housing(houses={HouseGrade.HUT: 3}),
    }
    state, _ = _run(state, 1)
    housing = state.civilizations[home].housing[colony.settlement_id]
    assert housing.empty_since == 0
    # Skip ahead: it has stood empty for nearly a year.
    state.day = 363
    state, results = _run(state, 4)
    [lost] = _events(results, "houses_lost")
    assert lost.payload == {"cause": "abandoned", "count": 1}
    assert state.civilizations[home].housing[colony.settlement_id].count == 2
    state, results = _run(state, 30)
    assert len(_events(results, "houses_lost")) == 1

    # Someone coming home stops the decay.
    mover = state.civilizations[home].population.living_ids[0]
    state.civilizations[home].population.people[mover].location = colony.tile
    state, _ = _run(state, 1)
    assert state.civilizations[home].housing[colony.settlement_id].empty_since is None
    validate_world(state)
