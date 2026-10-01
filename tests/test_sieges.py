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
from sovereign_world.logistics import JourneyOutcome, JourneyPhase
from sovereign_world.resources import Resource
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState, validate_world
from sovereign_world.territory import Settlement
from sovereign_world.walls import WALL_GRADES, WallGrade, Walls
from sovereign_world.war import Siege, SiegeEnd, WarObjective


def _world():
    state, home, rival, route = treaty_world(distance=4)
    return state, home, rival, route


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


def _stock(state: WorldState, civilization_id: EntityId, **goods: int) -> None:
    civilization = state.civilizations[civilization_id]
    quantities = dict(civilization.inventory.quantities)
    for name, quantity in goods.items():
        quantities[Resource(name)] = quantities.get(Resource(name), 0) + quantity
    civilization.inventory = civilization.inventory.model_copy(update={"quantities": quantities})


def _walls(state, civilization_id, grade, strength=None) -> None:
    civilization = state.civilizations[civilization_id]
    civilization.walls = (
        Walls(
            settlement_id=civilization.settlements[0].settlement_id,
            grade=grade,
            strength=WALL_GRADES[grade].strength if strength is None else strength,
            built_day=0,
        ),
    )


def _besiege(
    state, home, rival, route, *, fighters=8, provisions=200, cargo=None, command_id="siege"
) -> DirectOrder:
    return DirectOrder(
        command_id=command_id,
        kind=DirectOrderKind.SEND_WAR_PARTY,
        journey_id=EntityId(clear_journey_id("siege", days=12)),
        recipient_civilization_id=rival,
        traveller_ids=state.civilizations[home].population.living_ids[:fighters],
        route=route[:-1],
        cargo=cargo or {},
        war_objective=WarObjective.BESIEGE,
        extra_provisions=provisions,
    )


def _encamped(state, home, rival, route, **kwargs):
    order = _besiege(state, home, rival, route, **kwargs)
    assert _codes(state, home, order) == []
    sovereigns = {home: OneShotSovereign(order)}
    results: list[TransitionResult] = []
    while not any(journey.encamped for journey in state.journeys):
        assert state.day < 10, "the besiegers never made camp"
        state, more = _run(state, 1, sovereigns)
        results.extend(more)
    [camp] = [journey for journey in state.journeys if journey.encamped]
    return state, results, camp


def test_a_siege_camp_stands_beside_a_known_settlement() -> None:
    state, home, rival, route = _world()
    order = _besiege(state, home, rival, route)
    assert _codes(state, home, order) == []
    inside = order.model_copy(update={"route": route})
    assert _codes(state, home, inside) == ["invalid_route"], "a camp is not in the settlement"
    far = order.model_copy(update={"route": route[:-2]})
    assert _codes(state, home, far) == ["invalid_route"], "a camp is next to the settlement"


def test_besiegers_camp_start_the_war_and_both_sides_see_the_siege() -> None:
    state, home, rival, route = _world()
    state, results, camp = _encamped(state, home, rival, route)

    assert _events(results, "siege_began")
    assert _events(results, "undeclared_attack"), "a siege is a hostile act"
    [siege] = state.sieges
    assert siege.active and siege.camp == route[-2] and siege.settlement_tile == route[-1]
    assert camp.route_index == len(camp.route) - 1 and camp.phase is JourneyPhase.OUTBOUND
    assert build_council_report(state, rival).sieges == (siege,)
    assert build_council_report(state, home).sieges == (siege,)

    state, _ = _run(state, 5)
    [camp] = [journey for journey in state.journeys if journey.encamped]
    assert camp.route_index == len(camp.route) - 1, "the camp stays where it is"
    assert state.territory.cut_off and siege.settlement_id in state.territory.cut_off
    validate_world(state)


