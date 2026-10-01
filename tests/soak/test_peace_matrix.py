"""Seeded wars that end in peace: envoys carry the offer and the acceptance, tribute is
shipped each month, and nobody marches during the truce."""

import pytest
from logistics_helpers import treaty_world

from sovereign_world.commands import CommandEnvelope, CouncilReport, DirectOrder, DirectOrderKind
from sovereign_world.diplomacy import PeaceTerms, TreatyKind
from sovereign_world.engine import advance_day
from sovereign_world.hexmap import HexCoord
from sovereign_world.ids import EntityId
from sovereign_world.resources import Resource
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState, state_hash
from sovereign_world.war import WarObjective

DAYS = 200
PEACE = EntityId("treaty:peace")


class Aggressor:
    """Raid at the first council, sue for peace at the second, then pay tribute monthly."""

    def __init__(self, enemy: EntityId, route: tuple[HexCoord, ...], truce: int) -> None:
        self.enemy = enemy
        self.route = route
        self.truce = truce

    def decide(self, report: CouncilReport) -> CommandEnvelope:
        free = list(report.person_ids)
        orders: list[DirectOrder] = []
        peace = next((item for item in report.treaties if item.treaty_id == PEACE), None)
        if report.day == 0:
            orders.append(
                DirectOrder(
                    command_id="raid",
                    kind=DirectOrderKind.SEND_WAR_PARTY,
                    journey_id=EntityId(f"journey:{report.civilization_id}:raid"),
                    recipient_civilization_id=self.enemy,
                    traveller_ids=tuple(free[:8]),
                    route=self.route,
                    war_objective=WarObjective.RAID,
                )
            )
        elif report.day == 30:
            orders.append(
                DirectOrder(
                    command_id="sue",
                    kind=DirectOrderKind.OFFER_TREATY,
                    treaty_id=PEACE,
                    treaty_kind=TreatyKind.PEACE,
                    peace_terms=PeaceTerms(
                        truce_days=self.truce,
                        tribute_payer=report.civilization_id,
                        tribute={Resource.TIMBER: 10},
                        tribute_payments=3,
                    ),
                    message_id=EntityId(f"message:{report.civilization_id}:peace"),
                    ambassador_id=free[-1],
                    recipient_civilization_id=self.enemy,
                    message_text="Peace, and tribute.",
                    route=self.route,
                )
            )
        elif peace is not None and peace.in_force:
            orders.append(
                DirectOrder(
                    command_id=f"tribute:{report.day}",
                    kind=DirectOrderKind.DISPATCH_SHIPMENT,
                    journey_id=EntityId(f"journey:{report.civilization_id}:tribute:{report.day}"),
                    treaty_id=PEACE,
                    recipient_civilization_id=self.enemy,
                    traveller_ids=tuple(free[20:22]),
                    route=self.route,
                    cargo={Resource.TIMBER: 10},
                )
            )
        return _envelope(report, orders)


class Defender:
    """Accept any peace offered."""

    def __init__(self, enemy: EntityId, route: tuple[HexCoord, ...]) -> None:
        self.enemy = enemy
        self.route = route

    def decide(self, report: CouncilReport) -> CommandEnvelope:
        offered = [
            message.treaty_offer
            for message in report.received_messages
            if message.treaty_offer is not None and message.treaty_offer.offer_id == PEACE
        ]
        accepted = any(item.treaty_id == PEACE for item in report.treaties)
        orders = (
            [
                DirectOrder(
                    command_id="accept",
                    kind=DirectOrderKind.ACCEPT_TREATY,
                    treaty_id=PEACE,
                    message_id=EntityId(f"message:{report.civilization_id}:accept:{report.day}"),
                    ambassador_id=report.person_ids[-1],
                    recipient_civilization_id=self.enemy,
                    message_text="Accepted.",
                    route=self.route,
                )
            ]
            if offered and not accepted
            else []
        )
        return _envelope(report, orders)


def _envelope(report: CouncilReport, orders: list[DirectOrder]) -> CommandEnvelope:
    return CommandEnvelope(
        schema_version=1,
        civilization_id=report.civilization_id,
        council_day=report.day,
        correlation_id=report.report_id,
        commands=tuple(orders),
    )


def _simulate(initial: WorldState, first: EntityId, second: EntityId, route, truce: int):
    state = initial.model_copy(deep=True)
    sovereigns = {
        first: Aggressor(second, route, truce),
        second: Defender(first, tuple(reversed(route))),
    }
    rng = StableRng(state.config.seed)
    kinds: list[str] = []
    for _ in range(DAYS):
        transition = advance_day(state, rng, sovereigns=sovereigns)
        state = transition.state
        kinds.extend(event.kind for event in transition.events.events)
        peace = next((item for item in state.active_treaties if item.treaty_id == PEACE), None)
        if peace is not None and peace.truce_until is not None:
            for journey in state.journeys:
                assert not (
                    journey.kind.value == "campaign"
                    and peace.activated_day <= journey.departed_day < peace.truce_until
                    and peace.in_force
                ), "no war party sets out during the truce"
    return state, kinds


@pytest.mark.soak
@pytest.mark.parametrize("seed", range(6))
def test_seeded_peace_holds_its_truce_and_replays_exactly(seed: int) -> None:
    initial, first, second, route = treaty_world(seed=seed, distance=2 + seed % 4)
    truce = 60 + 30 * (seed % 3)

    final, kinds = _simulate(initial, first, second, route, truce)
    rerun, rerun_kinds = _simulate(initial, first, second, route, truce)

    assert "peace_made" in kinds
    assert "tribute_received" in kinds
    [peace] = [item for item in final.active_treaties if item.treaty_id == PEACE]
    assert not any(war.active for war in final.wars if war.started_day < peace.activated_day)
    assert state_hash(rerun) == state_hash(final)
    assert rerun_kinds == kinds
