import pytest
from logistics_helpers import (
    OneShotSovereign,
    ScheduledSovereign,
    clear_journey_id,
    envelope,
    treaty_world,
)

import sovereign_world.engine as engine
from sovereign_world.commands import (
    DirectOrder,
    DirectOrderKind,
    build_council_report,
    validate_envelope,
)
from sovereign_world.engine import TransitionResult, advance_day
from sovereign_world.espionage import (
    COURIER_CAUGHT_BP,
    FLUENT_CUT_BP,
    MIN_CAUGHT_BP,
    SPYCRAFT,
    WATCH_CAUGHT_BP,
    caught_chance_bp,
    observe,
)
from sovereign_world.hexmap import HexCoord
from sovereign_world.ids import EntityId
from sovereign_world.logistics import JourneyKind, JourneyOutcome
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState, validate_world
from sovereign_world.walls import WallGrade


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


def _spy(state, home, rival, route, *, spies=1, days=5, prefix="spy") -> DirectOrder:
    return DirectOrder(
        command_id=prefix,
        kind=DirectOrderKind.SEND_SPY,
        journey_id=EntityId(clear_journey_id(prefix, days=12)),
        recipient_civilization_id=rival,
        traveller_ids=state.civilizations[home].population.living_ids[-spies:],
        route=route,
        watch_days=days,
    )


@pytest.fixture
def unseen(monkeypatch):
    """Spies and couriers who are never found out."""
    monkeypatch.setattr(engine, "caught_chance_bp", lambda *_: 0)


@pytest.fixture
def careless(monkeypatch):
    """Spies and couriers who are always found out."""
    monkeypatch.setattr(engine, "caught_chance_bp", lambda *_: 10_000)


def test_spies_go_to_a_known_settlement_for_a_set_number_of_days() -> None:
    state, home, rival, route = treaty_world(distance=4)
    order = _spy(state, home, rival, route)
    assert _codes(state, home, order) == []
    assert _codes(state, home, order.model_copy(update={"watch_days": 0})) == ["invalid_watch"]
    assert _codes(state, home, _spy(state, home, rival, route, spies=5)) == ["invalid_journey"]
    assert _codes(state, home, order.model_copy(update={"route": route[:-1]})) == ["invalid_route"]
    stranger = EntityId("civilization:0000000009")
    assert "unknown_contact" in _codes(
        state, home, order.model_copy(update={"recipient_civilization_id": stranger})
    )


def test_a_party_is_as_hidden_as_its_least_careful_member() -> None:
    state, home, rival, _ = treaty_world(distance=4)
    first, second = list(state.civilizations[home].population.people.values())[:2]
    assert caught_chance_bp([first], rival, WATCH_CAUGHT_BP) == WATCH_CAUGHT_BP
    first.languages = {rival: 100}
    assert caught_chance_bp([first], rival, WATCH_CAUGHT_BP) == WATCH_CAUGHT_BP - FLUENT_CUT_BP
    first.skills = {**first.skills, SPYCRAFT: 20}
    assert caught_chance_bp([first], rival, WATCH_CAUGHT_BP) == MIN_CAUGHT_BP
    assert caught_chance_bp([first, second], rival, COURIER_CAUGHT_BP) == COURIER_CAUGHT_BP


def test_estimates_are_closer_from_spies_who_speak_the_language() -> None:
    state, home, rival, _ = treaty_world(distance=4)
    spy = next(iter(state.civilizations[home].population.people.values()))
    truth = {"residents": 100, "fighters": 60, "store_units": 5_000, "works": 2}

    def seen(seed: int):
        return observe(
            settlement_id=EntityId("settlement:x"),
            civilization_id=rival,
            tile=HexCoord(0, 0),
            day=0,
            wall_grade=WallGrade.PALISADE,
            towers=2,
            spies=[spy],
            roll=StableRng(seed).stream("estimate"),
            **truth,
        )

    strangers = [seen(seed) for seed in range(60)]
    assert all(70 <= item.residents <= 130 for item in strangers)
    assert max(abs(item.residents - 100) for item in strangers) > 10
    assert all(item.wall_grade is WallGrade.PALISADE and item.towers == 2 for item in strangers)
    assert all(item.store_units % 50 == 0 and item.fighters <= item.residents for item in strangers)
    spy.languages = {rival: 60}
    assert all(90 <= seen(seed).residents <= 110 for seed in range(60))


