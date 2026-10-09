"""How often councils sit is chosen when a world is made: every 7, 14, 21 or 28 days (30 for
worlds made before it could be chosen), and the fewest days between crisis councils (0 for
none). Worlds made before keep every hash."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from sovereign_world.cli import app
from sovereign_world.commands import build_council_report
from sovereign_world.config import RunManifest, WorldConfig
from sovereign_world.gateway.memory import state_summary
from sovereign_world.persistence import WorldStore
from sovereign_world.state import build_initial_state

cli = CliRunner()
INIT = ("init", "--seed", "21", "--width", "24", "--height", "24")


def _manifest(**config: int) -> RunManifest:
    return RunManifest.new(WorldConfig(seed=21, width=24, height=24, **config), "0.2.0")


def test_the_interval_is_one_to_four_weeks_or_the_old_month() -> None:
    for days in (7, 14, 21, 28, 30):
        assert WorldConfig(seed=1, width=24, height=24, council_interval_days=days)
    for days in (1, 10, 29, 31, 60):
        with pytest.raises(ValidationError, match="7, 14, 21 or 28"):
            WorldConfig(seed=1, width=24, height=24, council_interval_days=days)
    with pytest.raises(ValidationError):
        WorldConfig(seed=1, width=24, height=24, crisis_gap_days=-1)
    assert WorldConfig(seed=1, width=24, height=24).council_interval_days == 30


def test_the_default_crisis_gap_is_left_out_of_every_hash() -> None:
    plain, gap7 = _manifest(), _manifest(crisis_gap_days=7)
    assert "crisis_gap_days" not in plain.config.model_dump()
    assert "crisis_gap_days" not in plain.model_dump_json()
    state = build_initial_state(plain)
    assert "crisis_gap_days" not in json.dumps(state.model_dump(mode="json", exclude={"world_map"}))
    base = plain.model_dump()
    assert RunManifest.model_validate({**base, "run_id": plain.run_id}).content_hash() == (
        plain.content_hash()
    )
    assert gap7.config == plain.config
    other = _manifest(crisis_gap_days=3)
    assert other.config.model_dump()["crisis_gap_days"] == 3
    assert RunManifest.model_validate_json(other.model_dump_json()).config.crisis_gap_days == 3
    assert "crisis_gap_days" in json.dumps(
        build_initial_state(other).model_dump(mode="json", exclude={"world_map"})
    )


def test_a_cadence_changes_the_manifest_hash() -> None:
    plain = _manifest()

    def same(**config: int) -> str:
        changed = plain.model_copy(update={"config": plain.config.model_copy(update=config)})
        return RunManifest.model_validate_json(changed.model_dump_json()).content_hash()

    assert same() == plain.content_hash()
    assert same(council_interval_days=28) != plain.content_hash()
    assert same(crisis_gap_days=3) != plain.content_hash()
    assert same(crisis_gap_days=0) != same(crisis_gap_days=3)


def test_the_report_tells_the_cadence_only_when_it_is_not_the_old_one() -> None:
    old = build_initial_state(_manifest())
    civ = sorted(old.civilizations)[0]
    report = build_council_report(old, civ)
    assert "council_interval_days" not in report.model_dump()
    assert "council_interval_days" not in state_summary(report, 200_000)
    new = build_initial_state(_manifest(council_interval_days=14, crisis_gap_days=3))
    report = build_council_report(new, civ)
    dumped = report.model_dump()
    assert (dumped["council_interval_days"], dumped["crisis_gap_days"]) == (14, 3)
    summary = state_summary(report, 200_000)
    assert '"council_interval_days":14' in summary and '"crisis_gap_days":3' in summary
    none = build_council_report(build_initial_state(_manifest(crisis_gap_days=0)), civ)
    assert none.model_dump()["crisis_gap_days"] == 0


def test_init_offers_weeks_and_inspect_and_fork_keep_them(tmp_path: Path) -> None:
    for refused in ("30", "10"):
        result = cli.invoke(app, [*INIT, str(tmp_path / refused), "--council-interval", refused])
        assert result.exit_code == 2 and "7, 14, 21 or 28" in result.output
    made = cli.invoke(app, [*INIT, str(tmp_path / "default")])
    assert made.exit_code == 0, made.output
    assert WorldStore(tmp_path / "default").manifest().config.council_interval_days == 28
    shown = cli.invoke(app, ["inspect", str(tmp_path / "default")])
    assert "council interval: 28 days" in shown.output and "crisis gap: 7 days" in shown.output
    world = tmp_path / "fortnight"
    made = cli.invoke(app, [*INIT, str(world), "--council-interval", "14", "--crisis-gap", "0"])
    assert made.exit_code == 0, made.output
    assert "crisis councils: none" in cli.invoke(app, ["inspect", str(world)]).output
    assert cli.invoke(app, ["run", str(world), "--days", "2"]).exit_code == 0
    settings = tmp_path / "baseline.toml"
    settings.write_text("")
    forked = cli.invoke(
        app,
        ["fork", str(world), str(tmp_path / "fork"), "--day", "1", "--sovereigns", str(settings)],
    )
    assert forked.exit_code == 0, forked.output
    config = WorldStore(tmp_path / "fork").manifest().config
    assert (config.council_interval_days, config.crisis_gap_days) == (14, 0)
