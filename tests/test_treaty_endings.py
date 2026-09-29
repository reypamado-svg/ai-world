import pytest
from logistics_helpers import (
    OneShotSovereign,
    ScheduledSovereign,
    clear_journey_id,
    clear_message_id,
    envelope,
    roll_matching_id,
    treaty_world,
)

from sovereign_world.commands import (
    DirectOrder,
    DirectOrderKind,
    build_council_report,
    validate_envelope,
)
from sovereign_world.diplomacy import (
    ActiveTreaty,
    DiplomaticMessage,
    TreatyEndKind,
    TreatyKind,
)
from sovereign_world.engine import TransitionResult, advance_day
from sovereign_world.hexmap import HexCoord
from sovereign_world.ids import EntityId
from sovereign_world.logistics import JourneyOutcome, JourneyPhase, NoticeKind
from sovereign_world.resources import Resource
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState, validate_world

TRADE = EntityId("treaty:trade")
MIGRATION = EntityId("treaty:migration")


def _run(
    state: WorldState, days: int, sovereigns=None
) -> tuple[WorldState, list[TransitionResult]]:
    rng = StableRng(state.config.seed)
    results: list[TransitionResult] = []
    for _ in range(days):
        result = advance_day(state, rng, sovereigns=sovereigns)
        state = result.state
        results.append(result)
    return state, results


def _events(results: list[TransitionResult], kind: str):
    return [event for result in results for event in result.events.events if event.kind == kind]


def _treaty(state: WorldState, treaty_id: EntityId = TRADE) -> ActiveTreaty:
    return next(item for item in state.active_treaties if item.treaty_id == treaty_id)


def _cancel(
    state: WorldState,
    sender: EntityId,
    recipient: EntityId,
    route: tuple[HexCoord, ...],
    *,
    message_id: str,
    treaty_id: EntityId = TRADE,
) -> DirectOrder:
    return DirectOrder(
        command_id=f"cancel:{message_id}",
        kind=DirectOrderKind.CANCEL_TREATY,
        message_id=EntityId(message_id),
        ambassador_id=state.civilizations[sender].population.living_ids[20],
        recipient_civilization_id=recipient,
        message_text="We withdraw from our agreement.",
        route=route,
        treaty_id=treaty_id,
    )


def _repudiate(treaty_id: EntityId = TRADE) -> DirectOrder:
    return DirectOrder(
        command_id=f"repudiate:{treaty_id}",
        kind=DirectOrderKind.REPUDIATE_TREATY,
        treaty_id=treaty_id,
    )


def _shipment(
    state: WorldState,
    sender: EntityId,
    recipient: EntityId,
    route: tuple[HexCoord, ...],
    *,
    journey_id: str,
) -> DirectOrder:
    return DirectOrder(
        command_id=f"ship:{journey_id}",
        kind=DirectOrderKind.DISPATCH_SHIPMENT,
        journey_id=EntityId(journey_id),
        treaty_id=TRADE,
        recipient_civilization_id=recipient,
        traveller_ids=state.civilizations[sender].population.living_ids[:2],
        route=route,
        cargo={Resource.STONE: 80},
    )


def test_cancellation_takes_effect_only_when_the_notice_arrives() -> None:
    state, sender, recipient, route = treaty_world()
    order = _cancel(state, sender, recipient, route, message_id=clear_message_id("cancel"))

    state, results = _run(state, 2, {sender: OneShotSovereign(order)})
    assert _treaty(state).in_force, "the notice is still on the road"
    reply = _shipment(
        state, recipient, sender, tuple(reversed(route)), journey_id="journey:meanwhile"
    )
    assert validate_envelope(envelope(state, recipient, reply), state).errors == ()

    state, results = _run(state, 1)

    treaty = _treaty(state)
    assert treaty.ended_day == 2
    assert treaty.end_kind is TreatyEndKind.CANCELLED
    assert treaty.ended_by == sender
    [event] = _events(results, "treaty_cancelled")
    assert event.actor_id == str(recipient), "the recipient observes the notice"
    assert event.payload == {"by": str(sender)}
    notice = state.civilizations[recipient].received_messages[-1]
    assert notice.cancellation_of == TRADE
    validate_world(state)


def test_a_lost_cancellation_ends_nothing() -> None:
    state, sender, recipient, route = treaty_world()
    message_id = roll_matching_id("message:lost", "diplomacy", lambda roll: roll < 80)
    order = _cancel(state, sender, recipient, route, message_id=message_id)

    state, results = _run(state, 6, {sender: OneShotSovereign(order)})

    assert _events(results, "message_lost")
    assert _treaty(state).in_force
    assert not _events(results, "treaty_cancelled")


