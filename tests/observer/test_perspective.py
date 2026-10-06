"""A civilization's perspective (O5): built from its council report alone, saying what the
report says and nothing the civilization could not know."""

from __future__ import annotations

import inspect
import shutil
from pathlib import Path
from typing import Any

import pytest
from format_one import FIXTURE
from logistics_helpers import treaty_world
from noninterference import hide_unseen
from observer.war_run import record_war
from test_noninterference import _far_colony

from sovereign_world.commands import CouncilReport, build_council_report
from sovereign_world.diplomacy import TreatyEndKind
from sovereign_world.endings import Ruin
from sovereign_world.engine import _end_treaty
from sovereign_world.hexmap import HexCoord
from sovereign_world.ids import EntityId
from sovereign_world.observer import perspective
from sovereign_world.observer.perspective import perspective_record
from sovereign_world.observer.reader import RunReader
from sovereign_world.observer.run_export import encode_json
from sovereign_world.persistence import WorldStore
from sovereign_world.replay import replay_run
from sovereign_world.resources import Inventory
from sovereign_world.state import WorldState
from sovereign_world.war import Siege

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from sovereign_world.observer.server import build_app
from sovereign_world.observer.service import RunService

SOURCE = Path(inspect.getfile(perspective))
WAR_DAYS = 20


