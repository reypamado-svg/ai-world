"""Council reports read the people from the columns (Phase 5 S6, C7): the same lists as
before, a population summary and notable people, and nobody marked as changed."""

from __future__ import annotations

from copy import deepcopy

import numpy as np
import pytest
from parity import SCENARIOS, initial
from perf.synthetic import grown_world
from test_journal_deltas import _cession_world

from sovereign_world.commands import (
    ELDER_YEARS,
    GROWN_DAYS,
    NOTABLE_PEOPLE,
    _busy_for_orders,
    build_council_report,
)
from sovereign_world.culture import ancestry, culture
from sovereign_world.engine import advance_day
from sovereign_world.housing import residents_by_settlement
from sovereign_world.ids import EntityId
from sovereign_world.languages import native, speaks
from sovereign_world.logistics import JourneyKind, JourneyOutcome, JourneyPhase
from sovereign_world.people import FERTILE_HEALTH_BP
from sovereign_world.people_store import Sex
from sovereign_world.rng import StableRng
from sovereign_world.scripted import BaselineSovereign
from sovereign_world.state import WorldState


def _emigrants(state: WorldState, civilization_id: EntityId) -> set[EntityId]:
    latest = {}
    for journey in sorted(state.journeys, key=lambda item: (item.departed_day, item.journey_id)):
        if journey.kind is JourneyKind.MIGRATION:
            for person_id in journey.traveller_ids:
                latest[person_id] = journey
    return {
        person_id
        for person_id, journey in latest.items()
        if journey.sender_civilization_id == civilization_id
        and not (
            journey.phase is JourneyPhase.COMPLETE
            and journey.outcome in {JourneyOutcome.FAILED, JourneyOutcome.REFUSED}
        )
    }


def _old_people_fields(state: WorldState, civilization_id: EntityId) -> dict:
    """The report's people fields as they were worked out before, person by person."""
    civilization = state.civilizations[civilization_id]
    # A copy: reading people's dicts through proxies would mark them as changed.
    people = deepcopy(civilization.population.people)
    known_captives = set(civilization.known_captives)
    emigrants = _emigrants(state, civilization_id)
    found: dict[EntityId, list[EntityId]] = {}
    cultures: dict[EntityId, int] = {}
    ancestries: dict[EntityId, int] = {}
    assimilating = 0
    for person_id, person in sorted(people.items()):
        if not person.alive or person_id in known_captives:
            continue
        tongues = {native(person)} | {
            language for language in person.languages if speaks(person, language)
        }
        for language in tongues:
            found.setdefault(language, []).append(person_id)
        cultures[culture(person)] = cultures.get(culture(person), 0) + 1
        for origin in ancestry(person):
            ancestries[origin] = ancestries.get(origin, 0) + 1
        assimilating += person.culture is not None
    return {
        "person_ids": tuple(
            sorted(
                person_id
                for person_id in people
                if person_id not in emigrants and person_id not in known_captives
            )
        ),
        "speakers": {language: tuple(found[language]) for language in sorted(found)},
        "cultures": dict(sorted(cultures.items())),
        "ancestries": dict(sorted(ancestries.items())),
        "assimilating": assimilating,
        "captives": tuple(
            sorted(
                person_id
                for other in state.civilizations.values()
                for person_id, person in deepcopy(other.population.people).items()
                if person.alive and person.captive_of == civilization_id
            )
        ),
    }


def _mixed_world() -> WorldState:
    """A rules-2 world after 40 days, with newcomers, speakers, captives and the dead."""
    state = initial(SCENARIOS[1])
    sovereigns = {civilization_id: BaselineSovereign() for civilization_id in state.civilizations}
    rng = StableRng(state.config.seed)
    for _ in range(40):
        state = advance_day(state, rng, sovereigns=sovereigns).state
    state = state.model_copy(deep=True)
    first, second, *_ = sorted(state.civilizations)
    home = state.civilizations[first]
    living = home.population.living_ids
    for number, person_id in enumerate(living[:6]):
        person = home.population.people[person_id]
        person.culture = second
        person.ancestry = (second,) if number % 2 else (first, second)
        person.native_language = second if number % 3 else None
        person.languages = {first: 40 + 10 * number, second: 70}
    captive = state.civilizations[second].population.people[
        state.civilizations[second].population.living_ids[0]
    ]
    captive.captive_of = first
    captive.held_at = home.settlements[0].settlement_id
    home.known_captives = (living[7],)
    home.population.people[living[8]].alive = False
    home.population.people[living[8]].death_day = state.day - 1
    return state


