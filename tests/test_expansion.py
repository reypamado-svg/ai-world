import pytest
from logistics_helpers import OneShotSovereign, clear_journey_id, envelope, linked_world
from pydantic import ValidationError

from sovereign_world.commands import (
    Decree,
    DirectOrder,
    DirectOrderKind,
    build_council_report,
    validate_envelope,
)
from sovereign_world.engine import TransitionResult, advance_day
from sovereign_world.ids import EntityId
from sovereign_world.logistics import JourneyOutcome, NoticeKind
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState, validate_world
from sovereign_world.territory import HeldControl, Territory, TileOwner


def _world():
    _, state, home_id, rival_id, route = linked_world(distance=6)
    return state, home_id, rival_id, route


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


def _kinds(results: list[TransitionResult]) -> list[str]:
    return [event.kind for result in results for event in result.events.events]


def _order(state, civilization_id, kind, route, *, people=slice(-4, None), journey="journey:x"):
    return DirectOrder(
        command_id=f"order:{journey}",
        kind=kind,
        journey_id=EntityId(journey),
        traveller_ids=state.civilizations[civilization_id].population.living_ids[people],
        route=route,
    )


def test_settlers_found_a_settlement_that_grows_its_own_land() -> None:
    state, home, _, route = _world()
    found = _order(
        state, home, DirectOrderKind.FOUND_SETTLEMENT, route[:4], journey=clear_journey_id("found")
    )
    state, results = _run(state, 4, {home: OneShotSovereign(found)})

    assert "settlers_dispatched" in _kinds(results)
    assert "settlement_founded" in _kinds(results)
    colony = next(item for item in state.civilizations[home].settlements if not item.capital)
    assert colony.tile == route[3]
    assert build_council_report(state, home).settlements[-1] == colony

    state, _ = _run(state, 16)
    owners = state.territory.owner_of()
    assert owners[route[3]] == home, "an inhabited settlement anchors its tile"
    beyond = [tile for tile in state.world_map.neighbors(route[3]) if tile not in route]
    assert any(owners.get(tile) == home for tile in beyond), "the colony projects its own land"
    validate_world(state)


def test_founding_rules_apply_only_to_what_the_civilization_knows() -> None:
    state, home, rival, route = _world()
    too_close = _order(state, home, DirectOrderKind.FOUND_SETTLEMENT, route[:3], journey="j:close")
    near_rival = _order(state, home, DirectOrderKind.FOUND_SETTLEMENT, route[:5], journey="j:near")
    from_nowhere = _order(
        state, home, DirectOrderKind.FOUND_SETTLEMENT, route[1:5], journey="j:away"
    )
    for order, code in (
        (too_close, "invalid_destination"),
        (near_rival, "invalid_destination"),
        (from_nowhere, "invalid_route"),
    ):
        errors = validate_envelope(envelope(state, home, order), state).errors
        assert [error.code for error in errors] == [code], order.journey_id

    fine = _order(state, home, DirectOrderKind.FOUND_SETTLEMENT, route[:4], journey="j:fine")
    assert validate_envelope(envelope(state, home, fine), state).errors == ()

    civilization = state.civilizations[home]
    civilization.observations = tuple(
        observation.model_copy(update={"observed_owner": rival})
        if observation.tile == route[3]
        else observation
        for observation in civilization.observations
    )
    errors = validate_envelope(envelope(state, home, fine), state).errors
    assert [error.code for error in errors] == ["invalid_destination"], "seen as foreign land"


def test_settlers_turn_back_when_the_site_is_taken_before_they_arrive() -> None:
    state, home, rival, route = _world()
    state.territory = Territory(
        held=(HeldControl(tile=route[3], civilization_id=rival, value=60),),
        owners=(TileOwner(tile=route[3], civilization_id=rival, since_day=0),),
    )
    order = _order(
        state, home, DirectOrderKind.FOUND_SETTLEMENT, route[:4], journey=clear_journey_id("late")
    )
    assert validate_envelope(envelope(state, home, order), state).errors == (), (
        "the civilization has not seen who owns the site"
    )

    state, results = _run(state, 8, {home: OneShotSovereign(order)})

    kinds = _kinds(results)
    assert "founding_failed" in kinds
    assert "settlement_founded" not in kinds
    assert len(state.civilizations[home].settlements) == 1
    [journey] = state.journeys
    assert journey.outcome is JourneyOutcome.FAILED
    returned = next(
        item
        for item in state.civilizations[home].logistics_notices
        if item.kind is NoticeKind.PARTY_RETURNED
    )
    assert returned.reported_outcome is JourneyOutcome.FAILED
    people = state.civilizations[home].population.people
    assert all(people[person_id].location == route[0] for person_id in journey.traveller_ids)


def test_a_garrison_holds_land_and_is_recalled_home() -> None:
    state, home, _, route = _world()
    station = _order(
        state, home, DirectOrderKind.STATION_GARRISON, route[:3], journey=clear_journey_id("post")
    )
    state, results = _run(state, 30, {home: OneShotSovereign(station)})
    assert "garrison_stationed" in _kinds(results)
    [garrison] = state.civilizations[home].garrisons
    assert state.territory.owner_of().get(route[2]) == home, "the garrison holds its tile"
    assert garrison.tile == route[2]
    assert build_council_report(state, home).garrisons == (garrison,)

    members = garrison.member_ids
    busy = _order(state, home, DirectOrderKind.STATION_GARRISON, route[2:4], journey="j:again")
    busy = busy.model_copy(update={"traveller_ids": members})
    assert [
        error.code for error in validate_envelope(envelope(state, home, busy), state).errors
    ] == ["traveller_unavailable"]

    recall = DirectOrder(
        command_id="recall",
        kind=DirectOrderKind.RELOCATE_GROUP,
        journey_id=EntityId(clear_journey_id("home", start_day=30)),
        traveller_ids=members,
        route=tuple(reversed(route[:3])),
    )
    assert validate_envelope(envelope(state, home, recall), state).errors == ()
    state, results = _run(state, 4, {home: OneShotSovereign(recall)})

    kinds = _kinds(results)
    assert "garrison_disbanded" in kinds
    assert "group_relocated" in kinds
    assert state.civilizations[home].garrisons == ()
    people = state.civilizations[home].population.people
    assert all(people[person_id].location == route[0] for person_id in members)


def test_a_garrison_whose_members_all_die_is_disbanded() -> None:
    state, home, _, route = _world()
    station = _order(
        state, home, DirectOrderKind.STATION_GARRISON, route[:3], journey=clear_journey_id("doom")
    )
    state, _ = _run(state, 3, {home: OneShotSovereign(station)})
    [garrison] = state.civilizations[home].garrisons
    for person_id in garrison.member_ids:
        person = state.civilizations[home].population.people[person_id]
        person.alive = False
        person.death_day = state.day

    state, results = _run(state, 1)

    assert "garrison_disbanded" in _kinds(results)
    assert state.civilizations[home].garrisons == ()


def test_the_commands_that_did_nothing_are_gone() -> None:
    with pytest.raises(ValidationError):
        DirectOrder(command_id="survey", kind="request_survey")
    with pytest.raises(ValidationError):
        Decree(command_id="radius", kind="settlement_radius", value=3)
