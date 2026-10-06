"""The observer's server (O3): the export's bytes, live, read-only, behind a token."""

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
from format_one import DAYS as OLD_DAYS
from format_one import FIXTURE
from town_fixture import DAYS as TOWN_DAYS
from town_fixture import record_town

from sovereign_world.engine import advance_day
from sovereign_world.observer.run_export import export_run
from sovereign_world.persistence import WorldStore
from sovereign_world.replay import replay_run
from sovereign_world.rng import StableRng

pytest.importorskip("fastapi")

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from sovereign_world.observer.server import UI_ROOT, build_app
from sovereign_world.observer.service import RunService

TOKEN = "test-token-" + "x" * 32
AUTH = {"Authorization": f"Bearer {TOKEN}"}
SOURCE = Path(__file__).resolve().parents[2] / "src" / "sovereign_world" / "observer"


def _client(service: RunService) -> TestClient:
    return TestClient(build_app(service, TOKEN))


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


def _served(client: TestClient, export: Path) -> dict[str, bytes]:
    """Everything the static export holds, asked of the server under the same names."""
    manifest = json.loads(client.get("/api/run/manifest", headers=AUTH).content)
    files = {
        "manifest.json": client.get("/api/run/manifest", headers=AUTH).content,
        "ids.json": client.get("/api/run/ids", headers=AUTH).content,
    }
    for day in manifest["days"]:
        files[f"days/d{day:06d}.json"] = client.get(f"/api/run/days/{day}", headers=AUTH).content
        files[f"days/d{day:06d}.people.bin.gz"] = client.get(
            f"/api/run/days/{day}/people", headers=AUTH
        ).content
    for name in _files(export / "terrain"):
        answer = client.get(f"/api/run/terrain/{name}", headers=AUTH)
        assert answer.status_code == 200, name
        files[f"terrain/{name}"] = answer.content
    return files


def _paths(routes: list[Any]) -> list[tuple[str, str]]:
    """Every route's (method, path), also inside included routers (however this FastAPI keeps
    them)."""
    found: list[tuple[str, str]] = []
    for route in routes:
        if isinstance(route, APIRoute):
            found += [(method, route.path) for method in sorted(route.methods)]
        inner = getattr(route, "original_router", None) or getattr(route, "routes", None)
        if inner is not None:
            found += _paths(list(getattr(inner, "routes", inner)))
    return found


@pytest.fixture
def old_run(tmp_path: Path) -> Path:
    root = tmp_path / "old"
    shutil.copytree(FIXTURE, root)
    return root


def test_every_api_route_needs_the_token(old_run: Path) -> None:
    service = RunService(old_run)
    service.walk_all()
    app = build_app(service, TOKEN)
    client = TestClient(app)
    fill = {"day": "1", "civ": "0", "path": "manifest.json", "rest": "anything"}
    paths = _paths(app.routes)
    api = [(method, path) for method, path in paths if path.startswith("/api")]
    assert len(api) >= 10 and ("POST", "/api/control") in api
    for method, path in api:
        url = path.replace(":path", "")
        for name, value in fill.items():
            url = url.replace("{" + name + "}", value)
        for headers in (
            {},
            {"Authorization": "Bearer wrong"},
            {"Authorization": TOKEN},
            {"Authorization": f"Bearer {TOKEN}x"},
        ):
            answer = client.request(method, url, headers=headers, json={})
            assert answer.status_code == 401, (method, url, headers)
            assert answer.headers["www-authenticate"] == "Bearer"
            assert "x-history-epoch" not in answer.headers
        assert client.request(method, url, headers=AUTH, json={}).status_code != 401, url
    # A token passed in the address is not a token.
    assert client.get(f"/api/status?token={TOKEN}").status_code == 401
    with pytest.raises(ValueError):
        build_app(service, "")


