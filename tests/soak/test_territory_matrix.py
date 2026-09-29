"""Real consecutive days of derived territory: anchored settlements, thresholds, no flicker."""

from collections import Counter

import pytest

from sovereign_world.config import RunManifest, WorldConfig
from sovereign_world.engine import advance_day
from sovereign_world.hexmap import HexCoord
from sovereign_world.rng import StableRng
from sovereign_world.scripted import BaselineSovereign
from sovereign_world.state import WorldState, build_initial_state, state_hash
from sovereign_world.territory import LOSE_THRESHOLD, TAKE_THRESHOLD

DAYS = 90


def _simulate(initial: WorldState) -> tuple[WorldState, list[str]]:
    state = initial.model_copy(deep=True)
    rng = StableRng(state.config.seed)
    sovereigns = {civilization_id: BaselineSovereign() for civilization_id in state.civilizations}
    changes: Counter[HexCoord] = Counter()
    kinds: list[str] = []
    for _ in range(DAYS):
        transition = advance_day(state, rng, sovereigns=sovereigns)
        state = transition.state
        owners = state.territory.owner_of()
        held = state.territory.held_by_tile()
        settlement_tiles = {
            settlement.tile: civilization.civilization_id
            for civilization in state.civilizations.values()
            for settlement in civilization.settlements
        }
        for tile, civilization_id in settlement_tiles.items():
            inhabited = any(
                person.alive and person.location == tile
                for person in state.civilizations[civilization_id].population.people.values()
            )
            if inhabited:
                assert owners.get(tile) == civilization_id, "an inhabited settlement is its own"
        for event in transition.events.events:
            kinds.append(event.kind)
            if event.kind not in {"control_gained", "control_lost"}:
                continue
            tile = HexCoord(int(event.payload["q"]), int(event.payload["r"]))
            changes[tile] += 1
            value = held.get(tile, {}).get(str(event.actor_id), 0)
            if event.kind == "control_gained" and tile not in settlement_tiles:
                assert value >= TAKE_THRESHOLD or "from" in event.payload
            if event.kind == "control_lost" and tile not in settlement_tiles:
                assert value < LOSE_THRESHOLD or owners.get(tile) is not None
    assert max(changes.values(), default=0) <= 2, "a tile flickered between owners"
    return state, kinds


@pytest.mark.soak
@pytest.mark.parametrize("seed", range(16))
def test_derived_territory_is_stable_and_deterministic(seed: int) -> None:
    initial = build_initial_state(
        RunManifest.new(WorldConfig(seed=seed, width=24, height=24), engine_version="0.1.0")
    )

    final, kinds = _simulate(initial)
    rerun, rerun_kinds = _simulate(initial)

    assert "control_gained" in kinds
    assert all(
        any(owner.civilization_id == civilization_id for owner in final.territory.owners)
        for civilization_id in final.civilizations
    )
    assert state_hash(rerun) == state_hash(final)
    assert rerun_kinds == kinds
