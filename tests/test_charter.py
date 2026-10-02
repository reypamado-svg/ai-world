"""council-3: sovereigns are told how the land is crossed, in words made from the rules."""

import pytest

from sovereign_world.bridges import BRIDGE_LABOUR, BRIDGE_MATERIALS
from sovereign_world.commands import build_council_report
from sovereign_world.config import RunManifest, SovereignConfig, WorldConfig
from sovereign_world.gateway.factory import build_sovereigns
from sovereign_world.gateway.prompt import PROMPT_VERSION, charter, travel_rule
from sovereign_world.hexmap import Terrain
from sovereign_world.ids import EntityId
from sovereign_world.state import build_initial_state
from sovereign_world.travel import CROSSING_COST, DAY, ENTRY_COST, TILE_SPACING_M


def _report():
    state = build_initial_state(
        RunManifest.new(WorldConfig(seed=21, width=24, height=24), engine_version="0.2.0")
    )
    return build_council_report(state, sorted(state.civilizations)[0])


def _days(tenths: int) -> str:
    days = tenths / DAY
    return f"{days:g} day" + ("" if days == 1 else "s")


def test_the_charter_states_the_travel_rules_from_the_tables() -> None:
    rule = travel_rule()

    assert f"{TILE_SPACING_M // 1000} km apart" in rule
    for terrain, cost in ENTRY_COST.items():
        if cost is None:
            assert f"{terrain.value.capitalize()} cannot be entered on foot" in rule
        else:
            assert f"{_days(cost)} for " in rule
            assert terrain.value in rule
    assert ENTRY_COST[Terrain.GRASSLAND] == DAY, "a grassland tile is one day's walk"
    assert f"{_days(CROSSING_COST['stream'] or 0)} for a stream" in rule
    assert f"{_days(CROSSING_COST['river'] or 0)} for a river" in rule
    assert "a deep river cannot be crossed on foot" in rule
    for depth in ("river", "deep"):
        assert f"{BRIDGE_LABOUR[depth]} person-days" in rule
        for resource, quantity in BRIDGE_MATERIALS[depth].items():
            assert f"{quantity} {resource.value}" in rule
    assert rule in charter(_report())


def test_new_sovereigns_use_this_prompt_version() -> None:
    assert PROMPT_VERSION == "council-3"
    assert SovereignConfig().prompt_version == PROMPT_VERSION


def test_a_run_recorded_with_council_2_keeps_its_hash_and_must_fork() -> None:
    old = {
        "run_id": "6f1f7c32-3f0b-4f1e-9b55-2a2f1d1c0e13",
        "engine_version": "0.1.0",
        "config": {"seed": 21, "width": 24, "height": 24},
        "sovereigns": {
            "civilization:0001": {"provider": "openai", "model": "m", "prompt_version": "council-2"}
        },
    }
    manifest = RunManifest.model_validate(old)
    reloaded = RunManifest.model_validate_json(manifest.model_dump_json())

    assert reloaded.sovereigns["civilization:0001"].prompt_version == "council-2"
    assert reloaded.content_hash() == manifest.content_hash()
    with pytest.raises(ValueError, match="fork the run"):
        build_sovereigns(reloaded, (EntityId("civilization:0001"),))
