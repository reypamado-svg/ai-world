"""Two trading neighbours go to war: a small raid is thrown back, a larger one takes food."""

from pathlib import Path

from logistics_helpers import ScheduledSovereign, treaty_world

from sovereign_world.commands import DirectOrder, DirectOrderKind, build_council_report
from sovereign_world.config import RunManifest
from sovereign_world.engine import advance_day
from sovereign_world.events import DomainEvent
from sovereign_world.hexmap import HexCoord
from sovereign_world.ids import EntityId
from sovereign_world.persistence import WorldStore
from sovereign_world.replay import replay_run, verify_run
from sovereign_world.resources import Resource
from sovereign_world.rng import StableRng
from sovereign_world.state import state_hash, validate_world
from sovereign_world.war import WarObjective

DAYS = 50


def test_neighbours_go_from_trade_to_war_and_count_their_dead(tmp_path: Path) -> None:
    state, home, rival, route = treaty_world(distance=4)
    fields = HexCoord(route[-1].q, route[-1].r - 3)
    rival_people = state.civilizations[rival].population.people
    for person_id in sorted(rival_people)[10:]:
        rival_people[person_id].location = fields
    living = state.civilizations[home].population.living_ids

    def raid(day: int, fighters: tuple[EntityId, ...], axes: int) -> DirectOrder:
        return DirectOrder(
            command_id=f"raid:{day}",
            kind=DirectOrderKind.SEND_WAR_PARTY,
            journey_id=EntityId(f"journey:raid:{day}"),
            recipient_civilization_id=rival,
            traveller_ids=fighters,
            route=route,
            cargo={Resource.AXE: axes} if axes else {},
            war_objective=WarObjective.RAID,
        )

    declare = DirectOrder(
        command_id="declare",
        kind=DirectOrderKind.DECLARE_WAR,
        message_id=EntityId("message:war"),
        ambassador_id=living[-1],
        recipient_civilization_id=rival,
        message_text="Your granaries will be ours.",
        route=route,
    )
    sovereigns = {
        home: ScheduledSovereign(
            {0: (declare, raid(0, living[:4], 0)), 30: (raid(30, living[4:24], 8),)}
        )
    }
    manifest = RunManifest.model_validate(
        {"run_id": state.run_id, "config": state.config, "engine_version": "0.1.0"}
    )

    def run(store: WorldStore | None = None):
        current = state.model_copy(deep=True)
        rng = StableRng(current.config.seed)
        events: list[DomainEvent] = []
        for _ in range(DAYS):
            transition = advance_day(current, rng, sovereigns=sovereigns)
            current = transition.state
            if store is not None:
                store.append_transition(current, transition.events)
            events.extend(transition.events.events)
        return current, events

    store = WorldStore.create(tmp_path / "record", manifest, state)
    final, events = run(store)

    def of(kind: str) -> list[DomainEvent]:
        return [event for event in events if event.kind == kind]

    [war] = final.wars
    assert war.declared and war.defender_learned_day is not None
    assert [event.kind for event in of("treaty_breached")] == ["treaty_breached"]
    first, second = final.battles
    assert first.winner_id == rival, "four raiders were thrown back"
    assert second.winner_id == home, "twenty, with axes, broke the defence"
    [raided] = of("settlement_raided")
    assert raided.payload["units"] > 0
    dead = [event for event in of("person_died") if event.payload.get("cause") == "battle"]
    casualties = [item for battle in final.battles for item in battle.casualties]
    assert len(dead) == sum(item.died for item in casualties)
    for item in casualties:
        person = final.civilizations[item.civilization_id].population.people[item.person_id]
        assert person.alive != item.died, "every casualty is recorded against a named person"

    home_reports = build_council_report(final, home).war_reports
    rival_reports = build_council_report(final, rival).war_reports
    assert [report.won for report in home_reports] == [False, True]
    assert [report.won for report in rival_reports] == [True, False]
    assert all(report.enemy_id == rival for report in home_reports)

    validate_world(final)
    assert state_hash(replay_run(store)) == state_hash(final)
    assert verify_run(store).state_hash == store.state_hash(final)
    rerun, _ = run()
    assert state_hash(rerun) == state_hash(final)
