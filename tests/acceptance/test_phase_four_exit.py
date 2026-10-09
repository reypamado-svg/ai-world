"""Phase 4 exit (O6): one fresh run, taken through the whole observer.

The roadmap's gate for Phase 4: "all observer operations leave the authoritative world hash
unchanged, except advancing or restoring already committed history." Here a run is made with
the command line (the current rules and generator), exported with each civilization's
perspective, served, and every route asked. Every day is checked against a replay of the run,
every perspective against its council report and against a world where everything that
civilization cannot know is different, every chronicle event against the map, and at the end
the run's files, bytes and times, are what they were and the run still verifies.

The subprocess tests (a run with the observer attached, a run driven from the page) live with
the server and runner tests; `docs/observer-acceptance.md` lists every promise and its tests.
"""

from __future__ import annotations

import gc
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from noninterference import hide_unseen
from typer.testing import CliRunner

from sovereign_world.cli import app as cli
from sovereign_world.commands import build_council_report
from sovereign_world.config import CURRENT_RULES
from sovereign_world.ids import EntityId
from sovereign_world.observer.perspective import perspective_record
from sovereign_world.observer.projection import project_day
from sovereign_world.observer.run_export import day_record, encode_json, export_run
from sovereign_world.persistence import WorldStore
from sovereign_world.replay import replay_run

pytest.importorskip("fastapi")

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from sovereign_world.observer.server import build_app
from sovereign_world.observer.service import RunService

DAYS = 12
TOKEN = "phase-four-exit-" + "t" * 32
AUTH = {"Authorization": f"Bearer {TOKEN}"}
runner = CliRunner()


def _stamps(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _files(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _api_routes(routes: list[Any]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for route in routes:
        if isinstance(route, APIRoute) and route.path.startswith("/api"):
            found += [(method, route.path) for method in sorted(route.methods)]
        inner = getattr(route, "original_router", None) or getattr(route, "routes", None)
        if inner is not None:
            found += _api_routes(list(getattr(inner, "routes", inner)))
    return found


def test_a_fresh_run_passes_through_the_whole_observer(tmp_path: Path) -> None:
    run = tmp_path / "run"
    made = runner.invoke(cli, ["init", str(run), "--seed", "21", "--width", "24", "--height", "24"])
    assert made.exit_code == 0, made.stdout
    advanced = runner.invoke(cli, ["run", str(run), "--days", str(DAYS)])
    assert advanced.exit_code == 0, advanced.stdout
    # The in-process writer's database connection closes when collected, folding its
    # write-ahead log into the database; only then is the run at rest.
    gc.collect()
    assert not [p for p in run.iterdir() if p.name.endswith(("-wal", "-shm"))]
    before = _stamps(run)

    # The static export, with every civilization's perspective.
    export = tmp_path / "export"
    export_run(run, export, perspectives=True)
    exported = _files(export)

    # The server.
    service = RunService(run)
    service.walk_all()
    observer = build_app(service, TOKEN)
    client = TestClient(observer)
    manifest = client.get("/api/run/manifest", headers=AUTH).json()
    assert manifest["rules_version"] == CURRENT_RULES
    days = manifest["days"]
    assert days == list(range(DAYS + 1))
    civilizations = manifest["civilizations"]

    # (a) Every /api route needs the token.
    fill = {"day": "1", "civ": "0", "path": "manifest.json", "rest": "anything"}
    routes = _api_routes(observer.routes)
    assert len(routes) >= 12
    for method, path in routes:
        url = path.replace(":path", "")
        for name, value in fill.items():
            url = url.replace("{" + name + "}", value)
        assert client.request(method, url, json={}).status_code == 401, (method, url)
        assert client.request(method, url, headers=AUTH, json={}).status_code != 401, url

    # (b) The server's answers are the export's files, byte for byte.
    def ask(path: str) -> bytes:
        answer = client.get(path, headers=AUTH)
        assert answer.status_code == 200, (path, answer.status_code)
        assert answer.headers["x-history-epoch"] == "0"
        return answer.content

    assert ask("/api/run/ids") == exported["ids.json"]
    for name in (n for n in exported if n.startswith("terrain/")):
        assert ask(f"/api/run/terrain/{name.removeprefix('terrain/')}") == exported[name], name

    # (c), (d) Every day is its replay; every perspective is its council's report, and the
    # same from a world where everything that civilization cannot know is different.
    copy = tmp_path / "replayed"
    shutil.copytree(run, copy)
    store = WorldStore(copy)
    for day in days:
        record = ask(f"/api/run/days/{day}")
        assert record == exported[f"days/d{day:06d}.json"], day
        assert ask(f"/api/run/days/{day}/people") == exported[f"days/d{day:06d}.people.bin.gz"]
        replayed = replay_run(store, target_day=day)
        expected_hash = store.state_hash(replayed, fresh=True)
        assert json.loads(record)["state_hash"] == expected_hash, day
        assert record == encode_json(day_record(project_day(replayed), state_hash=expected_hash))
        for number, name in enumerate(civilizations):
            civ = EntityId(name)
            served = ask(f"/api/run/days/{day}/perspective/{number}")
            assert served == exported[f"days/d{day:06d}.perspective.{number}.json"], (day, number)
            assert served == encode_json(perspective_record(build_council_report(replayed, civ)))
            hidden = hide_unseen(replayed, civ)
            assert served == encode_json(perspective_record(build_council_report(hidden, civ)))
        if day > 0:
            changes = json.loads(ask(f"/api/run/days/{day}/changes?from={day - 1}"))
            assert changes["day"] == day and changes["from"] == day - 1
        ask(f"/api/run/days/{day}/routes")

        # (e) Every chronicle event is placed on the map, or says it is not.
        chronicle = json.loads(ask(f"/api/run/chronicle?day={day}"))
        assert chronicle["day"] == day
        for event in chronicle["events"]:
            place = event["place"]
            if place is not None:
                assert 0 <= place["q"] < manifest["width"], event
                assert 0 <= place["r"] < manifest["height"], event

    # (f) No runner is attached, so there is nothing to control.
    assert client.get("/api/control", headers=AUTH).status_code == 409
    assert client.post("/api/control", headers=AUTH, json={"paused": False}).status_code == 409
    status = client.get("/api/status", headers=AUTH).json()
    assert status["runner"] is None and status["ready"] == DAYS + 1

    # (g) Nothing the observer did touched the run, and the run still verifies.
    service.poll()
    assert _stamps(run) == before
    assert not [p for p in run.iterdir() if p.name.endswith(("-wal", "-shm", "-journal"))]
    verified = runner.invoke(cli, ["verify", str(run)])
    assert verified.exit_code == 0, verified.stdout
    assert f"verified through day {DAYS}" in verified.stdout
