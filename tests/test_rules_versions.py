"""Rules versions: new worlds use the current rules; older worlds keep theirs."""

from pathlib import Path

from typer.testing import CliRunner

from sovereign_world.cli import app
from sovereign_world.config import CURRENT_RULES, RunManifest, WorldConfig
from sovereign_world.persistence import WorldStore
from sovereign_world.rules import rules_for
from sovereign_world.state import WorldState, build_initial_state, state_hash

CONFIG = WorldConfig(seed=9, width=24, height=24)
RUN_ID = "6f1f7c32-3f0b-4f1e-9b55-2a2f1d1c0e14"


def _manifest(**update: object) -> RunManifest:
    return RunManifest.model_validate(
        {"run_id": RUN_ID, "engine_version": "0.1.0", "config": CONFIG, **update}
    )


def test_new_runs_use_the_current_rules() -> None:
    assert RunManifest.new(CONFIG, "0.1.0").rules_version == CURRENT_RULES == 3
    assert RunManifest.new(CONFIG, "0.1.0", rules_version=1).rules_version == 1


def test_a_rules_one_manifest_hashes_as_before_and_rules_two_differs() -> None:
    old = _manifest()
    assert old.rules_version == 1
    assert old.content_hash() == _manifest(rules_version=1).content_hash()
    assert _manifest(rules_version=2).content_hash() != old.content_hash()


def test_the_world_records_its_rules_and_dumps_them_only_from_version_two() -> None:
    old = build_initial_state(_manifest(generator_version=3))
    new = build_initial_state(_manifest(generator_version=3, rules_version=2))
    assert old.rules_version == 1
    assert new.rules_version == 2
    assert "rules_version" not in old.model_dump(mode="json")
    assert new.model_dump(mode="json")["rules_version"] == 2
    again = WorldState.model_validate_json(new.model_dump_json())
    assert again.rules_version == 2
    assert state_hash(again) == state_hash(new)
    assert state_hash(old) != state_hash(new)


def test_rules_switches() -> None:
    first, second = rules_for(1), rules_for(2)
    assert not (first.houses or first.decrees_expire or first.ranks or first.civil_research)
    assert second.houses and second.decrees_expire and second.ranks and second.civil_research
    assert not second.town_plans
    assert rules_for(3).town_plans and rules_for(3).houses


def test_init_makes_current_rules_and_a_fork_keeps_its_parents(tmp_path: Path) -> None:
    runner = CliRunner()
    world = tmp_path / "world"
    arguments = ["init", str(world), "--seed", "21", "--width", "24", "--height", "24"]
    assert runner.invoke(app, arguments).exit_code == 0
    assert WorldStore(world).manifest().rules_version == CURRENT_RULES
    assert runner.invoke(app, ["run", str(world), "--days", "3"]).exit_code == 0

    sovereigns = tmp_path / "sovereigns.toml"
    sovereigns.write_text("")
    fork = tmp_path / "fork"
    result = runner.invoke(
        app, ["fork", str(world), str(fork), "--day", "2", "--sovereigns", str(sovereigns)]
    )
    assert result.exit_code == 0, result.stdout
    assert WorldStore(fork).manifest().rules_version == CURRENT_RULES

    # An old world, made before rules versions, forks onto the old rules.
    old = tmp_path / "old"
    WorldStore.create(old, _manifest(), build_initial_state(_manifest()))
    old_fork = tmp_path / "old-fork"
    result = runner.invoke(
        app, ["fork", str(old), str(old_fork), "--day", "0", "--sovereigns", str(sovereigns)]
    )
    assert result.exit_code == 0, result.stdout
    assert WorldStore(old_fork).manifest().rules_version == 1
