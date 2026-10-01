import pytest
from logistics_helpers import OneShotSovereign, clear_journey_id, envelope, treaty_world

import sovereign_world.war as war_module
from sovereign_world.commands import DirectOrder, DirectOrderKind, validate_envelope
from sovereign_world.diplomacy import (
    DiplomaticMessage,
    PeaceTerms,
    TreatyEndKind,
    TreatyKind,
    TreatyOffer,
)
from sovereign_world.engine import TransitionResult, advance_day
from sovereign_world.ids import EntityId
from sovereign_world.logistics import JourneyKind, JourneyPhase
from sovereign_world.resources import Resource
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState, validate_world
from sovereign_world.war import OccupationEnd, SiegeEnd, WarObjective


@pytest.fixture
def certain_capture(monkeypatch):
    monkeypatch.setattr(war_module, "CAPTURE_BP", 10_000)


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


def _war_party(
    state, home, rival, route, *, fighters, objective=WarObjective.RAID, name="march", **extra
):
    return DirectOrder(
        command_id="march",
        kind=DirectOrderKind.SEND_WAR_PARTY,
        journey_id=EntityId(clear_journey_id(name, start_day=30, days=12)),
        recipient_civilization_id=rival,
        traveller_ids=tuple(
            person_id
            for person_id in state.civilizations[home].population.living_ids
            if state.civilizations[home].population.people[person_id].captive_of is None
        )[:fighters],
        route=route,
        war_objective=objective,
        **extra,
    )


def _make_peace(state: WorldState, proposer: EntityId, acceptor: EntityId, terms: PeaceTerms):
    """Put an accepted peace offer on the road back to its proposer; it lands in a day."""
    offer = TreatyOffer(
        offer_id=EntityId("treaty:peace"),
        proposer_civilization_id=proposer,
        recipient_civilization_id=acceptor,
        kind=TreatyKind.PEACE,
        proposed_day=state.day,
        terms=terms,
    )
    envoy = next(
        person_id
        for person_id in state.civilizations[acceptor].population.living_ids
        if state.civilizations[acceptor].population.people[person_id].captive_of is None
    )
    origin = state.civilizations[acceptor].population.people[envoy].location
    state.treaty_offers = tuple(
        sorted((*state.treaty_offers, offer), key=lambda item: item.offer_id)
    )
    state.diplomatic_missions = (
        *state.diplomatic_missions,
        DiplomaticMessage(
            message_id=EntityId("message:peace"),
            sender_civilization_id=acceptor,
            recipient_civilization_id=proposer,
            ambassador_id=envoy,
            route=(origin,),
            source_text="We accept.",
            departed_day=state.day,
            acceptance_of=offer.offer_id,
        ),
    )
    results: list[TransitionResult] = []
    while not any(treaty.treaty_id == offer.offer_id for treaty in state.active_treaties):
        assert len(results) < 10, "the peace never took effect"
        state, more = _run(state, 1)
        results.extend(more)
    return state, results


def test_peace_terms_ride_only_on_a_peace_offer_paid_by_a_party() -> None:
    state, home, rival, route = treaty_world(distance=4)
    terms = PeaceTerms(truce_days=60)
    offer = DirectOrder(
        command_id="offer",
        kind=DirectOrderKind.OFFER_TREATY,
        treaty_id=EntityId("treaty:t"),
        treaty_kind=TreatyKind.TRADE,
        peace_terms=terms,
        message_id=EntityId("message:m"),
        ambassador_id=state.civilizations[home].population.living_ids[0],
        recipient_civilization_id=rival,
        message_text="Peace.",
        route=route,
    )
    assert "invalid_treaty" in _codes(state, home, offer)
    stranger = EntityId("civilization:0000000009")
    outsider = terms.model_copy(update={"tribute_payer": stranger})
    peace = offer.model_copy(update={"treaty_kind": TreatyKind.PEACE, "peace_terms": outsider})
    assert "invalid_treaty" in _codes(state, home, peace)
    with pytest.raises(ValueError):
        PeaceTerms(truce_days=60, tribute={Resource.FOOD: 10})


def test_peace_ends_the_war_turns_war_parties_home_and_frees_prisoners_by_its_terms(
    certain_capture,
) -> None:
    state, home, rival, route = treaty_world(distance=4)
    raid = _war_party(state, home, rival, route, fighters=6)
    state, _ = _run(state, 6, {home: OneShotSovereign(raid)})
    [battle] = state.battles
    assert battle.winner_id == rival and battle.captured
    second = _war_party(state, home, rival, route, fighters=12, name="second").model_copy(
        update={"traveller_ids": state.civilizations[home].population.living_ids[20:32]}
    )
    state.day = 30
    state, _ = _run(state, 1, {home: OneShotSovereign(second)})
    [marching] = [
        journey
        for journey in state.journeys
        if journey.active
        and journey.kind is JourneyKind.CAMPAIGN
        and journey.phase is JourneyPhase.OUTBOUND
    ]

    kept = PeaceTerms(truce_days=90, proposer_frees=True, recipient_frees=False)
    state, results = _make_peace(state, home, rival, kept)

    assert not any(war.active for war in state.wars)
    assert _events(results, "peace_made")
    [turned] = [journey for journey in state.journeys if journey.journey_id == marching.journey_id]
    assert turned.phase in {JourneyPhase.RETURNING, JourneyPhase.COMPLETE}
    assert not [
        event for event in _events(results, "captive_freed") if event.payload["reason"] == "peace"
    ], "the rival kept its prisoners, as the terms allowed"

    state, home, rival, route = treaty_world(distance=4)
    state, _ = _run(
        state, 6, {home: OneShotSovereign(_war_party(state, home, rival, route, fighters=6))}
    )
    [battle] = state.battles
    freeing = PeaceTerms(truce_days=90, proposer_frees=False, recipient_frees=True)
    state, results = _make_peace(state, home, rival, freeing)
    freed = _events(results, "captive_freed")
    assert {event.subject_id for event in freed} == set(battle.captured)
    assert all(event.payload["reason"] == "peace" for event in freed)
    validate_world(state)


