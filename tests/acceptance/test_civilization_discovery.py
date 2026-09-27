from pathlib import Path

from sovereign_world.commands import build_council_report
from sovereign_world.config import RunManifest, WorldConfig
from sovereign_world.engine import advance_day
from sovereign_world.events import DomainEvent
from sovereign_world.persistence import WorldStore
from sovereign_world.replay import replay_run
from sovereign_world.rng import StableRng
from sovereign_world.scripted import BaselineSovereign
from sovereign_world.state import WorldState, build_initial_state, state_hash


def _run_scripted_world(
    root: Path,
    *,
    days: int,
) -> tuple[WorldState, WorldStore, tuple[DomainEvent, ...]]:
    config = WorldConfig(seed=21, width=48, height=48)
    manifest = RunManifest.new(config=config, engine_version="0.1.0")
    state = build_initial_state(manifest)
    store = WorldStore.create(root, manifest, state)
    sovereigns = {civilization_id: BaselineSovereign() for civilization_id in state.civilizations}
    events: list[DomainEvent] = []
    rng = StableRng(config.seed)
    for _ in range(days):
        transition = advance_day(state, rng, sovereigns=sovereigns)
        state = transition.state
        store.append_transition(state, transition.events)
        events.extend(transition.events.events)
    return state, store, tuple(events)


def test_scripted_society_learns_and_returns_a_private_survey(tmp_path: Path) -> None:
    final_state, store, events = _run_scripted_world(tmp_path, days=180)
    civilization_ids = sorted(final_state.civilizations)
    first_id, second_id = civilization_ids[:2]
    first_report = build_council_report(final_state, first_id)
    private_rival_tile = next(
        tile
        for tile in final_state.civilizations[second_id].known_tiles
        if tile not in final_state.civilizations[first_id].known_tiles
    )

    assert all(civilization.capabilities for civilization in final_state.civilizations.values())
    assert any(event.kind == "tile_observed" for event in events)
    assert private_rival_tile not in first_report.known_tiles
    assert state_hash(replay_run(store)) == state_hash(final_state)
