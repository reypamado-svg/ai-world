from logistics_helpers import OneShotSovereign, envelope, treaty_world

from sovereign_world.capabilities import CapabilityId
from sovereign_world.commands import (
    DirectOrder,
    DirectOrderKind,
    build_council_report,
    validate_envelope,
)
from sovereign_world.diplomacy import TreatyKind
from sovereign_world.engine import TransitionResult, advance_day
from sovereign_world.ids import EntityId
from sovereign_world.logistics import (
    DELAY_THRESHOLD,
    HAZARD_THRESHOLD,
    Journey,
    JourneyKind,
    JourneyOutcome,
    JourneyPhase,
    NoticeKind,
    advance_journeys_day,
)
from sovereign_world.people import ScheduledBirth, Sex
from sovereign_world.resources import Resource
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState, state_hash, validate_world


def _shipment(
    state: WorldState,
    sender: EntityId,
    recipient: EntityId,
    route,
    *,
    journey_id: str = "journey:stone-2",
    cargo: dict[Resource, int] | None = None,
    carriers: int = 2,
    treaty_id: str = "treaty:trade",
) -> DirectOrder:
    return DirectOrder(
        command_id=f"ship:{journey_id}",
        kind=DirectOrderKind.DISPATCH_SHIPMENT,
        journey_id=EntityId(journey_id),
        treaty_id=EntityId(treaty_id),
        recipient_civilization_id=recipient,
        traveller_ids=state.civilizations[sender].population.living_ids[:carriers],
        route=route,
        cargo={Resource.STONE: 80} if cargo is None else cargo,
    )


def _migration(
    state: WorldState,
    sender: EntityId,
    recipient: EntityId,
    route,
    migrants: tuple[EntityId, ...],
    *,
    journey_id: str = "journey:settlers",
) -> DirectOrder:
    return DirectOrder(
        command_id=f"migrate:{journey_id}",
        kind=DirectOrderKind.DISPATCH_MIGRATION,
        journey_id=EntityId(journey_id),
        treaty_id=EntityId("treaty:migration"),
        recipient_civilization_id=recipient,
        traveller_ids=migrants,
        route=route,
    )


def _run(
    state: WorldState,
    days: int,
    sovereigns=None,
) -> tuple[WorldState, list[TransitionResult]]:
    rng = StableRng(state.config.seed)
    results: list[TransitionResult] = []
    for _ in range(days):
        result = advance_day(state, rng, sovereigns=sovereigns)
        state = result.state
        results.append(result)
    return state, results


def _kinds(results: list[TransitionResult]) -> list[str]:
    return [event.kind for result in results for event in result.events.events]


def _journey_id_with_roll(low: int, high: int, *, day: int = 0, seed: int = 21) -> str:
    """Find a journey ID whose first travel roll falls in [low, high)."""
    rng = StableRng(seed)
    for index in range(100_000):
        journey_id = f"journey:probe-{index}"
        roll = int(rng.stream(f"day:{day}:logistics:travel:{journey_id}").integers(0, 10_000))
        if low <= roll < high:
            return journey_id
    raise AssertionError("no probe matched")


def test_shipment_requires_an_active_trade_treaty() -> None:
    for kind in (None, TreatyKind.MIGRATION, TreatyKind.PEACE):
        state, sender, recipient, route = treaty_world(kind)
        order = _shipment(state, sender, recipient, route)
        if kind is not None:
            order = order.model_copy(update={"treaty_id": EntityId(f"treaty:{kind.value}")})

        result = validate_envelope(envelope(state, sender, order), state)

        assert result.accepted == ()
        assert result.errors[0].code == "no_active_treaty"


def test_migration_requires_an_active_migration_treaty() -> None:
    state, sender, recipient, route = treaty_world(TreatyKind.TRADE)
    migrants = state.civilizations[sender].population.living_ids[:2]
    order = _migration(state, sender, recipient, route, migrants).model_copy(
        update={"treaty_id": EntityId("treaty:trade")}
    )

    result = validate_envelope(envelope(state, sender, order), state)

    assert result.errors[0].code == "no_active_treaty"


