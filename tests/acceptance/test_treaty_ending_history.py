"""Ratify, trade, breach, and cancel: every ending needs evidence and leaves history."""

from pathlib import Path

from logistics_helpers import linked_world

from sovereign_world.commands import (
    CommandEnvelope,
    CouncilReport,
    DirectOrder,
    DirectOrderKind,
    build_council_report,
)
from sovereign_world.diplomacy import TreatyEndKind, TreatyKind
from sovereign_world.engine import advance_day
from sovereign_world.events import DomainEvent
from sovereign_world.hexmap import HexCoord
from sovereign_world.ids import EntityId
from sovereign_world.logistics import JourneyOutcome, NoticeKind
from sovereign_world.persistence import WorldStore
from sovereign_world.replay import replay_run, verify_run
from sovereign_world.resources import Resource
from sovereign_world.rng import StableRng
from sovereign_world.state import state_hash, validate_world

TRADE = EntityId("treaty:trade")
MIGRATION = EntityId("treaty:migration")
CARGO = {Resource.STONE: 40}


def _envelope(report: CouncilReport, orders: list[DirectOrder]) -> CommandEnvelope:
    return CommandEnvelope(
        schema_version=1,
        civilization_id=report.civilization_id,
        council_day=report.day,
        correlation_id=report.report_id,
        commands=tuple(orders),
    )


class FaithlessProposer:
    """Ratifies two treaties, trades once, then ships again and breaks the trade treaty."""

    def __init__(self, partner: EntityId, route: tuple[HexCoord, ...]) -> None:
        self.partner = partner
        self.route = route

    def _shipment(self, report: CouncilReport, name: str, carriers: slice) -> DirectOrder:
        return DirectOrder(
            command_id=f"ship:{name}",
            kind=DirectOrderKind.DISPATCH_SHIPMENT,
            journey_id=EntityId(f"journey:{name}"),
            treaty_id=TRADE,
            recipient_civilization_id=self.partner,
            traveller_ids=report.person_ids[carriers],
            route=self.route,
            cargo=CARGO,
        )

    def decide(self, report: CouncilReport) -> CommandEnvelope:
        people = report.person_ids
        orders: list[DirectOrder] = []
        if report.day == 0:
            for index, (treaty_id, kind) in enumerate(
                ((TRADE, TreatyKind.TRADE), (MIGRATION, TreatyKind.MIGRATION))
            ):
                orders.append(
                    DirectOrder(
                        command_id=f"offer:{kind.value}",
                        kind=DirectOrderKind.OFFER_TREATY,
                        message_id=EntityId(f"message:offer-{kind.value}"),
                        ambassador_id=people[20 + index],
                        recipient_civilization_id=self.partner,
                        message_text=f"We propose {kind.value}.",
                        route=self.route,
                        treaty_id=treaty_id,
                        treaty_kind=kind,
                    )
                )
        elif report.day == 60:
            orders.append(self._shipment(report, "honest-caravan", slice(0, 2)))
        elif report.day == 90:
            orders.append(self._shipment(report, "last-caravan", slice(2, 4)))
            orders.append(
                DirectOrder(
                    command_id="repudiate",
                    kind=DirectOrderKind.REPUDIATE_TREATY,
                    treaty_id=TRADE,
                )
            )
        elif report.day == 120:
            orders.append(
                DirectOrder(
                    command_id="migrate",
                    kind=DirectOrderKind.DISPATCH_MIGRATION,
                    journey_id=EntityId("journey:too-late"),
                    treaty_id=MIGRATION,
                    recipient_civilization_id=self.partner,
                    traveller_ids=people[-2:],
                    route=self.route,
                )
            )
        return _envelope(report, orders)


