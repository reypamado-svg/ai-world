from sovereign_world.config import RunManifest, WorldConfig
from sovereign_world.engine import advance_day
from sovereign_world.resources import Resource
from sovereign_world.rng import StableRng
from sovereign_world.state import build_initial_state, state_hash


def _state(seed: int = 12):
    config = WorldConfig(seed=seed, width=48, height=48)
    manifest = RunManifest.new(config=config, engine_version="0.1.0")
    return build_initial_state(manifest)


def test_advance_day_consumes_food_and_increments_day() -> None:
    state = _state()
    before_food = sum(
        civilization.inventory.quantities[Resource.FOOD]
        for civilization in state.civilizations.values()
    )

    result = advance_day(state, StableRng(state.config.seed))

    after_food = sum(
        civilization.inventory.quantities[Resource.FOOD]
        for civilization in result.state.civilizations.values()
    )
    assert result.state.day == 1
    assert before_food - after_food == 128
    assert state.day == 0


def test_transition_is_independent_of_civilization_dict_insertion_order() -> None:
    first = _state()
    second = first.model_copy(
        update={"civilizations": dict(reversed(tuple(first.civilizations.items())))}
    )

    first_result = advance_day(first, StableRng(first.config.seed))
    second_result = advance_day(second, StableRng(second.config.seed))

    assert state_hash(first) == state_hash(second)
    assert state_hash(first_result.state) == state_hash(second_result.state)
    assert first_result.events.canonical_json() == second_result.events.canonical_json()


def test_hunger_accumulates_when_food_is_missing() -> None:
    state = _state()
    civilization_id = sorted(state.civilizations)[0]
    civilization = state.civilizations[civilization_id]
    empty = civilization.inventory.model_copy(update={"quantities": {}})
    state.civilizations[civilization_id] = civilization.model_copy(update={"inventory": empty})

    result = advance_day(state, StableRng(state.config.seed))

    population = result.state.civilizations[civilization_id].population
    assert all(person.nutrition_debt == 1 for person in population.people.values())
    assert any(event.kind == "food_shortage" for event in result.events.events)

