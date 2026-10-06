"""The route timings script (O6) runs on a recorded run and prints every measure."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from format_one import FIXTURE

pytest.importorskip("fastapi")

from observer.route_timings import main


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
