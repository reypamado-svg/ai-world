from dataclasses import replace

from logistics_helpers import (
    OneShotSovereign,
    clear_journey_id,
    envelope,
    river,
    treaty_world,
)

from sovereign_world.bridges import Bridge
from sovereign_world.commands import (
    DirectOrder,
    DirectOrderKind,
    build_council_report,
    validate_envelope,
)
from sovereign_world.diplomacy import TreatyEndKind, TreatyKind
from sovereign_world.engine import TransitionResult, _share_maps, _toll_rules, advance_day
from sovereign_world.exploration import Observation
from sovereign_world.hexmap import HexCoord, Terrain, edge_key
from sovereign_world.ids import EntityId
from sovereign_world.logistics import JourneyKind, JourneyOutcome, NoticeKind
from sovereign_world.resources import Resource
from sovereign_world.rng import StableRng
from sovereign_world.roads import Road, RoadGrade
from sovereign_world.state import WorldState, validate_world
from sovereign_world.territory import Garrison, HeldControl, Settlement, Territory, TileOwner
from sovereign_world.tolls import TollPost, cargo_charge
from sovereign_world.travel import DEEP_FLOW, entry_cost


def _world(kind: TreatyKind | None = TreatyKind.TRADE):
    state, home, rival, route = treaty_world(kind, distance=6)
    third = sorted(state.civilizations)[2]
    band = {
        tile.coord
        for tile in state.world_map.tiles
        if min(tile.coord.distance(step) for step in route) <= 1
    }
    state.world_map = replace(
        state.world_map,
        tiles=tuple(
            replace(tile, terrain=Terrain.GRASSLAND) if tile.coord in band else tile
            for tile in state.world_map.tiles
        ),
        rivers=tuple(
            edge for edge in state.world_map.rivers if edge.a not in band and edge.b not in band
        ),
    )
    civilization = state.civilizations[home]
    seen = {item.tile: item for item in civilization.observations}
    for tile in band:
        seen.setdefault(
            tile,
            Observation(
                tile=tile, observed_day=0, observer_id=civilization.population.living_ids[0]
            ),
        )
    civilization.observations = tuple(seen[tile] for tile in sorted(seen))
    civilization.known_tiles = tuple(sorted(seen))
    return state, home, rival, third, route


def _hold(state: WorldState, civilization_id: EntityId, *tiles: HexCoord) -> None:
    held = {(item.tile, item.civilization_id): item for item in state.territory.held}
    owners = {item.tile: item for item in state.territory.owners}
    for tile in tiles:
        held[(tile, civilization_id)] = HeldControl(
            tile=tile, civilization_id=civilization_id, value=90
        )
        owners[tile] = TileOwner(tile=tile, civilization_id=civilization_id, since_day=0)
    state.territory = Territory(
        held=tuple(held[key] for key in sorted(held, key=lambda key: (key[0], key[1]))),
        owners=tuple(owners[tile] for tile in sorted(owners)),
    )


def _road(state: WorldState, civilization_id: EntityId, *tiles: HexCoord) -> None:
    roads = {road.tile: road for road in state.roads}
    for tile in tiles:
        roads[tile] = Road(
            tile=tile,
            grade=RoadGrade.TRACK,
            civilization_id=civilization_id,
            built_day=0,
            graded_day=0,
        )
    state.roads = tuple(roads[tile] for tile in sorted(roads))


def _garrison(state: WorldState, civilization_id: EntityId, tile: HexCoord, size: int) -> None:
    civilization = state.civilizations[civilization_id]
    members = tuple(sorted(civilization.population.living_ids[-size:]))
    for person_id in members:
        civilization.population.people[person_id].location = tile
    civilization.garrisons = (
        *civilization.garrisons,
        Garrison(
            garrison_id=EntityId(f"garrison:{civilization_id}:{tile.q}"),
            civilization_id=civilization_id,
            tile=tile,
            member_ids=members,
            since_day=0,
        ),
    )


