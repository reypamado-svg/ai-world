"""Councils sit on the world's own cadence: on day 0 and every interval after it, and a
crisis calls a council at most once in the world's crisis gap (never, at a gap of 0)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from logistics_helpers import treaty_world
from test_gateway_wiring import _at_war

from sovereign_world.commands import build_council_report, council_day, crisis_council_due
from sovereign_world.config import RunManifest, WorldConfig
from sovereign_world.gateway.prompt import charter
from sovereign_world.gateway.records import recorded_councils
from sovereign_world.persistence import WorldStore
from sovereign_world.replay import rederive_run, verify_run
from sovereign_world.runner import run_days
from sovereign_world.state import build_initial_state, state_hash_v2


@pytest.mark.parametrize("interval", [7, 14, 21, 28])
def test_councils_sit_on_day_0_and_every_interval_after(tmp_path: Path, interval: int) -> None:
    manifest = RunManifest.new(
        WorldConfig(seed=21, width=24, height=24, council_interval_days=interval), "0.2.0"
    )
    store = WorldStore.create(tmp_path / "run", manifest, build_initial_state(manifest))
    final = run_days(store, 2 * interval + 1)
    days = {record.day for record in recorded_councils(store)}
    assert days == {0, interval, 2 * interval}
    assert verify_run(store).verified_through_day == 2 * interval + 1
    assert rederive_run(store).state_hash == state_hash_v2(final, fresh=True)


def _crisis(gap: int):  # type: ignore[no-untyped-def]
    state, home, rival, _ = treaty_world(distance=4)
    state.config = state.config.model_copy(update={"crisis_gap_days": gap})
    state.day = 5
    _at_war(state, home, rival, learned_day=4)
    return state, home


def test_a_crisis_gap_spaces_crisis_councils_and_0_holds_none() -> None:
    state, home = _crisis(7)
    assert crisis_council_due(state, home)
    state.civilizations[home].last_crisis_council = 1
    assert not crisis_council_due(state, home)
    state, home = _crisis(3)
    state.civilizations[home].last_crisis_council = 2
    assert crisis_council_due(state, home)
    state, home = _crisis(1)
    state.civilizations[home].last_crisis_council = 4
    assert crisis_council_due(state, home)
    state, home = _crisis(0)
    assert not crisis_council_due(state, home)


def test_no_crisis_council_on_a_regular_council_day() -> None:
    state, home = _crisis(7)
    state.config = state.config.model_copy(update={"council_interval_days": 7})
    state.day = 7
    _at_war(state, home, next(iter(set(state.civilizations) - {home})), learned_day=6)
    assert council_day(state) and not crisis_council_due(state, home)


DEFAULT_CHARTERS = {
    1: "dfcf46ccbd3e1208a540a226e836db7340c87b55453ded899ca0fc5cb7117da0",
    2: "90ee504d4100f348f4b4f3bad8a444f67678d919e0cd95f87119448b675c3d94",
    3: "7b214e17c0e68d96a1a44c2e6283ca3d213d416799a1f0449cbaf372f2bac70c",
}
"""The charters of 30-day worlds before the cadence could be chosen."""


def _charter(rules: int = 3, **config: int) -> str:
    manifest = RunManifest.new(
        WorldConfig(seed=21, width=24, height=24, **config), "0.2.0", rules_version=rules
    )
    state = build_initial_state(manifest)
    return charter(build_council_report(state, sorted(state.civilizations)[0]))


def test_a_monthly_world_reads_the_charter_it_always_did() -> None:
    for rules, digest in DEFAULT_CHARTERS.items():
        assert hashlib.sha256(_charter(rules).encode()).hexdigest() == digest
    assert "Once a month, and when a crisis strikes, your council" in _charter()
    assert "weighed at each monthly council and move" in _charter()


def test_the_charter_tells_the_worlds_own_cadence() -> None:
    fortnight = _charter(council_interval_days=14, crisis_gap_days=3)
    assert "Every 14 days (on day 0 and every 14th day after), and when a crisis strikes" in (
        fortnight
    )
    assert "(at most once in 3 days)" in fortnight
    assert "weighed at each regular council, every 14 days, and move" in fortnight
    weekly = _charter(council_interval_days=7, crisis_gap_days=0)
    assert "Every 7 days (on day 0 and every 7th day after), your council" in weekly
    assert "crisis strikes" not in weekly
    assert _charter(council_interval_days=28) != _charter()
