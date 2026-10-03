"""Working deposits and quarries (rules version 2): parties go out, work for days, and carry
home what they can bear; sites run out."""

import pytest
from logistics_helpers import OneShotSovereign, envelope

from sovereign_world.commands import (
    DirectOrder,
    DirectOrderKind,
    build_council_report,
    validate_envelope,
)
from sovereign_world.config import RunManifest, WorldConfig
from sovereign_world.engine import TransitionResult, advance_day
from sovereign_world.ids import EntityId
from sovereign_world.logistics import Journey, JourneyKind
from sovereign_world.resources import Resource
from sovereign_world.rng import StableRng
from sovereign_world.sites import Site, SiteKind
from sovereign_world.state import WorldState, build_initial_state, state_hash, validate_world
from sovereign_world.travel import way_to

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


def _own_site(state: WorldState, home, kind: SiteKind) -> Site:
    centre = state.civilizations[home].start_center
    known = set(state.civilizations[home].known_tiles)
    return min(
        (site for site in state.sites if site.kind is kind and site.tile in known),
        key=lambda site: site.tile.distance(centre),
    )


def _extract(
    state: WorldState,
    home,
    site: Site,
    *,
    workers: int = 2,
    days: int = 3,
    journey: str = "journey:extract",
):
    route = way_to(state.world_map, state.civilizations[home].start_center, frozenset({site.tile}))
    assert route is not None
    return DirectOrder(
        command_id="extract",
        kind=DirectOrderKind.EXTRACT,
        journey_id=EntityId(journey),
        traveller_ids=state.civilizations[home].population.living_ids[:workers],
        route=route,
        work_days=days,
    )


def _codes(state: WorldState, home, order) -> list[str]:
    return [e.code for e in validate_envelope(envelope(state, home, order), state).errors]


def test_a_party_works_a_quarry_and_carries_the_stone_home() -> None:
    state = _state()
    home = sorted(state.civilizations)[0]
    quarry = _own_site(state, home, SiteKind.QUARRY)
    order = _extract(state, home, quarry)
    assert _codes(state, home, order) == []
    stone = state.civilizations[home].inventory.quantities[Resource.STONE]

    state, results = _run(state, 4, {home: OneShotSovereign(order)})
    [journey] = [item for item in state.journeys if item.kind is JourneyKind.EXTRACTION]
    assert build_council_report(state, home).extractions == (journey,)
    state, more = _run(state, 30)
    results += more
    worked = _events(results, "site_worked")
    assert [event.payload["units"] for event in worked] == [8, 8, 8], "2 workers, 4 stone each"
    [left] = _events(results, "extractors_left_site")
    assert left.payload == {"reason": "done", "units": 24}
    assert _events(results, "extractors_returned")
    assert state.civilizations[home].inventory.quantities[Resource.STONE] == stone + 24
    [site] = [item for item in state.sites if item.site_id == quarry.site_id]
    assert site.remaining == quarry.richness - 24 and site.opened_day is not None
    assert not [item for item in state.journeys if item.active]
    validate_world(state)


def test_a_full_pack_or_a_spent_site_sends_the_party_home_early() -> None:
    state = _state()
    home = sorted(state.civilizations)[0]
    quarry = _own_site(state, home, SiteKind.QUARRY)
    state.sites = tuple(
        item.model_copy(update={"remaining": 10}) if item.site_id == quarry.site_id else item
        for item in state.sites
    )
    order = _extract(state, home, quarry, workers=4, days=10)
    state, results = _run(state, 40, {home: OneShotSovereign(order)})
    [left] = _events(results, "extractors_left_site")
    assert left.payload["reason"] == "spent" and left.payload["units"] == 10
    assert len(_events(results, "site_exhausted")) == 1
    [site] = [item for item in state.sites if item.site_id == quarry.site_id]
    assert site.remaining == 0 and site.spent_day is not None

    # The site is spent: no one is sent to it again.
    again = _extract(state, home, quarry, journey="journey:again")
    assert _codes(state, home, again) == ["invalid_destination"]


def test_extraction_orders_are_checked() -> None:
    state = _state()
    home = sorted(state.civilizations)[0]
    quarry = _own_site(state, home, SiteKind.QUARRY)
    ruin = next(site for site in state.sites if site.kind is SiteKind.ANCIENT_RUIN)
    with pytest.raises(ValueError):
        _extract(state, home, quarry, days=61)
    assert _codes(state, home, _extract(state, home, quarry, days=0)) == ["invalid_stay"]
    if ruin.tile in set(state.civilizations[home].known_tiles):
        assert _codes(state, home, _extract(state, home, ruin)) == ["invalid_destination"]
    # Provisions for a long stay leave no room in the packs.
    assert _codes(state, home, _extract(state, home, quarry, workers=1, days=60)) == [
        "cargo_over_capacity"
    ]
    old = _state(1)
    old_home = sorted(old.civilizations)[0]
    assert _codes(
        old, old_home, _extract(old, old_home, _own_site(old, old_home, SiteKind.QUARRY))
    ) == ["invalid_journey"]


def test_a_party_whose_workers_all_die_leaves_with_nothing() -> None:
    state = _state()
    home = sorted(state.civilizations)[0]
    quarry = _own_site(state, home, SiteKind.QUARRY)
    order = _extract(state, home, quarry, days=10)
    rng = StableRng(state.config.seed)
    sovereigns = {home: OneShotSovereign(order)}
    for _ in range(40):
        state = advance_day(state, rng, sovereigns=sovereigns).state
        [journey] = [item for item in state.journeys if item.kind is JourneyKind.EXTRACTION]
        if journey.working:
            break
    people = state.civilizations[home].population.people
    for person_id in journey.traveller_ids:
        people[person_id].alive = False
        people[person_id].death_day = state.day
    state, _ = _run(state, 1)
    [lost] = [item for item in state.journeys if item.kind is JourneyKind.EXTRACTION]
    assert not lost.active and not lost.working and lost.outcome.value == "perished"
    validate_world(state)


def test_journeys_without_work_dump_as_before() -> None:
    journey = Journey(
        journey_id=EntityId("journey:spy"),
        kind=JourneyKind.SALVAGE,
        sender_civilization_id=EntityId("civilization:1"),
        recipient_civilization_id=EntityId("civilization:1"),
        traveller_ids=(EntityId("person:1"),),
        route=(_state().civilizations[sorted(_state().civilizations)[0]].start_center,) * 2,
        departed_day=0,
    )
    dumped = journey.model_dump(mode="json")
    assert {"work_days", "days_worked", "working"}.isdisjoint(dumped)
    assert Journey.model_validate(dumped) == journey
    with pytest.raises(ValueError, match="extraction"):
        journey.model_validate({**dumped, "work_days": 3})


def test_a_rules_two_world_with_a_party_out_round_trips() -> None:
    state = _state()
    home = sorted(state.civilizations)[0]
    order = _extract(state, home, _own_site(state, home, SiteKind.QUARRY), days=5)
    state, _ = _run(state, 6, {home: OneShotSovereign(order)})
    again = WorldState.model_validate_json(state.model_dump_json())
    assert state_hash(again) == state_hash(state)
