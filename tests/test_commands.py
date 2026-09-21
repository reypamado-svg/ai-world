from sovereign_world.commands import (
    CommandEnvelope,
    Decree,
    DecreeKind,
    DirectOrder,
    DirectOrderKind,
    build_council_report,
    validate_envelope,
)
from sovereign_world.config import RunManifest, WorldConfig
from sovereign_world.ids import EntityId
from sovereign_world.state import build_initial_state


def _state():
    config = WorldConfig(seed=21, width=48, height=48)
    return build_initial_state(RunManifest.new(config=config, engine_version="0.1.0"))


def test_validation_accepts_valid_command_and_rejects_unknown_person() -> None:
    state = _state()
    civilization_id = sorted(state.civilizations)[0]
    envelope = CommandEnvelope(
        schema_version=1,
        civilization_id=civilization_id,
        council_day=0,
        correlation_id="report:0",
        commands=(
            Decree(
                command_id="decree:1",
                kind=DecreeKind.FOOD_RESERVE_TARGET,
                value=90,
                priority=100,
                duration_days=60,
            ),
            DirectOrder(
                command_id="order:1",
                kind=DirectOrderKind.ASSIGN_WORK,
                worker_ids=(EntityId("person:9999999999"),),
                priority=50,
            ),
        ),
    )

    result = validate_envelope(envelope, state)

    assert [command.command_id for command in result.accepted] == ["decree:1"]
    assert [error.code for error in result.errors] == ["unknown_person"]
    assert state.active_decrees == {}


def test_validation_rejects_cross_civilization_person() -> None:
    state = _state()
    civilization_ids = sorted(state.civilizations)
    foreign_person = state.civilizations[civilization_ids[1]].population.living_ids[0]
    envelope = CommandEnvelope(
        schema_version=1,
        civilization_id=civilization_ids[0],
        council_day=0,
        correlation_id="report:0",
        commands=(
            DirectOrder(
                command_id="order:1",
                kind=DirectOrderKind.ASSIGN_WORK,
                worker_ids=(foreign_person,),
            ),
        ),
    )

    result = validate_envelope(envelope, state)

    assert result.accepted == ()
    assert result.errors[0].code == "foreign_person"


def test_council_report_contains_only_civilizations_private_knowledge() -> None:
    state = _state()
    civilization_ids = sorted(state.civilizations)
    own_id, foreign_id = civilization_ids[0], civilization_ids[1]
    report = build_council_report(state, own_id)

    assert set(report.person_ids) == set(state.civilizations[own_id].population.people)
    assert set(report.person_ids).isdisjoint(state.civilizations[foreign_id].population.people)
    assert set(report.known_tiles) == set(state.civilizations[own_id].known_tiles)
    assert len(report.known_tiles) < len(state.world_map.tiles)

