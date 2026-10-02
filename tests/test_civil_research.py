"""Civil research (rules version 2): writing, irrigation, herbal care, fishing, surveying and
organised logistics."""

from dataclasses import replace

from logistics_helpers import OneShotSovereign, envelope, treaty_world
from test_rank_gates import _open, _rank
from test_research import _grant, _research, _run

import sovereign_world.engine as engine
from sovereign_world.capabilities import CapabilityId
from sovereign_world.commands import validate_envelope
from sovereign_world.hexmap import Terrain
from sovereign_world.institutions import InstitutionKind
from sovereign_world.ranks import RealmRank, SettlementRank
from sovereign_world.research import CIVIL_TOPICS, MILITARY_TOPICS, TOPICS
from sovereign_world.state import WorldState


def _codes(state: WorldState, civilization_id, *orders) -> list[str]:
    errors = validate_envelope(envelope(state, civilization_id, *orders), state).errors
    return [error.code for error in errors]


def _dry(state: WorldState, home) -> None:
    """No river, wetland or open water anywhere near the civilization's fields."""
    known = set(state.civilizations[home].known_tiles)
    near = known | {
        other for tile in known for other in tile.neighbors() if state.world_map.contains(other)
    }
    state.world_map = replace(
        state.world_map,
        tiles=tuple(
            replace(
                tile,
                terrain=Terrain.GRASSLAND,
                river=False,
                cover=(9_000, 1_000, 0, 0, 0, 0, 0) if tile.cover else (),
            )
            if tile.coord in near
            else tile
            for tile in state.world_map.tiles
        ),
        rivers=(),
    )


def test_the_civil_topics_and_their_order() -> None:
    assert set(CIVIL_TOPICS).isdisjoint(MILITARY_TOPICS)
    assert TOPICS == {**MILITARY_TOPICS, **CIVIL_TOPICS}
    assert CIVIL_TOPICS[CapabilityId.SURVEYING].requires == (CapabilityId.WRITING,)
    assert set(CIVIL_TOPICS[CapabilityId.ORGANIZED_LOGISTICS].requires) == {
        CapabilityId.WRITING,
        CapabilityId.SURVEYING,
    }


def test_older_worlds_have_no_civil_research() -> None:
    state, home, _, _ = treaty_world(distance=4)
    assert _codes(state, home, _research(state, home, CapabilityId.HERBAL_CARE, 2, 30)) == [
        "invalid_research"
    ]


def test_writing_needs_a_small_town() -> None:
    state, home, _, _ = treaty_world(distance=4, rules_version=2)
    order = _research(state, home, CapabilityId.WRITING, 2, 30)
    assert _codes(state, home, order) == ["rank_required"]
    _rank(state, home, SettlementRank.SMALL_TOWN)
    assert _codes(state, home, order) == []
    assert _codes(state, home, _research(state, home, CapabilityId.SURVEYING, 2, 30)) == [
        "invalid_research"
    ], "surveying needs writing first"


def test_irrigation_needs_cultivation_and_a_river_or_wetland_field() -> None:
    state, home, _, _ = treaty_world(distance=4, rules_version=2)
    _dry(state, home)
    order = _research(state, home, CapabilityId.IRRIGATION, 2, 30)
    known = {item.capability for item in state.civilizations[home].capabilities}
    if CapabilityId.CULTIVATION not in known:
        assert _codes(state, home, order) == ["invalid_research"]
        _grant(state, home, CapabilityId.CULTIVATION)
    assert _codes(state, home, order) == ["invalid_research"], "a dry land"
    field = state.civilizations[home].known_tiles[0]
    state.world_map = replace(
        state.world_map,
        tiles=tuple(
            replace(tile, cover=(7_000, 1_000, 0, 2_000, 0, 0, 0)) if tile.coord == field else tile
            for tile in state.world_map.tiles
        ),
    )
    assert _codes(state, home, order) == []


def test_fishing_needs_water_beside_a_settlement() -> None:
    state, home, _, _ = treaty_world(distance=4, rules_version=2)
    _dry(state, home)
    order = _research(state, home, CapabilityId.FISHING, 2, 30)
    assert _codes(state, home, order) == ["invalid_research"]
    capital = state.civilizations[home].start_center
    shore = next(tile for tile in capital.neighbors() if state.world_map.contains(tile))
    state.world_map = replace(
        state.world_map,
        tiles=tuple(
            replace(tile, terrain=Terrain.WATER, cover=()) if tile.coord == shore else tile
            for tile in state.world_map.tiles
        ),
    )
    assert _codes(state, home, order) == []


def test_herbal_care_is_discovered_and_opens_the_healers_house() -> None:
    state, home, _, _ = treaty_world(distance=4, rules_version=2)
    order = _research(state, home, CapabilityId.HERBAL_CARE, 4, 60)
    state, _ = _run(state, 52, {home: OneShotSovereign(order)})
    known = {item.capability for item in state.civilizations[home].capabilities}
    assert CapabilityId.HERBAL_CARE in known, "4 scholars, 1 point a day each: 200 in 50 days"


def test_a_kingdom_without_a_school_or_archive_learns_civil_arts_at_half_pace(
    monkeypatch,
) -> None:
    # Hold the realm's rank as set: the test world could not earn it.
    monkeypatch.setattr(engine, "_advance_ranks", lambda state: [])

    def points(*, kingdom: bool, school: bool) -> int:
        state, home, _, _ = treaty_world(distance=4, rules_version=2)
        if kingdom:
            state.civilizations[home].realm_rank_reached = RealmRank.KINGDOM
        if school:
            _open(
                state,
                home,
                InstitutionKind.SCHOOL,
                state.civilizations[home].population.living_ids[9],
            )
        order = _research(state, home, CapabilityId.HERBAL_CARE, 2, 10)
        state, _ = _run(state, 10, {home: OneShotSovereign(order)})
        return state.civilizations[home].research_points[CapabilityId.HERBAL_CARE]

    full = points(kingdom=False, school=False)
    assert full == 20
    assert points(kingdom=True, school=False) == 10
    assert points(kingdom=True, school=True) == 20
