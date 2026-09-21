from sovereign_world.commands import Decree, DecreeKind, build_council_report
from sovereign_world.config import RunManifest, WorldConfig
from sovereign_world.scripted import BaselineSovereign
from sovereign_world.state import build_initial_state


def test_baseline_sovereign_prioritizes_food_and_respects_command_limit() -> None:
    config = WorldConfig(seed=21, width=48, height=48)
    state = build_initial_state(RunManifest.new(config=config, engine_version="0.1.0"))
    civilization_id = sorted(state.civilizations)[0]
    report = build_council_report(state, civilization_id)

    envelope = BaselineSovereign().decide(report)

    assert len(envelope.commands) <= 8
    assert isinstance(envelope.commands[0], Decree)
    assert envelope.commands[0].kind is DecreeKind.FOOD_RESERVE_TARGET
    assert envelope.civilization_id == civilization_id