def test_peace_lifts_a_siege() -> None:
    state, home, rival, route = treaty_world(distance=4)
    siege = _war_party(
        state,
        home,
        rival,
        route[:-1],
        fighters=8,
        objective=WarObjective.BESIEGE,
        extra_provisions=100,
    )
    state, _ = _run(state, 4, {home: OneShotSovereign(siege)})
    assert any(item.active for item in state.sieges)
    state, results = _make_peace(state, home, rival, PeaceTerms(truce_days=30))
    [lifted] = _events(results, "siege_lifted")
    assert lifted.payload["reason"] == SiegeEnd.PEACE.value
    validate_world(state)


def test_peace_ends_an_occupation() -> None:
    state, home, rival, route = treaty_world(distance=4)
    people = state.civilizations[rival].population.people
    for person_id in sorted(people):
        people[person_id].location = route[-1].__class__(route[-1].q, route[-1].r - 3)
    occupy = _war_party(state, home, rival, route, fighters=8, objective=WarObjective.OCCUPY)
    state, _ = _run(state, 4, {home: OneShotSovereign(occupy)})
    assert any(item.active for item in state.occupations)
    state, results = _make_peace(state, home, rival, PeaceTerms(truce_days=30))
    [ended] = _events(results, "occupation_ended")
    assert ended.payload["reason"] == OccupationEnd.PEACE.value


def test_a_truce_forbids_war_parties_until_it_runs_out_or_war_is_declared() -> None:
    state, home, rival, route = treaty_world(distance=4)
    state, _ = _make_peace(state, home, rival, PeaceTerms(truce_days=60))
    raid = _war_party(state, home, rival, route, fighters=6)
    assert _codes(state, home, raid) == ["truce"]
    [peace] = [treaty for treaty in state.active_treaties if treaty.kind is TreatyKind.PEACE]
    assert peace.truce_until is not None
    state.day = peace.truce_until
    assert _codes(state, home, raid) == [], "the truce has run out"

    # Councils meet every thirty days; this one falls inside the truce.
    state.day = 30
    assert state.day < peace.truce_until
    declare = DirectOrder(
        command_id="declare",
        kind=DirectOrderKind.DECLARE_WAR,
        message_id=EntityId("message:war"),
        ambassador_id=state.civilizations[home].population.living_ids[-1],
        recipient_civilization_id=rival,
        message_text="War.",
        route=route,
    )
    state, _ = _run(state, 1, {home: OneShotSovereign(declare)})
    [peace] = [treaty for treaty in state.active_treaties if treaty.kind is TreatyKind.PEACE]
    assert peace.end_kind is TreatyEndKind.BREACHED and peace.ended_by == home
    assert _codes(state, home, raid) == [], "at war again"


def test_tribute_is_shipped_under_the_peace_and_a_missed_payment_breaks_it() -> None:
    state, home, rival, route = treaty_world(distance=4)
    terms = PeaceTerms(
        truce_days=120,
        tribute_payer=home,
        tribute={Resource.TIMBER: 20},
        tribute_payments=2,
    )
    state, _ = _make_peace(state, rival, home, terms)
    [peace] = [treaty for treaty in state.active_treaties if treaty.kind is TreatyKind.PEACE]
    ship = DirectOrder(
        command_id="tribute",
        kind=DirectOrderKind.DISPATCH_SHIPMENT,
        journey_id=EntityId(clear_journey_id("tribute", start_day=state.day, days=12)),
        treaty_id=peace.treaty_id,
        recipient_civilization_id=rival,
        traveller_ids=state.civilizations[home].population.living_ids[:2],
        route=route,
        cargo={Resource.TIMBER: 20},
    )
    assert _codes(state, home, ship) == []
    backwards = ship.model_copy(
        update={
            "recipient_civilization_id": home,
            "traveller_ids": state.civilizations[rival].population.living_ids[:2],
            "route": tuple(reversed(route)),
        }
    )
    assert "no_active_treaty" in _codes(state, rival, backwards), (
        "only the payer ships under the peace"
    )
    state.day = 30
    state, results = _run(state, 10, {home: OneShotSovereign(ship)})
    assert _events(results, "tribute_received")
    [peace] = [treaty for treaty in state.active_treaties if treaty.kind is TreatyKind.PEACE]
    assert peace.tribute_received == {Resource.TIMBER: 20} and peace.in_force

    # The second payment falls due sixty days after the peace and is never sent.
    state.day = peace.activated_day + 60 + 30
    state, results = _run(state, 2)
    [defaulted] = _events(results, "tribute_defaulted")
    assert defaulted.actor_id == str(home)
    [peace] = [treaty for treaty in state.active_treaties if treaty.kind is TreatyKind.PEACE]
    assert peace.end_kind is TreatyEndKind.BREACHED and peace.ended_by == home