def test_the_server_answers_with_the_exports_bytes(old_run: Path, tmp_path: Path) -> None:
    export = tmp_path / "export"
    export_run(old_run, export)
    service = RunService(old_run)
    service.walk_all()
    client = _client(service)
    assert _served(client, export) == _files(export)
    answer = client.get("/api/run/days/2", headers=AUTH)
    assert answer.headers["x-history-epoch"] == "0"
    assert answer.headers["content-type"] == "application/json"
    people = client.get("/api/run/days/2/people", headers=AUTH)
    assert people.headers["content-type"] == "application/gzip"
    status = json.loads(client.get("/api/status", headers=AUTH).content)
    assert status["ready"] == status["saved"] == OLD_DAYS + 1
    assert status["ready_through"] == OLD_DAYS
    # A client holding some of the ids asks only for the rest.
    ids = json.loads(client.get("/api/run/ids", headers=AUTH).content)
    rest = json.loads(client.get("/api/run/ids?from=5", headers=AUTH).content)
    assert rest == ids[5:]
    assert client.get("/api/run/ids?from=-1", headers=AUTH).status_code == 422


def test_a_designed_town_is_served_as_exported(tmp_path: Path) -> None:
    run = tmp_path / "town"
    record_town(run)
    export = tmp_path / "export"
    export_run(run, export)
    service = RunService(run)
    service.walk_all()
    served = _served(_client(service), export)
    assert len([name for name in served if name.endswith(".people.bin.gz")]) == TOWN_DAYS + 1
    assert served == _files(export)


def test_days_not_walked_or_not_saved(old_run: Path) -> None:
    service = RunService(old_run)
    client = _client(service)
    assert client.get("/api/run/days/0", headers=AUTH).status_code == 409
    assert json.loads(client.get("/api/run/days", headers=AUTH).content) == []
    service.step()
    assert client.get("/api/run/days/0", headers=AUTH).status_code == 200
    assert client.get("/api/run/days/1/people", headers=AUTH).status_code == 409
    service.walk_all()
    assert client.get("/api/run/days/999", headers=AUTH).status_code == 404
    assert client.get("/api/run/days/x", headers=AUTH).status_code == 422
    assert client.get("/api/run/terrain/nothing.json", headers=AUTH).status_code == 404
    assert client.get("/api/unknown", headers=AUTH).status_code == 404


def test_serving_writes_nothing_to_the_run(old_run: Path) -> None:
    before = _stamps(old_run)
    service = RunService(old_run, cache_days=2)
    service.walk_all()
    client = _client(service)
    for day in reversed(range(OLD_DAYS + 1)):
        client.get(f"/api/run/days/{day}", headers=AUTH)
        client.get(f"/api/run/days/{day}/people", headers=AUTH)
    client.get("/api/run/terrain/manifest.json", headers=AUTH)
    service.poll()
    assert _stamps(old_run) == before
    assert not [p for p in old_run.iterdir() if p.name.endswith(("-wal", "-shm", "-journal"))]


def test_the_server_never_builds_a_store() -> None:
    for name in ("service.py", "server.py"):
        source = (SOURCE / name).read_text()
        assert "WorldStore(" not in source and "import WorldStore" not in source, name
        assert "persistence import" not in source, name


def _extend(root: Path, days: int) -> None:
    """Save more days the way `run` does (a separate writer)."""
    store = WorldStore(root)
    state = replay_run(store)
    rng = StableRng(state.config.seed)
    for _ in range(days):
        transition = advance_day(state, rng)
        store.append_transition(transition.state, transition.events, previous=state)
        state = transition.state


def test_days_saved_later_are_served(old_run: Path) -> None:
    service = RunService(old_run)
    service.walk_all()
    client = _client(service)
    ids_before = len(json.loads(client.get("/api/run/ids", headers=AUTH).content))
    _extend(old_run, 2)
    service.walk_all()
    days = json.loads(client.get("/api/run/days", headers=AUTH).content)
    assert days == list(range(OLD_DAYS + 3))
    record = json.loads(client.get(f"/api/run/days/{OLD_DAYS + 2}", headers=AUTH).content)
    assert record["day"] == OLD_DAYS + 2
    assert len(json.loads(client.get("/api/run/ids", headers=AUTH).content)) >= ids_before


