"""Every day the observer serves is the day a replay of the run gives (O4): its record is the
projection of `replay_run(store, D)`, and it carries the state hash the run saved for D."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from format_one import FIXTURE
from town_fixture import record_town

from sovereign_world.engine import advance_day
from sovereign_world.observer.projection import project_day
from sovereign_world.observer.run_export import day_record, encode_json
from sovereign_world.persistence import WorldStore
from sovereign_world.replay import replay_run
from sovereign_world.rng import StableRng

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from sovereign_world.observer.server import build_app
from sovereign_world.observer.service import RunService

TOKEN = "replay-" + "t" * 32
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _check_every_day(run: Path, client: TestClient) -> int:
    """Compare each served day with a replay made from a copy (the server never opens a
    store; the test does, on its own copy). Returns how many days were checked."""
    copy = run.parent / f"{run.name}-replayed"
    shutil.rmtree(copy, ignore_errors=True)
    shutil.copytree(run, copy)
    store = WorldStore(copy)
    days = json.loads(client.get("/api/run/days", headers=AUTH).content)
    for day in days:
        served = client.get(f"/api/run/days/{day}", headers=AUTH).content
        replayed = replay_run(store, target_day=day)
        expected = store.state_hash(replayed, fresh=True)
        assert json.loads(served)["state_hash"] == expected, day
        assert served == encode_json(day_record(project_day(replayed), state_hash=expected)), day
    return len(days)


def test_every_served_day_is_its_replay_in_an_old_run(tmp_path: Path) -> None:
    run = tmp_path / "old"
    shutil.copytree(FIXTURE, run)
    service = RunService(run)
    service.walk_all()
    assert _check_every_day(run, TestClient(build_app(service, TOKEN))) == 6


def test_every_served_day_is_its_replay_in_a_designed_town(tmp_path: Path) -> None:
    run = tmp_path / "town"
    record_town(run)
    service = RunService(run)
    service.walk_all()
    assert _check_every_day(run, TestClient(build_app(service, TOKEN))) == 19


def test_days_saved_while_followed_are_their_replay_too(tmp_path: Path) -> None:
    run = tmp_path / "old"
    shutil.copytree(FIXTURE, run)
    service = RunService(run)
    service.walk_all()
    client = TestClient(build_app(service, TOKEN))
    # Another process goes on writing the run (here, in the test).
    store = WorldStore(run)
    state = replay_run(store)
    rng = StableRng(state.config.seed)
    for _ in range(3):
        transition = advance_day(state, rng)
        store.append_transition(transition.state, transition.events, previous=state)
        state = transition.state
    service.walk_all()
    assert _check_every_day(run, client) == 9
