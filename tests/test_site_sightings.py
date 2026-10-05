"""What a council knows of a site's store (rules version 2): only what its people have seen.
Work by others out of its sight changes neither its report nor which orders it may give."""

import pytest
from logistics_helpers import OneShotSovereign
from test_extraction import _codes, _events, _extract, _own_site, _run, _state

from sovereign_world.commands import build_council_report
from sovereign_world.ids import EntityId
from sovereign_world.logistics import JourneyKind, JourneyOutcome
from sovereign_world.sites import SiteKind, SiteSighting
from sovereign_world.state import WorldState, validate_world


def _view(state: WorldState, home: EntityId, site_id: EntityId):  # type: ignore[no-untyped-def]
    [view] = [
        item for item in build_council_report(state, home).known_sites if item.site_id == site_id
    ]
    return view


def _drain(state: WorldState, site_id: EntityId, remaining: int) -> None:
    """Another people's work on the site, out of this one's sight."""
    state.sites = tuple(
        item.model_copy(update={"remaining": remaining}) if item.site_id == site_id else item
        for item in state.sites
    )


def test_unseen_work_changes_neither_the_view_nor_the_orders() -> None:
    state = _state()
    home = sorted(state.civilizations)[0]
    quarry = _own_site(state, home, SiteKind.QUARRY)
    before = _view(state, home, quarry.site_id)
    assert before.remaining == quarry.richness
    for remaining in (10, 0):
        _drain(state, quarry.site_id, remaining)
        assert _view(state, home, quarry.site_id) == before
        assert _codes(state, home, _extract(state, home, quarry)) == []


def test_a_party_sent_to_a_secretly_spent_quarry_comes_home_empty_handed() -> None:
    state = _state()
    home = sorted(state.civilizations)[0]
    quarry = _own_site(state, home, SiteKind.QUARRY)
    _drain(state, quarry.site_id, 0)
    order = _extract(state, home, quarry)
    assert _codes(state, home, order) == []
    state, results = _run(state, 40, {home: OneShotSovereign(order)})
    assert not _events(results, "site_worked")
    [left] = _events(results, "extractors_left_site")
    assert left.payload["reason"] == "spent"
    [journey] = [item for item in state.journeys if item.kind is JourneyKind.EXTRACTION]
    assert journey.outcome is JourneyOutcome.FAILED
    # The party saw it spent: from then on the council knows, and sends no one again.
    view = _view(state, home, quarry.site_id)
    # As it walks away it still sees the site for a day or so.
    assert view.remaining == 0 and view.as_of_day >= left.day
    again = _extract(state, home, quarry, journey="journey:again")
    assert _codes(state, home, again) == ["invalid_destination"]
    validate_world(state)


def test_a_councils_own_work_is_what_it_knows() -> None:
    state = _state()
    home = sorted(state.civilizations)[0]
    quarry = _own_site(state, home, SiteKind.QUARRY)
    state, results = _run(state, 34, {home: OneShotSovereign(_extract(state, home, quarry))})
    [left] = _events(results, "extractors_left_site")
    view = _view(state, home, quarry.site_id)
    assert view.remaining == quarry.richness - 24 and view.as_of_day >= left.day
    assert [item.site_id for item in state.civilizations[home].site_sightings] == [quarry.site_id]
    others = [key for key in sorted(state.civilizations) if key != home]
    assert all(not state.civilizations[key].site_sightings for key in others)


def test_sightings_are_left_out_until_there_are_any_and_checked() -> None:
    state, _ = _run(_state(), 3)
    dumped = state.model_dump(mode="json")
    assert all("site_sightings" not in item for item in dumped["civilizations"].values())
    home = sorted(state.civilizations)[0]
    civilization = state.civilizations[home]
    site = state.sites[0]
    good = SiteSighting(site_id=site.site_id, remaining=0, as_of_day=state.day)
    civilization.site_sightings = (good,)
    validate_world(state)
    assert WorldState.model_validate_json(state.model_dump_json()) == state
    for bad in (
        (good, good),
        (good.model_copy(update={"site_id": EntityId("site:none")}),),
        (good.model_copy(update={"remaining": site.richness + 1}),),
        (good.model_copy(update={"as_of_day": state.day + 1}),),
    ):
        civilization.site_sightings = bad
        with pytest.raises(ValueError):
            validate_world(state)