def test_the_follower_thread_picks_up_new_days(old_run: Path) -> None:
    service = RunService(old_run, poll_seconds=0.05)
    service.start()
    try:
        client = _client(service)
        _wait(lambda: _ready(client) == OLD_DAYS + 1)
        _extend(old_run, 1)
        _wait(lambda: _ready(client) == OLD_DAYS + 2)
    finally:
        service.stop()


def _ready(client: TestClient) -> int:
    return int(json.loads(client.get("/api/status", headers=AUTH).content)["ready"])


def _wait(done: object, seconds: float = 20) -> None:
    assert callable(done)
    until = time.monotonic() + seconds
    while not done():
        assert time.monotonic() < until, "timed out"
        time.sleep(0.05)


def test_a_journal_cut_back_is_a_new_history(old_run: Path, tmp_path: Path) -> None:
    service = RunService(old_run)
    service.walk_all()
    client = _client(service)
    journal = old_run / "journal.jsonl"
    lines = journal.read_bytes().splitlines(keepends=True)
    keep = next(
        index
        for index, line in enumerate(lines)
        if b'"type":"transition"' in line and b'"day":2,' in line
    )
    journal.write_bytes(b"".join(lines[: keep + 1]))
    service.walk_all()
    answer = client.get("/api/run/days", headers=AUTH)
    assert answer.headers["x-history-epoch"] == "1"
    assert json.loads(answer.content) == [0, 1, 2]
    assert client.get("/api/run/days/4", headers=AUTH).status_code == 404
    manifest = json.loads(client.get("/api/run/manifest", headers=AUTH).content)
    assert manifest["history_epoch"] == 1 and manifest["days"] == [0, 1, 2]
    # The same three days exported from a copy cut back the same way: the same bytes.
    copy = tmp_path / "copy"
    shutil.copytree(FIXTURE, copy)
    (copy / "journal.jsonl").write_bytes(journal.read_bytes())
    export = tmp_path / "export"
    export_run(copy, export)
    served = _served(client, export)
    files = _files(export)
    # Only the epoch differs: the export read the cut journal fresh.
    assert json.loads(served.pop("manifest.json"))["days"] == [0, 1, 2]
    files.pop("manifest.json")
    assert served == files


def test_the_page_is_served_without_the_token_but_not_the_runs_or_tests(
    old_run: Path,
) -> None:
    client = _client(RunService(old_run))
    page = client.get("/")
    assert page.status_code == 200 and b"<html" in page.content.lower()
    assert client.get("/src/main.js").headers["content-type"].startswith("text/javascript")
    for refused in (
        "/data/runs/anything/manifest.json",
        "/data/runs",
        "/tests/serve.mjs",
        "/tests",
        "/../pyproject.toml",
        "/%2e%2e/pyproject.toml",
        "/src/../../pyproject.toml",
        "/.gitignore",
        "/nothing-here.js",
    ):
        assert client.get(refused).status_code == 404, refused
    assert (UI_ROOT / "index.html").exists()


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


