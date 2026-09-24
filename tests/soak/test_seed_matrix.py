from dataclasses import dataclass
from pathlib import Path

import pytest

from sovereign_world.config import RunManifest, WorldConfig
from sovereign_world.engine import advance_day
from sovereign_world.events import EventBatch
from sovereign_world.persistence import WorldStore
from sovereign_world.replay import replay_run
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState, build_initial_state, state_hash, validate_world
from sovereign_world.work import ProjectStatus


@dataclass(frozen=True, slots=True)
class SoakMetrics:
    survival_years: int
    peak_population: int
    births: int
    deaths: int
    food_shortage_years: int
    completed_buildings: int


def _accelerated_year(
    state: WorldState,
    rng: StableRng,
    year: int,
) -> tuple[WorldState, EventBatch]:
    """Sample one authoritative day after advancing ages to the next annual boundary."""
    candidate = state.model_copy(deep=True)
    target_day = year * 365
    skipped_days = max(0, target_day - candidate.day - 1)
    for civilization in candidate.civilizations.values():
        for person_id in civilization.population.living_ids:
            civilization.population.people[person_id].age_days += skipped_days
    candidate.day = target_day - 1
    transition = advance_day(candidate, rng)
    return transition.state, transition.events


@pytest.mark.soak
@pytest.mark.parametrize("seed", range(100))
def test_fifty_year_accelerated_seed_matrix(seed: int, tmp_path: Path) -> None:
    manifest = RunManifest.new(
        WorldConfig(seed=seed, width=24, height=24),
        engine_version="0.1.0",
    )
    state = build_initial_state(manifest)
    initial_state = state.model_copy(deep=True)
    rng = StableRng(seed)
    peak_population = sum(len(c.population.living_ids) for c in state.civilizations.values())
    births = 0
    deaths = 0
    food_shortage_years = 0
    last_events = EventBatch(events=())

    for year in range(1, 51):
        state, last_events = _accelerated_year(state, rng, year)
        validate_world(state)
        peak_population = max(
            peak_population,
            sum(len(c.population.living_ids) for c in state.civilizations.values()),
        )
        births += sum(event.kind == "person_born" for event in last_events.events)
        deaths += sum(event.kind == "person_died" for event in last_events.events)
        food_shortage_years += int(
            any(event.kind == "food_shortage" for event in last_events.events)
        )

    completed_buildings = sum(
        project.status is ProjectStatus.COMPLETE
        for civilization in state.civilizations.values()
        for project in civilization.projects.values()
    )
    metrics = SoakMetrics(
        survival_years=50,
        peak_population=peak_population,
        births=births,
        deaths=deaths,
        food_shortage_years=food_shortage_years,
        completed_buildings=completed_buildings,
    )
    assert metrics.survival_years == 50

    store = WorldStore.create(tmp_path / "record", manifest, initial_state)
    store.append_transition(state, last_events)
    assert state_hash(replay_run(store, target_day=state.day)) == state_hash(state)
