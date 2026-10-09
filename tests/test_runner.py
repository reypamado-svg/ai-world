"""The run loop, held and resumed: what it saves is what an unbroken run saves (O4)."""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

from typer.testing import CliRunner

from sovereign_world.cli import app
from sovereign_world.persistence import WorldStore
from sovereign_world.runner import RunControl, run_days

SOURCE = Path(__file__).resolve().parents[1] / "src" / "sovereign_world" / "runner.py"
cli = CliRunner()
COMMAND = (sys.executable, "-m", "sovereign_world.cli", "run")


def _checkpoints(root: Path) -> list[tuple[int, str, str]]:
    connection = sqlite3.connect(f"{(root / 'world.sqlite3').as_uri()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT day, content_hash, state_hash FROM checkpoints ORDER BY day"
        ).fetchall()
    finally:
        connection.close()
    return [(int(day), str(content), str(state)) for day, content, state in rows]


def _saved_day(root: Path) -> int:
    """The last day the journal holds (read as a file; no store is opened)."""
    days = [0]
    for line in (root / "journal.jsonl").read_text().splitlines():
        record = json.loads(line)
        if record["type"] == "transition":
            days.append(int(record["payload"]["day"]))
    return max(days)


def _init(root: Path) -> None:
    result = cli.invoke(app, ["init", str(root), "--seed", "21", "--width", "24", "--height", "24"])
    assert result.exit_code == 0, result.stdout


def test_a_held_run_saves_what_an_unbroken_run_saves(tmp_path: Path) -> None:
    """Paused twice, from another thread, across a council day: the same journal (councils and
    their prompt hashes included) and the same checkpoints as `run`."""
    base = tmp_path / "base"
    _init(base)
    held, plain = tmp_path / "held", tmp_path / "plain"
    shutil.copytree(base, held)
    shutil.copytree(base, plain)

    control = RunControl()
    reached = threading.Event()

    def on_day(state: object) -> None:
        if getattr(state, "day", None) in (3, 7):
            control.pause()
            reached.set()

    def release() -> None:
        for _ in range(2):
            assert reached.wait(timeout=120)
            reached.clear()
            time.sleep(0.2)
            assert control.paused
            control.resume()

    helper = threading.Thread(target=release)
    helper.start()
    final = run_days(WorldStore(held), 31, control=control, on_day=on_day)
    helper.join(timeout=10)
    assert final.day == 31

    result = cli.invoke(app, ["run", str(plain), "--days", "31"])
    assert result.exit_code == 0, result.stdout
    journal = (held / "journal.jsonl").read_bytes()
    assert b"prompt_hash" in journal
    assert journal == (plain / "journal.jsonl").read_bytes()
    assert _checkpoints(held) == _checkpoints(plain)


def test_a_stopped_run_saves_nothing_before_its_first_day(tmp_path: Path) -> None:
    root = tmp_path / "run"
    _init(root)
    before = (root / "journal.jsonl").read_bytes()
    control = RunControl(paused=True)
    control.stop()
    state = run_days(WorldStore(root), 5, control=control)
    assert state.day == 0
    assert (root / "journal.jsonl").read_bytes() == before
    assert [row[0] for row in _checkpoints(root)] == [0]


def _runner(root: Path, days: int) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [*COMMAND, str(root), "--days", str(days), "--controlled"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )


def _line(process: subprocess.Popen[str]) -> str:
    assert process.stdout is not None
    return process.stdout.readline().strip()


def test_the_controlled_runner_waits_for_resume_and_reports_each_day(tmp_path: Path) -> None:
    root = tmp_path / "run"
    _init(root)
    process = _runner(root, 3)
    assert process.stdin is not None
    try:
        assert _line(process) == "start 0 3"
        assert _line(process) == "paused"
        # Nothing is saved while it waits.
        time.sleep(1.0)
        assert _saved_day(root) == 0
        process.stdin.write("anything else\nresume\n")
        process.stdin.flush()
        assert _line(process) == "running"
        assert [_line(process) for _ in range(3)] == ["day 1", "day 2", "day 3"]
        assert _line(process) == "done 3"
        process.stdin.close()
        assert process.wait(timeout=60) == 0
    finally:
        process.kill()
    verified = cli.invoke(app, ["verify", str(root)])
    assert verified.exit_code == 0, verified.stdout
    assert [row[0] for row in _checkpoints(root)] == [0, 3]


def test_the_end_of_input_stops_the_runner_after_the_day_in_progress(tmp_path: Path) -> None:
    root = tmp_path / "run"
    _init(root)
    process = _runner(root, 30)
    assert process.stdin is not None
    try:
        assert [_line(process), _line(process)] == ["start 0 30", "paused"]
        process.stdin.write("resume\n")
        process.stdin.flush()
        assert _line(process) == "running"
        assert _line(process) == "day 1"
        process.stdin.write("pause\n")
        process.stdin.flush()
        assert _line(process) == "paused"
        # The day already under way may still be saved; after it, nothing more.
        time.sleep(1.5)
        held = _saved_day(root)
        time.sleep(1.0)
        assert _saved_day(root) == held <= 2
        process.stdin.close()
        rest = [_line(process) for _ in range(held)]
        assert rest[-1] == f"done {held}", rest
        assert process.wait(timeout=60) == 0
    finally:
        process.kill()
    verified = cli.invoke(app, ["verify", str(root)])
    assert verified.exit_code == 0, verified.stdout
    assert _checkpoints(root)[-1][0] == held


def test_the_runner_knows_nothing_of_the_observer() -> None:
    source = SOURCE.read_text()
    assert "sovereign_world.observer" not in source
    assert "SOVEREIGN_WORLD_OBSERVER_TOKEN" not in source