def test_a_blockade_stops_journeys_and_farming_but_not_a_sally() -> None:
    state, home, rival, route = _world()
    state.active_decrees[rival] = {"labor_priority": 80, "food_reserve_target": 2_000}
    state, results, _ = _encamped(state, home, rival, route)
    assert _events(results, "food_produced"), "the fields were worked before the siege"

    state, results = _run(state, 3)
    assert not [
        event for event in _events(results, "food_produced") if event.actor_id == str(rival)
    ], "under siege the fields lie outside the walls"
    defenders = state.civilizations[rival].population.living_ids
    back = tuple(reversed(route))
    march = DirectOrder(
        command_id="march",
        kind=DirectOrderKind.SEND_WAR_PARTY,
        journey_id=EntityId("journey:out"),
        recipient_civilization_id=home,
        traveller_ids=defenders[:6],
        route=back,
        war_objective=WarObjective.RAID,
    )
    assert "blockaded" in _codes(state, rival, march), "no raid leaves a besieged settlement"
    sally = march.model_copy(update={"route": back[:2], "war_objective": WarObjective.ATTACK})
    assert _codes(state, rival, sally) == [], "a sally against the camp may leave"


def test_catapults_batter_the_walls_until_they_fall_a_grade() -> None:
    state, home, rival, route = _world()
    _stock(state, home, catapult=1)
    _walls(state, rival, WallGrade.PALISADE, strength=10)
    state, _, _ = _encamped(state, home, rival, route, cargo={Resource.CATAPULT: 1})

    results: list[TransitionResult] = []
    while not _events(results, "walls_fell"):
        assert len(results) < 30, "the walls never fell"
        state, more = _run(state, 1)
        results.extend(more)

    [damaged] = _events(results, "walls_damaged")
    assert damaged.payload == {"grade": "palisade", "strength": 5}
    [fell] = _events(results, "walls_fell")
    assert fell.payload == {"grade": "earthwork", "strength": 10}
    [walls] = state.civilizations[rival].walls
    assert walls.grade is WallGrade.EARTHWORK, "the grade below stands at full strength"
    assert walls.strength == WALL_GRADES[WallGrade.EARTHWORK].strength


def test_walls_are_repaired_to_full_strength() -> None:
    state, home, _, _ = _world()
    _walls(state, home, WallGrade.EARTHWORK, strength=3)
    builders = state.civilizations[home].population.living_ids[:2]
    repair = DirectOrder(
        command_id="repair",
        kind=DirectOrderKind.REPAIR_WALLS,
        worker_ids=builders,
    )
    assert _codes(state, home, repair) == []
    state, results = _run(state, 5, {home: OneShotSovereign(repair)})
    [repaired] = _events(results, "walls_repaired")
    assert repaired.day == 3, "a quarter of 30 person-days, rounded up, is 8: four days by two"
    assert state.civilizations[home].walls[0].strength == WALL_GRADES[WallGrade.EARTHWORK].strength
    assert _codes(state, home, repair) == ["invalid_walls"], "sound walls need no repair"


def test_a_recalled_siege_is_lifted_and_the_camp_goes_home() -> None:
    state, home, rival, route = _world()
    state, _, camp = _encamped(state, home, rival, route)
    lift = DirectOrder(
        command_id="lift", kind=DirectOrderKind.LIFT_SIEGE, journey_id=camp.journey_id
    )
    assert _codes(state, home, lift, lift.model_copy(update={"command_id": "again"})) == [
        "invalid_siege_order"
    ], "one order per camp"
    state.day = 30
    state, results = _run(state, 6, {home: OneShotSovereign(lift)})

    [lifted] = _events(results, "siege_lifted")
    assert lifted.payload["reason"] == SiegeEnd.RECALLED.value
    [siege] = state.sieges
    assert not siege.active and siege.end is SiegeEnd.RECALLED
    assert _events(results, "war_party_returned")
    validate_world(state)