def _worlds() -> list[WorldState]:
    worlds = [_mixed_world()]
    state, step = _cession_world()
    for day in range(1, 151):
        state = step(state).state
        if day in (60, 150):
            worlds.append(state)
    return worlds


@pytest.fixture(scope="module")
def worlds() -> list[WorldState]:
    return _worlds()


def test_the_people_lists_are_as_they_were(worlds) -> None:
    for state in worlds:
        for civilization_id in sorted(state.civilizations):
            report = build_council_report(state, civilization_id)
            expected = _old_people_fields(state, civilization_id)
            for name, value in expected.items():
                assert getattr(report, name) == value, name


def test_a_report_marks_nobody_as_changed(worlds) -> None:
    for state in worlds:
        state = state.model_copy(deep=True)
        for civilization_id in sorted(state.civilizations):
            tables = {
                key: civilization.population.people.table
                for key, civilization in state.civilizations.items()
            }
            before = {key: table.objs_dirty.copy() for key, table in tables.items()}
            build_council_report(state, civilization_id)
            for key, table in tables.items():
                assert np.array_equal(table.objs_dirty, before[key])


def test_the_population_summary_counts_as_one_would_by_hand(worlds) -> None:
    for state in worlds:
        for civilization_id in sorted(state.civilizations):
            report = build_council_report(state, civilization_id)
            summary = report.population
            assert summary is not None
            civilization = state.civilizations[civilization_id]
            people = deepcopy(civilization.population.people)
            hidden = _emigrants(state, civilization_id) | set(civilization.known_captives)
            free = [
                person
                for person_id, person in people.items()
                if person.alive and person_id not in hidden
            ]
            assert summary.living == len(free)
            assert summary.children == sum(person.age_days < GROWN_DAYS for person in free)
            assert summary.elders == sum(person.age_days // 365 >= ELDER_YEARS for person in free)
            assert summary.grown == len(free) - summary.children - summary.elders
            assert summary.women_able_to_conceive == sum(
                person.sex is Sex.FEMALE
                and 18 * 365 <= person.age_days <= 42 * 365
                and person.health_bp >= FERTILE_HEALTH_BP
                for person in free
            )
            assert summary.hungry == sum(person.nutrition_debt > 0 for person in free)
            assert summary.ailing == sum(person.health_bp < FERTILE_HEALTH_BP for person in free)
            assert summary.newcomers == sum(person.culture is not None for person in free)
            assert summary.dead_this_year == sum(
                not person.alive
                and person.death_day is not None
                and person.death_day > state.day - 365
                for person in people.values()
            )
            residents = residents_by_settlement(state, civilization_id)
            assert summary.residents == {
                item.settlement_id: len(residents.get(item.settlement_id, ()))
                for item in civilization.settlements
            }
            busy = _busy_for_orders(state, civilization_id)
            assert summary.idle_workers == {
                item.settlement_id: sum(
                    person.age_days >= GROWN_DAYS
                    and person.captive_of is None
                    and person.location == item.tile
                    and person.person_id not in busy
                    for person in free
                )
                for item in civilization.settlements
            }
            assert summary.speakers == {
                language: len(found) for language, found in report.speakers.items()
            }


def test_notable_people_are_those_on_duty_then_the_idle_from_each_settlement() -> None:
    state = grown_world(2_000, size=24, generator=3, rules=2)
    sovereigns = {civilization_id: BaselineSovereign() for civilization_id in state.civilizations}
    rng = StableRng(state.config.seed)
    for _ in range(3):
        state = advance_day(state, rng, sovereigns=sovereigns).state
    for civilization_id in sorted(state.civilizations):
        report = build_council_report(state, civilization_id)
        notable = report.notable_people
        assert len(notable) == NOTABLE_PEOPLE
        on_duty = [person for person in notable if person.duty is not None]
        assert on_duty, "the baseline sets clerks and builders to work"
        assert notable[: len(on_duty)] == tuple(on_duty), "those on a duty come first"
        assert [person.person_id for person in on_duty] == sorted(
            person.person_id for person in on_duty
        )
        idle = [person.person_id for person in notable[len(on_duty) :]]
        assert idle == sorted(idle), "one settlement: the idle by lowest id"
        assert all(person.age_years >= GROWN_DAYS // 365 for person in notable[len(on_duty) :])
        summary = report.population
        assert summary is not None
        capital = state.civilizations[civilization_id].settlements[0].settlement_id
        assert summary.idle_workers[capital] >= len(idle)