def test_a_run_is_the_same_with_the_observer_attached(tmp_path: Path) -> None:
    """The observer runs as its own process beside the runner; the run's journal (councils
    and their prompt hashes included) and checkpoints come out byte for byte the same."""
    cli = Path(sys.executable).parent / "sovereign-world"
    base = tmp_path / "base"
    subprocess.run(
        [cli, "init", base, "--seed", "21", "--width", "24", "--height", "24"],
        check=True,
        capture_output=True,
    )
    alone, watched = tmp_path / "alone", tmp_path / "watched"
    shutil.copytree(base, alone)
    shutil.copytree(base, watched)
    port = _free_port()
    environment = {**os.environ, "SOVEREIGN_WORLD_OBSERVER_TOKEN": TOKEN}
    observer = subprocess.Popen(
        [cli, "observe", watched, "--port", str(port)],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:

        def status() -> dict[str, object] | None:
            request = urllib.request.Request(f"http://127.0.0.1:{port}/api/status", headers=AUTH)
            try:
                with urllib.request.urlopen(request, timeout=2) as answer:
                    return dict(json.loads(answer.read()))
            except (urllib.error.URLError, ConnectionError):
                return None

        _wait(lambda: status() is not None, seconds=60)
        subprocess.run([cli, "run", watched, "--days", "31"], check=True, capture_output=True)
        _wait(lambda: (status() or {}).get("ready_through") == 31, seconds=60)
        request = urllib.request.Request(f"http://127.0.0.1:{port}/api/run/days/31")
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(request, timeout=2)
        assert refused.value.code == 401
    finally:
        observer.terminate()
        output, errors = observer.communicate(timeout=30)
    assert TOKEN.encode() not in output + errors
    subprocess.run([cli, "run", alone, "--days", "31"], check=True, capture_output=True)
    journal = (watched / "journal.jsonl").read_bytes()
    assert b"prompt_hash" in journal
    assert journal == (alone / "journal.jsonl").read_bytes()
    assert _checkpoints(watched) == _checkpoints(alone)
    assert {path.name for path in watched.iterdir()} == {path.name for path in alone.iterdir()}


def test_changes_rebuild_each_day_from_another(old_run: Path) -> None:
    from sovereign_world.observer.changes import apply_changes
    from sovereign_world.observer.run_export import encode_json

    service = RunService(old_run)
    service.walk_all()
    client = _client(service)
    records = {
        day: client.get(f"/api/run/days/{day}", headers=AUTH).content for day in range(OLD_DAYS + 1)
    }
    for start, day in [(0, 1), (1, 2), (0, OLD_DAYS), (OLD_DAYS, 0), (3, 3)]:
        answer = client.get(f"/api/run/days/{day}/changes?from={start}", headers=AUTH)
        assert answer.status_code == 200
        changes = json.loads(answer.content)
        rebuilt = apply_changes(json.loads(records[start]), changes)
        assert encode_json(rebuilt) == records[day], (start, day)
        assert len(answer.content) <= len(records[day]) + 200
    unchanged = json.loads(client.get("/api/run/days/3/changes?from=3", headers=AUTH).content)
    assert unchanged["settlements"] == [] and unchanged["owners"] == {"set": [], "unset": []}
    with pytest.raises(ValueError):
        apply_changes(json.loads(records[1]), unchanged)
    assert client.get("/api/run/days/2/changes", headers=AUTH).status_code == 422
    assert client.get("/api/run/days/2/changes?from=99", headers=AUTH).status_code == 404


def test_a_run_whose_map_was_edited_is_shown_as_recorded(tmp_path: Path) -> None:
    from observer.war_run import record_war

    from sovereign_world.observer.reader import RunReader

    run = tmp_path / "war"
    record_war(run, 2)
    service = RunService(run)
    service.walk_all()
    client = _client(service)
    manifest = client.get("/api/run/terrain/manifest.json", headers=AUTH).json()
    assert manifest["map"].startswith("the run's own recorded map")
    recorded = RunReader(run).state_at(0).world_map
    rows = []
    for name in [
        f"chunks/c{cq}_{cr}.json"
        for cq in range(manifest["presentation"]["chunks"][0])
        for cr in range(manifest["presentation"]["chunks"][1])
    ]:
        chunk = client.get(f"/api/run/terrain/{name}", headers=AUTH)
        if chunk.status_code == 200:
            rows += chunk.json()["tiles"]
    shown = {(row[0], row[1]): row[2] for row in rows}
    assert shown == {(t.coord.q, t.coord.r): t.terrain.value for t in recorded.tiles}
