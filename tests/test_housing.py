"""Houses (rules version 2): every five people need one; people grow only where there is room."""

import pytest
from logistics_helpers import OneShotSovereign, clear_journey_id, linked_world, treaty_world
from test_allegiance import _colony as _walled_colony
from test_peace import _make_peace

import sovereign_world.engine as engine
from sovereign_world.capabilities import CapabilityId, CapabilityRecord
from sovereign_world.commands import DirectOrder, DirectOrderKind, build_council_report
from sovereign_world.config import RunManifest, WorldConfig
from sovereign_world.diplomacy import PeaceTerms
from sovereign_world.engine import advance_day
from sovereign_world.gateway.memory import state_summary
from sovereign_world.housing import (
    HOUSEHOLD,
    HouseGrade,
    Housing,
    best_grade,
    founding_housing,
)
from sovereign_world.ids import EntityId
from sovereign_world.resources import Inventory
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState, build_initial_state, validate_world
from sovereign_world.territory import Settlement

CONFIG = WorldConfig(seed=9, width=24, height=24)
GROWTH = {"population_growth_policy": 1, "population_growth_policy_expires": 10_000}
REAL_POPULATION_DAY = engine.advance_population_day


def _state(rules_version: int) -> WorldState:
    return build_initial_state(RunManifest.new(CONFIG, "0.1.0", rules_version=rules_version))


def _mothers_by_day(
    monkeypatch: pytest.MonkeyPatch, state: WorldState, days: int
) -> tuple[WorldState, dict[tuple[int, EntityId], frozenset[EntityId] | None]]:
    """Run some days, recording whom the engine let conceive at each civilization."""
    seen: dict[tuple[int, EntityId], frozenset[EntityId] | None] = {}

    def spy(population, *, day, eligible_mothers=None, **rest):
        seen[(day, population.civilization_id)] = eligible_mothers
        return REAL_POPULATION_DAY(population, day=day, eligible_mothers=eligible_mothers, **rest)

    monkeypatch.setattr(engine, "advance_population_day", spy)
    rng = StableRng(state.config.seed)
    for _ in range(days):
        state = advance_day(state, rng).state
    return state, seen


def test_founders_raise_seven_huts_under_rules_two_and_none_before() -> None:
    new, old = _state(2), _state(1)
    for civilization in new.civilizations.values():
        [capital] = civilization.settlements
        assert civilization.housing == {capital.settlement_id: Housing(houses={HouseGrade.HUT: 7})}
        assert civilization.housing[capital.settlement_id].slots == 35
    assert all(civilization.housing == {} for civilization in old.civilizations.values())
    validate_world(advance_day(new, StableRng(CONFIG.seed)).state)


def test_older_worlds_dump_report_and_prompt_exactly_as_before() -> None:
    old = _state(1)
    dumped = old.model_dump(mode="json")
    assert "rules_version" not in dumped
    assert all("housing" not in item for item in dumped["civilizations"].values())
    civilization_id = sorted(old.civilizations)[0]
    report = build_council_report(old, civilization_id)
    assert report.housing == {} and report.rules_version == 1
    assert {"housing", "rules_version"}.isdisjoint(report.model_dump(mode="json"))
    summary = state_summary(report, budget=1_000_000)
    assert "housing" not in summary and "rules_version" not in summary

    new = _state(2)
    view = build_council_report(new, civilization_id).housing
    [(settlement_id, housing)] = view.items()
    assert housing.slots == 35 and housing.residents == 32
    assert housing.buildable in set(HouseGrade)
    assert "housing" in state_summary(build_council_report(new, civilization_id), 1_000_000)
    assert settlement_id in new.model_dump(mode="json")["civilizations"][civilization_id]["housing"]


