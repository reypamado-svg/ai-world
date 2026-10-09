"""Town plans (rules version 3): the design model, the plain plan every settlement starts
with, and where plans are kept, moved and checked."""

import json
from dataclasses import replace

import pytest
from logistics_helpers import OneShotSovereign, clear_journey_id, linked_world, treaty_world
from pydantic import ValidationError
from test_allegiance import _colony
from test_peace import _make_peace

from sovereign_world.commands import (
    CommandEnvelope,
    DirectOrder,
    DirectOrderKind,
    build_council_report,
    validate_envelope,
)
from sovereign_world.config import RunManifest, WorldConfig
from sovereign_world.diplomacy import PeaceTerms
from sovereign_world.engine import advance_day
from sovereign_world.gateway.envelope import ReplyError, parse_reply
from sovereign_world.hexmap import Terrain
from sovereign_world.ids import EntityId
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState, build_initial_state, state_hash, validate_world
from sovereign_world.townplan import (
    DEFAULT_PLAN,
    SHELTERED_HOUSES,
    Place,
    PlanStyle,
    TownPlan,
    TownPlanSpec,
    default_plan,
    is_default,
    sections_of,
)

CONFIG = WorldConfig(seed=9, width=24, height=24)


def _state(rules_version: int) -> WorldState:
    return build_initial_state(RunManifest.new(CONFIG, "0.1.0", rules_version=rules_version))


def _plan(**update: object) -> dict[str, object]:
    return {"style": "ringed", "keep": "centre", "wall_ring": 2, "gates": [0, 3], **update}


def test_a_design_is_checked_and_its_gates_kept_in_order() -> None:
    spec = TownPlanSpec.model_validate(_plan(gates=[4, 1]))
    assert spec.gates == (1, 4)
    for bad in (
        _plan(gates=[]),
        _plan(gates=[1, 1]),
        _plan(gates=[6]),
        _plan(gates=[0, 1, 2, 3]),
        _plan(wall_ring=0),
        _plan(wall_ring=6),
        _plan(style="castle"),
        _plan(towers=4),
    ):
        with pytest.raises(ValidationError):
            TownPlanSpec.model_validate(bad)
    assert TownPlanSpec.model_validate(_plan(style="river_town")).needs_water()
    assert TownPlanSpec.model_validate(_plan(market="by_water")).needs_water()
    assert not spec.needs_water() and not spec.needs_heights()
    assert TownPlanSpec.model_validate(_plan(style="hill_fort")).needs_heights()


def test_ring_tables() -> None:
    assert [sections_of(ring) for ring in range(1, 6)] == [6, 10, 14, 18, 22]
    assert sorted(SHELTERED_HOUSES) == [1, 2, 3, 4, 5]
    assert DEFAULT_PLAN.style is PlanStyle.OPEN and DEFAULT_PLAN.keep is Place.EDGE
    plan = default_plan(EntityId("settlement:0000000001-0001"), 4)
    assert is_default(plan) and plan.spec() == DEFAULT_PLAN and plan.planned_day == 4


def test_rules_three_settlements_start_with_the_plain_plan_and_older_rules_have_none() -> None:
    new = _state(3)
    for civilization in new.civilizations.values():
        [capital] = civilization.settlements
        plan = civilization.town_plans[capital.settlement_id]
        assert is_default(plan) and plan.settlement_id == capital.settlement_id
        report = build_council_report(new, civilization.civilization_id)
        assert report.town_plans == civilization.town_plans
        assert "town_plans" in report.model_dump(mode="json")
    again = WorldState.model_validate_json(new.model_dump_json())
    assert state_hash(again) == state_hash(new)
    assert again.civilizations == new.civilizations

    old = _state(2)
    assert all(not item.town_plans for item in old.civilizations.values())
    dumped = old.model_dump(mode="json")
    assert all("town_plans" not in item for item in dumped["civilizations"].values())
    report = build_council_report(old, sorted(old.civilizations)[0])
    assert "town_plans" not in report.model_dump(mode="json")


