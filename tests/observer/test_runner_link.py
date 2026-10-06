"""The observer holding a runner it started (O4): the gate, the pipe, and a run driven from the
page that saves exactly what `run` saves."""

from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest
from format_one import FIXTURE

from sovereign_world.observer import TOKEN_ENV
from sovereign_world.observer.runner_link import (
    DONE,
    PAUSED,
    RunnerLink,
    RunnerState,
    child_environment,
)
from sovereign_world.observer.service import NoRunner, RunService

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from sovereign_world.observer.server import build_app

TOKEN = "runner-link-" + "t" * 32
AUTH = {"Authorization": f"Bearer {TOKEN}"}
SOURCE = Path(__file__).resolve().parents[2] / "src" / "sovereign_world" / "observer"


class FakeRunner:
    """Records what the gate asked, as the real link would send it (only on a change)."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.running = False

    def pause(self) -> None:
        if self.running:
            self.running = False
            self.sent.append("pause")

    def resume(self) -> None:
        if not self.running:
            self.running = True
            self.sent.append("resume")

    def state(self) -> RunnerState:
        return RunnerState(PAUSED, day=5, last_day=10)

    def close(self) -> None:
        self.sent.append("close")


@pytest.fixture
def old_run(tmp_path: Path) -> Path:
    root = tmp_path / "old"
    shutil.copytree(FIXTURE, root)
    return root


def test_the_runner_goes_on_only_while_the_page_plays_close_behind(old_run: Path) -> None:
    service = RunService(old_run)
    service.walk_all()
    runner = FakeRunner()
    service.attach(runner)
    assert runner.sent == []  # it starts paused, and stays so
    # The run holds days 0..5. Playing, but no day shown yet: still paused.
    service.set_control(paused=False)
    assert not runner.running
    for shown, lookahead, running in [
        (5, 3, True),  # 0 days ahead
        (3, 3, True),  # 2 ahead, lookahead 3
        (2, 3, False),  # 3 ahead: enough
        (2, 4, True),
        (1, 4, False),
        (5, 1, True),
    ]:
        service.set_control(shown=shown, lookahead=lookahead)
        assert runner.running is running, (shown, lookahead)
    service.set_control(paused=True)
    assert not runner.running
    # Only changes reach the runner.
    assert runner.sent == ["resume", "pause", "resume", "pause", "resume", "pause"]
    with pytest.raises(KeyError):
        service.set_control(shown=99)
    with pytest.raises(ValueError):
        service.set_control(lookahead=0)
    record = json.loads(service.status().body)
    assert record["control"] == {"paused": True, "lookahead": 1, "shown": 5}
    assert record["runner"] == {"phase": "paused", "day": 5, "last_day": 10, "exit_code": None}
    service.stop()
    assert runner.sent[-1] == "close"


def test_a_new_history_pauses_the_runner_until_the_page_starts_again(old_run: Path) -> None:
    service = RunService(old_run)
    service.walk_all()
    runner = FakeRunner()
    service.attach(runner)
    service.set_control(paused=False, shown=5)
    assert runner.running
    journal = old_run / "journal.jsonl"
    lines = journal.read_bytes().splitlines(keepends=True)
    keep = next(
        index
        for index, line in enumerate(lines)
        if b'"type":"transition"' in line and b'"day":2,' in line
    )
    journal.write_bytes(b"".join(lines[: keep + 1]))
    service.walk_all()
    assert not runner.running
    assert json.loads(service.status().body)["control"]["shown"] is None


def test_without_a_runner_the_control_answers_409(old_run: Path) -> None:
    service = RunService(old_run)
    with pytest.raises(NoRunner):
        service.set_control(paused=False)
    client = TestClient(build_app(service, TOKEN))
    assert client.get("/api/control", headers=AUTH).status_code == 409
    assert client.post("/api/control", headers=AUTH, json={"paused": False}).status_code == 409
    assert json.loads(client.get("/api/status", headers=AUTH).content)["runner"] is None


def test_the_control_route_checks_what_it_is_given(old_run: Path) -> None:
    service = RunService(old_run)
    service.walk_all()
    service.attach(FakeRunner())
    client = TestClient(build_app(service, TOKEN))
    ok = client.post("/api/control", headers=AUTH, json={"paused": False, "shown": 5})
    assert ok.status_code == 200 and ok.json()["control"]["shown"] == 5
    for body in ({"lookahead": 0}, {"lookahead": 31}, {"shown": -1}, {"speed": 100}):
        assert client.post("/api/control", headers=AUTH, json=body).status_code == 422, body
    assert client.post("/api/control", headers=AUTH, json={"shown": 99}).status_code == 404
    assert client.post("/api/control", json={"paused": False}).status_code == 401


def test_the_runner_never_sees_the_token() -> None:
    environ = {TOKEN_ENV: "secret", "ANTHROPIC_API_KEY": "key", "PATH": "/bin"}
    child = child_environment(environ)
    assert TOKEN_ENV not in child
    assert child == {"ANTHROPIC_API_KEY": "key", "PATH": "/bin"}
    source = (SOURCE / "runner_link.py").read_text()
    assert "WorldStore(" not in source and "persistence import" not in source


def _wait(done: Any, seconds: float = 60) -> None:
    until = time.monotonic() + seconds
    while not done():
        assert time.monotonic() < until, "timed out"
        time.sleep(0.05)


def test_a_started_runner_waits_then_saves_and_finishes(old_run: Path) -> None:
    changes: list[int] = []
    link = RunnerLink.spawn(old_run, 2, on_change=lambda: changes.append(1))
    try:
        _wait(lambda: link.state().phase == PAUSED)
        assert link.state().day == 5 and link.state().last_day == 7
        time.sleep(0.5)
        assert link.state().day == 5
        link.resume()
        _wait(lambda: link.state().phase == DONE)
        assert link.state().day == 7
        _wait(lambda: link.state().exit_code is not None)
        assert link.state().exit_code == 0
        assert changes
    finally:
        link.close(timeout=60)


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _checkpoints(root: Path) -> list[tuple[int, str, str]]:
    connection = sqlite3.connect(f"{(root / 'world.sqlite3').as_uri()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT day, content_hash, state_hash FROM checkpoints ORDER BY day"
        ).fetchall()
    finally:
        connection.close()
    return [(int(day), str(content), str(state)) for day, content, state in rows]


def test_a_run_driven_from_the_page_saves_what_run_saves(tmp_path: Path) -> None:
    """The observer starts the runner; a 'page' plays, follows the newest day, pauses at day
    10, and widens the lookahead from day 20. The journal (councils and their prompt hashes),
    the checkpoints and the files equal a plain `run` of the same days; the token appears in
    nothing the observer or runner print."""
    cli = Path(sys.executable).parent / "sovereign-world"
    base = tmp_path / "base"
    subprocess.run(
        [cli, "init", base, "--seed", "21", "--width", "24", "--height", "24"],
        check=True,
        capture_output=True,
    )
    driven, plain = tmp_path / "driven", tmp_path / "plain"
    shutil.copytree(base, driven)
    shutil.copytree(base, plain)
    port = _free_port()
    observer = subprocess.Popen(
        [cli, "observe", driven, "--port", str(port), "--run-days", "31"],
        env={**os.environ, TOKEN_ENV: TOKEN},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    def call(path: str, body: dict[str, object] | None = None) -> dict[str, Any] | None:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}",
            data=data,
            headers={**AUTH, "Content-Type": "application/json"},
            method="GET" if body is None else "POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as answer:
                return dict(json.loads(answer.read()))
        except (urllib.error.URLError, ConnectionError):
            return None

    try:
        _wait(lambda: (call("/api/status") or {}).get("runner", {}) not in ({}, None))
        status = call("/api/status")
        assert status is not None and status["runner"]["phase"] in ("starting", "paused")
        call("/api/control", {"paused": False})
        paused_at_ten = widened = False
        until = time.monotonic() + 300
        while True:
            assert time.monotonic() < until, "timed out"
            status = call("/api/status") or {}
            ready = status.get("ready_through")
            runner = status.get("runner") or {}
            if ready is not None:
                # The page shows the newest ready day, as Follow latest does.
                control = status["control"]
                if control["shown"] is not None:
                    # Never further ahead than the lookahead, plus the day under way.
                    ahead = status["saved_days"][1] - control["shown"]
                    assert ahead <= control["lookahead"] + 1, status
                call("/api/control", {"shown": ready})
            if ready is not None and ready >= 10 and not paused_at_ten:
                call("/api/control", {"paused": True})
                time.sleep(1.5)
                held = (call("/api/status") or {}).get("saved_days", [0, 0])[1]
                time.sleep(1.0)
                assert (call("/api/status") or {}).get("saved_days", [0, 0])[1] == held
                call("/api/control", {"paused": False})
                paused_at_ten = True
            if ready is not None and ready >= 20 and not widened:
                call("/api/control", {"lookahead": 5})
                widened = True
            if runner.get("phase") == "done" and ready == 31:
                break
            time.sleep(0.2)
        assert paused_at_ten and widened
    finally:
        observer.terminate()
        output, errors = observer.communicate(timeout=120)
    assert TOKEN.encode() not in output + errors
    subprocess.run([cli, "run", plain, "--days", "31"], check=True, capture_output=True)
    journal = (driven / "journal.jsonl").read_bytes()
    assert b"prompt_hash" in journal
    assert journal == (plain / "journal.jsonl").read_bytes()
    assert _checkpoints(driven) == _checkpoints(plain)
    assert {p.name for p in driven.iterdir()} == {p.name for p in plain.iterdir()}
