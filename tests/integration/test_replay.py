from pathlib import Path

from sovereign_world.config import RunManifest, WorldConfig
from sovereign_world.engine import advance_day
from sovereign_world.persistence import WorldStore
from sovereign_world.replay import replay_run, verify_run
from sovereign_world.rng import StableRng
from sovereign_world.scripted import BaselineSovereign
from sovereign_world.state import build_initial_state, state_hash


def test_replay_matches_recorded_hashes(tmp_path: Path) -> None:
    config = WorldConfig(seed=44, width=48, height=48)
    manifest = RunManifest.new(config=config, engine_version="0.1.0")
    state = build_initial_state(manifest)
    store = WorldStore.create(tmp_path, manifest, state)
    expected = {0: state_hash(state)}
    rng = StableRng(config.seed)
    for _ in range(5):
        result = advance_day(state, rng)
        state = result.state
        store.append_transition(state, result.events)
        expected[state.day] = state_hash(state)
        if state.day in {3, 5}:
            store.save_checkpoint(state)

    replayed = replay_run(store, target_day=5)
    verification = verify_run(store)

    assert state_hash(replayed) == expected[5]
    assert verification.verified_through_day == 5
    assert verification.state_hash == expected[5]


def test_replay_preserves_private_exploration_history(tmp_path: Path) -> None:
    config = WorldConfig(seed=44, width=48, height=48)
    manifest = RunManifest.new(config=config, engine_version="0.1.0")
    state = build_initial_state(manifest)
    store = WorldStore.create(tmp_path, manifest, state)
    sovereigns = {civilization_id: BaselineSovereign() for civilization_id in state.civilizations}
    rng = StableRng(config.seed)
    for _ in range(8):
        transition = advance_day(state, rng, sovereigns=sovereigns)
        state = transition.state
        store.append_transition(state, transition.events)

    replayed = replay_run(store)

    assert state_hash(replayed) == state_hash(state)
    assert all(civilization.observations for civilization in replayed.civilizations.values())