class WaryPartner:
    """Accepts both offers, then lawfully withdraws from the migration treaty."""

    def __init__(self, route_home: tuple[HexCoord, ...]) -> None:
        self.route_home = route_home
        self.accepted: set[EntityId] = set()

    def decide(self, report: CouncilReport) -> CommandEnvelope:
        orders: list[DirectOrder] = []
        for message in report.received_messages:
            offer = message.treaty_offer
            if offer is None or offer.offer_id in self.accepted:
                continue
            self.accepted.add(offer.offer_id)
            orders.append(
                DirectOrder(
                    command_id=f"accept:{offer.offer_id}",
                    kind=DirectOrderKind.ACCEPT_TREATY,
                    message_id=EntityId(f"message:accept-{offer.kind.value}"),
                    ambassador_id=report.person_ids[len(self.accepted)],
                    recipient_civilization_id=offer.proposer_civilization_id,
                    message_text=f"We accept {offer.kind.value}.",
                    route=self.route_home,
                    treaty_id=offer.offer_id,
                )
            )
        if report.day == 90:
            orders.append(
                DirectOrder(
                    command_id="cancel:migration",
                    kind=DirectOrderKind.CANCEL_TREATY,
                    message_id=EntityId("message:cancel-migration"),
                    ambassador_id=report.person_ids[10],
                    recipient_civilization_id=next(iter(report.contacts)).civilization_id,
                    message_text="We will receive no more settlers.",
                    route=self.route_home,
                    treaty_id=MIGRATION,
                )
            )
        return _envelope(report, orders)


def test_treaties_end_by_evidence_and_turn_travellers_away(tmp_path: Path) -> None:
    manifest, state, proposer, partner, route = linked_world()
    store = WorldStore.create(tmp_path / "record", manifest, state)
    sovereigns = {
        proposer: FaithlessProposer(partner, route),
        partner: WaryPartner(tuple(reversed(route))),
    }
    stone = {
        civilization_id: state.civilizations[civilization_id].inventory.quantities[Resource.STONE]
        for civilization_id in (proposer, partner)
    }
    rng = StableRng(manifest.config.seed)
    events: list[DomainEvent] = []
    partner_notices_by_day: dict[int, set[NoticeKind]] = {}
    for _ in range(130):
        transition = advance_day(state, rng, sovereigns=sovereigns)
        state = transition.state
        store.append_transition(state, transition.events)
        events.extend(transition.events.events)
        partner_notices_by_day[state.day] = {
            item.kind for item in build_council_report(state, partner).logistics_notices
        }

    treaties = {treaty.treaty_id: treaty for treaty in state.active_treaties}
    trade, migration = treaties[TRADE], treaties[MIGRATION]
    assert (trade.end_kind, trade.ended_by, trade.ended_day) == (
        TreatyEndKind.BREACHED,
        proposer,
        90,
    )
    assert migration.end_kind is TreatyEndKind.CANCELLED
    assert migration.ended_by == partner
    assert migration.ended_day is not None and 90 < migration.ended_day < 120

    journeys = {journey.journey_id: journey for journey in state.journeys}
    assert journeys[EntityId("journey:honest-caravan")].outcome is JourneyOutcome.DELIVERED
    assert journeys[EntityId("journey:last-caravan")].outcome is JourneyOutcome.REFUSED
    assert EntityId("journey:too-late") not in journeys
    rejected = [
        event.payload.get("code")
        for event in events
        if event.kind == "command_rejected" and event.day == 120
    ]
    assert rejected == ["no_active_treaty"]

    assert state.civilizations[proposer].inventory.quantities[Resource.STONE] == (
        stone[proposer] - CARGO[Resource.STONE]
    ), "only the delivered caravan's goods left for good"
    assert state.civilizations[partner].inventory.quantities[Resource.STONE] == (
        stone[partner] + CARGO[Resource.STONE]
    )

    refusal_day = next(event.day for event in events if event.kind == "shipment_refused")
    assert NoticeKind.SHIPMENT_TURNED_AWAY not in partner_notices_by_day[refusal_day]
    assert NoticeKind.SHIPMENT_TURNED_AWAY in partner_notices_by_day[refusal_day + 1]
    [turned_away] = [
        item
        for item in state.civilizations[partner].logistics_notices
        if item.kind is NoticeKind.SHIPMENT_TURNED_AWAY
    ]
    assert turned_away.treaty_id == TRADE
    assert any(
        message.cancellation_of == MIGRATION
        for message in state.civilizations[proposer].received_messages
    ), "the proposer received the cancellation notice"

    validate_world(state)
    assert state_hash(replay_run(store)) == state_hash(state)
    assert verify_run(store).state_hash == store.state_hash(state)
