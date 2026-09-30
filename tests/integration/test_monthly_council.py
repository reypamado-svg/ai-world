from sovereign_world.commands import CommandEnvelope, CouncilReport, Decree, DecreeKind
from sovereign_world.config import RunManifest, WorldConfig
from sovereign_world.engine import advance_day
from sovereign_world.resources import Resource
from sovereign_world.rng import StableRng
from sovereign_world.scripted import BaselineSovereign
from sovereign_world.state import build_initial_state
from sovereign_world.work import ProjectStatus


class FailingSovereign:
    def decide(self, report: CouncilReport) -> CommandEnvelope:
        raise RuntimeError("provider unavailable")


def test_day_zero_council_applies_valid_decree_and_isolates_failure() -> None:
    config = WorldConfig(seed=21, width=48, height=48)
    state = build_initial_state(RunManifest.new(config=config, engine_version="0.1.0"))
    civilization_ids = sorted(state.civilizations)
    sovereigns = {
        civilization_ids[0]: FailingSovereign(),
        civilization_ids[1]: BaselineSovereign(),
        civilization_ids[2]: BaselineSovereign(),
        civilization_ids[3]: BaselineSovereign(),
    }

    result = advance_day(state, StableRng(config.seed), sovereigns=sovereigns)

    assert civilization_ids[0] not in result.state.active_decrees
    assert result.state.active_decrees[civilization_ids[1]]["food_reserve_target"] == 90
    assert any(event.kind == "sovereign_unavailable" for event in result.events.events)


def test_standing_decree_remains_active_for_sixty_days() -> None:
    config = WorldConfig(seed=21, width=48, height=48)
    state = build_initial_state(RunManifest.new(config=config, engine_version="0.1.0"))
    civilization_id = sorted(state.civilizations)[0]
    sovereigns = {civilization_id: BaselineSovereign()}
    rng = StableRng(config.seed)

    for _ in range(60):
        state = advance_day(state, rng, sovereigns=sovereigns).state

    assert state.active_decrees[civilization_id]["food_reserve_target"] == 90
    assert state.active_decrees[civilization_id]["food_reserve_target_expires"] >= 60


def test_invalid_command_does_not_block_valid_decree() -> None:
    config = WorldConfig(seed=21, width=48, height=48)
    state = build_initial_state(RunManifest.new(config=config, engine_version="0.1.0"))
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
                value=120,
                duration_days=60,
            ),
        ),
    )

    class FixedSovereign:
        def decide(self, report: CouncilReport) -> CommandEnvelope:
            return envelope

    result = advance_day(
        state,
        StableRng(config.seed),
        sovereigns={civilization_id: FixedSovereign()},
    )

    assert result.state.active_decrees[civilization_id]["food_reserve_target"] == 120


def test_baseline_sovereign_builds_starter_structures() -> None:
    config = WorldConfig(seed=21, width=24, height=24)
    state = build_initial_state(RunManifest.new(config=config, engine_version="0.1.0"))
    sovereigns = {civilization_id: BaselineSovereign() for civilization_id in state.civilizations}

    result = advance_day(state, StableRng(config.seed), sovereigns=sovereigns)

    for civilization in result.state.civilizations.values():
        assert len(civilization.projects) == 2
        assert all(
            project.status is ProjectStatus.COMPLETE for project in civilization.projects.values()
        )


def test_food_reserve_decree_prevents_baseline_starvation() -> None:
    config = WorldConfig(seed=21, width=24, height=24)
    state = build_initial_state(RunManifest.new(config=config, engine_version="0.1.0"))
    sovereigns = {civilization_id: BaselineSovereign() for civilization_id in state.civilizations}
    rng = StableRng(config.seed)

    for _ in range(1_000):
        state = advance_day(state, rng, sovereigns=sovereigns).state

    assert all(civilization.population.living_ids for civilization in state.civilizations.values())
    assert all(
        civilization.inventory.quantities.get(Resource.FOOD, 0) > 0
        for civilization in state.civilizations.values()
    )