def test_journey_orders_reject_bad_routes_goods_and_travellers() -> None:
    state, sender, recipient, route = treaty_world()
    civilization = state.civilizations[sender]
    away = civilization.population.living_ids[-1]
    civilization.population.people[away].location = route[1]
    cases = {
        "invalid_route": _shipment(state, sender, recipient, route[:-1]),
        "invalid_journey": _shipment(state, sender, recipient, route, carriers=0),
        "cargo_over_capacity": _shipment(
            state, sender, recipient, route, cargo={Resource.STONE: 101}
        ),
        "insufficient_goods": _shipment(
            state, sender, recipient, route, carriers=16, cargo={Resource.STONE: 301}
        ),
        "invalid_cargo": _shipment(state, sender, recipient, route, cargo={}),
        "traveller_not_home": _shipment(state, sender, recipient, route).model_copy(
            update={"traveller_ids": (away,)}
        ),
    }
    for code, order in cases.items():
        result = validate_envelope(envelope(state, sender, order), state)
        assert [error.code for error in result.errors] == [code], code


def test_one_envelope_cannot_ship_the_same_goods_or_carriers_twice() -> None:
    state, sender, recipient, route = treaty_world()
    first = _shipment(
        state,
        sender,
        recipient,
        route,
        journey_id="journey:a",
        cargo={Resource.STONE: 100},
        carriers=4,
    )
    reused_goods = _shipment(
        state,
        sender,
        recipient,
        route,
        journey_id="journey:b",
        cargo={Resource.STONE: 201},
        carriers=5,
    ).model_copy(update={"traveller_ids": state.civilizations[sender].population.living_ids[10:15]})
    reused_carrier = _shipment(
        state,
        sender,
        recipient,
        route,
        journey_id="journey:c",
        cargo={Resource.STONE: 10},
        carriers=1,
    )

    result = validate_envelope(envelope(state, sender, first, reused_goods, reused_carrier), state)

    assert [command.command_id for command in result.accepted] == ["ship:journey:a"]
    assert [error.code for error in result.errors] == [
        "insufficient_goods",
        "traveller_unavailable",
    ]


def test_goods_reach_the_recipient_only_on_physical_arrival() -> None:
    state, sender, recipient, route = treaty_world()
    order = _shipment(state, sender, recipient, route)
    sovereigns = {sender: OneShotSovereign(order)}
    rng = StableRng(state.config.seed)
    recipient_stone = state.civilizations[recipient].inventory.quantities[Resource.STONE]
    sender_stone = state.civilizations[sender].inventory.quantities[Resource.STONE]
    kinds: list[str] = []
    for _ in range(12):
        result = advance_day(state, rng, sovereigns=sovereigns)
        state = result.state
        day_kinds = [event.kind for event in result.events.events]
        kinds.extend(day_kinds)
        current = state.civilizations[recipient].inventory.quantities[Resource.STONE]
        if "shipment_received" in kinds:
            assert current == recipient_stone + 80
        else:
            assert current == recipient_stone
            assert state.civilizations[recipient].logistics_notices == ()

    journey = state.journeys[0]
    assert state.civilizations[sender].inventory.quantities[Resource.STONE] == sender_stone - 80
    assert journey.outcome is JourneyOutcome.DELIVERED
    assert journey.phase is JourneyPhase.COMPLETE
    assert journey.arrived_day is not None
    assert journey.arrived_day >= len(route) - 2
    assert kinds.index("shipment_dispatched") < kinds.index("shipment_arrived")
    assert kinds.index("shipment_arrived") < kinds.index("shipment_received")
    assert kinds.index("shipment_received") < kinds.index("shipment_returned")
    carriers = journey.traveller_ids
    people = state.civilizations[sender].population.people
    assert all(people[person_id].location == route[0] for person_id in carriers)


