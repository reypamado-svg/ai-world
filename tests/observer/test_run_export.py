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
from town_fixture import EXPORTED, record_town

from sovereign_world.config import RunManifest, WorldConfig
from sovereign_world.defence import DefenceOrder
from sovereign_world.observer.projection import AWAY, project_day
from sovereign_world.observer.run_export import PEOPLE_LAYOUT, day_record, export_run, people_bytes
from sovereign_world.rings import Citadel, empty_ring
from sovereign_world.state import build_initial_state
from sovereign_world.walls import WallGrade

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


def test_chosen_days_are_exported_in_order_once_each(run: Path, tmp_path: Path) -> None:
    export_run(run, tmp_path / "out", days=(OLD_DAYS, 0, OLD_DAYS))
    manifest = json.loads((tmp_path / "out" / "manifest.json").read_text())
    assert manifest["days"] == [0, OLD_DAYS]


def test_a_planned_town_exports_its_plan_and_walls() -> None:
    config = WorldConfig(seed=9, width=24, height=24)
    state = build_initial_state(RunManifest.new(config, "0.1.0", rules_version=3))
    home = sorted(state.civilizations)[0]
    civilization = state.civilizations[home]
    [capital] = civilization.settlements
    ring = empty_ring(capital.settlement_id, 2, (0,), 0)
    sections = tuple(
        item.model_copy(update={"grade": WallGrade.PALISADE, "strength": 25}) if index < 3 else item
        for index, item in enumerate(ring.sections)
    )
    civilization.wall_rings = {
        capital.settlement_id: ring.model_copy(update={"sections": sections})
    }
    record = day_record(project_day(state), state_hash="")
    row = next(item for item in record["settlements"] if item["id"] == capital.settlement_id)
    assert row["plan"] == {
        "style": "open",
        "keep": "edge",
        "market": None,
        "shrine": None,
        "craft_quarter": None,
        "wall_ring": 2,
        "gates": [0],
        "planned_day": 0,
    }
    assert row["walls"] == {
        "ring": 2,
        "gates": [0],
        "sections": [["palisade", 25]] * 3 + [[None, 0]] * 7,
        "towers": 0,
    }
    assert "defence" not in row, "a ring with no works dumps only the four old keys"
    others = [item for item in record["settlements"] if item["id"] != capital.settlement_id]
    assert all("walls" not in item and "plan" in item for item in others)

    # Export version 3: the works, where there are any, and the standing defence order.
    full = ring.model_copy(
        update={
            "sections": tuple(
                item.model_copy(
                    update={"grade": WallGrade.PALISADE, "strength": 25, "gatehouse": item.gate}
                )
                for item in ring.sections
            ),
            "towers": 2,
            "tower_sections": (0, 4),
            "ditch": 2,
            "stakes": True,
        }
    )
    civilization.wall_rings = {capital.settlement_id: full}
    civilization.citadels = {
        capital.settlement_id: Citadel(grade=WallGrade.PALISADE, strength=20, built_day=0)
    }
    civilization.defence_orders = {
        capital.settlement_id: DefenceOrder(settlement_id=capital.settlement_id, set_day=3)
    }
    record = day_record(project_day(state), state_hash="")
    row = next(item for item in record["settlements"] if item["id"] == capital.settlement_id)
    assert row["walls"] == {
        "ring": 2,
        "gates": [0],
        "sections": [["palisade", 25]] * 10,
        "towers": 2,
        "tower_sections": [0, 4],
        "gatehouses": [0],
        "ditch": 2,
        "stakes": True,
        "citadel": {"grade": "palisade", "strength": 20},
    }
    assert row["defence"] == {
        "posture": "everyone",
        "reserve_bp": 0,
        "tower_crews": "any",
        "arms_priority": "any",
        "set_day": 3,
    }
    older = build_initial_state(RunManifest.new(config, "0.1.0", rules_version=2))
    assert all(
        "plan" not in item and "walls" not in item
        for item in day_record(project_day(older), state_hash="")["settlements"]
    )


TOWN = COMMITTED.parent / "run-town"


def test_the_committed_town_fixture_is_a_fresh_export(tmp_path: Path) -> None:
    """A designed, half-walled capital (rules 3) for the observer's town test."""
    record_town(tmp_path / "town")
    export_run(tmp_path / "town", tmp_path / "fresh", days=EXPORTED)
    day = json.loads((tmp_path / "fresh" / "days" / f"d{EXPORTED[-1]:06d}.json").read_text())
    walled = [item for item in day["settlements"] if item.get("walls") and item["capital"]]
    built = {
        item["id"]: sum(grade is not None for grade, _ in item["walls"]["sections"])
        for item in walled
    }
    assert sorted(built.values()) == [6, 10], built
    assert all(item["plan"]["style"] == "ringed" for item in walled)
    [fortified] = [item for item in walled if "citadel" in item["walls"]]
    assert fortified["walls"]["gatehouses"] == [0, 5] and fortified["defence"]
    manifest = json.loads((tmp_path / "fresh" / "manifest.json").read_text())
    assert manifest["export_version"] == 4
    assert _files(TOWN) == _files(tmp_path / "fresh"), (
        "observer/tests/fixtures/run-town is stale: run tests/observer/town_fixture.py's "
        "record_town and export days 0 and 18 there"
    )


def test_the_export_names_a_council_interval_only_when_it_is_not_monthly() -> None:
    from sovereign_world.config import RunManifest, WorldConfig
    from sovereign_world.observer.run_export import manifest_record

    def record(**config: int) -> dict[str, object]:
        manifest = RunManifest.new(WorldConfig(seed=1, width=24, height=24, **config), "0.2.0")
        return manifest_record(
            manifest,
            journal_format=2,
            history_epoch=0,
            civilizations=("a",),
            days=(0,),
            saved=(0,),
        )

    assert "council_interval_days" not in record()
    assert record(council_interval_days=28)["council_interval_days"] == 28
