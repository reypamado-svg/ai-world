import os
import subprocess
import sys

import pytest
from logistics_helpers import OneShotSovereign, ScheduledSovereign, envelope, treaty_world

from sovereign_world.capabilities import CapabilityId, CapabilityRecord
from sovereign_world.commands import (
    DirectOrder,
    DirectOrderKind,
    ProjectKind,
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
from sovereign_world.resources import Inventory, Resource
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState, validate_world
from sovereign_world.work import ConstructionProject, WorkKind, WorkOrder


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


def _clear_road_id(prefix: str, *, start_day: int = 0, days: int = 8, seed: int = 21) -> str:
    """Find a journey ID whose travel rolls allow a move on every day in the window."""
    rng = StableRng(seed)
    for index in range(100_000):
        journey_id = f"journey:{prefix}-{index}"
        if all(
            int(rng.stream(f"day:{day}:logistics:travel:{journey_id}").integers(0, 10_000))
            >= DELAY_THRESHOLD
            for day in range(start_day, start_day + days)
        ):
            return journey_id
    raise AssertionError("no clear road found")


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
    order = _shipment(state, sender, recipient, route, journey_id=_clear_road_id("goods"))
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
    assert journey.arrived_day == len(route) - 2, "one tile per day from day 0"
    assert kinds.index("shipment_dispatched") < kinds.index("shipment_arrived")
    assert kinds.index("shipment_arrived") < kinds.index("shipment_received")
    assert kinds.index("shipment_received") < kinds.index("shipment_returned")
    carriers = journey.traveller_ids
    people = state.civilizations[sender].population.people
    assert all(people[person_id].location == route[0] for person_id in carriers)


def test_logistics_knowledge_stays_private_until_observed() -> None:
    state, sender, recipient, route = treaty_world()
    order = _shipment(state, sender, recipient, route, journey_id=_clear_road_id("private"))
    sovereigns = {sender: OneShotSovereign(order)}
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

    order = _shipment(state, sender, recipient, route, journey_id=_clear_road_id("failed"))
    state, results = _run(state, 12, {sender: OneShotSovereign(order)})

    kinds = _kinds(results)
    journey = state.journeys[0]
    assert "shipment_failed" in kinds
    assert "shipment_received" not in kinds
    assert journey.outcome is JourneyOutcome.FAILED
    assert state.civilizations[sender].inventory.quantities[Resource.STONE] == sender_stone
    returned = next(
        item
        for item in state.civilizations[sender].logistics_notices
        if item.kind is NoticeKind.SHIPMENT_CARRIERS_RETURNED
    )
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
    migrants = tuple(sorted((mother, origin.population.living_ids[-1])))
    order = _migration(state, sender, recipient, route, migrants, journey_id=_clear_road_id("kin"))
    before = {person_id: origin.population.people[person_id].model_copy() for person_id in migrants}
    sovereigns = {sender: OneShotSovereign(order)}
    rng = StableRng(state.config.seed)
    kinds: list[str] = []
    for _ in range(10):
        result = advance_day(state, rng, sovereigns=sovereigns)
        state = result.state
        if state.day == 1:
            state.civilizations[sender].population.scheduled_births = (
                ScheduledBirth(due_day=200, parent_ids=(mother, father)),
            )
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


def test_travel_hazard_kills_a_migrant_permanently_and_unobserved() -> None:
    state, sender, recipient, route = treaty_world(TreatyKind.MIGRATION)
    migrants = state.civilizations[sender].population.living_ids[:3]
    state.journeys = (
        Journey(
            journey_id=EntityId(_journey_id_with_roll(0, HAZARD_THRESHOLD)),
            kind=JourneyKind.MIGRATION,
            treaty_id=EntityId("treaty:migration"),
            sender_civilization_id=sender,
            recipient_civilization_id=recipient,
            traveller_ids=migrants,
            route=route,
            departed_day=0,
        ),
    )

    state, results = _run(state, 12)

    hazard_deaths = [
        event
        for result in results
        for event in result.events.events
        if event.kind == "person_died" and event.payload.get("cause") == "travel hazard"
    ]
    assert "migrant_lost" in _kinds(results)
    assert len(hazard_deaths) == 1
    victim = EntityId(str(hazard_deaths[0].subject_id))
    assert hazard_deaths[0].actor_id is None, "nobody at home witnessed the death"
    assert hazard_deaths[0].day == 0
    grave = state.civilizations[sender].population.people[victim]
    assert not grave.alive
    assert grave.death_day == 0
    assert victim not in state.civilizations[recipient].population.people
    survivors = set(migrants) - {victim}
    assert survivors <= set(state.civilizations[recipient].population.people)


def test_a_party_killed_by_hazards_perishes_and_nobody_arrives() -> None:
    state, sender, recipient, route = treaty_world(TreatyKind.MIGRATION)
    migrant = state.civilizations[sender].population.living_ids[0]
    state.journeys = (
        Journey(
            journey_id=EntityId(_journey_id_with_roll(0, HAZARD_THRESHOLD)),
            kind=JourneyKind.MIGRATION,
            treaty_id=EntityId("treaty:migration"),
            sender_civilization_id=sender,
            recipient_civilization_id=recipient,
            traveller_ids=(migrant,),
            route=route,
            departed_day=0,
        ),
    )

    state, results = _run(state, 6)

    kinds = _kinds(results)
    journey = state.journeys[0]
    assert journey.outcome is JourneyOutcome.PERISHED
    assert journey.phase is JourneyPhase.COMPLETE
    assert "migration_party_perished" in kinds
    assert "migrants_received" not in kinds
    assert not state.civilizations[sender].population.people[migrant].alive
    assert migrant not in state.civilizations[recipient].population.people


def test_carriers_who_perish_carrying_undelivered_goods_destroy_them() -> None:
    state, sender, recipient, route = treaty_world()
    carriers = state.civilizations[sender].population.living_ids[:2]
    people = {sender: state.civilizations[sender].population.people}
    for person_id in carriers:
        people[sender][person_id].alive = False
    returning = Journey(
        journey_id=EntityId("journey:homeward"),
        kind=JourneyKind.SHIPMENT,
        treaty_id=EntityId("treaty:trade"),
        sender_civilization_id=sender,
        recipient_civilization_id=recipient,
        traveller_ids=carriers,
        route=route,
        cargo={Resource.STONE: 80},
        carrying_cargo=True,
        departed_day=0,
        route_index=2,
        phase=JourneyPhase.RETURNING,
        outcome=JourneyOutcome.FAILED,
    )

    result = advance_journeys_day((returning,), people, day=5, rng=StableRng(21))

    assert result.journeys[0].outcome is JourneyOutcome.PERISHED
    assert not result.journeys[0].carrying_cargo
    assert result.perished_ids == (returning.journey_id,)
    assert result.cargo_returned == ()


def test_failed_migration_walks_home_and_rejoins_the_roster() -> None:
    state, sender, recipient, route = treaty_world(TreatyKind.MIGRATION)
    for person in state.civilizations[recipient].population.people.values():
        person.alive = False
        person.death_day = 0
    migrants = state.civilizations[sender].population.living_ids[-2:]
    order = _migration(
        state, sender, recipient, route, migrants, journey_id=_clear_road_id("homesick")
    )

    state, results = _run(state, 3, {sender: OneShotSovereign(order)})
    assert "migration_failed" in _kinds(results)
    assert set(migrants).isdisjoint(build_council_report(state, sender).person_ids)

    state, results = _run(state, 5)

    assert "migration_returned" in _kinds(results)
    assert set(migrants) <= set(build_council_report(state, sender).person_ids)
    notice = next(
        item
        for item in state.civilizations[sender].logistics_notices
        if item.kind is NoticeKind.MIGRANTS_RETURNED
    )
    assert notice.reported_outcome is JourneyOutcome.FAILED
    people = state.civilizations[sender].population.people
    assert all(people[person_id].location == route[0] for person_id in migrants)


def test_former_explorer_can_migrate_without_breaking_the_world() -> None:
    state, sender, recipient, route = treaty_world(TreatyKind.MIGRATION)
    explorer = state.civilizations[sender].population.living_ids[0]
    expedition = DirectOrder(
        command_id="explore",
        kind=DirectOrderKind.START_EXPEDITION,
        expedition_id=EntityId("expedition:loop"),
        explorer_ids=(explorer,),
        route=(route[0], route[1], route[0]),
    )
    migration = _migration(
        state,
        sender,
        recipient,
        route,
        (explorer,),
        journey_id=_clear_road_id("explorer", start_day=30),
    )
    sovereigns = {sender: ScheduledSovereign({0: (expedition,), 30: (migration,)})}

    state, results = _run(state, 36, sovereigns)

    assert "expedition_returned" in _kinds(results)
    assert "migrants_received" in _kinds(results)
    assert explorer in state.civilizations[recipient].population.people
    validate_world(state)


def test_a_migrant_sent_back_home_reappears_on_the_home_roster() -> None:
    state, sender, recipient, route = treaty_world(TreatyKind.MIGRATION)
    traveller = state.civilizations[sender].population.living_ids[-1]
    outbound = _migration(
        state, sender, recipient, route, (traveller,), journey_id=_clear_road_id("out")
    )
    homeward = _migration(
        state,
        recipient,
        sender,
        tuple(reversed(route)),
        (traveller,),
        journey_id=_clear_road_id("back", start_day=30),
    )
    sovereigns = {
        sender: ScheduledSovereign({0: (outbound,)}),
        recipient: ScheduledSovereign({30: (homeward,)}),
    }

    state, _ = _run(state, 36, sovereigns)

    assert state.civilizations[sender].population.people[traveller].alive
    assert traveller in build_council_report(state, sender).person_ids
    assert traveller not in build_council_report(state, recipient).person_ids


def test_origin_forgets_a_capability_whose_last_practitioner_emigrates() -> None:
    state, sender, recipient, route = treaty_world(TreatyKind.MIGRATION)
    origin = state.civilizations[sender]
    scribe = origin.population.living_ids[-1]
    origin.population.people[scribe].skills[CapabilityId.WRITING.value] = 300
    origin.capabilities = tuple(
        sorted(
            (
                *origin.capabilities,
                CapabilityRecord(
                    capability=CapabilityId.WRITING,
                    practitioner_ids=(scribe,),
                    discovered_day=0,
                ),
            ),
            key=lambda record: record.capability.value,
        )
    )
    order = _migration(
        state, sender, recipient, route, (scribe,), journey_id=_clear_road_id("scribe")
    )

    state, results = _run(state, 4, {sender: OneShotSovereign(order)})

    learned = [
        event
        for result in results
        for event in result.events.events
        if event.kind in {"capability_learned", "capability_forgotten"}
        and event.payload.get("capability") == "writing"
    ]
    assert sorted((event.kind, event.actor_id) for event in learned) == [
        ("capability_forgotten", str(sender)),
        ("capability_learned", str(recipient)),
    ]
    assert CapabilityId.WRITING not in {
        record.capability for record in state.civilizations[sender].capabilities
    }
    assert CapabilityId.WRITING in {
        record.capability for record in state.civilizations[recipient].capabilities
    }


def test_expectant_mothers_cannot_set_out() -> None:
    state, sender, recipient, route = treaty_world(TreatyKind.MIGRATION)
    population = state.civilizations[sender].population
    mother = next(
        person_id
        for person_id in population.living_ids
        if population.people[person_id].sex is Sex.FEMALE
    )
    father = next(
        person_id
        for person_id in population.living_ids
        if population.people[person_id].sex is Sex.MALE
    )
    population.scheduled_births = (ScheduledBirth(due_day=2, parent_ids=(mother, father)),)

    result = validate_envelope(
        envelope(state, sender, _migration(state, sender, recipient, route, (mother,))), state
    )

    assert [error.code for error in result.errors] == ["expectant_traveller"]


def test_travellers_hold_one_duty_at_a_time() -> None:
    state, sender, recipient, route = treaty_world()
    people = state.civilizations[sender].population.living_ids
    carriers = people[:2]
    ship = _shipment(state, sender, recipient, route)
    build = DirectOrder(
        command_id="build",
        kind=DirectOrderKind.START_PROJECT,
        worker_ids=carriers,
        project_id=EntityId("project:hut"),
        project_kind=ProjectKind.STORAGE,
    )
    teach = DirectOrder(
        command_id="teach",
        kind=DirectOrderKind.START_TEACHING,
        assignment_id=EntityId("teaching:1"),
        teacher_id=carriers[0],
        apprentice_id=people[5],
        capability=state.civilizations[sender].capabilities[0].capability,
    )
    explore = DirectOrder(
        command_id="explore",
        kind=DirectOrderKind.START_EXPEDITION,
        expedition_id=EntityId("expedition:a"),
        explorer_ids=(people[6],),
        route=(route[0], route[1]),
    )
    embassy = DirectOrder(
        command_id="embassy",
        kind=DirectOrderKind.SEND_MESSAGE,
        message_id=EntityId("message:a"),
        ambassador_id=people[6],
        recipient_civilization_id=recipient,
        message_text="Greetings.",
        route=route,
    )

    result = validate_envelope(envelope(state, sender, ship, build, teach, explore, embassy), state)

    assert [command.command_id for command in result.accepted] == [ship.command_id, "explore"]
    assert [error.code for error in result.errors] == [
        "person_travelling",
        "person_travelling",
        "traveller_unavailable",
    ]


def test_travellers_contribute_no_labour_at_home() -> None:
    state, sender, recipient, route = treaty_world()
    civilization = state.civilizations[sender]
    carriers = civilization.population.living_ids[:2]
    civilization.projects[EntityId("project:hut")] = ConstructionProject(
        project_id=EntityId("project:hut"),
        location=civilization.start_center,
        required_materials={Resource.TIMBER: 40},
        delivered_materials={Resource.TIMBER: 40},
        required_labor_minutes=960,
    )
    civilization.work_orders = (
        WorkOrder(
            order_id=EntityId("work:hut"),
            kind=WorkKind.CONSTRUCT,
            worker_ids=carriers,
            project_id=EntityId("project:hut"),
        ),
    )
    state.journeys = (
        Journey(
            journey_id=EntityId(_clear_road_id("labour")),
            kind=JourneyKind.SHIPMENT,
            treaty_id=EntityId("treaty:trade"),
            sender_civilization_id=sender,
            recipient_civilization_id=recipient,
            traveller_ids=carriers,
            route=route,
            cargo={Resource.STONE: 10},
            carrying_cargo=True,
            departed_day=0,
        ),
    )

    state, results = _run(state, 1)

    assert "work_completed" not in _kinds(results)
    hut = state.civilizations[sender].projects[EntityId("project:hut")]
    assert hut.completed_labor_minutes == 0


def test_unfunded_shipment_is_recorded_privately_and_never_departs() -> None:
    state, sender, recipient, route = treaty_world()
    people = state.civilizations[sender].population.living_ids
    build = DirectOrder(
        command_id="build",
        kind=DirectOrderKind.START_PROJECT,
        worker_ids=people[10:12],
        project_id=EntityId("project:store"),
        project_kind=ProjectKind.STORAGE,
    )
    ship = _shipment(state, sender, recipient, route, carriers=6, cargo={Resource.STONE: 280})

    state, results = _run(state, 1, {sender: OneShotSovereign(build, ship)})

    assert "shipment_unfunded" in _kinds(results)
    assert state.journeys == ()
    assert state.civilizations[sender].inventory.quantities[Resource.STONE] == 270
    assert [item.kind for item in state.civilizations[sender].logistics_notices] == [
        NoticeKind.SHIPMENT_UNFUNDED
    ]


def test_receipt_beyond_storage_capacity_is_wasted() -> None:
    state, sender, recipient, route = treaty_world()
    order = _shipment(state, sender, recipient, route, journey_id=_clear_road_id("overflow"))
    state, _ = _run(state, len(route) - 2, {sender: OneShotSovereign(order)})
    storehouse = state.civilizations[recipient].inventory
    stone = storehouse.quantities[Resource.STONE]
    state.civilizations[recipient].inventory = Inventory(
        capacity=storehouse.total_units + 25, quantities=dict(storehouse.quantities)
    )

    state, results = _run(state, 1)

    receipt = next(
        event
        for result in results
        for event in result.events.events
        if event.kind == "shipment_received"
    )
    assert receipt.payload == {"units": 25, "wasted": 55}
    assert state.civilizations[recipient].inventory.quantities[Resource.STONE] == stone + 25
    assert state.civilizations[recipient].logistics_notices[0].cargo == {Resource.STONE: 25}


def test_validate_world_rejects_malformed_journeys() -> None:
    state, sender, recipient, route = treaty_world()
    state, _ = _run(state, 1)
    carriers = state.civilizations[sender].population.living_ids[:2]
    journey = Journey(
        journey_id=EntityId("journey:check"),
        kind=JourneyKind.SHIPMENT,
        treaty_id=EntityId("treaty:trade"),
        sender_civilization_id=sender,
        recipient_civilization_id=recipient,
        traveller_ids=carriers,
        route=route,
        cargo={Resource.STONE: 10},
        carrying_cargo=True,
        departed_day=1,
    )
    state.journeys = (journey,)
    validate_world(state)
    broken = {
        "matching active treaty": (journey.model_copy(update={"treaty_id": "treaty:none"}),),
        "two journeys": (
            journey,
            journey.model_copy(update={"journey_id": EntityId("journey:twin")}),
        ),
        "route position": (journey.model_copy(update={"route_index": 2}),),
        "sorted": (
            journey.model_copy(update={"journey_id": EntityId("journey:z")}),
            journey.model_copy(
                update={"journey_id": EntityId("journey:a"), "traveller_ids": carriers[:1]}
            ),
        ),
    }
    for message, journeys in broken.items():
        candidate = state.model_copy(deep=True)
        candidate.journeys = journeys
        with pytest.raises(ValueError, match=message):
            validate_world(candidate)


DETERMINISM_SCRIPT = """
import hashlib
import sys
from uuid import UUID
sys.path.insert(0, "tests")
from logistics_helpers import OneShotSovereign, treaty_world
from sovereign_world.commands import DirectOrder, DirectOrderKind
from sovereign_world.engine import advance_day
from sovereign_world.ids import EntityId
from sovereign_world.resources import Resource
from sovereign_world.rng import StableRng
from sovereign_world.state import state_hash

state, sender, recipient, route = treaty_world()
state.run_id = UUID(int=0)
state.manifest_hash = "0" * 64
order = DirectOrder(
    command_id="ship",
    kind=DirectOrderKind.DISPATCH_SHIPMENT,
    journey_id=EntityId("journey:hashseed"),
    treaty_id=EntityId("treaty:trade"),
    recipient_civilization_id=recipient,
    traveller_ids=state.civilizations[sender].population.living_ids[:2],
    route=route,
    cargo={Resource.TIMBER: 40, Resource.STONE: 50},
)
sovereigns = {sender: OneShotSovereign(order)}
rng = StableRng(state.config.seed)
events = []
for _ in range(8):
    result = advance_day(state, rng, sovereigns=sovereigns)
    state = result.state
    events.append(result.events.canonical_json())
print(state_hash(state), hashlib.sha256("\\n".join(events).encode()).hexdigest())
"""


def test_journey_history_is_identical_across_processes() -> None:
    outputs = set()
    for hash_seed in ("0", "1", "4242"):
        completed = subprocess.run(
            [sys.executable, "-c", DETERMINISM_SCRIPT],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "PYTHONHASHSEED": hash_seed},
        )
        outputs.add(completed.stdout.strip())
    assert len(outputs) == 1, outputs
