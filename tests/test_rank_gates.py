"""What ranks unlock, and the buildings that come with them (rules version 2)."""

from logistics_helpers import OneShotSovereign, envelope, treaty_world
from test_stores import _build, _grant
from test_tolls import _hold, _road
from test_tolls import _world as _toll_world
from test_war import _war_party

import sovereign_world.engine as engine
from sovereign_world.capabilities import CapabilityId
from sovereign_world.commands import (
    DirectOrder,
    DirectOrderKind,
    validate_envelope,
)
from sovereign_world.diplomacy import PeaceTerms, TreatyKind
from sovereign_world.engine import TransitionResult, advance_day
from sovereign_world.ids import EntityId
from sovereign_world.institutions import Institution, InstitutionKind
from sovereign_world.ranks import RealmRank, SettlementRank
from sovereign_world.resources import Resource
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState, validate_world
from sovereign_world.stores import StorehouseGrade
from sovereign_world.war import ARMS, Drill


def _codes(state: WorldState, civilization_id, *orders) -> list[str]:
    errors = validate_envelope(envelope(state, civilization_id, *orders), state).errors
    return [error.code for error in errors]


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


def _capital(state: WorldState, civilization_id):
    return next(item for item in state.civilizations[civilization_id].settlements if item.capital)


def _rank(state: WorldState, civilization_id, rank: SettlementRank) -> None:
    capital = _capital(state, civilization_id)
    state.civilizations[civilization_id].ranks_reached = {capital.settlement_id: rank}


def _open(state: WorldState, civilization_id, kind: InstitutionKind, staff) -> None:
    """An institution already built and kept at the capital."""
    capital = _capital(state, civilization_id)
    civilization = state.civilizations[civilization_id]
    civilization.institutions = (
        *civilization.institutions,
        Institution(
            institution_id=EntityId(f"institution:{kind.value}"),
            kind=kind,
            settlement_id=capital.settlement_id,
            tile=capital.tile,
            staff_ids=(staff,),
            founded_day=0,
            person_days_done=100,
            opened_day=0,
        ),
    )


def _found(state: WorldState, civilization_id, kind: InstitutionKind, workers, command_id=None):
    return DirectOrder(
        command_id=command_id or f"found:{kind.value}",
        kind=DirectOrderKind.FOUND_INSTITUTION,
        institution_kind=kind,
        worker_ids=workers,
    )


def test_warehouses_and_depots_need_a_town() -> None:
    state, home, _, _ = treaty_world()
    civilization = state.civilizations[home]
    builders = civilization.population.living_ids[:4]
    granary = civilization.storehouses[0].storehouse_id
    _grant(state, home, builders[0], CapabilityId.TIMBERCRAFT)
    _grant(state, home, builders[1], CapabilityId.STONEWORKING)
    civilization.inventory = civilization.inventory.model_copy(
        update={"quantities": {**civilization.inventory.quantities, Resource.PLANK: 40}}
    )
    warehouse = _build(state, home, builders, StorehouseGrade.WAREHOUSE, storehouse_id=granary)
    assert _codes(state, home, warehouse) == [], "rules 1 has no ranks"
    state.rules_version = 2
    assert _codes(state, home, warehouse) == ["rank_required"]
    _rank(state, home, SettlementRank.SMALL_TOWN)
    assert _codes(state, home, warehouse) == []
    depot = _build(state, home, builders, StorehouseGrade.DEPOT, storehouse_id=granary)
    assert "rank_required" in _codes(state, home, depot)
    _rank(state, home, SettlementRank.TOWN)
    assert "rank_required" not in _codes(state, home, depot)


def test_tolls_go_to_a_small_town() -> None:
    state, home, _, _, route = _toll_world()
    capital = route[0]
    _hold(state, home, capital)
    _road(state, home, capital)
    order = DirectOrder(
        command_id="toll",
        kind=DirectOrderKind.SET_TOLL,
        route=(capital,),
        toll_rate_bp=500,
        toll_food_per_head=1,
    )
    assert _codes(state, home, order) == []
    state.rules_version = 2
    assert _codes(state, home, order) == ["rank_required"]
    _rank(state, home, SettlementRank.SMALL_TOWN)
    assert _codes(state, home, order) == []


