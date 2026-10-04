"""A recorded run exported for the observer: exact, reproducible, and the run untouched (O2)."""

from __future__ import annotations

import gzip
import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from format_one import DAYS as OLD_DAYS
from format_one import FIXTURE
from perf.synthetic import grown_world

from sovereign_world.observer.projection import AWAY, project_day
from sovereign_world.observer.run_export import PEOPLE_LAYOUT, export_run, people_bytes

COMMITTED = Path(__file__).resolve().parents[2] / "observer" / "tests" / "fixtures" / "run-small"
"""The browser tests' run: an export of the format-1 fixture, checked here to stay current."""


def _files(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _stamps(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(root.iterdir())
        if path.is_file()
    }


def decode_people(blob: bytes) -> dict[str, np.ndarray]:
    raw = gzip.decompress(blob)
    width = sum(np.dtype(dtype).itemsize for _, dtype in PEOPLE_LAYOUT)
    count = len(raw) // width
    assert count * width == len(raw)
    columns: dict[str, np.ndarray] = {}
    at = 0
    for name, dtype in PEOPLE_LAYOUT:
        size = np.dtype(dtype).itemsize * count
        columns[name] = np.frombuffer(raw[at : at + size], dtype=dtype)
        at += size
    return columns


@pytest.fixture
def run(tmp_path: Path) -> Path:
    root = tmp_path / "run"
    shutil.copytree(FIXTURE, root)
    return root


def test_the_same_run_exports_the_same_bytes_and_is_left_alone(run: Path, tmp_path: Path) -> None:
    before = _stamps(run)
    first = export_run(run, tmp_path / "a")
    export_run(run, tmp_path / "b")
    assert first.days == tuple(range(OLD_DAYS + 1))
    assert _files(tmp_path / "a") == _files(tmp_path / "b")
    assert _stamps(run) == before
    assert not [p for p in run.iterdir() if p.name.endswith(("-wal", "-shm", "-journal"))]


def test_the_days_say_what_the_engine_recorded(run: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    export_run(run, out)
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["days"] == list(range(OLD_DAYS + 1))
    assert (manifest["seed"], manifest["width"], manifest["height"]) == (11, 24, 24)
    terrain = json.loads((out / "terrain" / "manifest.json").read_text())
    assert terrain["engine"]["seed"] == 11 and terrain["engine"]["generator_version"] == 2
    ids = json.loads((out / "ids.json").read_text())
    assert len(ids) == len(set(ids))
    for day in manifest["days"]:
        record = json.loads((out / "days" / f"d{day:06d}.json").read_text())
        people = decode_people((out / "days" / f"d{day:06d}.people.bin.gz").read_bytes())
        n = len(people["id"])
        assert n == record["counts"]["living"]
        assert len(set(people["id"].tolist())) == n
        assert int(np.count_nonzero(people["settlement"] == AWAY)) == record["counts"]["away"]
        for index, settlement in enumerate(record["settlements"]):
            assert int(np.count_nonzero(people["settlement"] == index)) == settlement["residents"]
        assert sum(item[3] for item in record["travellers"]) == record["counts"]["away"]
        assert all(ids[number].startswith("person:") for number in people["id"].tolist())


def test_an_export_writes_only_where_it_is_told(run: Path, tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        export_run(run, run / "inside")
    out = tmp_path / "out"
    out.mkdir()
    (out / "keep.txt").write_text("not an export")
    with pytest.raises(FileExistsError):
        export_run(run, out)
    with pytest.raises(FileExistsError):
        export_run(run, out, replace=True)
    assert (out / "keep.txt").read_text() == "not an export"
    export_run(run, tmp_path / "again")
    summary = export_run(run, tmp_path / "again", replace=True, days=(0, 5))
    assert summary.days == (0, 5)
    assert sorted(p.name for p in (tmp_path / "again" / "days").iterdir()) == [
        "d000000.json",
        "d000000.people.bin.gz",
        "d000005.json",
        "d000005.people.bin.gz",
    ]


def test_a_hundred_thousand_people_take_well_under_a_megabyte_a_day() -> None:
    view = project_day(grown_world(100_000, size=48, generator=3, rules=2))
    blob = people_bytes(view, list(range(len(view.people))))
    assert len(view.people) == 100_000
    assert len(blob) < 1_000_000, len(blob)


def test_the_committed_browser_fixture_is_a_fresh_export(run: Path, tmp_path: Path) -> None:
    export_run(run, tmp_path / "fresh")
    assert _files(COMMITTED) == _files(tmp_path / "fresh"), (
        "observer/tests/fixtures/run-small is stale: re-export tests/fixtures/format-one there"
    )