def test_logistics_knowledge_stays_private_until_observed() -> None:
    state, sender, recipient, route = treaty_world()
    sovereigns = {sender: OneShotSovereign(_shipment(state, sender, recipient, route))}
    state, results = _run(state, 2, sovereigns)

    assert "shipment_received" not in _kinds(results)
    sender_report = build_council_report(state, sender)
    recipient_report = build_council_report(state, recipient)
    assert [item.kind for item in sender_report.logistics_notices] == [
        NoticeKind.SHIPMENT_DISPATCHED
    ]
    assert recipient_report.logistics_notices == ()

    state, results = _run(state, 1)
    assert "shipment_received" in _kinds(results)
    assert [item.kind for item in build_council_report(state, recipient).logistics_notices] == [
        NoticeKind.SHIPMENT_RECEIVED
    ]
    assert [item.kind for item in build_council_report(state, sender).logistics_notices] == [
        NoticeKind.SHIPMENT_DISPATCHED
    ], "the sender learns the outcome only when carriers return"

    state, _ = _run(state, 5)
    returned = [
        item
        for item in build_council_report(state, sender).logistics_notices
        if item.kind is NoticeKind.SHIPMENT_CARRIERS_RETURNED
    ]
    assert returned[0].reported_outcome is JourneyOutcome.DELIVERED


def test_travel_hazard_loses_cargo_and_carriers_turn_back() -> None:
    state, sender, recipient, route = treaty_world()
    journey_id = _journey_id_with_roll(0, HAZARD_THRESHOLD)
    order = _shipment(state, sender, recipient, route, journey_id=journey_id)
    recipient_stone = state.civilizations[recipient].inventory.quantities[Resource.STONE]

    state, results = _run(state, 10, {sender: OneShotSovereign(order)})

    kinds = _kinds(results)
    journey = state.journeys[0]
    assert "shipment_lost" in kinds
    assert "shipment_received" not in kinds
    assert journey.outcome is JourneyOutcome.LOST
    assert journey.phase is JourneyPhase.COMPLETE
    assert state.civilizations[recipient].inventory.quantities[Resource.STONE] == recipient_stone
    assert state.civilizations[sender].inventory.quantities[Resource.STONE] == 220


def test_delay_holds_the_party_in_place() -> None:
    state, sender, recipient, route = treaty_world()
    journey_id = _journey_id_with_roll(HAZARD_THRESHOLD, DELAY_THRESHOLD)
    state, results = _run(
        state,
        1,
        {
            sender: OneShotSovereign(
                _shipment(state, sender, recipient, route, journey_id=journey_id)
            )
        },
    )

    journey = state.journeys[0]
    assert "shipment_delayed" in _kinds(results)
    assert journey.route_index == 0
    assert journey.delayed_days == 1


def test_delivery_fails_without_living_recipients_and_goods_come_home() -> None:
    state, sender, recipient, route = treaty_world()
    for person in state.civilizations[recipient].population.people.values():
        person.alive = False
        person.death_day = 0
    sender_stone = state.civilizations[sender].inventory.quantities[Resource.STONE]

    state, results = _run(
        state, 12, {sender: OneShotSovereign(_shipment(state, sender, recipient, route))}
    )

    kinds = _kinds(results)
    journey = state.journeys[0]
    assert "shipment_failed" in kinds
    assert "shipment_received" not in kinds
    assert journey.outcome is JourneyOutcome.FAILED
    assert state.civilizations[sender].inventory.quantities[Resource.STONE] == sender_stone
    returned = state.civilizations[sender].logistics_notices[0]
    assert returned.kind is NoticeKind.SHIPMENT_CARRIERS_RETURNED
    assert returned.cargo == {Resource.STONE: 80}