def test_war_parties_grow_with_the_realm() -> None:
    state, home, rival, route = treaty_world(distance=4)
    big = _war_party(state, home, rival, route, fighters=24, journey="journey:big")
    small = _war_party(state, home, rival, route, fighters=16, journey="journey:small")
    assert _codes(state, home, big) == [], "rules 1 parties are uncapped"
    state.rules_version = 2
    assert _codes(state, home, big) == ["party_too_large"]
    assert _codes(state, home, small) == []
    state.civilizations[home].realm_rank_reached = RealmRank.KINGDOM
    assert _codes(state, home, big) == []


def test_only_a_kingdom_demands_tribute() -> None:
    state, home, rival, route = treaty_world(kind=None)

    def offer(payer) -> DirectOrder:
        ambassador = state.civilizations[home].population.living_ids[0]
        return DirectOrder(
            command_id="offer",
            kind=DirectOrderKind.OFFER_TREATY,
            treaty_id=EntityId("treaty:peace"),
            treaty_kind=TreatyKind.PEACE,
            message_id=EntityId("message:peace"),
            ambassador_id=ambassador,
            recipient_civilization_id=rival,
            route=route,
            message_text="Peace, and pay us.",
            peace_terms=PeaceTerms(
                truce_days=60, tribute_payer=payer, tribute={Resource.FOOD: 10}, tribute_payments=1
            ),
        )

    demand, pay = offer(rival), offer(home)
    assert _codes(state, home, demand) == [], "rules 1 lets anyone demand tribute"
    state.rules_version = 2
    assert _codes(state, home, demand) == ["rank_required"]
    assert "rank_required" not in _codes(state, home, pay), "anyone may offer to pay"
    state.civilizations[home].realm_rank_reached = RealmRank.KINGDOM
    assert "rank_required" not in _codes(state, home, demand)


def test_a_hall_is_built_anywhere_but_other_works_need_rank_and_slots() -> None:
    state, home, _, _ = treaty_world()
    civilization = state.civilizations[home]
    people = civilization.population.living_ids
    hall = _found(state, home, InstitutionKind.HALL, people[:2])
    assert _codes(state, home, hall) == ["invalid_institution"], "rules 1 has no hall"
    state.rules_version = 2
    assert _codes(state, home, hall) == []

    grounds = _found(state, home, InstitutionKind.TRAINING_GROUNDS, people[2:4])
    assert _codes(state, home, grounds) == ["rank_required"]
    _rank(state, home, SettlementRank.SMALL_TOWN)
    assert _codes(state, home, grounds) == []

    # A village keeps one institution besides its hall; a small town three.
    _rank(state, home, SettlementRank.VILLAGE)
    _grant(state, home, people[4], CapabilityId.TIMBERCRAFT)
    workshop = _found(state, home, InstitutionKind.WORKSHOP, people[4:6])
    assert _codes(state, home, hall, workshop) == []
    _open(state, home, InstitutionKind.WORKSHOP, people[6])
    healers = _found(state, home, InstitutionKind.HEALERS_HOUSE, people[7:8])
    _grant(state, home, people[7], CapabilityId.HERBAL_CARE)
    assert _codes(state, home, healers) == ["rank_required"]
    assert _codes(state, home, hall) == [], "the hall takes no slot"
    _rank(state, home, SettlementRank.SMALL_TOWN)
    assert _codes(state, home, healers) == []


