"""The chronicle places each event where the record puts it (O3)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from format_one import FIXTURE
from observer.war_run import record_war

from sovereign_world.events import DomainEvent, EventPhase
from sovereign_world.hexmap import HexCoord
from sovereign_world.ids import EntityId
from sovereign_world.observer.chronicle import (
    DAY_BEFORE,
    ROUTINE,
    TODAY,
    PlaceIndex,
    chronicle_entries,
    place,
)
from sovereign_world.observer.reader import RunReader
from sovereign_world.state import WorldState

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from sovereign_world.observer.server import build_app
from sovereign_world.observer.service import RunService

TOKEN = "chronicle-" + "t" * 32
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _event(
    kind: str,
    actor: str | None = None,
    subject: str | None = None,
    **payload: Any,
) -> DomainEvent:
    return DomainEvent(
        run_id=UUID(int=11),
        day=1,
        phase=EventPhase.REPORT,
        sequence=1,
        kind=kind,
        actor_id=actor,
        subject_id=subject,
        payload=payload,
    )


@pytest.fixture(scope="module")
def days() -> tuple[WorldState, WorldState]:
    reader = RunReader(FIXTURE)
    return reader.state_at(0), reader.state_at(1)


def _first_settlement(state: WorldState) -> tuple[str, str, HexCoord]:
    civilization_id, civilization = next(iter(state.civilizations.items()))
    settlement = civilization.settlements[0]
    return str(civilization_id), str(settlement.settlement_id), settlement.tile


def test_an_event_with_its_own_tile_is_placed_there(days: tuple[WorldState, WorldState]) -> None:
    before, today = PlaceIndex(days[0]), PlaceIndex(days[1])
    civilization_id, settlement_id, _ = _first_settlement(days[1])
    spot = place(_event("control_gained", civilization_id, "tile:9,9", q=3, r=4), today, before)
    assert spot is not None and (spot.q, spot.r, spot.basis) == (3, 4, "recorded")
    spot = place(_event("tile_observed", civilization_id, tile_q=5, tile_r=6), today, before)
    assert spot is not None and (spot.q, spot.r) == (5, 6)
    # A flag is not a coordinate; the settlement named is used instead.
    spot = place(_event("x", civilization_id, settlement=settlement_id, q=True, r=1), today, before)
    assert spot is not None and spot.basis == settlement_id


def test_ids_are_looked_up_in_the_days_world_in_order(days: tuple[WorldState, WorldState]) -> None:
    before, today = PlaceIndex(days[0]), PlaceIndex(days[1])
    civilization_id, settlement_id, settlement_tile = _first_settlement(days[1])
    civilization = days[1].civilizations[EntityId(civilization_id)]
    person_id = civilization.population.living_ids[-1]
    person_tile = civilization.population.people[person_id].location
    # The payload's settlement comes before the subject and the actor.
    spot = place(_event("x", civilization_id, person_id, settlement=settlement_id), today, before)
    assert spot is not None and (spot.q, spot.r) == (settlement_tile.q, settlement_tile.r)
    assert (spot.basis, spot.as_of, spot.capital) == (settlement_id, TODAY, False)
    # Then the subject: a person where they stand that day.
    spot = place(_event("x", civilization_id, person_id), today, before)
    assert spot is not None and (spot.q, spot.r) == (person_tile.q, person_tile.r)
    assert spot.basis == person_id
    # Then the actor: a civilization at its capital, and said so.
    spot = place(_event("x", civilization_id, "council:unknown"), today, before)
    capital = next(s for s in civilization.settlements if s.capital).tile
    assert spot is not None and (spot.q, spot.r, spot.capital) == (capital.q, capital.r, True)
    assert spot.record() == {
        "q": capital.q,
        "r": capital.r,
        "basis": civilization_id,
        "as_of": TODAY,
        "capital": True,
    }
    # A tile id is a tile.
    spot = place(_event("x", None, "tile:7,8"), today, before)
    assert spot is not None and (spot.q, spot.r) == (7, 8)


def test_endings_look_at_the_day_before_first(days: tuple[WorldState, WorldState]) -> None:
    before, today = PlaceIndex(days[0]), PlaceIndex(days[1])
    civilization_id, settlement_id, _ = _first_settlement(days[1])
    spot = place(_event("person_died", civilization_id, settlement_id), today, before)
    assert spot is not None and spot.as_of == DAY_BEFORE
    spot = place(_event("house_built", civilization_id, settlement_id), today, before)
    assert spot is not None and spot.as_of == TODAY


def test_an_id_missing_from_one_day_is_found_in_the_other(
    days: tuple[WorldState, WorldState],
) -> None:
    civilization_id, settlement_id, tile = _first_settlement(days[1])
    gone = days[1].model_copy(deep=True)
    civilization = gone.civilizations[EntityId(civilization_id)]
    civilization.settlements = tuple(
        s for s in civilization.settlements if s.settlement_id != settlement_id
    )
    today, before = PlaceIndex(gone), PlaceIndex(days[0])
    spot = place(_event("founded", None, settlement_id), today, before)
    assert spot is not None and (spot.q, spot.r, spot.as_of) == (tile.q, tile.r, DAY_BEFORE)
    # Found in neither: not placed.
    assert place(_event("x", None, "settlement:none"), today, before) is None
    assert place(_event("x", None, None), today, None) is None
    assert place(_event("x", None, "tile:north,3"), today, before) is None


def test_a_journey_is_where_its_first_traveller_stands(
    days: tuple[WorldState, WorldState],
) -> None:
    state = days[1].model_copy(deep=True)
    civilization_id, _, _ = _first_settlement(state)
    civilization = state.civilizations[EntityId(civilization_id)]
    traveller = civilization.population.living_ids[0]
    civilization.population.people[traveller].location = HexCoord(2, 3)
    index = PlaceIndex(state)
    index._parties["journey:test"] = ((str(traveller),), (HexCoord(9, 9),), 0)
    index._parties["journey:empty"] = ((), (HexCoord(1, 1), HexCoord(4, 4)), 5)
    found = index.find("journey:test")
    assert found == (HexCoord(2, 3), False)
    assert index.find("journey:empty") == (HexCoord(4, 4), False)


def test_a_war_is_placed_on_the_map(tmp_path: Path) -> None:
    run = tmp_path / "war"
    record_war(run)
    service = RunService(run)
    service.walk_all()
    client = TestClient(build_app(service, TOKEN))
    reader = RunReader(run)
    width, height = reader.manifest().config.width, reader.manifest().config.height
    kinds: dict[str, list[dict[str, Any]]] = {}
    placed = unplaced = 0
    for day in reader.days()[1:]:
        answer = client.get(f"/api/run/chronicle?day={day}", headers=AUTH)
        assert answer.status_code == 200
        record = json.loads(answer.content)
        assert record["day"] == day
        for entry in record["events"]:
            assert entry["routine"] == (entry["kind"] in ROUTINE)
            kinds.setdefault(entry["kind"], []).append(entry)
            spot = entry["place"]
            if spot is None:
                unplaced += 1
                continue
            placed += 1
            assert 0 <= spot["q"] < width and 0 <= spot["r"] < height, entry
            assert spot["basis"] == "recorded" or spot["as_of"] in (TODAY, DAY_BEFORE)
    assert {"battle_won", "settlement_ceded", "peace_made"} <= set(kinds), sorted(kinds)
    assert placed > 0 and unplaced <= placed // 20, (placed, unplaced)
    for entry in kinds["battle_won"]:
        assert entry["place"] is not None and not entry["place"].get("capital"), entry
    [ceded] = kinds["settlement_ceded"]
    colony = next(
        s
        for c in reader.state_at(ceded["day"]).civilizations.values()
        for s in c.settlements
        if not s.capital
    )
    assert ceded["place"] is not None
    assert (ceded["place"]["q"], ceded["place"]["r"]) == (colony.tile.q, colony.tile.r)
    # The war party on the road: its route, and where its first traveller stands.
    on_road = []
    for day in reader.days():
        parties = json.loads(client.get(f"/api/run/days/{day}/routes", headers=AUTH).content)
        assert parties["day"] == day
        on_road += [party for party in parties["parties"] if party["kind"] == "campaign"]
    assert on_road, "a war party was on the road"
    for party in on_road:
        assert party["route"] and 0 <= party["at"] <= len(party["route"])
        assert party["civilization"] in (0, 1) and party["people"]
        if party["tile"] is not None:
            q, r = party["tile"]
            assert 0 <= q < width and 0 <= r < height
    # The same day twice is the same bytes; a day not saved is not found.
    first = client.get("/api/run/chronicle?day=3", headers=AUTH).content
    assert client.get("/api/run/chronicle?day=3", headers=AUTH).content == first
    assert client.get("/api/run/chronicle?day=999", headers=AUTH).status_code == 404
    assert client.get("/api/run/chronicle", headers=AUTH).status_code == 422


def test_the_format_one_chronicle_matches_its_events(tmp_path: Path) -> None:
    run = tmp_path / "old"
    shutil.copytree(FIXTURE, run)
    reader = RunReader(run)
    entries = chronicle_entries(reader.events_at(1), reader.state_at(1), reader.state_at(0))
    events = reader.events_at(1)
    assert [entry["kind"] for entry in entries] == [event.kind for event in events]
    assert [entry["sequence"] for entry in entries] == [event.sequence for event in events]
    assert all(entry["place"] is not None for entry in entries if not entry["routine"])