def _post(
    state: WorldState,
    civilization_id: EntityId,
    route: tuple[HexCoord, ...],
    *,
    rate: int = 0,
    food: int = 0,
    every: int = 30,
    chest: dict[Resource, int] | None = None,
) -> TollPost:
    post = TollPost(
        post_id=EntityId(f"toll:{civilization_id}:{route[0].q}"),
        civilization_id=civilization_id,
        tile=route[0],
        cargo_rate_bp=rate,
        food_per_head=food,
        deposit_every_days=every,
        deposit_route=route,
        set_day=0,
        last_deposit_day=0,
        chest=chest or {},
    )
    civilization = state.civilizations[civilization_id]
    civilization.toll_posts = tuple(
        sorted((*civilization.toll_posts, post), key=lambda item: item.tile)
    )
    return post


def _path_home(state: WorldState, owner: EntityId, start: HexCoord) -> tuple[HexCoord, ...]:
    """A passable path from a tile to the owner's capital, for a deposit route."""
    goal = state.civilizations[owner].start_center
    previous: dict[HexCoord, HexCoord | None] = {start: None}
    frontier = [start]
    while frontier:
        tile = frontier.pop(0)
        if tile == goal:
            break
        for neighbor in sorted(state.world_map.neighbors(tile)):
            if neighbor not in previous and entry_cost(state.world_map, neighbor) is not None:
                previous[neighbor] = tile
                frontier.append(neighbor)
    path = [goal]
    while (step := previous[path[-1]]) is not None:
        path.append(step)
    return tuple(reversed(path))


def _post_at_garrison(
    state: WorldState, owner: EntityId, tile: HexCoord, *, size: int = 2, **rates: int
) -> TollPost:
    """A garrisoned toll post on a held road tile, whose deposits are months away."""
    _hold(state, owner, tile)
    _road(state, owner, tile)
    _garrison(state, owner, tile, size)
    return _post(state, owner, _path_home(state, owner, tile), every=90, **rates)


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


def _shipment(state, home, rival, route, journey_id: str) -> DirectOrder:
    return DirectOrder(
        command_id=f"ship:{journey_id}",
        kind=DirectOrderKind.DISPATCH_SHIPMENT,
        journey_id=EntityId(journey_id),
        treaty_id=EntityId("treaty:trade"),
        recipient_civilization_id=rival,
        traveller_ids=state.civilizations[home].population.living_ids[:2],
        route=route,
        cargo={Resource.TIMBER: 40},
    )


def test_a_shipment_pays_a_share_of_its_cargo_once_into_the_post_chest() -> None:
    state, home, rival, third, route = _world()
    post = _post_at_garrison(state, third, route[3], rate=1_000)
    timber_before = state.civilizations[rival].inventory.quantities[Resource.TIMBER]
    order = _shipment(state, home, rival, route, clear_journey_id("toll", days=14))

    state, results = _run(state, 14, {home: OneShotSovereign(order)})

    [paid] = _events(results, "toll_paid")
    assert paid.payload["units"] == 4, "ten percent of forty timber, paid only on the way out"
    [collected] = _events(results, "toll_collected")
    assert collected.subject_id == str(post.post_id)
    [chest_post] = state.civilizations[third].toll_posts
    assert chest_post.chest == {Resource.TIMBER: 4}, "takings wait in the chest"
    received = state.civilizations[rival].inventory.quantities[Resource.TIMBER] - timber_before
    assert received == 36
    notices = {item.kind for item in state.civilizations[home].logistics_notices}
    assert NoticeKind.TOLL_PAID in notices
    assert NoticeKind.TOLL_COLLECTED in {
        item.kind for item in state.civilizations[third].logistics_notices
    }
    known = {view.tile: view for view in build_council_report(state, home).known_tolls}
    assert known[route[3]].cargo_rate_bp == 1_000, "the carriers saw the post"
    validate_world(state)


def test_trade_partners_pass_each_others_tolls_free() -> None:
    state, home, rival, _, route = _world()
    _post_at_garrison(state, rival, route[4], rate=2_000, food=2)
    order = _shipment(state, home, rival, route, clear_journey_id("free", days=14))

    state, results = _run(state, 14, {home: OneShotSovereign(order)})

    assert not _events(results, "toll_paid")
    assert _events(results, "shipment_received")[0].payload["units"] == 40
    known = {view.tile for view in build_council_report(state, home).known_tolls}
    assert route[4] in known, "passing free, the carriers still saw the post"


