from hypothesis import given, settings
from hypothesis import strategies as st

from sovereign_world.config import RunManifest, WorldConfig
from sovereign_world.engine import advance_day
from sovereign_world.rng import StableRng
from sovereign_world.state import build_initial_state


@given(days=st.integers(min_value=1, max_value=12))
@settings(max_examples=12, deadline=None)
def test_engine_preserves_world_invariants(days: int) -> None:
    config = WorldConfig(seed=55, width=48, height=48)
    state = build_initial_state(RunManifest.new(config=config, engine_version="0.1.0"))
    rng = StableRng(config.seed)

    for expected_day in range(1, days + 1):
        state = advance_day(state, rng).state
        assert state.day == expected_day
        for civilization in state.civilizations.values():
            assert all(quantity >= 0 for quantity in civilization.inventory.quantities.values())
            assert all(
                not person.alive or person.death_day is None
                for person in civilization.population.people.values()
            )
            assert set(civilization.population.living_ids).isdisjoint(
                civilization.population.dead_ids
            )

