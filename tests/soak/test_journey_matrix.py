"""Seeded two-way trade and migration histories with daily conservation invariants.

A garrisoned toll post stands beside the second capital, so goods also pass through
toll chests and courier deposits, and must still be conserved.
"""

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
from sovereign_world.roads import Road, RoadGrade
from sovereign_world.state import WorldState, state_hash
from sovereign_world.territory import Garrison, HeldControl, Territory, TileOwner
from sovereign_world.tolls import TollPost

DAYS = 75
CONSERVED = (Resource.STONE, Resource.TIMBER)


class TradingSovereign:
    """Dispatch a caravan and a small migrant party at every council, then maybe end a treaty."""

    def __init__(
        self,
        partner: EntityId,
        route: tuple[HexCoord, ...],
        ending: DirectOrderKind | None = None,
    ) -> None:
        self.partner = partner
        self.route = route
        self.ending = ending

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
        if report.day == 30 and self.ending is DirectOrderKind.REPUDIATE_TREATY:
            orders += (
                DirectOrder(
                    command_id="repudiate",
                    kind=DirectOrderKind.REPUDIATE_TREATY,
                    treaty_id=EntityId("treaty:trade"),
                ),
            )
        if report.day == 30 and self.ending is DirectOrderKind.CANCEL_TREATY:
            orders += (
                DirectOrder(
                    command_id="cancel",
                    kind=DirectOrderKind.CANCEL_TREATY,
                    message_id=EntityId(f"message:{report.civilization_id}:cancel"),
                    ambassador_id=people[10],
                    recipient_civilization_id=self.partner,
                    message_text="We withdraw from the migration treaty.",
                    route=self.route,
                    treaty_id=EntityId("treaty:migration"),
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
    _toll_post(state, second, route[-2], route[-1])
    return manifest, state, first, second, route


def _toll_post(state: WorldState, owner: EntityId, tile: HexCoord, home: HexCoord) -> None:
    """A garrisoned toll beside the owner's capital, deposited there every ten days."""
    civilization = state.civilizations[owner]
    members = tuple(sorted(civilization.population.living_ids[12:15]))
    for person_id in members:
        civilization.population.people[person_id].location = tile
    civilization.garrisons = (
        Garrison(
            garrison_id=EntityId("garrison:toll"),
            civilization_id=owner,
            tile=tile,
            member_ids=members,
            since_day=0,
        ),
    )
    civilization.toll_posts = (
        TollPost(
            post_id=EntityId("toll:matrix"),
            civilization_id=owner,
            tile=tile,
            cargo_rate_bp=1_000,
            food_per_head=1,
            deposit_every_days=10,
            deposit_route=(tile, home),
            set_day=0,
            last_deposit_day=0,
        ),
    )
    state.roads = (
        Road(tile=tile, grade=RoadGrade.TRACK, civilization_id=owner, built_day=0, graded_day=0),
    )
    state.territory = Territory(
        held=(HeldControl(tile=tile, civilization_id=owner, value=90),),
        owners=(TileOwner(tile=tile, civilization_id=owner, since_day=0),),
    )


def _stock(state: WorldState, resource: Resource) -> int:
    return sum(
        civilization.inventory.quantities.get(resource, 0)
        for civilization in state.civilizations.values()
    )


def _in_flight_or_destroyed(state: WorldState, resource: Resource) -> int:
    """Goods on the road, lost or perished with their carriers, or waiting in a toll chest."""
    carried = sum(
        journey.cargo.get(resource, 0)
        for journey in state.journeys
        if journey.kind in {JourneyKind.SHIPMENT, JourneyKind.DEPOSIT}
        and (
            journey.carrying_cargo
            or journey.outcome in {JourneyOutcome.LOST, JourneyOutcome.PERISHED}
        )
    )
    chests = sum(
        post.chest.get(resource, 0)
        for civilization in state.civilizations.values()
        for post in civilization.toll_posts
    )
    return carried + chests


def _simulate(
    initial: WorldState,
    sovereigns_for: tuple[EntityId, EntityId, tuple],
    store: WorldStore | None = None,
) -> tuple[WorldState, list[str]]:
    first, second, route = sovereigns_for
    ending = (DirectOrderKind.REPUDIATE_TREATY, DirectOrderKind.CANCEL_TREATY, None)[
        initial.config.seed % 3
    ]
    sovereigns = {
        first: TradingSovereign(
            second, route, ending if ending is DirectOrderKind.REPUDIATE_TREATY else None
        ),
        second: TradingSovereign(
            first,
            tuple(reversed(route)),
            ending if ending is DirectOrderKind.CANCEL_TREATY else None,
        ),
    }
    state = initial.model_copy(deep=True)
    rng = StableRng(state.config.seed)
    totals = {resource: _stock(state, resource) for resource in CONSERVED}
    ever_seen: set[EntityId] = set()
    dead: set[EntityId] = set()
    kinds: list[str] = []
    packs: dict[EntityId, int] = {}
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
        for journey in state.journeys:
            previous = packs.get(journey.journey_id, journey.provisions_packed)
            assert journey.provisions <= previous, "a pack grew on the road"
            assert journey.active or journey.provisions == 0, "a finished party kept its food"
            packs[journey.journey_id] = journey.provisions
        treaty_of = {
            journey.journey_id: journey.treaty_id
            for journey in state.journeys
            if journey.treaty_id is not None
        }
        ended_on = {treaty.treaty_id: treaty.ended_day for treaty in state.active_treaties}
        trade_ended = ended_on[EntityId("treaty:trade")]
        for event in transition.events.events:
            if event.kind == "toll_paid":
                assert event.payload["owner"] != event.actor_id, "a toll charged its own"
                assert trade_ended is not None and trade_ended <= event.day, (
                    "a trade partner was charged"
                )
        for event in transition.events.events:
            if event.subject_id not in treaty_of:
                continue
            ended_day = ended_on[treaty_of[EntityId(event.subject_id)]]
            if event.kind in {"shipment_received", "migrants_received"}:
                assert ended_day is None or ended_day > event.day, "received after the end"
            if event.kind in {"shipment_dispatched", "migration_dispatched"}:
                assert ended_day is None or ended_day >= event.day, "dispatched after the end"
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
    if seed % 3 == 0:
        assert "treaty_breached" in kinds
        assert "toll_paid" in kinds, "once the trade treaty is broken, tolls are charged"
        assert "toll_deposited" in kinds, "couriers carried the chest home"
    else:
        assert "toll_paid" not in kinds, "trade partners pass free"
    if seed % 3 == 1:
        assert "treaty_cancelled" in kinds or "message_lost" in kinds
    assert any(journey.outcome is not JourneyOutcome.PENDING for journey in final.journeys), (
        "no journey resolved"
    )
    assert state_hash(rerun) == state_hash(final)
    assert rerun_kinds == kinds
    assert state_hash(replay_run(store)) == state_hash(final)
    assert verify_run(store).state_hash == state_hash(final)
