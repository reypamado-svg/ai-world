from logistics_helpers import OneShotSovereign, clear_journey_id, envelope, treaty_world

from sovereign_world.commands import DirectOrder, DirectOrderKind, validate_envelope
from sovereign_world.diplomacy import TreatyKind
from sovereign_world.engine import TransitionResult, _dispatch_journey, advance_day
from sovereign_world.hexmap import HexCoord, Terrain, Tile, WorldMap
from sovereign_world.ids import EntityId
from sovereign_world.logistics import (
    Journey,
    JourneyKind,
    NoticeKind,
    advance_journeys_day,
    forage_chance_bp,
    provisions_needed,
)
from sovereign_world.resources import Inventory, Resource
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState, validate_world


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


def _migration(state: WorldState, sender, recipient, route, migrants, journey_id: str):
    return DirectOrder(
        command_id=f"migrate:{journey_id}",
        kind=DirectOrderKind.DISPATCH_MIGRATION,
        journey_id=EntityId(journey_id),
        treaty_id=EntityId("treaty:migration"),
        recipient_civilization_id=recipient,
        traveller_ids=migrants,
        route=route,
    )


def _consumed(result: TransitionResult, civilization_id: EntityId) -> int:
    return next(
        int(event.payload["units"])
        for event in result.events.events
        if event.kind == "food_consumed" and event.actor_id == str(civilization_id)
    )


def _tile(q: int, terrain: Terrain, soil: int, river: bool = False) -> Tile:
    return Tile(
        coord=HexCoord(q, 0),
        terrain=terrain,
        elevation=500,
        moisture=500,
        temperature=500,
        soil=soil,
        timber=0,
        stone=0,
        ore=0,
        river=river,
    )


def test_packing_covers_the_road_plus_a_delay_margin() -> None:
    assert provisions_needed(6, 2) == 2 * (6 + 2)
    assert provisions_needed(3, 3) == 3 * (3 + 2)
    assert provisions_needed(24, 1) == 24 + 6
    assert provisions_needed(12, 1, extra=5) == 12 + 3 + 5


def test_foraging_depends_on_the_land() -> None:
    world = WorldMap(
        width=4,
        height=1,
        tiles=(
            _tile(0, Terrain.FOREST, 1_000, river=True),
            _tile(1, Terrain.GRASSLAND, 500),
            _tile(2, Terrain.DESERT, 0),
            _tile(3, Terrain.TUNDRA, 200),
        ),
    )

    assert forage_chance_bp(world, HexCoord(0, 0)) == 4_000 + 3_000 + 1_500, "the best land"
    assert forage_chance_bp(world, HexCoord(1, 0)) == 3_000 + 1_500
    assert forage_chance_bp(world, HexCoord(2, 0)) == 500
    assert forage_chance_bp(world, HexCoord(3, 0)) == 1_000 + 600


def test_food_leaves_at_dispatch_and_travellers_never_eat_twice() -> None:
    state, sender, recipient, route = treaty_world(TreatyKind.MIGRATION)
    migrants = state.civilizations[sender].population.living_ids[-3:]
    order = _migration(state, sender, recipient, route, migrants, clear_journey_id("fed"))
    living = len(state.civilizations[sender].population.living_ids)
    food_before = state.civilizations[sender].inventory.quantities[Resource.FOOD]

    state, results = _run(state, 2, {sender: OneShotSovereign(order)})

    journey = state.journeys[0]
    packed = provisions_needed(len(route) - 1, 3)
    assert journey.provisions_packed == packed
    home_eaten = [_consumed(result, sender) for result in results]
    assert home_eaten == [living - 3, living - 3], "migrants do not eat at home"
    assert journey.provisions == packed - 2 * 3, "each migrant ate 1 a day from the pack"
    food_after = state.civilizations[sender].inventory.quantities[Resource.FOOD]
    assert food_after == food_before - packed - sum(home_eaten)
    validate_world(state)


def test_received_migrants_bring_their_leftover_food() -> None:
    state, sender, recipient, route = treaty_world(TreatyKind.MIGRATION)
    migrants = state.civilizations[sender].population.living_ids[-2:]
    order = _migration(state, sender, recipient, route, migrants, clear_journey_id("gift"))
    packed = provisions_needed(len(route) - 1, 2)

    state, results = _run(state, len(route) - 1, {sender: OneShotSovereign(order)})

    received = next(
        event
        for result in results
        for event in result.events.events
        if event.kind == "migrants_received"
    )
    # On the road at the end of every day before arrival: 2 days x 2 migrants eaten.
    leftover = packed - 2 * 2
    assert received.payload["provisions"] == leftover
    [notice] = state.civilizations[recipient].logistics_notices
    assert notice.kind is NoticeKind.MIGRANTS_RECEIVED
    assert notice.cargo == {Resource.FOOD: leftover}
    assert state.journeys[0].provisions == 0


