"""Ratify trade and migration through ambassadors, then move real goods and people."""

from pathlib import Path

from logistics_helpers import linked_world

from sovereign_world.commands import (
    CommandEnvelope,
    CouncilReport,
    DirectOrder,
    DirectOrderKind,
    build_council_report,
)
from sovereign_world.diplomacy import TreatyKind
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

CARGO = {Resource.STONE: 60, Resource.TIMBER: 40}


class ProposingSovereign:
    """Offers trade and migration, then acts only on acceptances that physically arrived."""

    def __init__(self, partner: EntityId, route: tuple[HexCoord, ...]) -> None:
        self.partner = partner
        self.route = route

    def decide(self, report: CouncilReport) -> CommandEnvelope:
        people = report.person_ids
        orders: list[DirectOrder] = []
        if report.day == 0:
            for index, kind in enumerate((TreatyKind.TRADE, TreatyKind.MIGRATION)):
                orders.append(
                    DirectOrder(
                        command_id=f"offer:{kind.value}",
                        kind=DirectOrderKind.OFFER_TREATY,
                        message_id=EntityId(f"message:offer-{kind.value}"),
                        ambassador_id=people[20 + index],
                        recipient_civilization_id=self.partner,
                        message_text=f"We propose {kind.value}.",
                        route=self.route,
                        treaty_id=EntityId(f"treaty:{kind.value}"),
                        treaty_kind=kind,
                    )
                )
        accepted = {
            message.acceptance_of
            for message in report.received_messages
            if message.acceptance_of is not None
        }
        dispatched = {notice.journey_id for notice in report.logistics_notices}
        if EntityId("treaty:trade") in accepted and "journey:first-caravan" not in dispatched:
            orders.append(
                DirectOrder(
                    command_id="dispatch:caravan",
                    kind=DirectOrderKind.DISPATCH_SHIPMENT,
                    journey_id=EntityId("journey:first-caravan"),
                    treaty_id=EntityId("treaty:trade"),
                    recipient_civilization_id=self.partner,
                    traveller_ids=people[0:2],
                    route=self.route,
                    cargo=CARGO,
                )
            )
        if EntityId("treaty:migration") in accepted and "journey:settlers" not in dispatched:
            orders.append(
                DirectOrder(
                    command_id="dispatch:settlers",
                    kind=DirectOrderKind.DISPATCH_MIGRATION,
                    journey_id=EntityId("journey:settlers"),
                    treaty_id=EntityId("treaty:migration"),
                    recipient_civilization_id=self.partner,
                    traveller_ids=people[10:14],
                    route=self.route,
                )
            )
        return CommandEnvelope(
            schema_version=1,
            civilization_id=report.civilization_id,
            council_day=report.day,
            correlation_id=report.report_id,
            commands=tuple(orders),
        )


class AcceptingSovereign:
    """Accepts every treaty offer that has physically reached it."""

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
        return CommandEnvelope(
            schema_version=1,
            civilization_id=report.civilization_id,
            council_day=report.day,
            correlation_id=report.report_id,
            commands=tuple(orders),
        )


def test_ratified_treaties_move_real_goods_and_people(tmp_path: Path) -> None:
    manifest, state, proposer, partner, route = linked_world()
    store = WorldStore.create(tmp_path / "record", manifest, state)
    sovereigns = {
        proposer: ProposingSovereign(partner, route),
        partner: AcceptingSovereign(tuple(reversed(route))),
    }
    partner_stock = dict(state.civilizations[partner].inventory.quantities)
    rng = StableRng(manifest.config.seed)
    events: list[DomainEvent] = []
    for _ in range(90):
        transition = advance_day(state, rng, sovereigns=sovereigns)
        state = transition.state
        store.append_transition(state, transition.events)
        events.extend(transition.events.events)
    kinds = [event.kind for event in events]

    activations = [event for event in events if event.kind == "treaty_activated"]
    first_activation = min(event.day for event in activations)
    first_dispatch = min(
        event.day
        for event in events
        if event.kind in {"shipment_dispatched", "migration_dispatched"}
    )
    assert {event.subject_id for event in activations} == {"treaty:trade", "treaty:migration"}
    assert first_dispatch > first_activation

    caravan = next(item for item in state.journeys if item.journey_id == "journey:first-caravan")
    settlers = next(item for item in state.journeys if item.journey_id == "journey:settlers")
    assert caravan.outcome is JourneyOutcome.DELIVERED
    assert settlers.outcome is JourneyOutcome.DELIVERED
    assert "shipment_received" in kinds
    assert "migrants_received" in kinds

    partner_state = state.civilizations[partner]
    for resource, quantity in CARGO.items():
        assert partner_state.inventory.quantities[resource] == partner_stock[resource] + quantity
    living_arrivals = [
        person_id
        for person_id in settlers.traveller_ids
        if person_id in partner_state.population.people
    ]
    assert living_arrivals
    assert all(
        partner_state.population.people[person_id].civilization_id == partner
        for person_id in living_arrivals
    )

    partner_notices = {item.kind for item in build_council_report(state, partner).logistics_notices}
    proposer_notices = {
        item.kind for item in build_council_report(state, proposer).logistics_notices
    }
    assert partner_notices == {NoticeKind.SHIPMENT_RECEIVED, NoticeKind.MIGRANTS_RECEIVED}
    assert NoticeKind.SHIPMENT_RECEIVED not in proposer_notices
    assert NoticeKind.MIGRANTS_RECEIVED not in proposer_notices
    for bystander in sorted(state.civilizations)[2:]:
        report = build_council_report(state, bystander)
        assert report.logistics_notices == ()
        assert report.received_messages == ()

    validate_world(state)
    assert state_hash(replay_run(store)) == state_hash(state)
    assert verify_run(store).state_hash == state_hash(state)
