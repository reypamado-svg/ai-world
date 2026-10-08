"""The calibration batch (sealed trial): scripted histories played on every core into one CSV,
resumable, deterministic, and summarised in a fairness report."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from sovereign_world.calibration.__main__ import main
from sovereign_world.calibration.batch import (
    done_keys,
    drop_partial_histories,
    run_batch,
    seeds_of,
    specs_of,
)
from sovereign_world.calibration.histories import FIELDS, HistorySpec, Row, run_history
from sovereign_world.calibration.report import write_report
from sovereign_world.rulehash import engine_hash, rule_hash


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def test_seeds_and_specs_are_spelt_out() -> None:
    assert seeds_of("0-3,7,2") == [0, 1, 2, 3, 7]
    specs = specs_of([1, 2], 24, 30, "all", "builders,mixed")
    assert len(specs) == 2 * 4 * 2
    assert {spec.rotation for spec in specs} == {0, 1, 2, 3}
    assert len({spec.key for spec in specs}) == len(specs)


def test_a_history_is_the_same_every_time() -> None:
    spec = HistorySpec(
        seed=3, size=24, days=40, rotation=1, assignment=("builder",) * 4, label="builders"
    )
    first = run_history(spec)
    assert first == run_history(spec)
    assert [row.position for row in first] == [1, 2, 3, 0]
    assert abs(sum(row.winner_share for row in first) - 1.0) < 1e-9


def test_the_quick_preset_writes_every_row_and_carries_on_where_it_stopped(tmp_path: Path) -> None:
    out = tmp_path / "batch"
    assert main(["run", "--out", str(out), "--quick", "--workers", "1"]) == 0
    rows = _rows(out / "histories.csv")
    assert len(rows) == 8 * 4
    assert list(rows[0]) == list(FIELDS)
    run = json.loads((out / "run.json").read_text())
    assert run["played_now"] == 8 and run["rule_hash"] == rule_hash()
    # Played again: nothing new to play, and no row twice.
    again = run_batch(
        out, specs_of([0, 1], 24, 60, "0,1", "builders,mixed", interval=28), workers=1
    )
    assert again["played_now"] == 0
    assert len(_rows(out / "histories.csv")) == 8 * 4
    assert len(done_keys(out / "histories.csv")) == 8


def test_two_workers_write_the_same_rows_as_one(tmp_path: Path) -> None:
    specs = specs_of([5], 24, 30, "0,1", "builders")
    run_batch(tmp_path / "one", specs, workers=1)
    run_batch(tmp_path / "two", specs, workers=2)

    def ordered(path: Path) -> list[tuple[str, ...]]:
        return sorted(tuple(row.values()) for row in _rows(path / "histories.csv"))

    assert ordered(tmp_path / "one") == ordered(tmp_path / "two")


def test_the_report_is_written_from_the_batch(tmp_path: Path) -> None:
    out = tmp_path / "batch"
    main(["run", "--out", str(out), "--quick", "--workers", "1"])
    code = main(["report", str(out)])
    report = json.loads((out / "report.json").read_text())
    assert code == (0 if report["passed"] else 1)
    assert report["rule_hash"] == rule_hash()
    text = (out / "report.md").read_text()
    for position in range(4):
        assert f"builders: position {position} mean population" in text
    assert "Mixed by policy" in text
    assert (out / "summary.csv").exists()


def test_the_rule_hash_follows_the_code_but_not_the_observer(tmp_path: Path) -> None:
    root = tmp_path / "pkg"
    (root / "observer").mkdir(parents=True)
    (root / "engine.py").write_text("x = 1\n")
    (root / "observer" / "page.py").write_text("y = 1\n")
    first = rule_hash(root)
    (root / "observer" / "page.py").write_text("y = 2\n")
    assert rule_hash(root) == first
    (root / "engine.py").write_text("x = 2\n")
    assert rule_hash(root) != first
    (root / "engine.py").write_bytes(b"x = 2\r\n")
    assert rule_hash(root) == rule_hash(root)


def test_the_engine_hash_follows_the_engine_and_policies_but_not_what_runs_them(
    tmp_path: Path,
) -> None:
    root = tmp_path / "pkg"
    for name in (
        "engine.py",
        "cli.py",
        "runner.py",
        "gateway/x.py",
        "observer/y.py",
        "calibration/policies.py",
        "calibration/batch.py",
    ):
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text("x = 1\n")
    engine, rules = engine_hash(root), rule_hash(root)
    for name, engine_moves, rule_moves in (
        ("cli.py", False, True),
        ("runner.py", False, True),
        ("gateway/x.py", False, True),
        ("calibration/batch.py", False, True),
        ("observer/y.py", False, False),
        ("calibration/policies.py", True, True),
        ("engine.py", True, True),
    ):
        (root / name).write_text((root / name).read_text() + "y = 2\n")
        assert (engine_hash(root) != engine) == engine_moves, name
        assert (rule_hash(root) != rules) == rule_moves, name
        engine, rules = engine_hash(root), rule_hash(root)


def test_a_history_the_engine_fails_on_is_named_and_the_others_go_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sovereign_world.calibration import batch

    specs = specs_of([1, 2], 24, 5, "0", "builders")
    broken = specs[0].key

    def play(spec: HistorySpec) -> list[Row]:
        if spec.key == broken:
            raise ValueError("cargo and provisions exceed carrier capacity\nmore detail")
        return run_history(spec)

    monkeypatch.setattr(batch, "run_history", play)
    summary = run_batch(tmp_path, specs, workers=1)
    assert summary["failed"] == [
        f"{broken}: ValueError: cargo and provisions exceed carrier capacity"
    ]
    assert len(done_keys(tmp_path / "histories.csv")) == 1
    assert (tmp_path / "failures.txt").read_text().startswith(broken)
    run = json.loads((tmp_path / "run.json").read_text())
    assert run["engine_hash"] == engine_hash() and run["rule_hash"] == rule_hash()
    # A batch missing a history is not finished, and neither is its report.
    assert summary["finished"] is False and run["finished"] is False
    report = write_report(tmp_path)
    assert report["batch_finished"] is False and report["failed"] == summary["failed"]


def test_a_batch_records_the_cadence_and_size_it_measured(tmp_path: Path) -> None:
    monthly = specs_of([1], 24, 5, "0", "builders")
    assert monthly[0].key == "builders|1|24|5|0"
    weekly = specs_of([1], 24, 15, "0", "builders", 7)
    assert weekly[0].key == "builders|1|24|15|0|7" and weekly[0].interval == 7
    run_batch(tmp_path, weekly, workers=1)
    run = json.loads((tmp_path / "run.json").read_text())
    assert (run["council_interval_days"], run["size"], run["days"], run["civilizations"]) == (
        7,
        24,
        15,
        4,
    )
    assert main(["report", str(tmp_path)]) in (0, 1)
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["council_interval_days"] == 7 and report["size"] == 24
    assert "councils every 7 days" in (tmp_path / "report.md").read_text()


def test_three_civilizations_are_specified_played_and_recorded(tmp_path: Path) -> None:
    specs = specs_of([1, 2], 24, 30, "all", "builders,mixed", interval=28, civilizations=3)
    assert len(specs) == 2 * 3 * 2
    assert {spec.rotation for spec in specs} == {0, 1, 2}
    assert {spec.assignment for spec in specs} == {
        ("builder",) * 3,
        ("expander", "trader", "raider"),
    }
    with pytest.raises(ValueError, match="rotations must be 0 to 2"):
        specs_of([1], 24, 30, "3", "builders", civilizations=3)
    first = run_history(specs[1])
    assert [row.position for row in first] == [1, 2, 0]
    out = tmp_path / "three"
    args = ["run", "--out", str(out), "--quick", "--civilizations", "3", "--workers", "1"]
    assert main(args) == 0
    assert len(_rows(out / "histories.csv")) == 8 * 3
    run = json.loads((out / "run.json").read_text())
    assert run["civilizations"] == 3
    # Run again: nothing played twice, even at another council interval than 30.
    paced = ["--council-interval", "28"]
    again = tmp_path / "again"
    assert main(["run", "--out", str(again), "--quick", "--civilizations", "3", *paced]) == 0
    assert main(["run", "--out", str(again), "--quick", "--civilizations", "3", *paced]) == 0
    assert json.loads((again / "run.json").read_text())["played_now"] == 0
    assert len(_rows(again / "histories.csv")) == 8 * 3
    # A folder made for three civilizations is not added to with four.
    with pytest.raises(ValueError, match="civilizations 3"):
        run_batch(out, specs_of([0], 24, 60, "0", "builders", interval=28), workers=1)


def test_a_folder_played_under_another_engine_is_refused(tmp_path: Path) -> None:
    out = tmp_path / "batch"
    specs = specs_of([3], 24, 5, "0", "builders", interval=28)
    run_batch(out, specs, workers=1)
    record = json.loads((out / "run.json").read_text())
    record["engine_hash"] = "0" * 64
    (out / "run.json").write_text(json.dumps(record))
    before = (out / "histories.csv").read_bytes(), (out / "run.json").read_bytes()
    with pytest.raises(ValueError, match="0000000000.*" + engine_hash()[:12] + ".*new folder"):
        run_batch(out, specs, workers=1)
    assert ((out / "histories.csv").read_bytes(), (out / "run.json").read_bytes()) == before
    assert main(["run", "--out", str(out), "--seeds", "3", "--size", "24", "--days", "5"]) == 2


def test_rows_without_run_json_are_refused(tmp_path: Path) -> None:
    out = tmp_path / "batch"
    specs = specs_of([3], 24, 5, "0", "builders", interval=28)
    run_batch(out, specs, workers=1)
    (out / "run.json").unlink()
    with pytest.raises(ValueError, match=r"no run\.json"):
        run_batch(out, specs, workers=1)


def test_run_json_names_the_engine_before_the_first_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sovereign_world.calibration import batch

    out = tmp_path / "batch"
    specs = specs_of([3], 24, 5, "0", "builders", interval=28, civilizations=3)

    def stopped(spec: HistorySpec) -> list[Row]:
        raise KeyboardInterrupt

    monkeypatch.setattr(batch, "run_history", stopped)
    with pytest.raises(KeyboardInterrupt):
        run_batch(out, specs, workers=1)
    record = json.loads((out / "run.json").read_text())
    assert record["engine_hash"] == engine_hash() and record["finished"] is False
    assert record["civilizations"] == 3
    monkeypatch.undo()
    done = run_batch(out, specs, workers=1)
    assert done["finished"] is True and done["played_now"] == 1


def test_a_history_with_missing_rows_is_played_again_and_its_rows_replaced(
    tmp_path: Path,
) -> None:
    specs = specs_of([1, 2], 24, 5, "0", "builders", interval=28)
    whole, cut = tmp_path / "whole", tmp_path / "cut"
    run_batch(whole, specs, workers=1)
    run_batch(cut, specs, workers=1)
    lines = (cut / "histories.csv").read_text().splitlines(keepends=True)
    # The last history lost its last row, and the row before it was torn mid-field.
    torn = lines[:-1]
    torn[-1] = torn[-1][: len(torn[-1]) // 2]
    (cut / "histories.csv").write_text("".join(torn))
    again = run_batch(cut, specs, workers=1)
    assert again["played_now"] == 1
    rows = _rows(cut / "histories.csv")
    assert len(rows) == 8
    assert sorted(tuple(row.values()) for row in rows) == sorted(
        tuple(row.values()) for row in _rows(whole / "histories.csv")
    )


def test_done_keys_counts_only_complete_histories(tmp_path: Path) -> None:
    path = tmp_path / "histories.csv"
    rows = _rows_of(run_history(specs_of([4], 24, 5, "0", "builders")[0]))
    other = _rows_of(run_history(specs_of([5], 24, 5, "0", "builders")[0]))
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows[:3] + other)
    assert done_keys(path, 4) == {"builders|5|24|5|0"}
    assert done_keys(path) == {"builders|4|24|5|0", "builders|5|24|5|0"}


def test_the_report_refuses_a_history_with_missing_rows(tmp_path: Path) -> None:
    out = tmp_path / "batch"
    main(["run", "--out", str(out), "--quick", "--workers", "1"])
    lines = (out / "histories.csv").read_text().splitlines(keepends=True)
    (out / "histories.csv").write_text("".join(lines[:-1]))
    assert main(["report", str(out)]) == 2
    report = json.loads((out / "run.json").read_text())
    assert report["finished"] is True


def _rows_of(rows: list[Row]) -> list[dict[str, object]]:
    return [row.values() for row in rows]


def test_a_last_share_cut_short_is_not_taken_for_a_whole_history(tmp_path: Path) -> None:
    out = tmp_path / "batch"
    specs = specs_of([1], 24, 5, "0", "builders", interval=28, civilizations=3)
    run_batch(out, specs, workers=1)
    table = out / "histories.csv"
    rows = _rows(table)
    # A three-way tie: each holds a third of the win, written as a long fraction.
    for row in rows:
        row["living"] = rows[0]["living"]
        row["winner_share"] = repr(1 / 3)
    with table.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    lines = table.read_text().splitlines(keepends=True)
    assert drop_partial_histories(table, 3) == 0
    # The last field cut short, its line end still written: the value parses, but is wrong.
    lines[-1] = lines[-1].replace(repr(1 / 3), "0.3")
    table.write_text("".join(lines))
    assert main(["report", str(out)]) == 2
    assert drop_partial_histories(table, 3) == 3
    assert done_keys(table, 3) == set()
    assert run_batch(out, specs, workers=1)["played_now"] == 1


def test_a_torn_last_line_is_dropped_and_rows_must_name_each_civilization(tmp_path: Path) -> None:
    out = tmp_path / "batch"
    specs = specs_of([1, 2], 24, 5, "0", "builders", interval=28, civilizations=3)
    run_batch(out, specs, workers=1)
    table = out / "histories.csv"
    whole = table.read_text()
    # The last line lost only its line end: it reads whole, but cannot be trusted.
    table.write_text(whole.rstrip("\r\n"))
    assert drop_partial_histories(table, 3) == 3
    assert len(done_keys(table, 3)) == 1
    # A history whose rows name one civilization twice is not whole either.
    lines = whole.splitlines(keepends=True)
    twice = lines[1].split(",")
    other = lines[2].split(",")
    other[5] = twice[5]
    lines[2] = ",".join(other)
    table.write_text("".join(lines))
    assert drop_partial_histories(table, 3) == 3
    again = run_batch(out, specs, workers=1)
    assert again["played_now"] == 1 and again["finished"] is True
    assert sorted(_rows(table), key=lambda row: tuple(row.values())) == sorted(
        csv.DictReader(whole.splitlines()), key=lambda row: tuple(row.values())
    )
