from logistics_helpers import OneShotSovereign, clear_journey_id, envelope, treaty_world

from sovereign_world.commands import (
    DirectOrder,
    DirectOrderKind,
    build_council_report,
    validate_envelope,
)
from sovereign_world.engine import TransitionResult, _learn_by_sight, advance_day
from sovereign_world.hexmap import HexCoord
from sovereign_world.ids import EntityId
from sovereign_world.logistics import JourneyOutcome
from sovereign_world.resources import Resource
from sovereign_world.rng import StableRng
from sovereign_world.roads import Road, RoadGrade
from sovereign_world.state import WorldState, validate_world
from sovereign_world.stores import StorehouseGrade
from sovereign_world.territory import Settlement
from sovereign_world.war import Occupation, OccupationEnd, WarObjective


def _world():
    return treaty_world(distance=4)


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


def _codes(state: WorldState, civilization_id: EntityId, *orders: DirectOrder) -> list[str]:
    errors = validate_envelope(envelope(state, civilization_id, *orders), state).errors
    return [error.code for error in errors]


def _send_away(state: WorldState, civilization_id: EntityId, keep: int, tile: HexCoord) -> None:
    people = state.civilizations[civilization_id].population.people
    for person_id in sorted(people)[keep:]:
        people[person_id].location = tile


def _arm(state: WorldState, civilization_id: EntityId, axes: int) -> None:
    civilization = state.civilizations[civilization_id]
    quantities = dict(civilization.inventory.quantities)
    quantities[Resource.AXE] = max(quantities.get(Resource.AXE, 0), axes)
    civilization.inventory = civilization.inventory.model_copy(update={"quantities": quantities})


def _occupy_order(state, home, rival, route, *, fighters=12, provisions=40, **extra):
    return DirectOrder(
        command_id="occupy",
        kind=DirectOrderKind.SEND_WAR_PARTY,
        journey_id=EntityId(clear_journey_id("occupy", days=12)),
        recipient_civilization_id=rival,
        traveller_ids=state.civilizations[home].population.living_ids[:fighters],
        route=route,
        cargo={Resource.AXE: min(fighters, 8)},
        war_objective=WarObjective.OCCUPY,
        extra_provisions=provisions,
        **extra,
    )


def _occupied(state, home, rival, route, *, keep=3, **kwargs):
    _send_away(state, rival, keep, HexCoord(route[-1].q, route[-1].r - 3))
    _arm(state, home, 8)
    order = _occupy_order(state, home, rival, route, **kwargs)
    assert _codes(state, home, order) == []
    results: list[TransitionResult] = []
    while not state.occupations:
        assert state.day < 10, "the settlement was never occupied"
        state, more = _run(state, 1, {home: OneShotSovereign(order)})
        results.extend(more)
    [party] = [journey for journey in state.journeys if journey.encamped]
    return state, results, party


def test_occupiers_march_on_a_known_settlement() -> None:
    state, home, rival, route = _world()
    _arm(state, home, 8)
    order = _occupy_order(state, home, rival, route)
    assert _codes(state, home, order) == []
    short = order.model_copy(update={"route": route[:-1]})
    assert _codes(state, home, short) == ["invalid_route"]


def test_a_beaten_settlement_is_held_and_its_store_feeds_the_occupiers() -> None:
    state, home, rival, route = _world()
    state, results, party = _occupied(state, home, rival, route)

    assert _events(results, "settlement_occupied")
    [battle] = state.battles
    assert battle.winner_id == home and battle.tile == route[-1]
    [occupation] = state.occupations
    assert occupation.active and occupation.owner_id == rival and occupation.tile == route[-1]
    assert party.route_index == len(party.route) - 1
    assert build_council_report(state, rival).occupations == (occupation,)
    assert build_council_report(state, home).occupations == (occupation,)

    rival_food = state.civilizations[rival].inventory.quantities[Resource.FOOD]
    packed = party.provisions
    state, results = _run(state, 3)
    [held] = [journey for journey in state.journeys if journey.encamped]
    assert held.provisions == packed, "each day the occupiers take what they eat"
    # Villagers sent off to a nearby tile still eat from their settlement's store.
    residents = len(state.civilizations[rival].population.living_ids)
    occupiers = sum(
        state.civilizations[home].population.people[person_id].alive
        for person_id in held.traveller_ids
    )
    eaten = rival_food - state.civilizations[rival].inventory.quantities[Resource.FOOD]
    assert eaten == 3 * (residents + occupiers)
    validate_world(state)


