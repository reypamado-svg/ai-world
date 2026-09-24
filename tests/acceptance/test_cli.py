from pathlib import Path

from typer.testing import CliRunner

from sovereign_world.cli import app

runner = CliRunner()


def test_cli_run_can_be_inspected_replayed_and_verified(tmp_path: Path) -> None:
    world = tmp_path / "world"

    initialized = runner.invoke(app, ["init", str(world), "--seed", "21"])
    assert initialized.exit_code == 0
    assert "initialized day 0" in initialized.stdout

    advanced = runner.invoke(app, ["run", str(world), "--days", "30"])
    assert advanced.exit_code == 0
    assert "advanced to day 30" in advanced.stdout

    inspected = runner.invoke(app, ["inspect", str(world)])
    assert inspected.exit_code == 0
    civilization_lines = [
        line for line in inspected.stdout.splitlines() if line.startswith("civilization: ")
    ]
    assert len(civilization_lines) == 4

    checkpointed = runner.invoke(app, ["checkpoint", str(world)])
    assert checkpointed.exit_code == 0
    assert "checkpoint saved at day 30" in checkpointed.stdout

    replayed = runner.invoke(app, ["replay", str(world), "--day", "30"])
    assert replayed.exit_code == 0
    assert "replayed day 30" in replayed.stdout

    verified = runner.invoke(app, ["verify", str(world)])
    assert verified.exit_code == 0
    assert "verified through day 30" in verified.stdout


def test_cli_exposes_no_state_editing_command() -> None:
    help_result = runner.invoke(app, ["--help"])
    assert help_result.exit_code == 0
    for forbidden in ("set", "patch", "heal", "spawn", "grant", "delete", "map-edit"):
        assert forbidden not in help_result.stdout
