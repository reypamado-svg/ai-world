"""Territory's faster paths (Phase 5 S6) give exactly what the step-by-step ones do."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st
from parity import SCENARIOS, initial

from sovereign_world.engine import advance_day
from sovereign_world.hexmap import HexCoord, Terrain, edge_key
from sovereign_world.rng import StableRng
from sovereign_world.roads import RoadGrade
from sovereign_world.scripted import BaselineSovereign
from sovereign_world.territory import Territory, _influence_field_stepwise, influence_field

WORLD = initial(SCENARIOS[1]).world_map
LAND = [tile.coord for tile in WORLD.tiles if tile.terrain is not Terrain.WATER]
RIVERS = [edge_key(river.a, river.b) for river in WORLD.rivers]


@settings(max_examples=60, deadline=None)
@given(
    st.lists(st.tuples(st.sampled_from(LAND), st.integers(-10, 400)), min_size=1, max_size=4),
    st.dictionaries(st.sampled_from(LAND), st.sampled_from(list(RoadGrade)), max_size=40),
    st.frozensets(st.sampled_from(RIVERS), max_size=8) if RIVERS else st.just(frozenset()),
)
def test_the_influence_field_is_the_same_tile_for_tile_and_in_order(
    sources: list[tuple[HexCoord, int]], roads: dict, bridges: frozenset
) -> None:
    fast = influence_field(WORLD, sources, roads, bridges)
    slow = _influence_field_stepwise(WORLD, sources, roads, bridges)
    assert list(fast.items()) == list(slow.items())


def test_a_source_off_the_map_is_still_worked_out() -> None:
    off = HexCoord(-50, -50)
    assert influence_field(WORLD, [(off, 80)]) == _influence_field_stepwise(WORLD, [(off, 80)])


def test_each_days_territory_is_canonical_as_if_checked() -> None:
    state = initial(SCENARIOS[1])
    sovereigns = {civilization_id: BaselineSovereign() for civilization_id in state.civilizations}
    rng = StableRng(state.config.seed)
    for _ in range(40):
        state = advance_day(state, rng, sovereigns=sovereigns).state
        territory = state.territory
        assert territory.held, "the run holds land"
        assert Territory.model_validate(territory.model_dump()) == territory