def test_conception_needs_a_house_for_everyone_and_three_months_of_food(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    roomy = _state(2)
    cramped = _state(2)
    for state in (roomy, cramped):
        state.active_decrees = {key: dict(GROWTH) for key in state.civilizations}
    first = sorted(cramped.civilizations)[0]
    [capital] = cramped.civilizations[first].settlements
    # Thirty places for thirty-two people: no room to grow.
    cramped.civilizations[first].housing = {
        capital.settlement_id: Housing(houses={HouseGrade.HUT: 6})
    }
    _, roomy_seen = _mothers_by_day(monkeypatch, roomy, 31)
    _, cramped_seen = _mothers_by_day(monkeypatch, cramped, 31)

    women = roomy_seen[(30, first)]
    assert women, "with 35 places for 32 people, the capital's women may conceive"
    assert women <= set(roomy.civilizations[first].population.people)
    assert cramped_seen[(30, first)] == frozenset()
    other = sorted(cramped.civilizations)[1]
    assert cramped_seen[(30, other)], "other civilizations are untouched"

    # Without the growth decree no one conceives, house or no house.
    still = _state(2)
    _, still_seen = _mothers_by_day(monkeypatch, still, 31)
    assert still_seen[(30, first)] == frozenset()


def test_a_hungry_colony_holds_back_only_its_own_mothers(monkeypatch: pytest.MonkeyPatch) -> None:
    state, home, _, route = treaty_world(rules_version=2, distance=6)
    civilization = state.civilizations[home]
    colony = Settlement(
        settlement_id=EntityId(f"settlement:{home.rsplit(':', 1)[-1]}-0002"),
        civilization_id=home,
        tile=route[3],
        founded_day=0,
    )
    civilization.settlements = (*civilization.settlements, colony)
    settlers = civilization.population.living_ids[-10:]
    for person_id in settlers:
        civilization.population.people[person_id].location = colony.tile
    civilization.stores = {colony.settlement_id: Inventory(capacity=10_000)}
    civilization.housing = {
        **civilization.housing,
        colony.settlement_id: founding_housing(len(settlers)),
    }
    state.active_decrees = {home: dict(GROWTH)}
    _, seen = _mothers_by_day(monkeypatch, state, 31)
    mothers = seen[(30, home)]
    assert mothers and mothers.isdisjoint(settlers), "the colony has no food in store"
    assert all(
        civilization.population.people[person_id].location != colony.tile for person_id in mothers
    )


def test_captives_held_at_a_settlement_need_room_too() -> None:
    state = _state(2)
    holder, other = sorted(state.civilizations)[:2]
    [capital] = state.civilizations[holder].settlements
    for person_id in state.civilizations[other].population.living_ids[:4]:
        person = state.civilizations[other].population.people[person_id]
        person.captive_of = holder
        person.held_at = capital.settlement_id
        person.location = capital.tile
    [view] = build_council_report(state, holder).housing.values()
    assert view.residents == 36 and view.slots == 35


def test_settlers_raise_huts_as_they_arrive() -> None:
    _, state, home, _, route = linked_world(distance=6, rules_version=2)
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
    assert civilization.housing[colony.settlement_id] == Housing(houses={HouseGrade.HUT: 2})
    validate_world(state)


def test_a_ceded_settlement_takes_its_houses_and_a_fallen_people_keeps_none() -> None:
    state, home, rival, route = treaty_world(distance=6, rules_version=2)
    colony = _walled_colony(state, rival, route[3], people=6)
    state.civilizations[rival].housing = {
        **state.civilizations[rival].housing,
        colony.settlement_id: Housing(houses={HouseGrade.HUT: 2, HouseGrade.HOUSE: 1}),
    }
    state, _ = _make_peace(
        state, home, rival, PeaceTerms(truce_days=60, ceded_settlement=colony.settlement_id)
    )
    assert colony.settlement_id not in state.civilizations[rival].housing
    assert state.civilizations[home].housing[colony.settlement_id].count == 3
    validate_world(state)

    fallen = state.civilizations[rival]
    for person in fallen.population.people.values():
        person.alive = False
        person.death_day = state.day
    state = advance_day(state, StableRng(state.config.seed)).state
    assert state.civilizations[rival].eliminated_day is not None
    assert state.civilizations[rival].housing == {}


def test_houses_stand_only_in_their_own_settlements() -> None:
    # Day-0 observations are put in order by the first day; validate after it.
    state = advance_day(_state(2), StableRng(CONFIG.seed)).state
    validate_world(state)
    first, second = sorted(state.civilizations)[:2]
    [foreign] = state.civilizations[second].settlements
    state.civilizations[first].housing = {
        **state.civilizations[first].housing,
        foreign.settlement_id: founding_housing(5),
    }
    with pytest.raises(ValueError, match="own settlements"):
        validate_world(state)
    with pytest.raises(ValueError):
        Housing(houses={HouseGrade.HUT: 0})


def test_houses_and_grades() -> None:
    assert founding_housing(32).count == 7
    assert founding_housing(1).count == 1
    assert founding_housing(10).slots == 2 * HOUSEHOLD
    housing = Housing(houses={HouseGrade.STONE_HOUSE: 1, HouseGrade.HUT: 2})
    left, lost = housing.minus(2)
    assert lost == 2 and left.houses == {HouseGrade.STONE_HOUSE: 1}
    assert housing.plus(HouseGrade.HOUSE, 2).houses == {
        HouseGrade.HUT: 2,
        HouseGrade.HOUSE: 2,
        HouseGrade.STONE_HOUSE: 1,
    }

    def records(*capabilities: CapabilityId) -> tuple[CapabilityRecord, ...]:
        return tuple(
            CapabilityRecord(capability=item, practitioner_ids=(), discovered_day=0)
            for item in capabilities
        )

    assert best_grade(records()) is HouseGrade.HUT
    assert best_grade(records(CapabilityId.TIMBERCRAFT)) is HouseGrade.HOUSE
    assert best_grade(records(CapabilityId.STONEWORKING)) is HouseGrade.STONE_HOUSE