def test_repudiation_breaches_the_treaty_at_once() -> None:
    state, sender, recipient, route = treaty_world()

    state, results = _run(state, 1, {recipient: OneShotSovereign(_repudiate())})

    treaty = _treaty(state)
    assert treaty.ended_day == 0
    assert treaty.end_kind is TreatyEndKind.BREACHED
    assert treaty.ended_by == recipient
    [event] = _events(results, "treaty_breached")
    assert event.actor_id == str(recipient)
    assert event.payload == {"injured": str(sender)}
    for civilization, partner, path in (
        (sender, recipient, route),
        (recipient, sender, tuple(reversed(route))),
    ):
        order = _shipment(state, civilization, partner, path, journey_id=f"journey:{civilization}")
        errors = validate_envelope(envelope(state, civilization, order), state).errors
        assert [error.code for error in errors] == ["no_active_treaty"]


def test_goods_in_transit_are_turned_away_and_carried_home() -> None:
    state, sender, recipient, route = treaty_world()
    sender_stone = state.civilizations[sender].inventory.quantities[Resource.STONE]
    recipient_stone = state.civilizations[recipient].inventory.quantities[Resource.STONE]
    shipment = _shipment(state, sender, recipient, route, journey_id=clear_journey_id("doomed"))
    sovereigns = {
        sender: OneShotSovereign(shipment),
        recipient: OneShotSovereign(_repudiate()),
    }

    state, results = _run(state, 3, sovereigns)

    [refusal] = _events(results, "shipment_refused")
    assert refusal.actor_id == str(recipient)
    assert not _events(results, "shipment_received")
    assert state.civilizations[recipient].inventory.quantities[Resource.STONE] == recipient_stone
    [turned_away] = state.civilizations[recipient].logistics_notices
    assert turned_away.kind is NoticeKind.SHIPMENT_TURNED_AWAY
    assert turned_away.treaty_id == TRADE
    sender_kinds = [item.kind for item in state.civilizations[sender].logistics_notices]
    assert NoticeKind.SHIPMENT_CARRIERS_RETURNED not in sender_kinds, "carriers are still away"

    state, results = _run(state, 4)

    journey = state.journeys[0]
    assert journey.outcome is JourneyOutcome.REFUSED
    assert journey.phase is JourneyPhase.COMPLETE
    assert state.civilizations[sender].inventory.quantities[Resource.STONE] == sender_stone
    returned = next(
        item
        for item in state.civilizations[sender].logistics_notices
        if item.kind is NoticeKind.SHIPMENT_CARRIERS_RETURNED
    )
    assert returned.reported_outcome is JourneyOutcome.REFUSED
    # 2 carriers pack 2 x (6 travel days + 2 margin) = 16 food and eat 2 a day for the
    # 5 days they are still on the road, so 6 come home.
    assert returned.cargo == {Resource.STONE: 80, Resource.FOOD: 6}
    assert returned.treaty_id == TRADE
    validate_world(state)


def test_refused_migrants_walk_home_and_keep_their_allegiance() -> None:
    state, sender, recipient, route = treaty_world(TreatyKind.MIGRATION)
    migrants = state.civilizations[sender].population.living_ids[-2:]
    migration = DirectOrder(
        command_id="migrate",
        kind=DirectOrderKind.DISPATCH_MIGRATION,
        journey_id=EntityId(clear_journey_id("unwelcome")),
        treaty_id=MIGRATION,
        recipient_civilization_id=recipient,
        traveller_ids=migrants,
        route=route,
    )
    sovereigns = {
        sender: OneShotSovereign(migration),
        recipient: OneShotSovereign(_repudiate(MIGRATION)),
    }

    state, results = _run(state, 3, sovereigns)
    assert _events(results, "migration_refused")
    assert set(migrants).isdisjoint(state.civilizations[recipient].population.people)
    assert set(migrants).isdisjoint(build_council_report(state, sender).person_ids)

    state, results = _run(state, 4)

    assert _events(results, "migration_returned")
    people = state.civilizations[sender].population.people
    assert all(people[person_id].civilization_id == sender for person_id in migrants)
    assert all(people[person_id].location == route[0] for person_id in migrants)
    assert set(migrants) <= set(build_council_report(state, sender).person_ids)


def test_a_breaching_partys_own_caravan_is_turned_away_too() -> None:
    state, sender, recipient, route = treaty_world()
    shipment = _shipment(state, sender, recipient, route, journey_id=clear_journey_id("own"))

    state, results = _run(state, 3, {sender: OneShotSovereign(shipment, _repudiate())})

    assert _events(results, "shipment_dispatched")
    assert _events(results, "treaty_breached")
    assert _events(results, "shipment_refused")


def test_a_council_cannot_dispatch_under_a_treaty_it_just_ended() -> None:
    state, sender, recipient, route = treaty_world()
    shipment = _shipment(state, sender, recipient, route, journey_id="journey:late")

    result = validate_envelope(envelope(state, sender, _repudiate(), shipment), state)

    assert [command.command_id for command in result.accepted] == [f"repudiate:{TRADE}"]
    assert [error.code for error in result.errors] == ["no_active_treaty"]