def test_an_occupied_settlement_stops_anchoring_its_owner() -> None:
    state, home, rival, route = _world()
    state, _, _ = _occupied(state, home, rival, route)
    state, _ = _run(state, 10)
    holders = state.territory.held_by_tile().get(route[-1], {})
    assert holders.get(home, 0) > 0, "the occupiers hold the settlement's ground"
    assert state.territory.owner_of()[route[-1]] == rival, "the owner keeps the settlement"


def test_residents_of_an_occupied_settlement_cannot_be_sent_anywhere() -> None:
    state, home, rival, route = _world()
    state, _, _ = _occupied(state, home, rival, route, keep=6)
    residents = [
        person_id
        for person_id, person in state.civilizations[rival].population.people.items()
        if person.alive and person.location == route[-1]
    ]
    march = DirectOrder(
        command_id="march",
        kind=DirectOrderKind.SEND_WAR_PARTY,
        journey_id=EntityId("journey:out"),
        recipient_civilization_id=home,
        traveller_ids=tuple(sorted(residents))[:2],
        route=tuple(reversed(route)),
        war_objective=WarObjective.RAID,
    )
    assert "occupied" in _codes(state, rival, march)


def test_occupiers_burn_a_storehouse_and_its_room_is_lost() -> None:
    state, home, rival, route = _world()
    state, _, party = _occupied(state, home, rival, route)
    owner = state.civilizations[rival]
    granary = owner.storehouses[0]
    capacity = owner.inventory.capacity
    burn = DirectOrder(
        command_id="burn",
        kind=DirectOrderKind.BURN_STOREHOUSE,
        journey_id=party.journey_id,
        storehouse_id=granary.storehouse_id,
    )
    assert _codes(state, home, burn) == []
    state.day = 30
    state, results = _run(state, 1, {home: OneShotSovereign(burn)})

    [burned] = _events(results, "storehouse_burned")
    assert burned.payload["grade"] == StorehouseGrade.PIT.value
    owner = state.civilizations[rival]
    assert owner.inventory.capacity == capacity - 5_000 + 2_000
    assert owner.inventory.total_units <= owner.inventory.capacity
    [house] = [item for item in owner.storehouses if item.storehouse_id == granary.storehouse_id]
    assert house.grade is StorehouseGrade.PIT


def test_recalled_occupiers_go_home_with_what_they_took() -> None:
    state, home, rival, route = _world()
    state, _, party = _occupied(state, home, rival, route)
    lift = DirectOrder(
        command_id="lift", kind=DirectOrderKind.LIFT_SIEGE, journey_id=party.journey_id
    )
    storm = lift.model_copy(update={"kind": DirectOrderKind.STORM_SETTLEMENT})
    assert _codes(state, home, storm) == ["invalid_siege_order"], "occupiers already hold it"
    state.day = 30
    state, results = _run(state, 8, {home: OneShotSovereign(lift)})

    [ended] = _events(results, "occupation_ended")
    assert ended.payload["reason"] == OccupationEnd.RECALLED.value
    assert _events(results, "war_party_returned")
    validate_world(state)