def test_spies_watch_then_bring_home_what_they_saw(unseen) -> None:
    state, home, rival, route = treaty_world(distance=4)
    order = _spy(state, home, rival, route, days=5)
    state, results = _run(state, 20, {home: OneShotSovereign(order)})

    assert _events(results, "spies_on_watch") and _events(results, "spies_left_watch")
    [delivered] = _events(results, "spy_report_delivered")
    assert delivered.payload["by_courier"] is False
    [report] = state.civilizations[home].spy_reports
    capital = state.civilizations[rival].settlements[0]
    assert report.estimate.settlement_id == capital.settlement_id
    assert report.estimate.civilization_id == rival and report.estimate.residents > 0
    [journey] = [item for item in state.journeys if item.journey_id == order.journey_id]
    assert journey.outcome is JourneyOutcome.DELIVERED and journey.watched == 5
    spy = state.civilizations[home].population.people[order.traveller_ids[0]]
    assert spy.skills[SPYCRAFT] == 1
    state.day = 30
    assert build_council_report(state, home).spy_reports == (report,)
    assert build_council_report(state, rival).spy_reports == ()
    validate_world(state)


def test_caught_spies_are_held_and_their_sender_is_known(careless) -> None:
    state, home, rival, route = treaty_world(distance=4)
    order = _spy(state, home, rival, route, spies=2, days=5)
    state, results = _run(state, 20, {home: OneShotSovereign(order)})

    assert len(_events(results, "spy_caught")) == 2
    assert not _events(results, "spy_report_delivered"), "their findings are lost"
    assert state.civilizations[home].spy_reports == ()
    capital = state.civilizations[rival].settlements[0]
    people = state.civilizations[home].population.people
    for person_id in order.traveller_ids:
        assert people[person_id].captive_of == rival
        assert people[person_id].held_at == capital.settlement_id
    caught = state.civilizations[rival].caught_spies
    assert {item.person_id for item in caught} == set(order.traveller_ids)
    assert {item.sender_civilization_id for item in caught} == {home}
    state.day = 30
    assert build_council_report(state, rival).caught_spies == caught
    assert set(order.traveller_ids) <= set(build_council_report(state, rival).captives)
    validate_world(state)


def test_a_courier_brings_the_findings_so_far_home_ahead_of_the_spies(unseen) -> None:
    state, home, rival, route = treaty_world(distance=4)
    order = _spy(state, home, rival, route, spies=2, days=60)
    state, _ = _run(state, 30, {home: OneShotSovereign(order)})
    [party] = build_council_report(state, home).spy_missions
    assert party.watching and party.findings is not None
    courier_id = party.traveller_ids[0]
    courier = DirectOrder(
        command_id="courier",
        kind=DirectOrderKind.SEND_COURIER,
        journey_id=party.journey_id,
        traveller_ids=(courier_id,),
        route=tuple(reversed(route)),
    )
    assert _codes(state, home, courier) == []
    assert _codes(state, home, courier.model_copy(update={"route": route})) == ["invalid_route"]
    both = courier.model_copy(update={"traveller_ids": party.traveller_ids})
    assert _codes(state, home, both) == ["invalid_courier"]
    assert _codes(state, rival, courier) == ["invalid_courier"]

    state, results = _run(state, 15, {home: ScheduledSovereign({30: (courier,)})})

    assert _events(results, "courier_sent")
    [delivered] = _events(results, "spy_report_delivered")
    assert delivered.payload["by_courier"] is True
    [report] = state.civilizations[home].spy_reports
    assert report.by_courier and report.journey_id == party.journey_id
    assert report.estimate.day < 30
    [still] = [item for item in state.journeys if item.journey_id == party.journey_id]
    assert still.watching and still.traveller_ids == party.traveller_ids[1:]
    runner = state.civilizations[home].population.people[courier_id]
    assert runner.location == route[0]
    validate_world(state)


def test_a_courier_crossing_the_watched_land_can_be_stopped(unseen, monkeypatch) -> None:
    state, home, rival, route = treaty_world(distance=4)
    order = _spy(state, home, rival, route, spies=2, days=60)
    state, _ = _run(state, 30, {home: OneShotSovereign(order)})
    [party] = build_council_report(state, home).spy_missions
    courier = DirectOrder(
        command_id="courier",
        kind=DirectOrderKind.SEND_COURIER,
        journey_id=party.journey_id,
        traveller_ids=(party.traveller_ids[0],),
        route=tuple(reversed(route)),
    )
    monkeypatch.setattr(
        engine,
        "caught_chance_bp",
        lambda people, language, base: 10_000 if base == COURIER_CAUGHT_BP else 0,
    )
    state, results = _run(state, 15, {home: ScheduledSovereign({30: (courier,)})})

    [caught] = _events(results, "spy_caught")
    assert caught.payload["courier"] is True and caught.subject_id == party.traveller_ids[0]
    assert state.civilizations[home].spy_reports == ()
    [lost] = [
        item
        for item in state.journeys
        if item.kind is JourneyKind.COURIER and item.outcome is JourneyOutcome.CAUGHT
    ]
    assert lost.sender_civilization_id == home
    validate_world(state)