def test_storming_sends_the_camp_into_the_settlement_to_fight() -> None:
    state, home, rival, route = _world()
    _stock(state, home, axe=8)
    state, _, camp = _encamped(state, home, rival, route, fighters=16, cargo={Resource.AXE: 8})
    storm = DirectOrder(
        command_id="storm",
        kind=DirectOrderKind.STORM_SETTLEMENT,
        journey_id=camp.journey_id,
        war_objective=WarObjective.RAID,
    )
    state.day = 30
    state, results = _run(state, 3, {home: OneShotSovereign(storm)})

    [lifted] = _events(results, "siege_lifted")
    assert lifted.payload["reason"] == SiegeEnd.STORMED.value
    [battle] = [item for item in state.battles]
    assert battle.tile == route[-1], "the storm is fought in the settlement"


def test_a_hungry_or_thinned_camp_goes_home() -> None:
    state, home, rival, route = _world()
    state, results, _ = _encamped(state, home, rival, route, provisions=0)
    state, results = _run(state, 4)
    [lifted] = _events(results, "siege_lifted")
    assert lifted.payload["reason"] == SiegeEnd.STARVED.value

    state, home, rival, route = _world()
    state, _, camp = _encamped(state, home, rival, route, fighters=5, provisions=80)
    people = state.civilizations[home].population.people
    for person_id in camp.traveller_ids[:2]:
        people[person_id].alive = False
        people[person_id].death_day = state.day
    state, results = _run(state, 1)
    [lifted] = _events(results, "siege_lifted")
    assert lifted.payload["reason"] == SiegeEnd.TOO_FEW.value


def test_relief_breaks_the_camp() -> None:
    state, home, rival, route = _world()
    state, _, camp = _encamped(state, home, rival, route, fighters=4, provisions=60)
    defenders = state.civilizations[rival].population.living_ids
    relief = DirectOrder(
        command_id="relief",
        kind=DirectOrderKind.SEND_WAR_PARTY,
        journey_id=EntityId(clear_journey_id("relief", start_day=30, days=12)),
        recipient_civilization_id=home,
        traveller_ids=defenders[:20],
        route=tuple(reversed(route))[:2],
        war_objective=WarObjective.ATTACK,
    )
    assert _codes(state, rival, relief) == []
    state.day = 30
    state, results = _run(state, 3, {rival: OneShotSovereign(relief)})

    [battle] = state.battles
    assert battle.tile == camp.route[-1] and battle.winner_id == rival
    [lifted] = _events(results, "siege_lifted")
    assert lifted.payload["reason"] == SiegeEnd.BROKEN.value
    [broken] = [item for item in state.journeys if item.journey_id == camp.journey_id]
    assert broken.outcome is JourneyOutcome.ROUTED and not broken.encamped
    validate_world(state)


def test_a_defender_learns_of_a_siege_only_once_the_camp_is_in_sight() -> None:
    state, home, rival, _ = _world()
    capital = state.civilizations[rival].settlements[0]
    far = HexCoord(capital.tile.q + 6, capital.tile.r)
    colony = Settlement(
        settlement_id=EntityId("settlement:0000000002-0009"),
        civilization_id=rival,
        tile=far,
        founded_day=0,
    )
    state.civilizations[rival].settlements = (*state.civilizations[rival].settlements, colony)
    siege = Siege(
        siege_id=EntityId("siege:far"),
        journey_id=EntityId("journey:far"),
        besieger_id=home,
        defender_id=rival,
        settlement_id=colony.settlement_id,
        settlement_tile=far,
        camp=HexCoord(far.q + 1, far.r),
        started_day=0,
    )
    state.sieges = (siege,)
    _learn_by_sight(state)
    assert build_council_report(state, rival).sieges == (), "no one is there to see it"
    assert build_council_report(state, home).sieges == state.sieges

    resident = state.civilizations[rival].population.living_ids[0]
    state.civilizations[rival].population.people[resident].location = far
    _learn_by_sight(state)
    [seen] = build_council_report(state, rival).sieges
    assert seen.defender_learned_day == state.day