def test_settlers_start_with_the_plain_plan() -> None:
    _, state, home, _, route = linked_world(distance=6, rules_version=3)
    # A new settlement needs water: give the site a marsh.
    state.world_map = replace(
        state.world_map,
        tiles=tuple(
            replace(tile, cover=(6_000, 1_000, 1_000, 2_000, 0, 0, 0))
            if tile.coord == route[3]
            else tile
            for tile in state.world_map.tiles
        ),
    )
    found = DirectOrder(
        command_id="order:found",
        kind=DirectOrderKind.FOUND_SETTLEMENT,
        journey_id=EntityId(clear_journey_id("found")),
        traveller_ids=state.civilizations[home].population.living_ids[-6:],
        route=route[:4],
    )
    rng = StableRng(state.config.seed)
    sovereigns = {home: OneShotSovereign(found)}
    for _ in range(4):
        state = advance_day(state, rng, sovereigns=sovereigns).state
    civilization = state.civilizations[home]
    colony = next(item for item in civilization.settlements if not item.capital)
    plan = civilization.town_plans[colony.settlement_id]
    assert is_default(plan) and plan.planned_day == colony.founded_day
    validate_world(state)


def test_a_ceded_settlement_keeps_its_plan_and_a_fallen_people_keeps_none() -> None:
    state, home, rival, route = treaty_world(distance=6, rules_version=3)
    colony = _colony(state, rival, route[3], people=6)
    designed = TownPlan(
        **TownPlanSpec.model_validate(_plan()).model_dump(),
        settlement_id=colony.settlement_id,
        planned_day=state.day,
    )
    state.civilizations[rival].town_plans = dict(
        sorted({**state.civilizations[rival].town_plans, colony.settlement_id: designed}.items())
    )
    state, _ = _make_peace(
        state, home, rival, PeaceTerms(truce_days=60, ceded_settlement=colony.settlement_id)
    )
    assert colony.settlement_id not in state.civilizations[rival].town_plans
    assert state.civilizations[home].town_plans[colony.settlement_id] == designed
    validate_world(state)

    fallen = state.civilizations[rival]
    for person in fallen.population.people.values():
        person.alive = False
        person.death_day = state.day
    state = advance_day(state, StableRng(state.config.seed)).state
    assert state.civilizations[rival].eliminated_day is not None
    assert state.civilizations[rival].town_plans == {}


def test_plans_belong_to_their_own_settlements_and_suit_the_land() -> None:
    # Day-0 observations are put in order by the first day; validate after it.
    state = advance_day(_state(3), StableRng(CONFIG.seed)).state
    validate_world(state)
    first, second = sorted(state.civilizations)[:2]
    [own] = state.civilizations[first].settlements
    [foreign] = state.civilizations[second].settlements

    def check(plans: dict[EntityId, TownPlan], match: str) -> None:
        saved = state.civilizations[first].town_plans
        state.civilizations[first].town_plans = plans
        with pytest.raises(ValueError, match=match):
            validate_world(state)
        state.civilizations[first].town_plans = saved

    check(
        {
            **state.civilizations[first].town_plans,
            foreign.settlement_id: default_plan(foreign.settlement_id, 0),
        },
        "own settlements",
    )
    check({own.settlement_id: default_plan(foreign.settlement_id, 0)}, "does not match")

    def designed(**update: object) -> TownPlan:
        spec = TownPlanSpec.model_validate(_plan(**update))
        return TownPlan(**spec.model_dump(), settlement_id=own.settlement_id, planned_day=1)

    terrain = state.world_map.tile(own.tile).terrain
    assert terrain not in (Terrain.HILLS, Terrain.MOUNTAIN)
    check({own.settlement_id: designed(style="hill_fort")}, "suit its settlement's land")

    # Dry the capital's surroundings, and a river town no longer fits.
    dry = {own.tile, *own.tile.neighbors()}
    wet = state.world_map
    state.world_map = replace(
        wet,
        rivers=(),
        tiles=tuple(
            replace(tile, terrain=Terrain.GRASSLAND, river=False, cover=(10_000, 0, 0, 0, 0, 0, 0))
            if tile.coord in dry
            else tile
            for tile in wet.tiles
        ),
    )
    check({own.settlement_id: designed(style="river_town")}, "suit its settlement's land")
    state.civilizations[first].town_plans = {own.settlement_id: designed(market="edge")}
    validate_world(state)


