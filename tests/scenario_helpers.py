"""Run a whole scenario: journal every day, keep every council's report and the state it
was built from, then prove the run replays exactly."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from sovereign_world.commands import (
    CommandEnvelope,
    CouncilReport,
    DirectOrder,
    DirectOrderKind,
    build_council_report,
)
from sovereign_world.config import RunManifest
from sovereign_world.diplomacy import PeaceTerms, TreatyKind
from sovereign_world.engine import advance_day
from sovereign_world.events import DomainEvent
from sovereign_world.hexmap import HexCoord
from sovereign_world.ids import EntityId
from sovereign_world.persistence import WorldStore
from sovereign_world.replay import replay_run, verify_run
from sovereign_world.rng import StableRng
from sovereign_world.scripted import Sovereign
from sovereign_world.state import WorldState, state_hash, validate_world
from sovereign_world.war import WarObjective

Sovereigns = Callable[[], Mapping[EntityId, Sovereign]]
"""Builds a fresh set of sovereigns, so a rerun starts from the same minds."""


@dataclass(frozen=True)
class Council:
    """One civilization's report on a council day, with the state it was built from."""

    state: WorldState
    civilization_id: EntityId
    report: CouncilReport


@dataclass(frozen=True)
class Run:
    final: WorldState
    events: tuple[DomainEvent, ...]
    councils: tuple[Council, ...]
    store: WorldStore

    def of(self, kind: str) -> list[DomainEvent]:
        return [event for event in self.events if event.kind == kind]

    def reports(self, civilization_id: EntityId) -> list[CouncilReport]:
        return [
            council.report
            for council in self.councils
            if council.civilization_id == civilization_id
        ]


def _simulate(
    initial: WorldState,
    make_sovereigns: Sovereigns,
    days: int,
    store: WorldStore | None = None,
) -> tuple[WorldState, list[DomainEvent], list[Council]]:
    state = initial.model_copy(deep=True)
    sovereigns = make_sovereigns()
    rng = StableRng(state.config.seed)
    events: list[DomainEvent] = []
    councils: list[Council] = []
    for _ in range(days):
        if store is not None and state.day % state.config.council_interval_days == 0:
            # Every living civilization's report as its council would read it this morning.
            snapshot = state.model_copy(deep=True)
            councils.extend(
                Council(snapshot, civilization_id, build_council_report(snapshot, civilization_id))
                for civilization_id in sorted(snapshot.civilizations)
                if snapshot.civilizations[civilization_id].eliminated_day is None
            )
        transition = advance_day(state, rng, sovereigns=sovereigns)
        state = transition.state
        if store is not None:
            store.append_transition(state, transition.events)
        events.extend(transition.events.events)
    return state, events, councils


def run_scenario(
    initial: WorldState,
    make_sovereigns: Sovereigns,
    days: int,
    root: Path,
) -> Run:
    manifest = RunManifest.model_validate(
        {"run_id": initial.run_id, "config": initial.config, "engine_version": "0.1.0"}
    )
    store = WorldStore.create(root / "record", manifest, initial)
    final, events, councils = _simulate(initial, make_sovereigns, days, store)
    return Run(final=final, events=tuple(events), councils=tuple(councils), store=store)


def assert_replays(
    run: Run,
    initial: WorldState,
    make_sovereigns: Sovereigns,
    days: int,
) -> None:
    """The journal replays to the same world, and so does running it again from scratch."""
    validate_world(run.final)
    final_hash = state_hash(run.final)
    assert state_hash(replay_run(run.store)) == final_hash
    assert verify_run(run.store).state_hash == final_hash
    rerun, events, _ = _simulate(initial, make_sovereigns, days)
    assert state_hash(rerun) == final_hash
    assert [event.kind for event in events] == [event.kind for event in run.events]


def envelope_of(report: CouncilReport, orders: list[DirectOrder]) -> CommandEnvelope:
    return CommandEnvelope(
        schema_version=1,
        civilization_id=report.civilization_id,
        council_day=report.day,
        correlation_id=report.report_id,
        commands=tuple(orders),
    )


class Welcoming:
    """Take in anyone who asks."""

    def decide(self, report: CouncilReport) -> CommandEnvelope:
        return envelope_of(
            report,
            [
                DirectOrder(
                    command_id=f"admit:{index}",
                    kind=DirectOrderKind.ANSWER_PETITION,
                    journey_id=journey.journey_id,
                    admit=True,
                )
                for index, journey in enumerate(report.petitions)
            ],
        )


PEACE = EntityId("treaty:peace")


class Victor:
    """Raid at the first council, then accept whatever peace is offered."""

    def __init__(self, enemy: EntityId, route: tuple[HexCoord, ...]) -> None:
        self.enemy = enemy
        self.route = route

    def decide(self, report: CouncilReport) -> CommandEnvelope:
        orders: list[DirectOrder] = []
        if report.day == 0:
            orders.append(
                DirectOrder(
                    command_id="raid",
                    kind=DirectOrderKind.SEND_WAR_PARTY,
                    journey_id=EntityId(f"journey:{report.civilization_id}:raid"),
                    recipient_civilization_id=self.enemy,
                    traveller_ids=report.person_ids[:10],
                    route=self.route,
                    war_objective=WarObjective.RAID,
                )
            )
        offered = any(
            message.treaty_offer is not None and message.treaty_offer.offer_id == PEACE
            for message in report.received_messages
        )
        if offered and not any(item.treaty_id == PEACE for item in report.treaties):
            orders.append(
                DirectOrder(
                    command_id=f"accept:{report.day}",
                    kind=DirectOrderKind.ACCEPT_TREATY,
                    treaty_id=PEACE,
                    message_id=EntityId(f"message:{report.civilization_id}:accept:{report.day}"),
                    ambassador_id=report.person_ids[-1],
                    recipient_civilization_id=self.enemy,
                    message_text="Accepted.",
                    route=self.route,
                )
            )
        return envelope_of(report, orders)


class Vanquished:
    """Sue for peace at the second council, giving up the colony."""

    def __init__(self, enemy: EntityId, route: tuple[HexCoord, ...], colony: EntityId) -> None:
        self.enemy = enemy
        self.route = route
        self.colony = colony

    def decide(self, report: CouncilReport) -> CommandEnvelope:
        orders = (
            [
                DirectOrder(
                    command_id="sue",
                    kind=DirectOrderKind.OFFER_TREATY,
                    treaty_id=PEACE,
                    treaty_kind=TreatyKind.PEACE,
                    peace_terms=PeaceTerms(truce_days=90, ceded_settlement=self.colony),
                    message_id=EntityId(f"message:{report.civilization_id}:peace"),
                    ambassador_id=report.person_ids[0],
                    recipient_civilization_id=self.enemy,
                    message_text="Take the colony, and leave us in peace.",
                    route=self.route,
                )
            ]
            if report.day == 30
            else []
        )
        return envelope_of(report, orders)
