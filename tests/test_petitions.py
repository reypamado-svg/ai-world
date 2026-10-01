from logistics_helpers import OneShotSovereign, clear_journey_id, envelope, treaty_world

from sovereign_world.commands import (
    DirectOrder,
    DirectOrderKind,
    build_council_report,
    validate_envelope,
)
from sovereign_world.engine import TransitionResult, advance_day
from sovereign_world.ids import EntityId
from sovereign_world.logistics import JourneyOutcome
from sovereign_world.resources import Resource
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState, validate_world


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


def _release(state, home, rival, route, people=3):
    return DirectOrder(
        command_id="release",
        kind=DirectOrderKind.RELEASE_PEOPLE,
        journey_id=EntityId(clear_journey_id("release", days=12)),
        recipient_civilization_id=rival,
        traveller_ids=state.civilizations[home].population.living_ids[-people:],
        route=route,
        extra_provisions=10,
    )


def _waiting(state, home, rival, route):
    order = _release(state, home, rival, route)
    assert _codes(state, home, order) == []
    state, results = _run(state, 8, {home: OneShotSovereign(order)})
    [journey] = [item for item in state.journeys if item.waiting]
    return state, results, journey, order


def _answer(journey, admit: bool) -> DirectOrder:
    return DirectOrder(
        command_id="answer",
        kind=DirectOrderKind.ANSWER_PETITION,
        journey_id=journey.journey_id,
        admit=admit,
    )


def test_released_people_walk_to_a_known_settlement_and_carry_nothing_else() -> None:
    state, home, rival, route = treaty_world(distance=4)
    order = _release(state, home, rival, route)
    assert _codes(state, home, order) == []
    assert _codes(state, home, order.model_copy(update={"route": route[:-1]})) == [
        "invalid_route"
    ], "they go to one of the other civilization's settlements"
    assert "invalid_cargo" in _codes(
        state, home, order.model_copy(update={"cargo": {Resource.FOOD: 5}})
    )
    stranger = EntityId("civilization:0000000009")
    assert "unknown_contact" in _codes(
        state, home, order.model_copy(update={"recipient_civilization_id": stranger})
    )


def test_petitioners_wait_at_the_gate_and_are_seen_by_the_receiving_council() -> None:
    state, home, rival, route = treaty_world(distance=4)
    state, results, journey, _ = _waiting(state, home, rival, route)
    assert _events(results, "people_released") and _events(results, "petition_arrived")
    assert journey.route_index == len(journey.route) - 1
    assert build_council_report(state, rival).petitions == (journey,)
    state, _ = _run(state, 5)
    [still] = [item for item in state.journeys if item.waiting]
    assert still.route_index == journey.route_index, "they wait where they are"
    validate_world(state)


def test_admitted_petitioners_change_allegiance_and_bring_their_food() -> None:
    state, home, rival, route = treaty_world(distance=4)
    state, _, journey, order = _waiting(state, home, rival, route)
    assert _codes(state, home, _answer(journey, True)) == ["invalid_petition"]
    food = state.civilizations[rival].inventory.quantities[Resource.FOOD]
    packed = journey.provisions
    state.day = 30
    state, results = _run(state, 1, {rival: OneShotSovereign(_answer(journey, True))})

    [admitted] = _events(results, "petition_admitted")
    assert admitted.payload["people"] == len(order.traveller_ids)
    rival_people = state.civilizations[rival].population.people
    for person_id in order.traveller_ids:
        assert person_id in rival_people and person_id not in (
            state.civilizations[home].population.people
        )
        assert rival_people[person_id].allegiances[-1].reason == "release"
    eaten = len(state.civilizations[rival].population.living_ids)
    assert state.civilizations[rival].inventory.quantities[Resource.FOOD] == food + packed - eaten
    validate_world(state)


def test_refused_petitioners_walk_home_still_themselves() -> None:
    state, home, rival, route = treaty_world(distance=4)
    state, _, journey, order = _waiting(state, home, rival, route)
    state.day = 30
    state, results = _run(state, 8, {rival: OneShotSovereign(_answer(journey, False))})

    assert _events(results, "petition_refused") and _events(results, "petitioners_returned")
    [back] = [item for item in state.journeys if item.journey_id == journey.journey_id]
    assert back.outcome is JourneyOutcome.REFUSED and not back.active
    home_people = state.civilizations[home].population.people
    assert all(person_id in home_people for person_id in order.traveller_ids)
    assert all(home_people[person_id].location == route[0] for person_id in order.traveller_ids)


def test_petitioners_left_unanswered_through_a_council_are_refused() -> None:
    state, home, rival, route = treaty_world(distance=4)
    state, _, journey, _ = _waiting(state, home, rival, route)
    assert journey.arrived_day is not None and journey.arrived_day < 30
    state.day = 30
    state, results = _run(state, 1)
    assert not _events(results, "petition_refused"), "the council at day 30 may still answer"
    state.day = 60
    state, results = _run(state, 1)
    [refused] = _events(results, "petition_refused")
    assert refused.subject_id == str(journey.journey_id)