def _order(
    settlement_id: EntityId | None, command_id: str = "plan", **update: object
) -> DirectOrder:
    return DirectOrder(
        command_id=command_id,
        kind=DirectOrderKind.PLAN_SETTLEMENT,
        settlement_id=settlement_id,
        town_plan=TownPlanSpec.model_validate(_plan(**update)),
    )


def _validate(state: WorldState, civilization_id: EntityId, *orders: DirectOrder):
    return validate_envelope(
        CommandEnvelope(
            schema_version=2,
            civilization_id=civilization_id,
            council_day=state.day,
            correlation_id="test",
            commands=orders,
        ),
        state,
    )


def test_a_council_designs_its_settlement_and_the_engine_records_it() -> None:
    state = _state(3)
    home = sorted(state.civilizations)[0]
    [capital] = state.civilizations[home].settlements
    order = _order(capital.settlement_id, market="by_store", gates=[3, 0])
    assert not _validate(state, home, order).errors
    result = advance_day(state, StableRng(CONFIG.seed), sovereigns={home: OneShotSovereign(order)})
    plan = result.state.civilizations[home].town_plans[capital.settlement_id]
    assert plan.spec() == order.town_plan and plan.planned_day == 0
    assert not is_default(plan)
    [event] = [item for item in result.events.events if item.kind == "settlement_planned"]
    assert event.subject_id == capital.settlement_id
    assert event.payload == {"style": "ringed", "keep": "centre", "wall_ring": 2, "gates": "0,3"}
    validate_world(result.state)
    report = build_council_report(result.state, home)
    assert report.town_plans[capital.settlement_id] == plan


def test_each_refusal_of_a_plan_order() -> None:
    state = _state(3)
    home, other = sorted(state.civilizations)[:2]
    [capital] = state.civilizations[home].settlements
    [foreign] = state.civilizations[other].settlements

    def refusal(*orders: DirectOrder) -> list[str]:
        result = _validate(state, home, *orders)
        return [f"{item.code}: {item.message}" for item in result.errors]

    assert refusal(_order(foreign.settlement_id)) == [
        "invalid_town_plan: only this civilization's own settlements are planned"
    ]
    assert refusal(_order(None)) == [
        "invalid_town_plan: a plan order names its settlement and gives its design"
    ]
    assert refusal(_order(capital.settlement_id), _order(capital.settlement_id, "again")) == [
        "invalid_town_plan: a settlement is planned once a council"
    ]
    assert state.world_map.tile(capital.tile).terrain not in (Terrain.HILLS, Terrain.MOUNTAIN)
    assert refusal(_order(capital.settlement_id, style="hill_fort")) == [
        "invalid_town_plan: a hill fort needs hills or mountains"
    ]
    older = _state(2)
    [old_capital] = older.civilizations[home].settlements
    result = _validate(older, home, _order(old_capital.settlement_id))
    assert [item.code for item in result.errors] == ["invalid_town_plan"]
    # A design that repeats a gate does not fit the reply schema, so the model is asked again.
    reply = {"commands": [_order(capital.settlement_id).model_dump(mode="json")]}
    reply["commands"][0]["town_plan"]["gates"] = [2, 2]
    with pytest.raises(ReplyError, match="town_plan"):
        parse_reply(json.dumps(reply))
    assert parse_reply(
        json.dumps({"commands": [_order(capital.settlement_id).model_dump(mode="json")]})
    )


def test_orders_without_a_plan_dump_as_before() -> None:
    order = DirectOrder(command_id="x", kind=DirectOrderKind.ASSIGN_WORK)
    assert "town_plan" not in order.model_dump(mode="json")
    planned = _order(EntityId("settlement:0000000001-0001"))
    assert planned.model_dump(mode="json")["town_plan"]["gates"] == [0, 3]
