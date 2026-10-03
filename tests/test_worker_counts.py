"""Rules 2: work at home may count its workers at a settlement instead of naming them."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from logistics_helpers import OneShotSovereign
from parity import SCENARIOS, initial
from pydantic import ValidationError

from sovereign_world.commands import (
    GROWN_DAYS,
    CommandEnvelope,
    DirectOrder,
    DirectOrderKind,
    ProjectKind,
    build_council_report,
    idle_at,
    validate_envelope,
)
from sovereign_world.config import RunManifest
from sovereign_world.engine import advance_day
from sovereign_world.gateway.envelope import parse_reply, to_envelope
from sovereign_world.gateway.prompt import charter, workers_rule
from sovereign_world.gateway.provider import ScriptedProvider
from sovereign_world.gateway.records import journal_councils
from sovereign_world.gateway.sovereign import GatewaySovereign
from sovereign_world.ids import EntityId
from sovereign_world.institutions import InstitutionKind
from sovereign_world.persistence import WorldStore
from sovereign_world.replay import rederive_run
from sovereign_world.rng import StableRng
from sovereign_world.scripted import BaselineSovereign
from sovereign_world.state import WorldState, state_hash


def _world(rules: int = 2) -> tuple[WorldState, EntityId, EntityId]:
    state = initial(SCENARIOS[1] if rules == 2 else SCENARIOS[0])
    civilization_id = sorted(state.civilizations)[0]
    capital = state.civilizations[civilization_id].settlements[0].settlement_id
    return state, civilization_id, capital


def _project(count: int | None, settlement: EntityId | None, **update) -> DirectOrder:
    return DirectOrder(
        command_id=update.pop("command_id", "project"),
        kind=DirectOrderKind.START_PROJECT,
        project_id=EntityId(update.pop("project_id", "project:storage:counted")),
        project_kind=ProjectKind.STORAGE,
        worker_count=count,
        settlement_id=settlement,
        **update,
    )


def _hall(**fields) -> DirectOrder:
    return DirectOrder(
        command_id="hall",
        kind=DirectOrderKind.FOUND_INSTITUTION,
        institution_kind=InstitutionKind.HALL,
        **fields,
    )


def _validate(state: WorldState, civilization_id: EntityId, *orders: DirectOrder):
    envelope = CommandEnvelope(
        schema_version=2,
        civilization_id=civilization_id,
        council_day=state.day,
        correlation_id="test",
        commands=orders,
    )
    return validate_envelope(envelope, state)


def _brute_idle(state, civilization_id, settlement_id, busy=frozenset()) -> list[EntityId]:
    civilization = state.civilizations[civilization_id]
    tile = next(
        item.tile for item in civilization.settlements if item.settlement_id == settlement_id
    )
    return sorted(
        person_id
        for person_id, person in civilization.population.people.items()
        if person.alive
        and person.captive_of is None
        and person.age_days >= GROWN_DAYS
        and person.location == tile
        and person_id not in busy
    )


def test_a_counted_order_takes_the_lowest_numbered_idle_grown_ups() -> None:
    state, civilization_id, capital = _world()
    result = _validate(state, civilization_id, _project(3, capital))
    assert not result.errors
    (order,) = result.accepted
    assert isinstance(order, DirectOrder)
    assert list(order.worker_ids) == _brute_idle(state, civilization_id, capital)[:3]


def test_orders_in_one_council_never_share_people() -> None:
    state, civilization_id, capital = _world()
    idle = _brute_idle(state, civilization_id, capital)
    named_first = _hall(worker_ids=(idle[0],))
    result = _validate(
        state,
        civilization_id,
        named_first,
        _project(2, capital),
        _project(2, capital, command_id="second", project_id="project:storage:second"),
    )
    assert not result.errors
    _, first, second = result.accepted
    assert isinstance(first, DirectOrder) and isinstance(second, DirectOrder)
    assert list(first.worker_ids) == idle[1:3]
    assert list(second.worker_ids) == idle[3:5]
    # Counted first, a later order naming one of them is refused.
    result = _validate(state, civilization_id, _project(2, capital), _hall(worker_ids=(idle[0],)))
    assert [error.code for error in result.errors] == ["person_travelling"]


@pytest.mark.parametrize(
    ("order", "code"),
    [
        (_project(2, None), "invalid_workers"),
        (_project(None, EntityId("settlement:0000000001-0001")), "invalid_workers"),
        (
            _project(2, EntityId("settlement:0000000001-0001"), worker_ids=("person:0000000001",)),
            "invalid_workers",
        ),
        (
            DirectOrder(
                command_id="far",
                kind=DirectOrderKind.START_EXPEDITION,
                worker_count=2,
                settlement_id=EntityId("settlement:0000000001-0001"),
            ),
            "invalid_workers",
        ),
        (_project(2, EntityId("settlement:0000000009-0001")), "unknown_settlement"),
        (_project(33, EntityId("settlement:0000000001-0001")), "too_few_idle_workers"),
    ],
)
def test_each_way_a_count_is_refused(order: DirectOrder, code: str) -> None:
    state, civilization_id, capital = _world()
    assert capital == "settlement:0000000001-0001"
    result = _validate(state, civilization_id, order)
    assert [error.code for error in result.errors] == [code]
    assert not result.accepted


def test_rules_one_takes_named_workers_only() -> None:
    state, civilization_id, capital = _world(rules=1)
    result = _validate(state, civilization_id, _project(2, capital))
    assert [error.code for error in result.errors] == ["invalid_workers"]
    assert "named workers only" in result.errors[0].message


def test_the_report_counts_exactly_who_an_order_can_take() -> None:
    state, civilization_id, capital = _world()
    report = build_council_report(state, civilization_id)
    assert report.population is not None
    idle = report.population.idle_workers[capital]
    assert idle == len(_brute_idle(state, civilization_id, capital))
    assert not _validate(state, civilization_id, _project(idle, capital)).errors
    refused = _validate(state, civilization_id, _project(idle + 1, capital))
    assert [error.code for error in refused.errors] == ["too_few_idle_workers"]


def test_drill_counts_only_those_able_to_fight() -> None:
    state, civilization_id, capital = _world()
    state = state.model_copy(deep=True)
    table = state.civilizations[civilization_id].population.people.table
    oldest = _brute_idle(state, civilization_id, capital)[0]
    table.person(table.index[oldest]).age_days = 70 * 365
    tile = state.civilizations[civilization_id].settlements[0].tile
    assert oldest not in idle_at(table, tile, set(), 5, fit=True)
    assert oldest in idle_at(table, tile, set(), 5)


def test_the_engine_starts_the_work_with_the_people_counted() -> None:
    state, civilization_id, capital = _world()
    expected = _brute_idle(state, civilization_id, capital)[:2]
    rng = StableRng(state.config.seed)
    after = advance_day(
        state, rng, sovereigns={civilization_id: OneShotSovereign(_project(2, capital))}
    ).state
    orders = after.civilizations[civilization_id].work_orders
    assert [list(order.worker_ids) for order in orders] == [expected]


def test_envelopes_and_orders_keep_their_old_shape() -> None:
    state, civilization_id, _ = _world()
    report = build_council_report(state, civilization_id)
    with pytest.raises(ValidationError):
        CommandEnvelope(
            schema_version=3, civilization_id=civilization_id, council_day=0, correlation_id="x"
        )
    assert to_envelope(parse_reply('{"commands": []}'), report).schema_version == 2
    baseline = BaselineSovereign().decide(report)
    assert baseline.schema_version == 1
    assert not validate_envelope(baseline, state).errors
    for order in baseline.commands:
        dumped = order.model_dump(mode="json")
        assert "worker_count" not in dumped and "settlement_id" not in dumped


def test_rules_two_is_told_it_may_count_workers() -> None:
    rules_two = charter(build_council_report(_world()[0], _world()[1]))
    rules_one = charter(build_council_report(_world(1)[0], _world(1)[1]))
    assert workers_rule() in rules_two
    assert workers_rule() not in rules_one
    assert "worker_count" in rules_two


def test_a_counted_reply_is_recorded_and_rederived(tmp_path: Path) -> None:
    state, civilization_id, capital = _world()
    reply = json.dumps(
        {
            "commands": [
                {
                    "command_id": "store",
                    "kind": "start_project",
                    "project_id": "project:storage:counted",
                    "project_kind": "storage",
                    "worker_count": 2,
                    "settlement_id": capital,
                }
            ],
            "rationale": "Two hands for a granary.",
        }
    )
    manifest = RunManifest.model_validate(
        {
            "run_id": state.run_id,
            "config": state.config,
            "engine_version": "0.1.0",
            "rules_version": 2,
        }
    )
    store = WorldStore.create(tmp_path / "record", manifest, state)
    sovereigns = {civilization_id: GatewaySovereign(ScriptedProvider([reply]))}
    rng = StableRng(state.config.seed)
    for _ in range(3):
        transition = advance_day(state, rng, sovereigns=sovereigns)
        state = transition.state
        store.append_transition(state, transition.events)
        journal_councils(store, sovereigns.values())
    assert [list(order.worker_ids) for order in state.civilizations[civilization_id].work_orders]
    assert rederive_run(store).state_hash == state_hash(state)