def test_migrants_pay_food_per_head_at_a_capital_gate_straight_into_its_store() -> None:
    state, home, rival, _, route = _world(TreatyKind.MIGRATION)
    _hold(state, rival, route[-1])
    _road(state, rival, route[-1])
    gate = _post(state, rival, (route[-1],), food=2)
    assert gate.at_storehouse
    order = DirectOrder(
        command_id="migrate",
        kind=DirectOrderKind.DISPATCH_MIGRATION,
        journey_id=EntityId(clear_journey_id("gate", days=8)),
        treaty_id=EntityId("treaty:migration"),
        recipient_civilization_id=rival,
        traveller_ids=state.civilizations[home].population.living_ids[-2:],
        route=route,
    )

    state, results = _run(state, 8, {home: OneShotSovereign(order)})

    [paid] = _events(results, "toll_paid")
    assert paid.payload["units"] == 4
    [post] = state.civilizations[rival].toll_posts
    assert post.chest == {}, "the capital's gate is its storehouse"
    collected = next(
        item
        for item in state.civilizations[rival].logistics_notices
        if item.kind is NoticeKind.TOLL_COLLECTED
    )
    assert collected.cargo == {Resource.FOOD: 4}
    assert _events(results, "migrants_received")


def _relocation_world():
    state, home, _, third, route = _world()
    colony = Settlement(
        settlement_id=EntityId(f"settlement:{home.rsplit(':', 1)[-1]}-0002"),
        civilization_id=home,
        tile=route[5],
        founded_day=0,
    )
    state.civilizations[home].settlements = (*state.civilizations[home].settlements, colony)
    _post_at_garrison(state, third, route[3], food=2)
    order = DirectOrder(
        command_id="move",
        kind=DirectOrderKind.RELOCATE_GROUP,
        journey_id=EntityId(clear_journey_id("move", days=12)),
        traveller_ids=state.civilizations[home].population.living_ids[-2:],
        route=route[:6],
    )
    state, _ = _run(state, 2, {home: OneShotSovereign(order)})
    [journey] = state.journeys
    assert journey.route_index == 2, "the party waits on the tile before the post"
    return state, home, route, journey


def _set_provisions(state: WorldState, units: int) -> None:
    [journey] = state.journeys
    state.journeys = (journey.model_copy(update={"provisions": units}),)


def test_a_party_that_cannot_afford_the_toll_goes_round_it() -> None:
    state, home, route, journey = _relocation_world()
    # Paying 4 would leave 5 for the 3 days (6 food) still ahead; going round takes 4 days.
    _set_provisions(state, 9)

    state, results = _run(state, 8)

    assert [event.kind for event in _events(results, "toll_avoided")] == ["toll_avoided"]
    assert not _events(results, "toll_paid")
    [journey] = state.journeys
    assert route[3] not in journey.route, "the new route skirts the post"
    assert journey.route[0] == route[0] and journey.route[-1] == route[5]
    steps = zip(journey.route, journey.route[1:], strict=False)
    assert all(first.distance(second) == 1 for first, second in steps)
    assert _events(results, "group_relocated")
    notices = {item.kind for item in state.civilizations[home].logistics_notices}
    assert NoticeKind.TOLL_AVOIDED in notices


def test_a_party_that_can_neither_pay_nor_go_round_turns_back() -> None:
    state, home, route, _ = _relocation_world()
    _set_provisions(state, 7)

    state, results = _run(state, 6)

    assert _events(results, "toll_refused")
    [journey] = state.journeys
    assert journey.outcome is JourneyOutcome.TURNED_BACK and not journey.active
    people = state.civilizations[home].population.people
    assert all(people[person_id].location == route[0] for person_id in journey.traveller_ids)


