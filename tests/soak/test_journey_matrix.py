"""Seeded two-way trade and migration histories with daily conservation invariants."""

from pathlib import Path

import pytest
from logistics_helpers import linked_world

from sovereign_world.commands import CommandEnvelope, CouncilReport, DirectOrder, DirectOrderKind
from sovereign_world.config import RunManifest
from sovereign_world.diplomacy import ActiveTreaty, TreatyKind, TreatyOffer
from sovereign_world.engine import advance_day
from sovereign_world.hexmap import HexCoord
from sovereign_world.ids import EntityId
from sovereign_world.logistics import JourneyKind, JourneyOutcome
from sovereign_world.persistence import WorldStore
from sovereign_world.replay import replay_run, verify_run
from sovereign_world.resources import Resource
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState, state_hash

DAYS = 75
CONSERVED = (Resource.STONE, Resource.TIMBER)


class TradingSovereign:
    """Dispatch a caravan and a small migrant party at every council."""

    def __init__(self, partner: EntityId, route: tuple[HexCoord, ...]) -> None:
        self.partner = partner
        self.route = route

    def decide(self, report: CouncilReport) -> CommandEnvelope:
        council = report.day // 30
        people = report.person_ids
        orders = (
            DirectOrder(
                command_id=f"ship:{report.day}",
                kind=DirectOrderKind.DISPATCH_SHIPMENT,
                journey_id=EntityId(f"journey:{report.civilization_id}:ship:{council}"),
                treaty_id=EntityId("treaty:trade"),
                recipient_civilization_id=self.partner,
                traveller_ids=people[council * 2 : council * 2 + 2],
                route=self.route,
                cargo={Resource.STONE: 30, Resource.TIMBER: 20},
            ),
            DirectOrder(
                command_id=f"migrate:{report.day}",
                kind=DirectOrderKind.DISPATCH_MIGRATION,
                journey_id=EntityId(f"journey:{report.civilization_id}:migrate:{council}"),
                treaty_id=EntityId("treaty:migration"),
                recipient_civilization_id=self.partner,
                traveller_ids=people[-2:],
                route=self.route,
            ),
        )
        return CommandEnvelope(
            schema_version=1,
            civilization_id=report.civilization_id,
            council_day=report.day,
            correlation_id=report.report_id,
            commands=orders,
        )


def _bound_world(seed: int) -> tuple[RunManifest, WorldState, EntityId, EntityId, tuple]:
    manifest, state, first, second, route = linked_world(seed=seed, distance=2 + seed % 5)
    offers = []
    treaties = []
    for kind in (TreatyKind.MIGRATION, TreatyKind.TRADE):
        treaty_id = EntityId(f"treaty:{kind.value}")
        offers.append(
            TreatyOffer(
                offer_id=treaty_id,
                proposer_civilization_id=first,
                recipient_civilization_id=second,
                kind=kind,
                proposed_day=0,
            )
        )
        treaties.append(
            ActiveTreaty(
                treaty_id=treaty_id,
                proposer_civilization_id=first,
                recipient_civilization_id=second,
                kind=kind,
                offered_day=0,
                activated_day=0,
            )
        )
    state.treaty_offers = tuple(offers)
    state.active_treaties = tuple(treaties)
    return manifest, state, first, second, route


def _stock(state: WorldState, resource: Resource) -> int:
    return sum(
        civilization.inventory.quantities.get(resource, 0)
        for civilization in state.civilizations.values()
    )


def _in_flight_or_destroyed(state: WorldState, resource: Resource) -> int:
    return sum(
        journey.cargo.get(resource, 0)
        for journey in state.journeys
        if journey.kind is JourneyKind.SHIPMENT
        and (
            journey.carrying_cargo
            or journey.outcome in {JourneyOutcome.LOST, JourneyOutcome.PERISHED}
        )
    )


def _simulate(
    initial: WorldState,
    sovereigns_for: tuple[EntityId, EntityId, tuple],
    store: WorldStore | None = None,
) -> tuple[WorldState, list[str]]:
    first, second, route = sovereigns_for
    sovereigns = {
        first: TradingSovereign(second, route),
        second: TradingSovereign(first, tuple(reversed(route))),
    }
    state = initial.model_copy(deep=True)
    rng = StableRng(state.config.seed)
    totals = {resource: _stock(state, resource) for resource in CONSERVED}
    ever_seen: set[EntityId] = set()
    dead: set[EntityId] = set()
    kinds: list[str] = []
    for _ in range(DAYS):
        transition = advance_day(state, rng, sovereigns=sovereigns)
        state = transition.state
        kinds.extend(event.kind for event in transition.events.events)
        if store is not None:
            store.append_transition(state, transition.events)
        for resource in CONSERVED:
            assert (
                _stock(state, resource) + _in_flight_or_destroyed(state, resource)
                == totals[resource]
            ), f"{resource} was created or destroyed outside a journey"
        people = {
            person_id: person
            for civilization in state.civilizations.values()
            for person_id, person in civilization.population.people.items()
        }
        assert ever_seen <= set(people), "a person record vanished"
        ever_seen = set(people)
        assert not any(people[person_id].alive for person_id in dead), "the dead returned"
        dead |= {person_id for person_id, person in people.items() if not person.alive}
        for journey in state.journeys:
            if journey.kind is JourneyKind.MIGRATION and journey.arrived_day is None:
                recipient = state.civilizations[journey.recipient_civilization_id]
                assert set(journey.traveller_ids).isdisjoint(recipient.population.people)
    return state, kinds


@pytest.mark.soak
@pytest.mark.parametrize("seed", range(24))
def test_seeded_journey_histories_conserve_goods_people_and_replay(
    seed: int, tmp_path: Path
) -> None:
    manifest, initial, first, second, route = _bound_world(seed)
    store = WorldStore.create(tmp_path / "record", manifest, initial)

    final, kinds = _simulate(initial, (first, second, route), store)
    rerun, rerun_kinds = _simulate(initial, (first, second, route))

    assert "shipment_dispatched" in kinds
    assert "migration_dispatched" in kinds
    assert any(journey.outcome is not JourneyOutcome.PENDING for journey in final.journeys), (
        "no journey resolved"
    )
    assert state_hash(rerun) == state_hash(final)
    assert rerun_kinds == kinds
    assert state_hash(replay_run(store)) == state_hash(final)
    assert verify_run(store).state_hash == state_hash(final)
