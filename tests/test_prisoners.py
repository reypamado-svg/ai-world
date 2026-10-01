import pytest
from logistics_helpers import OneShotSovereign, clear_journey_id, envelope, treaty_world

import sovereign_world.engine as engine_module
import sovereign_world.war as war_module
from sovereign_world.commands import (
    DirectOrder,
    DirectOrderKind,
    build_council_report,
    validate_envelope,
)
from sovereign_world.engine import TransitionResult, advance_day
from sovereign_world.hexmap import HexCoord
from sovereign_world.ids import EntityId
from sovereign_world.logistics import JourneyKind
from sovereign_world.resources import Resource
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState, validate_world
from sovereign_world.war import CAPTIVES_PER_WINNER, Fighter, WarObjective, resolve_battle


@pytest.fixture
def certain_capture(monkeypatch):
    """Every pursuit blow takes its man alive, so the scenarios below are sure to capture."""
    monkeypatch.setattr(war_module, "CAPTURE_BP", 10_000)


def _world():
    return treaty_world(distance=4)


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


def _codes(state: WorldState, civilization_id: EntityId, *orders: DirectOrder) -> list[str]:
    errors = validate_envelope(envelope(state, civilization_id, *orders), state).errors
    return [error.code for error in errors]


def _raid(state, home, rival, route, *, fighters, axes=0):
    civilization = state.civilizations[home]
    civilization.inventory = civilization.inventory.model_copy(
        update={"quantities": {**civilization.inventory.quantities, Resource.AXE: 8}}
    )
    return DirectOrder(
        command_id="raid",
        kind=DirectOrderKind.SEND_WAR_PARTY,
        journey_id=EntityId(clear_journey_id("raid", days=12)),
        recipient_civilization_id=rival,
        traveller_ids=civilization.population.living_ids[:fighters],
        route=route,
        cargo={Resource.AXE: axes} if axes else {},
        war_objective=WarObjective.RAID,
    )


def _send_away(state: WorldState, civilization_id: EntityId, keep: int, tile: HexCoord) -> None:
    people = state.civilizations[civilization_id].population.people
    for person_id in sorted(people)[keep:]:
        people[person_id].location = tile


def _fighters(prefix: str, count: int) -> list[Fighter]:
    return [
        Fighter(
            person_id=EntityId(f"person:{prefix}{index:02d}"),
            civilization_id=EntityId(f"civilization:{prefix}"),
            strength=100,
            health_bp=10_000,
            veteran=False,
            hungry=False,
        )
        for index in range(count)
    ]


def test_some_of_the_rout_is_taken_alive_but_never_more_than_the_winners_can_hold() -> None:
    taken = 0
    for trial in range(200):
        outcome = resolve_battle(
            _fighters("a", 30),
            _fighters("d", 10),
            defence_bp=10_000,
            attacker_morale_bp=2_000,
            defender_morale_bp=3_000,
            rng=StableRng(3),
            stream=f"trial:{trial}",
        )
        wounded = {casualty.person_id for casualty in outcome.casualties}
        assert not wounded & set(outcome.captured), "a captive was taken, not cut down"
        taken += len(outcome.captured)
    assert taken > 0, "over many battles, some fleeing fighters are captured"


def test_winners_hold_no_more_captives_than_twice_their_number(certain_capture) -> None:
    outcome = resolve_battle(
        _fighters("a", 2),
        _fighters("d", 40),
        defence_bp=10_000,
        attacker_morale_bp=2_000,
        defender_morale_bp=100,
        rng=StableRng(3),
        stream="few-winners",
    )
    standing = 2 - sum(casualty.person_id.startswith("person:a") for casualty in outcome.casualties)
    assert len(outcome.captured) <= CAPTIVES_PER_WINNER * standing


def test_defenders_at_home_keep_the_raiders_they_catch(certain_capture) -> None:
    state, home, rival, route = _world()
    order = _raid(state, home, rival, route, fighters=6)
    state, results = _run(state, 8, {home: OneShotSovereign(order)})

    [battle] = state.battles
    assert battle.winner_id == rival and battle.captured
    settlement = state.civilizations[rival].settlements[0]
    people = state.civilizations[home].population.people
    for person_id in battle.captured:
        captive = people[person_id]
        assert captive.captive_of == rival and captive.held_at == settlement.settlement_id
        assert captive.location == settlement.tile and captive.civilization_id == home
    assert len(_events(results, "captured")) == len(battle.captured)
    assert build_council_report(state, rival).captives == battle.captured
    assert build_council_report(state, home).held_captive == battle.captured
    assert not set(battle.captured) & set(build_council_report(state, home).person_ids)
    validate_world(state)


