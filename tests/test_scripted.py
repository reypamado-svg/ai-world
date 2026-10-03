from sovereign_world.commands import (
    Decree,
    DecreeKind,
    DirectOrder,
    DirectOrderKind,
    ProjectKind,
    build_council_report,
)
from sovereign_world.config import RunManifest, WorldConfig
from sovereign_world.scripted import BaselineSovereign
from sovereign_world.state import build_initial_state


def test_baseline_sovereign_prioritizes_food_and_respects_command_limit() -> None:
    config = WorldConfig(seed=21, width=48, height=48)
    state = build_initial_state(
        RunManifest.new(config=config, engine_version="0.1.0", rules_version=1)
    )
    civilization_id = sorted(state.civilizations)[0]
    report = build_council_report(state, civilization_id)

    envelope = BaselineSovereign().decide(report)

    assert len(envelope.commands) <= 8
    assert isinstance(envelope.commands[0], Decree)
    assert envelope.commands[0].kind is DecreeKind.FOOD_RESERVE_TARGET
    assert envelope.civilization_id == civilization_id


def test_under_rules_two_the_baseline_keeps_room_spare_and_raises_a_hall() -> None:
    config = WorldConfig(seed=21, width=48, height=48)
    state = build_initial_state(RunManifest.new(config=config, engine_version="0.1.0"))
    civilization_id = sorted(state.civilizations)[0]
    commands = BaselineSovereign().decide(build_council_report(state, civilization_id)).commands

    assert len(commands) <= 8
    assert commands[0].kind is DecreeKind.FOOD_RESERVE_TARGET
    [housing] = [item for item in commands if item.kind is DecreeKind.HOUSING_POLICY]
    assert isinstance(housing, Decree) and housing.value == 10
    [materials] = [item for item in commands if item.kind is DecreeKind.MATERIALS_RESERVE_TARGET]
    assert isinstance(materials, Decree) and materials.value == 300

    def shelters(commands) -> list[DirectOrder]:
        return [
            item
            for item in commands
            if isinstance(item, DirectOrder) and item.project_kind is ProjectKind.SHELTER
        ]

    # The first day's eight orders go to decrees, the storehouse, the hall and the survey;
    # the housing policy starts the first house.
    assert shelters(commands) == []
    state.day = 30
    later = BaselineSovereign().decide(build_council_report(state, civilization_id)).commands
    [shelter] = shelters(later)
    # 32 people in 35 places: one more house brings the room to 40.
    assert shelter.house_count == 1
    assert shelter.house_grade is not None
    assert any(
        isinstance(item, DirectOrder) and item.kind is DirectOrderKind.FOUND_INSTITUTION
        for item in commands
    )