def test_treaty_endings_reject_invalid_orders() -> None:
    state, sender, recipient, route = treaty_world()
    outsider = sorted(state.civilizations)[2]
    pending = _cancel(state, sender, recipient, route, message_id="message:second")
    state.diplomatic_missions = (
        DiplomaticMessage(
            message_id=EntityId("message:pending"),
            sender_civilization_id=sender,
            recipient_civilization_id=recipient,
            ambassador_id=state.civilizations[sender].population.living_ids[21],
            route=route,
            source_text="We withdraw.",
            departed_day=0,
            cancellation_of=TRADE,
        ),
    )
    cases = {
        "cancellation_pending": (sender, (pending,)),
        "unknown_treaty": (outsider, (_repudiate(),)),
        "invalid_treaty": (
            sender,
            (DirectOrder(command_id="bare", kind=DirectOrderKind.REPUDIATE_TREATY),),
        ),
        "duplicate_treaty_act": (
            sender,
            (_repudiate(), _repudiate().model_copy(update={"command_id": "again"})),
        ),
    }
    for code, (civilization, orders) in cases.items():
        result = validate_envelope(envelope(state, civilization, *orders), state)
        assert [error.code for error in result.errors] == [code], code
    unknown = _repudiate(EntityId("treaty:none"))
    assert validate_envelope(envelope(state, sender, unknown), state).errors[0].code == (
        "unknown_treaty"
    )


def test_an_ended_treaty_cannot_end_again_or_be_revived() -> None:
    state, sender, recipient, _ = treaty_world()
    state, _ = _run(state, 1, {recipient: OneShotSovereign(_repudiate())})

    again = validate_envelope(envelope(state, sender, _repudiate()), state)

    assert [error.code for error in again.errors] == ["unknown_treaty"]
    assert _treaty(state).ended_by == recipient
    ambassador = state.civilizations[recipient].population.living_ids[0]
    state.diplomatic_missions = (
        *state.diplomatic_missions,
        DiplomaticMessage(
            message_id=EntityId("message:late-acceptance"),
            sender_civilization_id=recipient,
            recipient_civilization_id=sender,
            ambassador_id=ambassador,
            route=(state.civilizations[recipient].population.people[ambassador].location,),
            source_text="We accept after all.",
            departed_day=state.day,
            acceptance_of=TRADE,
        ),
    )

    state, results = _run(state, 6)

    assert any(
        event.payload.get("message_id") == "message:late-acceptance"
        for event in _events(results, "message_delivered")
    )
    assert not _events(results, "treaty_activated")
    assert not _treaty(state).in_force


def test_ended_treaty_records_are_consistent() -> None:
    base = dict(
        treaty_id=TRADE,
        proposer_civilization_id=EntityId("civilization:0000000001"),
        recipient_civilization_id=EntityId("civilization:0000000002"),
        kind=TreatyKind.TRADE,
        offered_day=0,
        activated_day=5,
    )
    with pytest.raises(ValueError, match="records its day, kind, and party"):
        ActiveTreaty(**base, ended_day=6)
    with pytest.raises(ValueError, match="cannot end before it activates"):
        ActiveTreaty(
            **base,
            ended_day=4,
            end_kind=TreatyEndKind.CANCELLED,
            ended_by=EntityId("civilization:0000000001"),
        )
    with pytest.raises(ValueError, match="only a party"):
        ActiveTreaty(
            **base,
            ended_day=6,
            end_kind=TreatyEndKind.BREACHED,
            ended_by=EntityId("civilization:0000000003"),
        )
    treaty = ActiveTreaty(**base)
    with pytest.raises(ValueError, match="not a party"):
        treaty.counterparty(EntityId("civilization:0000000003"))


def test_injured_party_learns_nothing_until_it_observes_the_breach() -> None:
    state, sender, recipient, _ = treaty_world()
    before = build_council_report(state, sender)

    state, _ = _run(state, 1, {recipient: OneShotSovereign(_repudiate())})

    after = build_council_report(state, sender)
    assert after.received_messages == before.received_messages
    assert after.logistics_notices == before.logistics_notices


def test_both_parties_may_still_trade_while_a_notice_travels() -> None:
    state, sender, recipient, route = treaty_world()
    cancel = _cancel(state, sender, recipient, route, message_id=clear_message_id("slow"))
    shipment = _shipment(
        state, sender, recipient, route, journey_id=clear_journey_id("race", start_day=30)
    )
    sovereigns = {sender: ScheduledSovereign({0: (cancel,), 30: (shipment,)})}

    state, results = _run(state, 31, sovereigns)

    rejected = [event.payload.get("code") for event in _events(results, "command_rejected")]
    assert rejected == ["no_active_treaty"], "the shipment came after the notice arrived"