def test_bronze_arms_are_made_at_an_open_armoury() -> None:
    state, home, _, _ = treaty_world()
    civilization = state.civilizations[home]
    smiths = civilization.population.living_ids[:2]
    _grant(state, home, smiths[0], CapabilityId.BRONZE_WORKING)
    civilization.inventory = civilization.inventory.model_copy(
        update={
            "quantities": {
                **civilization.inventory.quantities,
                Resource.METAL: 50,
                Resource.TIMBER: 500,
            }
        }
    )
    order = DirectOrder(
        command_id="arms",
        kind=DirectOrderKind.CRAFT_EQUIPMENT,
        worker_ids=smiths,
        craft_item=Resource.BRONZE_ARMS,
    )
    assert _codes(state, home, order) == []
    state.rules_version = 2
    assert _codes(state, home, order) == ["building_required"]
    _open(state, home, InstitutionKind.ARMOURY, civilization.population.living_ids[5])
    assert _codes(state, home, order) == []


def test_an_armoury_speeds_up_its_settlement_s_crafting() -> None:
    def days_to_finish(with_armoury: bool) -> int:
        state, home, _, _ = treaty_world(rules_version=2)
        civilization = state.civilizations[home]
        maker = civilization.population.living_ids[0]
        _grant(state, home, maker, CapabilityId.TIMBERCRAFT)
        if with_armoury:
            _open(state, home, InstitutionKind.ARMOURY, civilization.population.living_ids[9])
        order = DirectOrder(
            command_id="spears",
            kind=DirectOrderKind.CRAFT_EQUIPMENT,
            worker_ids=(maker,),
            craft_item=Resource.SPEAR,
            craft_quantity=4,
        )
        assert _codes(state, home, order) == []
        state, results = _run(state, 12, {home: OneShotSovereign(order)})
        *_, done = [
            event
            for result in results
            for event in result.events.events
            if event.kind == "equipment_crafted"
        ]
        return done.day

    assert days_to_finish(True) < days_to_finish(False)


def test_training_grounds_take_drill_further() -> None:
    def arms_after(with_grounds: bool) -> int:
        state, home, _, _ = treaty_world(rules_version=2)
        civilization = state.civilizations[home]
        recruit = civilization.population.living_ids[0]
        civilization.population.people[recruit].skills = {
            **civilization.population.people[recruit].skills,
            ARMS: 19,
        }
        if with_grounds:
            _open(
                state, home, InstitutionKind.TRAINING_GROUNDS, civilization.population.living_ids[9]
            )
        civilization.drills = (
            Drill(drill_id=EntityId("drill:1"), person_ids=(recruit,), started_day=0, days=60),
        )
        state, _ = _run(state, 30)
        return state.civilizations[home].population.people[recruit].skills[ARMS]

    assert arms_after(False) == 20
    assert arms_after(True) == 25


def test_an_open_hall_extends_its_settlement_s_land() -> None:
    def owned(with_hall: bool) -> int:
        state, home, _, _ = treaty_world(rules_version=2)
        if with_hall:
            _open(
                state,
                home,
                InstitutionKind.HALL,
                state.civilizations[home].population.living_ids[9],
            )
        state, _ = _run(state, 20)
        validate_world(state)
        return sum(item.civilization_id == home for item in state.territory.owners)

    assert owned(True) > owned(False)


def test_scholars_in_a_city_earn_a_point_more(monkeypatch) -> None:
    # Hold the ranks as set: the test world could not earn them.
    monkeypatch.setattr(engine, "_advance_ranks", lambda state: [])

    def points(rank: SettlementRank) -> int:
        state, home, _, _ = treaty_world(rules_version=2)
        civilization = state.civilizations[home]
        scholar = civilization.population.living_ids[0]
        if rank is not SettlementRank.VILLAGE:
            _rank(state, home, rank)
        order = DirectOrder(
            command_id="study",
            kind=DirectOrderKind.RESEARCH,
            worker_ids=(scholar,),
            research_topic=CapabilityId.SPEAR_FORMATIONS,
            research_days=10,
        )
        state, _ = _run(state, 10, {home: OneShotSovereign(order)})
        return state.civilizations[home].research_points[CapabilityId.SPEAR_FORMATIONS]

    assert points(SettlementRank.CITY) == points(SettlementRank.VILLAGE) + 10
