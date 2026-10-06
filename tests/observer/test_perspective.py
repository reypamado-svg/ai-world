"""A civilization's perspective (O5): built from its council report alone, saying what the
report says and nothing the civilization could not know."""

from __future__ import annotations

import inspect
import shutil
from pathlib import Path

import pytest
from format_one import FIXTURE
from observer.war_run import record_war

from sovereign_world.commands import CouncilReport, build_council_report
from sovereign_world.hexmap import HexCoord
from sovereign_world.ids import EntityId
from sovereign_world.observer import perspective
from sovereign_world.observer.perspective import perspective_record
from sovereign_world.observer.reader import RunReader
from sovereign_world.observer.run_export import encode_json
from sovereign_world.state import WorldState

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