@pytest.fixture(scope="module")
def war(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("perspective") / "war"
    record_war(root, WAR_DAYS)
    return root


def _state(root: Path, day: int) -> WorldState:
    return RunReader(root).state_at(day)


def test_the_serializer_takes_only_a_council_report() -> None:
    parameters = list(inspect.signature(perspective_record).parameters.values())
    assert [p.name for p in parameters] == ["report"]
    assert parameters[0].annotation in ("CouncilReport", CouncilReport)
    source = SOURCE.read_text()
    for forbidden in (
        "WorldState",
        "RunReader",
        "WorldStore",
        "state_at",
        "project_day",
        "build_council_report(",
        "import build_council_report",
        "sovereign_world.state",
        "sovereign_world.engine",
        "sovereign_world.persistence",
        "observer.reader",
    ):
        assert forbidden not in source, forbidden


def test_the_record_says_what_the_report_says(war: Path) -> None:
    state = _state(war, WAR_DAYS)
    for civilization_id in sorted(state.civilizations):
        report = build_council_report(state, civilization_id)
        record = perspective_record(report)
        assert record["day"] == WAR_DAYS
        assert record["civilization"] == str(civilization_id)
        assert record["known_tiles"] == sorted([t.q, t.r] for t in report.known_tiles)
        # Known terrain is the recorded map's.
        for q, r, terrain in record["known_terrain"]:
            assert state.world_map.tile(HexCoord(q=q, r=r)).terrain.value == terrain
        assert [row["id"] for row in record["settlements"]] == [
            str(s.settlement_id) for s in report.settlements
        ]
        assert {s["civilization"] for s in record["settlements"]} == {str(civilization_id)}
        assert report.population is not None
        for row in record["settlements"]:
            assert row["residents"] == report.population.residents.get(EntityId(row["id"]), 0)
        assert record["counts"]["at_home"] == sum(report.population.residents.values())
        assert len(record["foreign_settlements"]) == len(report.contacts)
        assert {(c["q"], c["r"]) for c in record["foreign_settlements"]} == {
            (c.settlement.q, c.settlement.r) for c in report.contacts
        }
        assert record["tile_dates"] == sorted(
            [v.tile.q, v.tile.r, v.as_of_day, None if v.owner is None else str(v.owner)]
            for v in report.observed_control
        )
        parties = {p["id"] for p in record["parties"]}
        assert parties == {
            str(j.journey_id)
            for j in (*report.spy_missions, *report.extractions, *report.petitions)
        }
        assert len(record["people"]["notable"]) == len(report.notable_people)
        if report.notable_people:
            first, row = report.notable_people[0], record["people"]["notable"][0]
            assert row["id"] == str(first.person_id)
            assert row["age"] == first.age_years
            assert row["duty_label"] == first.duty
    # The two at war have met: each knows the other's settlement; the others know none.
    known = [
        len(perspective_record(build_council_report(state, c))["foreign_settlements"])
        for c in sorted(state.civilizations)
    ]
    assert known == [1, 1, 0, 0]


def test_the_same_report_gives_the_same_bytes(war: Path) -> None:
    state = _state(war, WAR_DAYS)
    civilization_id = sorted(state.civilizations)[0]
    first = encode_json(perspective_record(build_council_report(state, civilization_id)))
    again = encode_json(
        perspective_record(build_council_report(_state(war, WAR_DAYS), civilization_id))
    )
    assert first == again


def test_an_old_rules_report_serializes(tmp_path: Path) -> None:
    run = tmp_path / "old"
    shutil.copytree(FIXTURE, run)
    state = _state(run, 5)
    for civilization_id in sorted(state.civilizations):
        record = perspective_record(build_council_report(state, civilization_id))
        assert record["rules_version"] == 1
        for row in record["settlements"]:
            assert row["houses"] == {} and row["rank"] == "village"
            assert "plan" not in row and "walls" not in row


def test_the_record_follows_the_reports_own_knowledge(war: Path) -> None:
    state = _state(war, WAR_DAYS)
    report = build_council_report(state, sorted(state.civilizations)[0])
    fewer = report.model_copy(update={"known_tiles": report.known_tiles[1:]})
    assert encode_json(perspective_record(fewer)) != encode_json(perspective_record(report))


# ------------------------------------------------------------------ the endpoint (C1)
TOKEN = "perspective-" + "t" * 32
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _served(root: Path) -> tuple[RunService, TestClient]:
    service = RunService(root)
    service.walk_all()
    return service, TestClient(build_app(service, TOKEN))


def _bytes(state: WorldState, civilization_id: EntityId) -> bytes:
    return encode_json(perspective_record(build_council_report(state, civilization_id)))


@pytest.mark.parametrize("which", ["war", "old"])
def test_the_endpoint_is_the_report_of_the_days_replay(
    which: str, war: Path, tmp_path: Path
) -> None:
    if which == "war":
        run = war
    else:
        run = tmp_path / "old"
        shutil.copytree(FIXTURE, run)
    _, client = _served(run)
    copy = tmp_path / "replayed"
    shutil.copytree(run, copy)
    store = WorldStore(copy)
    manifest = client.get("/api/run/manifest", headers=AUTH).json()
    civilizations = [EntityId(c) for c in manifest["civilizations"]]
    for day in client.get("/api/run/days", headers=AUTH).json():
        replayed = replay_run(store, target_day=day)
        for number, civilization_id in enumerate(civilizations):
            answer = client.get(f"/api/run/days/{day}/perspective/{number}", headers=AUTH)
            assert answer.status_code == 200, (day, number)
            assert answer.headers["x-history-epoch"] == "0"
            assert answer.content == _bytes(replayed, civilization_id), (day, number)
            again = client.get(f"/api/run/days/{day}/perspective/{number}", headers=AUTH)
            assert again.content == answer.content


def test_the_perspective_does_not_change_when_hidden_facts_change(war: Path) -> None:
    """The O5 noninterference test: what the endpoint serves for a civilization is what it
    would serve from a world where everything that civilization cannot know is different."""
    _, client = _served(war)
    reader = RunReader(war)
    manifest = client.get("/api/run/manifest", headers=AUTH).json()
    for day in (0, 10, WAR_DAYS):
        state = reader.state_at(day)
        for number, name in enumerate(manifest["civilizations"]):
            civilization_id = EntityId(name)
            served = client.get(f"/api/run/days/{day}/perspective/{number}", headers=AUTH)
            assert served.content == _bytes(hide_unseen(state, civilization_id), civilization_id)


def _unlearned_capture(state: WorldState, home: EntityId, rival: EntityId) -> EntityId:
    taken = state.civilizations[home].population.living_ids[0]
    person = state.civilizations[home].population.people[taken]
    person.captive_of = rival
    person.held_at = state.civilizations[rival].settlements[0].settlement_id
    return home


def _unseen_siege(state: WorldState, home: EntityId, rival: EntityId) -> EntityId:
    colony = _far_colony(state, rival)
    state.sieges = (
        Siege(
            siege_id=EntityId("siege:far"),
            journey_id=EntityId("journey:far"),
            besieger_id=home,
            defender_id=rival,
            settlement_id=colony.settlement_id,
            settlement_tile=colony.tile,
            camp=HexCoord(q=colony.tile.q + 1, r=colony.tile.r),
            started_day=0,
        ),
    )
    return rival


def _unheard_treaty_ending(state: WorldState, home: EntityId, rival: EntityId) -> EntityId:
    [treaty] = state.active_treaties
    _end_treaty(state, treaty.treaty_id, TreatyEndKind.BREACHED, home)
    return rival


def _unseen_ruin(state: WorldState, home: EntityId, rival: EntityId) -> EntityId:
    colony = _far_colony(state, rival)
    state.ruins = (
        Ruin(
            ruin_id=EntityId("ruin:far"),
            tile=colony.tile,
            former_settlement_id=colony.settlement_id,
            former_civilization_id=rival,
            since_day=0,
            store=Inventory(capacity=2_000),
        ),
    )
    return home


@pytest.mark.parametrize(
    "hide", [_unlearned_capture, _unseen_siege, _unheard_treaty_ending, _unseen_ruin]
)
def test_each_hidden_fact_leaves_the_perspective_alone(hide: Any) -> None:
    state, home, rival, _ = treaty_world(distance=4)
    viewer = hide(state, home, rival)
    assert _bytes(hide_unseen(state, viewer), viewer) == _bytes(state, viewer)


def test_a_perspective_changes_with_its_own_facts() -> None:
    state, home, rival, _ = treaty_world(distance=4)
    before = _bytes(state, home)
    _far_colony(state, home)
    assert _bytes(state, home) != before
    assert _bytes(hide_unseen(state, rival), rival) == _bytes(state, rival)


def test_perspective_errors(tmp_path: Path) -> None:
    run = tmp_path / "old"
    shutil.copytree(FIXTURE, run)
    service = RunService(run)
    client = TestClient(build_app(service, TOKEN))
    service.step()
    assert client.get("/api/run/days/1/perspective/0", headers=AUTH).status_code == 409
    service.walk_all()
    assert client.get("/api/run/days/999/perspective/0", headers=AUTH).status_code == 404
    assert client.get("/api/run/days/1/perspective/4", headers=AUTH).status_code == 404
    assert client.get("/api/run/days/1/perspective/-1", headers=AUTH).status_code == 404
    assert client.get("/api/run/days/1/perspective/x", headers=AUTH).status_code == 422
    assert client.get("/api/run/days/1/perspective/0").status_code == 401


def test_serving_perspectives_writes_nothing_to_the_run(tmp_path: Path) -> None:
    run = tmp_path / "old"
    shutil.copytree(FIXTURE, run)
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in run.iterdir()}
    _, client = _served(run)
    for day in range(6):
        for number in range(4):
            assert client.get(f"/api/run/days/{day}/perspective/{number}", headers=AUTH).is_success
    assert {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in run.iterdir()} == before


def test_the_service_builds_the_report_in_one_place() -> None:
    service = (SOURCE.parent / "service.py").read_text()
    assert service.count("build_council_report(") == 1
    assert "build_council_report" not in (SOURCE.parent / "server.py").read_text()