def test_couriers_carry_the_chest_home_at_the_deposit_interval() -> None:
    state, _, rival, _, route = _world()
    _hold(state, rival, route[4])
    _road(state, rival, route[4])
    _garrison(state, rival, route[4], 3)
    _post(state, rival, route[4:], rate=500, every=10, chest={Resource.TIMBER: 60})
    before = state.civilizations[rival].inventory.quantities[Resource.TIMBER]

    state, results = _run(state, 10)
    assert not _events(results, "toll_deposit_dispatched"), "not due until day 10"

    state, more = _run(state, 1)
    [dispatched] = _events(more, "toll_deposit_dispatched")
    assert dispatched.payload["couriers"] == 2, "one collector always stays behind"
    [post] = state.civilizations[rival].toll_posts
    assert post.chest == {} and post.last_deposit_day == 10
    assert state.civilizations[rival].inventory.quantities[Resource.TIMBER] == before

    state, later = _run(state, 12)
    [deposited] = _events(later, "toll_deposited")
    assert deposited.payload["units"] == 60
    assert state.civilizations[rival].inventory.quantities[Resource.TIMBER] == before + 60
    assert _events(later, "toll_couriers_returned")
    [journey] = [item for item in state.journeys if item.kind is JourneyKind.DEPOSIT]
    people = state.civilizations[rival].population.people
    assert all(people[person_id].location == route[4] for person_id in journey.traveller_ids)
    validate_world(state)


def test_a_deposit_with_no_spare_collector_is_skipped_and_reported() -> None:
    state, _, rival, _, route = _world()
    _hold(state, rival, route[4])
    _road(state, rival, route[4])
    _garrison(state, rival, route[4], 1)
    _post(state, rival, route[4:], rate=500, every=10, chest={Resource.TIMBER: 5})

    state, results = _run(state, 11)

    [skipped] = _events(results, "toll_deposit_skipped")
    assert skipped.payload["cause"] == "no spare collector"
    [post] = state.civilizations[rival].toll_posts
    assert post.chest == {Resource.TIMBER: 5}
    assert NoticeKind.TOLL_DEPOSIT_SKIPPED in {
        item.kind for item in state.civilizations[rival].logistics_notices
    }


def test_a_toll_lapses_without_collectors_and_charges_nothing() -> None:
    state, _, _, third, route = _world()
    _post_at_garrison(state, third, route[3], rate=1_000)
    assert route[3] in _toll_rules(state).gates
    for garrison in state.civilizations[third].garrisons:
        for person_id in garrison.member_ids:
            person = state.civilizations[third].population.people[person_id]
            person.alive = False
            person.death_day = 0

    state, results = _run(state, 1)

    assert _events(results, "toll_lapsed")
    [post] = state.civilizations[third].toll_posts
    assert not post.collecting
    assert route[3] not in _toll_rules(state).gates


def test_set_toll_needs_a_held_staffed_road_tile() -> None:
    state, home, _, _, route = _world()
    capital = route[0]
    _hold(state, home, capital)

    def codes(order: DirectOrder) -> list[str]:
        errors = validate_envelope(envelope(state, home, order), state).errors
        return [error.code for error in errors]

    order = DirectOrder(
        command_id="toll",
        kind=DirectOrderKind.SET_TOLL,
        route=(capital,),
        toll_rate_bp=500,
        toll_food_per_head=1,
    )
    assert codes(order) == ["invalid_toll"], "no road at the gate yet"
    _road(state, home, capital)
    assert codes(order) == []
    lift = order.model_copy(update={"toll_rate_bp": 0, "toll_food_per_head": 0})
    assert codes(lift) == ["invalid_toll"], "nothing to lift"
    stray = order.model_copy(update={"route": (route[1],)})
    assert codes(stray) == ["invalid_toll"], "the tile is not held"

    state, results = _run(state, 1, {home: OneShotSovereign(order)})
    assert _events(results, "toll_set")
    [post] = state.civilizations[home].toll_posts
    assert post.at_storehouse and post.cargo_rate_bp == 500
    assert build_council_report(state, home).toll_posts == (post,)