def test_raiders_march_their_captives_home_and_hold_them_there(certain_capture) -> None:
    state, home, rival, route = _world()
    _send_away(state, rival, 6, HexCoord(route[-1].q, route[-1].r - 3))
    order = _raid(state, home, rival, route, fighters=14, axes=8)
    state, results = _run(state, 12, {home: OneShotSovereign(order)})

    [battle] = state.battles
    assert battle.winner_id == home and battle.captured
    assert _events(results, "war_party_returned")
    capital = state.civilizations[home].settlements[0]
    people = state.civilizations[rival].population.people
    for person_id in battle.captured:
        assert people[person_id].captive_of == home
        assert people[person_id].held_at == capital.settlement_id
        assert people[person_id].location == capital.tile
    validate_world(state)


def test_captives_eat_and_farm_where_they_are_held_and_cannot_be_ordered(certain_capture) -> None:
    state, home, rival, route = _world()
    order = _raid(state, home, rival, route, fighters=6)
    state, _ = _run(state, 8, {home: OneShotSovereign(order)})
    [battle] = state.battles
    captive = battle.captured[0]
    march = DirectOrder(
        command_id="home",
        kind=DirectOrderKind.DRILL,
        worker_ids=(captive,),
        drill_days=10,
    )
    assert "held_captive" in _codes(state, home, march)

    rival_food = state.civilizations[rival].inventory.quantities[Resource.FOOD]
    home_food = state.civilizations[home].inventory.quantities[Resource.FOOD]
    state, _ = _run(state, 2)
    rival_eaters = len(state.civilizations[rival].population.living_ids)
    home_eaters = len(state.civilizations[home].population.living_ids) - len(battle.captured)
    assert rival_food - state.civilizations[rival].inventory.quantities[Resource.FOOD] == 2 * (
        rival_eaters + len(battle.captured)
    ), "the captor feeds its captives"
    assert home_food - state.civilizations[home].inventory.quantities[Resource.FOOD] == (
        2 * home_eaters
    ), "their own civilization no longer does"


def test_released_captives_walk_home(certain_capture) -> None:
    state, home, rival, route = _world()
    order = _raid(state, home, rival, route, fighters=6)
    state, _ = _run(state, 8, {home: OneShotSovereign(order)})
    [battle] = state.battles
    release = DirectOrder(
        command_id="release",
        kind=DirectOrderKind.RELEASE_PRISONERS,
        captive_ids=battle.captured,
    )
    assert _codes(state, rival, release) == []
    assert _codes(state, home, release) == ["invalid_release"], "only the captor lets them go"
    state.day = 30
    state, results = _run(state, 10, {rival: OneShotSovereign(release)})

    freed = _events(results, "captive_freed")
    assert {event.subject_id for event in freed} == set(battle.captured)
    assert all(event.payload["reason"] == "released" for event in freed)
    walks = [
        journey
        for journey in state.journeys
        if journey.kind is JourneyKind.RELOCATION and journey.sender_civilization_id == home
    ]
    assert walks and not any(journey.active for journey in walks), "they reached home"
    home_tile = state.civilizations[home].settlements[0].tile
    people = state.civilizations[home].population.people
    assert all(
        people[person_id].captive_of is None and people[person_id].location == home_tile
        for person_id in battle.captured
        if people[person_id].alive
    )
    validate_world(state)


def test_captives_escape_at_a_council(certain_capture, monkeypatch) -> None:
    state, home, rival, route = _world()
    order = _raid(state, home, rival, route, fighters=6)
    state, _ = _run(state, 8, {home: OneShotSovereign(order)})
    [battle] = state.battles
    monkeypatch.setattr(engine_module, "ESCAPE_BP", 10_000)
    state.day = 29
    state, results = _run(state, 2)

    escaped = _events(results, "captive_freed")
    assert {event.subject_id for event in escaped} == set(battle.captured)
    assert all(event.payload["reason"] == "escaped" and event.day == 30 for event in escaped)
