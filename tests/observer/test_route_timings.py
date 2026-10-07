"""The route timings script (O6) runs on a recorded run and prints every measure."""

from __future__ import annotations

import gc
import shutil
from pathlib import Path

import pytest
from format_one import FIXTURE

pytest.importorskip("fastapi")

from observer.route_timings import main
from typer.testing import CliRunner

from sovereign_world.cli import app as cli


def test_the_route_timings_table_lists_every_measure(tmp_path: Path) -> None:
    run = tmp_path / "old"
    shutil.copytree(FIXTURE, run)
    before = {p.name: p.read_bytes() for p in run.iterdir()}
    text = main([str(run), "--repeats", "1"])
    for measure in (
        "walk, per day",
        "status",
        "a day's record",
        "a day's people",
        "changes from the day before",
        "routes",
        "chronicle",
        "perspective (civilization 0)",
        "terrain manifest",
        "a day dropped from the cache",
    ):
        assert f"| {measure} |" in text, measure
    assert {p.name: p.read_bytes() for p in run.iterdir()} == before
    with pytest.raises(SystemExit):
        main([])


def test_the_route_timings_table_handles_a_run_of_one_day(tmp_path: Path) -> None:
    """A run just initialised has only day 0: no "changes" row, and the rest still timed."""
    run = tmp_path / "fresh"
    made = CliRunner().invoke(
        cli, ["init", str(run), "--seed", "21", "--width", "24", "--height", "24"]
    )
    assert made.exit_code == 0, made.output
    gc.collect()  # the store the command opened folds its write-ahead files away
    before = {p.name: p.read_bytes() for p in run.iterdir()}
    text = main([str(run), "--repeats", "1"])
    assert "| changes from the day before |" not in text
    for measure in ("walk, per day", "a day's record", "perspective (civilization 0)"):
        assert f"| {measure} |" in text, measure
    assert "| a day dropped from the cache |" in text
    assert {p.name: p.read_bytes() for p in run.iterdir()} == before