def test_trade_partners_may_build_roads_on_each_others_land() -> None:
    state, home, rival, _, route = _world()
    civilization = state.civilizations[home]
    civilization.observations = tuple(
        item.model_copy(update={"observed_owner": rival}) if item.tile == route[1] else item
        for item in civilization.observations
    )
    _hold(state, rival, route[1])
    order = DirectOrder(
        command_id="road",
        kind=DirectOrderKind.BUILD_ROAD,
        journey_id=EntityId(clear_journey_id("partner", days=10)),
        traveller_ids=civilization.population.living_ids[-8:],
        route=route[:2],
        road_grade=RoadGrade.FOOTPATH,
    )
    assert validate_envelope(envelope(state, home, order), state).errors == ()

    ended = state.active_treaties[0].ended(0, TreatyEndKind.BREACHED, rival)
    unbound = state.model_copy(deep=True)
    unbound.active_treaties = (ended,)
    errors = validate_envelope(envelope(unbound, home, order), unbound).errors
    assert [error.code for error in errors] == ["foreign_land"]

    state, results = _run(state, 8, {home: OneShotSovereign(order)})
    built = {(event.payload["q"], event.payload["r"]) for event in _events(results, "road_built")}
    assert (route[1].q, route[1].r) in built, "the crew built on the partner's land"


def test_trade_partners_share_road_maps_and_note_when_their_roads_join() -> None:
    state, home, rival, _, route = _world()
    _road(state, home, *route[:4])
    _road(state, rival, *route[4:])
    state.civilizations[home].observations = tuple(
        item.model_copy(update={"observed_road": RoadGrade.TRACK})
        if item.tile in route[:4]
        else item
        for item in state.civilizations[home].observations
    )
    _share_maps(state, state.active_treaties[0])
    shown = {view.tile for view in state.civilizations[rival].road_intel}
    assert set(route[:4]) <= shown, "the partner learns the other's roads"
    known = {view.tile for view in build_council_report(state, rival).known_roads}
    assert set(route[:4]) <= known

    state, results = _run(state, 2)
    [joined] = _events(results, "roads_joined")
    assert joined.payload["road_tiles"] == len(route)
    assert state.joined_roads == (state.active_treaties[0].treaty_id,)


def test_cargo_tolls_never_take_all_of_anything() -> None:
    assert cargo_charge({Resource.STONE: 1, Resource.TIMBER: 2}, 2_000) == {Resource.TIMBER: 1}
    assert cargo_charge({Resource.STONE: 100}, 2_000) == {Resource.STONE: 20}
    assert cargo_charge({Resource.STONE: 100}, 0) == {}


def _moat(state: WorldState, route: tuple[HexCoord, ...]) -> tuple[HexCoord, HexCoord]:
    """Deep rivers sealing both lanes round the post at route[3]; returns one moat border."""
    q, r = route[2].q, route[2].r
    up, down = HexCoord(q + 1, r - 1), HexCoord(q, r + 1)
    for outside, inside in (
        (route[2], up),
        (HexCoord(q, r - 1), up),
        (route[2], down),
        (HexCoord(q - 1, r + 1), down),
    ):
        river(state, outside, inside, DEEP_FLOW)
    return edge_key(route[2], up)


def test_a_way_round_uses_only_bridges_the_party_knows_of() -> None:
    for builder_is_home, avoided in ((False, False), (True, True)):
        state, home, route, _ = _relocation_world()
        third = sorted(state.civilizations)[2]
        a, b = _moat(state, route)
        builder = home if builder_is_home else third
        state.bridges = (Bridge(a=a, b=b, civilization_id=builder, built_day=0),)
        _set_provisions(state, 9)

        state, results = _run(state, 8)

        # A rival's bridge out of sight is unknown, so it cannot be planned over.
        assert bool(_events(results, "toll_avoided")) is avoided, builder
        [journey] = state.journeys
        if not avoided:
            assert journey.outcome is JourneyOutcome.TURNED_BACK


def test_roads_join_across_a_deep_river_only_by_a_bridge() -> None:
    state, home, rival, _, route = _world()
    _road(state, home, *route[:4])
    _road(state, rival, *route[4:])
    river(state, route[3], route[4], DEEP_FLOW)

    state, results = _run(state, 2)
    assert not _events(results, "roads_joined"), "nobody can cross between the two roads"
    assert state.joined_roads == ()

    a, b = edge_key(route[3], route[4])
    state.bridges = (Bridge(a=a, b=b, civilization_id=home, built_day=state.day),)
    state, results = _run(state, 1)
    assert _events(results, "roads_joined")