def test_the_origin_cannot_see_the_arrival_in_its_food_use() -> None:
    state, sender, recipient, route = treaty_world(TreatyKind.MIGRATION)
    migrants = state.civilizations[sender].population.living_ids[-4:]
    order = _migration(state, sender, recipient, route, migrants, clear_journey_id("quiet"))

    state, results = _run(state, len(route) + 1, {sender: OneShotSovereign(order)})

    arrival_day = next(
        event.day
        for result in results
        for event in result.events.events
        if event.kind == "migrants_received"
    )
    assert not any(
        event.kind in {"person_died", "person_born"} and event.actor_id == str(sender)
        for result in results
        for event in result.events.events
    ), "no natural births or deaths muddy this comparison"
    home_eaten = [_consumed(result, sender) for result in results]
    assert home_eaten[arrival_day] == home_eaten[arrival_day - 1] == home_eaten[0]


def test_an_empty_pack_forages_and_the_unlucky_go_hungry() -> None:
    state, sender, recipient, route = treaty_world(TreatyKind.MIGRATION)
    migrants = state.civilizations[sender].population.living_ids[-6:]
    journey = Journey(
        journey_id=EntityId("journey:lean"),
        kind=JourneyKind.MIGRATION,
        treaty_id=EntityId("treaty:migration"),
        sender_civilization_id=sender,
        recipient_civilization_id=recipient,
        traveller_ids=migrants,
        route=route,
        provisions_packed=2,
        provisions=2,
        departed_day=0,
    )
    people = {sender: state.civilizations[sender].population.people}
    debt_before = {person_id: people[sender][person_id].nutrition_debt for person_id in migrants}

    result = advance_journeys_day(
        (journey,),
        people,
        day=1,
        rng=StableRng(21),
        treaties_in_force=frozenset({journey.treaty_id}),
        world_map=state.world_map,
    )

    assert result.journeys[0].provisions == 0
    assert result.exhausted_ids == (journey.journey_id,)
    [foraging] = result.foraging
    assert foraging.fed + foraging.hungry == 4, "two ate from the pack, four had to forage"
    after = result.people_by_civilization[sender]
    hungrier = [
        person_id
        for person_id in migrants
        if after[person_id].nutrition_debt == debt_before[person_id] + 1
    ]
    assert len(hungrier) == foraging.hungry
    assert all(
        after[person_id].nutrition_debt <= debt_before[person_id] + 1 for person_id in migrants
    )
    assert set(hungrier).isdisjoint(sorted(migrants)[:2]), "the first two ate from the pack"


def test_journeys_that_cannot_carry_or_afford_their_food_are_refused() -> None:
    state, sender, recipient, route = treaty_world()
    carriers = state.civilizations[sender].population.living_ids[:2]
    too_heavy = DirectOrder(
        command_id="heavy",
        kind=DirectOrderKind.DISPATCH_SHIPMENT,
        journey_id=EntityId("journey:heavy"),
        treaty_id=EntityId("treaty:trade"),
        recipient_civilization_id=recipient,
        traveller_ids=carriers,
        route=route,
        cargo={Resource.STONE: 90},
    )
    result = validate_envelope(envelope(state, sender, too_heavy), state)
    assert [error.code for error in result.errors] == ["cargo_over_capacity"]

    storehouse = state.civilizations[sender].inventory.quantities
    state.civilizations[sender].inventory = Inventory(
        capacity=100_000, quantities={**storehouse, Resource.FOOD: 10}
    )
    hungry = too_heavy.model_copy(update={"cargo": {Resource.STONE: 50}})
    result = validate_envelope(envelope(state, sender, hungry), state)
    assert [error.code for error in result.errors] == ["insufficient_provisions"]

    greedy = too_heavy.model_copy(update={"cargo": {Resource.FOOD: 11}})
    result = validate_envelope(envelope(state, sender, greedy), state)
    assert [error.code for error in result.errors] == ["insufficient_goods"]


def test_extra_provisions_are_packed_on_request() -> None:
    state, sender, recipient, route = treaty_world(TreatyKind.MIGRATION)
    migrants = state.civilizations[sender].population.living_ids[-2:]
    order = _migration(state, sender, recipient, route, migrants, "journey:generous").model_copy(
        update={"extra_provisions": 7}
    )

    state, _ = _run(state, 1, {sender: OneShotSovereign(order)})

    expected = provisions_needed(len(route) - 1, 2) + 7
    assert state.journeys[0].provisions_packed == expected


def test_an_unfunded_migration_never_departs_and_is_noted_privately() -> None:
    state, sender, recipient, route = treaty_world(TreatyKind.MIGRATION)
    migrants = state.civilizations[sender].population.living_ids[-2:]
    order = _migration(state, sender, recipient, route, migrants, "journey:broke")
    storehouse = state.civilizations[sender].inventory.quantities
    state.civilizations[sender].inventory = Inventory(
        capacity=100_000, quantities={**storehouse, Resource.FOOD: 3}
    )

    events = _dispatch_journey(state, sender, order)

    assert [event.kind for event in events] == ["migration_unfunded"]
    assert state.journeys == ()
    [notice] = state.civilizations[sender].logistics_notices
    assert notice.kind is NoticeKind.MIGRATION_UNFUNDED
    assert state.civilizations[sender].inventory.quantities[Resource.FOOD] == 3