def test_migrants_change_allegiance_only_on_arrival_with_history_intact() -> None:
    state, sender, recipient, route = treaty_world(TreatyKind.MIGRATION)
    origin = state.civilizations[sender]
    mother = next(
        person_id
        for person_id in origin.population.living_ids
        if origin.population.people[person_id].sex is Sex.FEMALE
    )
    father = next(
        person_id
        for person_id in origin.population.living_ids
        if origin.population.people[person_id].sex is Sex.MALE
    )
    origin.population.people[mother].skills[CapabilityId.HERBAL_CARE.value] = 250
    origin.population.scheduled_births = (ScheduledBirth(due_day=200, parent_ids=(mother, father)),)
    migrants = tuple(sorted((mother, origin.population.living_ids[-1])))
    order = _migration(state, sender, recipient, route, migrants)
    before = {person_id: origin.population.people[person_id].model_copy() for person_id in migrants}
    sovereigns = {sender: OneShotSovereign(order)}
    rng = StableRng(state.config.seed)
    kinds: list[str] = []
    for _ in range(10):
        result = advance_day(state, rng, sovereigns=sovereigns)
        state = result.state
        kinds.extend(event.kind for event in result.events.events)
        destination_people = state.civilizations[recipient].population.people
        if "migrants_received" not in kinds:
            assert not set(migrants) & set(destination_people)
            assert set(migrants) <= set(state.civilizations[sender].population.people)
        assert set(migrants).isdisjoint(build_council_report(state, sender).person_ids)

    destination = state.civilizations[recipient]
    assert "migration_arrived" in kinds
    assert "migrants_received" in kinds
    for person_id in migrants:
        person = destination.population.people[person_id]
        assert person.civilization_id == recipient
        assert person.birth_day == before[person_id].birth_day
        assert person.parent_ids == before[person_id].parent_ids
        assert person.location == route[-1]
        assert person_id not in state.civilizations[sender].population.people
    assert destination.population.scheduled_births[-1].parent_ids == (mother, father)
    assert state.civilizations[sender].population.scheduled_births == ()
    herbal = next(
        record
        for record in destination.capabilities
        if record.capability is CapabilityId.HERBAL_CARE
    )
    assert mother in herbal.practitioner_ids
    assert [item.kind for item in destination.logistics_notices] == [NoticeKind.MIGRANTS_RECEIVED]
    assert set(migrants).isdisjoint(build_council_report(state, sender).person_ids)
    validate_world(state)


def test_migrant_who_dies_on_the_road_stays_dead_and_never_arrives() -> None:
    state, sender, recipient, route = treaty_world(TreatyKind.MIGRATION)
    migrants = state.civilizations[sender].population.living_ids[:3]
    journey = Journey(
        journey_id=EntityId(_journey_id_with_roll(0, HAZARD_THRESHOLD)),
        kind=JourneyKind.MIGRATION,
        treaty_id=EntityId("treaty:migration"),
        sender_civilization_id=sender,
        recipient_civilization_id=recipient,
        traveller_ids=migrants,
        route=route,
        departed_day=0,
    )
    state.journeys = (journey,)

    state, results = _run(state, 12)

    kinds = _kinds(results)
    dead = [
        person_id
        for person_id in migrants
        if person_id in state.civilizations[sender].population.people
        and not state.civilizations[sender].population.people[person_id].alive
    ]
    assert "migrant_lost" in kinds
    assert len(dead) >= 1
    death_events = [
        event
        for result in results
        for event in result.events.events
        if event.kind == "person_died" and event.payload.get("cause") == "travel hazard"
    ]
    assert {event.subject_id for event in death_events} == set(dead)
    arrivals = set(state.civilizations[recipient].population.people) & set(migrants)
    assert arrivals.isdisjoint(dead)
    assert all(
        state.civilizations[sender].population.people[person_id].death_day is not None
        for person_id in dead
    )


def test_a_party_that_all_dies_perishes_permanently() -> None:
    state, sender, recipient, route = treaty_world(TreatyKind.MIGRATION)
    migrants = state.civilizations[sender].population.living_ids[:2]
    journey = Journey(
        journey_id=EntityId("journey:doomed"),
        kind=JourneyKind.MIGRATION,
        treaty_id=EntityId("treaty:migration"),
        sender_civilization_id=sender,
        recipient_civilization_id=recipient,
        traveller_ids=migrants,
        route=route,
        departed_day=0,
    )
    people = {sender: state.civilizations[sender].population.people}
    for person_id in migrants:
        people[sender][person_id].alive = False
        people[sender][person_id].death_day = 0

    result = advance_journeys_day((journey,), people, day=1, rng=StableRng(21))

    assert result.perished_ids == (journey.journey_id,)
    assert result.journeys[0].outcome is JourneyOutcome.PERISHED
    assert result.arrived == ()
    assert not any(
        person.alive
        for person in result.people_by_civilization[sender].values()
        if person.person_id in migrants
    )


def test_journey_history_is_deterministic() -> None:
    initial, sender, recipient, route = treaty_world()

    def history() -> tuple[str, list[str]]:
        state = initial.model_copy(deep=True)
        state, results = _run(
            state, 10, {sender: OneShotSovereign(_shipment(state, sender, recipient, route))}
        )
        return state_hash(state), [result.events.canonical_json() for result in results]

    assert history() == history()