def test_residents_rise_when_they_outnumber_the_occupiers_three_to_one() -> None:
    state, home, rival, route = _world()
    state, _, party = _occupied(state, home, rival, route, fighters=6, keep=3)
    people = state.civilizations[rival].population.people
    away = sorted(people)[3:]
    for person_id in away[:20]:
        people[person_id].location = route[-1]

    state, results = _run(state, 1)

    [rose] = _events(results, "residents_rose")
    assert rose.payload["occupiers"] == 6 and rose.payload["residents"] >= 18
    [rising] = [battle for battle in state.battles if battle.day == rose.day]
    assert rising.winner_id == rival, "twenty-one residents throw out six occupiers"
    [ended] = _events(results, "occupation_ended")
    assert ended.payload["reason"] == OccupationEnd.ROSE.value
    [routed] = [item for item in state.journeys if item.journey_id == party.journey_id]
    assert routed.outcome is JourneyOutcome.ROUTED and not routed.encamped
    validate_world(state)


def test_road_wreckers_pull_down_each_enemy_road_they_cross() -> None:
    state, home, rival, route = treaty_world(distance=6)
    # Influence takes a couple of weeks to settle into owned land.
    state, _ = _run(state, 20)
    owners = state.territory.owner_of()
    enemy_road = [tile for tile in route[1:-1] if owners.get(tile) == rival]
    assert enemy_road, "the rival holds some of the route"
    roads = {road.tile: road for road in state.roads}
    for tile in route[1:-1]:
        roads[tile] = Road(
            tile=tile, grade=RoadGrade.GRAVEL, civilization_id=rival, built_day=0, graded_day=0
        )
    state.roads = tuple(roads[tile] for tile in sorted(roads))
    _send_away(state, rival, 0, HexCoord(route[-1].q, route[-1].r - 3))
    raid = DirectOrder(
        command_id="wreck",
        kind=DirectOrderKind.SEND_WAR_PARTY,
        journey_id=EntityId(clear_journey_id("wreck", start_day=30, days=16)),
        recipient_civilization_id=rival,
        traveller_ids=state.civilizations[home].population.living_ids[:6],
        route=route,
        war_objective=WarObjective.ATTACK,
        wreck_roads=True,
        extra_provisions=40,
    )
    state.day = 30
    state, results = _run(state, 12, {home: OneShotSovereign(raid)})

    wrecked = _events(results, "road_wrecked")
    assert {HexCoord(event.payload["q"], event.payload["r"]) for event in wrecked} == set(
        enemy_road
    ), "only the enemy's roads, each once"
    grades = {road.tile: road.grade for road in state.roads}
    assert all(grades[tile] is RoadGrade.GRADED for tile in enemy_road), "gravel falls to graded"
    assert all(grades[tile] is RoadGrade.GRAVEL for tile in route[1:-1] if tile not in enemy_road)
    assert len({event.day for event in wrecked}) == len(enemy_road), "a day on each tile"


def test_an_owner_learns_its_settlement_is_held_only_once_it_is_in_sight() -> None:
    state, home, rival, _ = treaty_world(distance=4)
    capital = state.civilizations[rival].settlements[0]
    far = HexCoord(capital.tile.q + 6, capital.tile.r)
    colony = Settlement(
        settlement_id=EntityId("settlement:0000000002-0009"),
        civilization_id=rival,
        tile=far,
        founded_day=0,
    )
    state.civilizations[rival].settlements = (*state.civilizations[rival].settlements, colony)
    state.occupations = (
        Occupation(
            occupation_id=EntityId("occupation:far"),
            journey_id=EntityId("journey:far"),
            occupier_id=home,
            owner_id=rival,
            settlement_id=colony.settlement_id,
            tile=far,
            started_day=0,
        ),
    )
    _learn_by_sight(state)
    assert build_council_report(state, rival).occupations == ()
    assert len(build_council_report(state, home).occupations) == 1

    resident = state.civilizations[rival].population.living_ids[0]
    state.civilizations[rival].population.people[resident].location = far
    _learn_by_sight(state)
    [seen] = build_council_report(state, rival).occupations
    assert seen.owner_learned_day == state.day
