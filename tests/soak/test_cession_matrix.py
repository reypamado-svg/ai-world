"""Seeded peace settlements that cede a colony with its people, with daily invariants."""

import pytest
from logistics_helpers import treaty_world
from scenario_helpers import Vanquished, Victor

from sovereign_world.engine import advance_day
from sovereign_world.hexmap import HexCoord, Terrain
from sovereign_world.ids import EntityId
from sovereign_world.resources import Inventory, Resource
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState, state_hash
from sovereign_world.territory import Settlement

DAYS = 150


def _prepare(seed: int) -> tuple[WorldState, EntityId, EntityId, tuple[HexCoord, ...], EntityId]:
    state, first, second, route = treaty_world(seed=seed, distance=4 + seed % 3)
    loser = state.civilizations[second]
    # A colony off to the side of the loser's capital, with a handful of settlers.
    tile = next(
        coord
        for coord in sorted(
            HexCoord(route[-1].q + dq, route[-1].r + dr)
            for dq in range(-4, 5)
            for dr in range(-4, 5)
        )
        if coord.distance(route[-1]) == 3
        and coord not in route
        and state.world_map.contains(coord)
        and state.world_map.tile(coord).terrain is not Terrain.WATER
    )
    colony = Settlement(
        settlement_id=EntityId(f"settlement:{second.rsplit(':', 1)[-1]}-0002"),
        civilization_id=second,
        tile=tile,
        founded_day=0,
    )
    loser.settlements = (*loser.settlements, colony)
    for person_id in loser.population.living_ids[-(4 + seed % 4) :]:
        loser.population.people[person_id].location = tile
    loser.stores = {
        colony.settlement_id: Inventory(capacity=2_000, quantities={Resource.FOOD: 400})
    }
    return state, first, second, route, colony.settlement_id


def _simulate(initial: WorldState, first, second, route, colony):
    state = initial.model_copy(deep=True)
    sovereigns = {
        first: Victor(second, route),
        second: Vanquished(first, tuple(reversed(route)), colony),
    }
    rng = StableRng(state.config.seed)
    kinds: list[str] = []
    for _ in range(DAYS):
        transition = advance_day(state, rng, sovereigns=sovereigns)
        state = transition.state
        kinds.extend(event.kind for event in transition.events.events)
        seen: set[EntityId] = set()
        for civilization_id, civilization in state.civilizations.items():
            for person_id, person in civilization.population.people.items():
                assert person_id not in seen, "a person belongs to one civilization"
                seen.add(person_id)
                assert person.civilization_id == civilization_id
                if person.allegiances:
                    assert person.allegiances[-1].to_civilization_id == civilization_id
    return state, kinds


@pytest.mark.soak
@pytest.mark.parametrize("seed", range(6))
def test_seeded_cessions_move_people_whole_and_replay_exactly(seed: int) -> None:
    initial, first, second, route, colony = _prepare(seed)

    final, kinds = _simulate(initial, first, second, route, colony)
    rerun, rerun_kinds = _simulate(initial, first, second, route, colony)

    assert "peace_made" in kinds and "settlement_ceded" in kinds
    assert any(item.settlement_id == colony for item in final.civilizations[first].settlements)
    assert not any(item.settlement_id == colony for item in final.civilizations[second].settlements)
    assert state_hash(rerun) == state_hash(final)
    assert rerun_kinds == kinds
