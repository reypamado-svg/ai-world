from itertools import combinations

import pytest

from sovereign_world.config import RunManifest, WorldConfig
from sovereign_world.engine import advance_day
from sovereign_world.rng import StableRng
from sovereign_world.scripted import BaselineSovereign
from sovereign_world.state import build_initial_state, state_hash
from sovereign_world.worldgen import WorldGenerationError, generate_world


def test_generated_world_repeats_for_same_seed() -> None:
    config = WorldConfig(seed=9, width=48, height=48)

    first = generate_world(config, StableRng(config.seed))
    second = generate_world(config, StableRng(config.seed))

    assert first.world_map.content_hash() == second.world_map.content_hash()
    assert first.starts == second.starts


def test_generated_starts_are_viable_and_separated() -> None:
    config = WorldConfig(seed=9, width=48, height=48)

    generated = generate_world(config, StableRng(config.seed))

    assert len(generated.starts) == 4
    assert all(start.viability.has_water for start in generated.starts)
    assert all(start.viability.food_units_per_day >= 64 for start in generated.starts)
    assert all(start.viability.construction_units >= 64 for start in generated.starts)
    assert all(start.viability.strength for start in generated.starts)
    assert all(start.viability.vulnerability for start in generated.starts)
    for left, right in combinations(generated.starts, 2):
        assert left.center.distance(right.center) >= 12


def test_each_start_has_one_known_regional_capability() -> None:
    config = WorldConfig(seed=9, width=48, height=48)
    manifest = RunManifest.new(config=config, engine_version="0.1.0")

    state = build_initial_state(manifest)

    assert all(civilization.capabilities for civilization in state.civilizations.values())


def test_generation_failure_is_bounded_and_reports_seed() -> None:
    config = WorldConfig(seed=77, width=24, height=24)

    with pytest.raises(WorldGenerationError) as captured:
        generate_world(
            config,
            StableRng(config.seed),
            max_attempts=3,
            min_start_distance=50,
        )

    assert captured.value.seed == 77
    assert captured.value.attempts == 3
    assert "separation" in captured.value.failures


@pytest.mark.parametrize(("size", "count"), [(24, 2), (24, 3), (48, 2), (48, 3)])
def test_two_and_three_civilizations_generate_and_run_deterministically(
    size: int, count: int
) -> None:
    config = WorldConfig(seed=9, width=size, height=size, civilizations=count)
    manifest = RunManifest.new(config=config, engine_version="test")

    def daily() -> list[str]:
        state = build_initial_state(manifest)
        assert len(state.civilizations) == count
        sovereigns = {
            civilization_id: BaselineSovereign() for civilization_id in state.civilizations
        }
        rng = StableRng(config.seed)
        hashes = [state_hash(state)]
        for _ in range(60):
            state = advance_day(state, rng, sovereigns=sovereigns).state
            hashes.append(state_hash(state))
        return hashes

    assert daily() == daily()
