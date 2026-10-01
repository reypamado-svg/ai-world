from logistics_helpers import OneShotSovereign, clear_journey_id, envelope, treaty_world

from sovereign_world.commands import (
    CommandEnvelope,
    CouncilReport,
    DirectOrder,
    DirectOrderKind,
    build_council_report,
    validate_envelope,
)
from sovereign_world.endings import BREAKUP_GRACE_DAYS, EndingKind
from sovereign_world.engine import TransitionResult, advance_day
from sovereign_world.hexmap import HexCoord
from sovereign_world.ids import EntityId
from sovereign_world.resources import Resource
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState, validate_world
from sovereign_world.walls import WALL_GRADES, WallGrade, Walls
from sovereign_world.war import WarObjective


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


def _kill(state: WorldState, civilization_id: EntityId) -> None:
    for person in state.civilizations[civilization_id].population.people.values():
        if person.alive:
            person.alive = False
            person.death_day = state.day
            person.health_bp = 0
    state.civilizations[civilization_id].population = state.civilizations[
        civilization_id
    ].population.model_copy(update={"scheduled_births": ()})


def _field(state: WorldState, civilization_id: EntityId) -> HexCoord:
    capital = state.civilizations[civilization_id].settlements[0].tile
    return next(
        tile
        for tile in sorted(state.world_map.neighbors(capital))
        if state.world_map.tile(tile).terrain.value != "water"
    )


class Welcoming:
    """Take in anyone who asks."""

    def decide(self, report: CouncilReport) -> CommandEnvelope:
        return CommandEnvelope(
            schema_version=1,
            civilization_id=report.civilization_id,
            council_day=report.day,
            correlation_id=report.report_id,
            commands=tuple(
                DirectOrder(
                    command_id=f"admit:{index}",
                    kind=DirectOrderKind.ANSWER_PETITION,
                    journey_id=journey.journey_id,
                    admit=True,
                )
                for index, journey in enumerate(report.petitions)
            ),
        )


def test_a_civilization_with_no_one_at_home_is_homeless_until_someone_returns() -> None:
    state, _, rival, _ = treaty_world(distance=4)
    field = _field(state, rival)
    people = state.civilizations[rival].population.people
    for person in people.values():
        person.location = field
    state, results = _run(state, 1)
    assert _events(results, "civilization_homeless")
    assert state.civilizations[rival].homeless_since == 0

    capital = state.civilizations[rival].settlements[0].tile
    first = state.civilizations[rival].population.living_ids[0]
    state.civilizations[rival].population.people[first].location = capital
    state, results = _run(state, 1)
    assert _events(results, "civilization_recovered")
    assert state.civilizations[rival].homeless_since is None


def test_a_homeless_civilization_breaks_up_toward_the_nearest_it_knows() -> None:
    state, home, rival, _ = treaty_world(distance=4)
    field = _field(state, rival)
    for person in state.civilizations[rival].population.people.values():
        person.location = field
    state.civilizations[rival].homeless_since = 0
    state.day = BREAKUP_GRACE_DAYS - BREAKUP_GRACE_DAYS % 30 + 30
    living = len(state.civilizations[rival].population.living_ids)

    state, results = _run(state, 12, {home: Welcoming()})
    drifted = _events(results, "people_drifted")
    assert sum(event.payload["people"] for event in drifted) == living // 4
    assert all(event.payload["to"] == str(home) for event in drifted)

    state.day = 30 * (state.day // 30 + 1)
    state, results = _run(state, 1, {home: Welcoming()})
    [admitted] = _events(results, "petition_admitted")
    assert admitted.payload["people"] == living // 4
    assert len(state.civilizations[rival].population.living_ids) == living - living // 4
    validate_world(state)


def test_a_civilization_with_no_one_left_is_eliminated_and_leaves_ruins() -> None:
    state, home, rival, route = treaty_world(distance=4)
    rival_civilization = state.civilizations[rival]
    capital = rival_civilization.settlements[0]
    rival_civilization.walls = (
        Walls(
            settlement_id=capital.settlement_id,
            grade=WallGrade.PALISADE,
            strength=WALL_GRADES[WallGrade.PALISADE].strength,
            built_day=0,
        ),
    )
    stone = rival_civilization.inventory.quantities[Resource.STONE]
    _kill(state, rival)

    state, results = _run(state, 1)

    assert _events(results, "civilization_eliminated")
    gone = state.civilizations[rival]
    assert gone.eliminated_day == 0 and gone.settlements == ()
    [ruin] = state.ruins
    assert ruin.tile == capital.tile and ruin.former_civilization_id == rival
    assert ruin.store.quantities[Resource.STONE] == stone
    assert ruin.walls is not None and ruin.walls.grade is WallGrade.PALISADE
    assert len(ruin.storehouses) == 5
    assert not any(treaty.in_force for treaty in state.active_treaties)
    assert capital.tile not in state.territory.owner_of()
    assert build_council_report(state, home).ruins == (ruin,)
    raid = DirectOrder(
        command_id="raid",
        kind=DirectOrderKind.SEND_WAR_PARTY,
        journey_id=EntityId("journey:raid"),
        recipient_civilization_id=rival,
        traveller_ids=state.civilizations[home].population.living_ids[:4],
        route=route,
        war_objective=WarObjective.RAID,
    )
    assert "eliminated" in _codes(state, home, raid)
    validate_world(state)


def test_salvagers_bring_home_what_they_can_carry_from_a_ruin() -> None:
    state, home, rival, route = treaty_world(distance=4)
    _kill(state, rival)
    state, _ = _run(state, 1)
    [ruin] = state.ruins
    salvage = DirectOrder(
        command_id="salvage",
        kind=DirectOrderKind.SALVAGE,
        journey_id=EntityId(clear_journey_id("salvage", start_day=30, days=12)),
        traveller_ids=state.civilizations[home].population.living_ids[:4],
        route=route,
    )
    assert _codes(state, home, salvage) == []
    assert _codes(state, home, salvage.model_copy(update={"route": route[:-1]})) == [
        "invalid_destination"
    ]
    food = state.civilizations[home].inventory.quantities[Resource.FOOD]
    state.day = 30
    state, results = _run(state, 10, {home: OneShotSovereign(salvage)})

    [salvaged] = _events(results, "ruin_salvaged")
    assert 0 < salvaged.payload["units"] <= 4 * 50
    assert _events(results, "salvagers_returned")
    [after] = state.ruins
    assert after.store.total_units == ruin.store.total_units - salvaged.payload["units"]
    assert state.civilizations[home].inventory.quantities[Resource.FOOD] > food - 10 * 40


def test_settlers_on_a_ruin_take_over_its_store_storehouses_and_walls() -> None:
    state, home, rival, route = treaty_world(distance=6)
    rival_civilization = state.civilizations[rival]
    capital = rival_civilization.settlements[0]
    rival_civilization.walls = (
        Walls(
            settlement_id=capital.settlement_id,
            grade=WallGrade.EARTHWORK,
            strength=WALL_GRADES[WallGrade.EARTHWORK].strength,
            built_day=0,
        ),
    )
    _kill(state, rival)
    state, _ = _run(state, 1)
    [ruin] = state.ruins
    found = DirectOrder(
        command_id="found",
        kind=DirectOrderKind.FOUND_SETTLEMENT,
        journey_id=EntityId(clear_journey_id("found", start_day=30, days=12)),
        traveller_ids=state.civilizations[home].population.living_ids[-4:],
        route=route,
    )
    state.civilizations[home].contacts = ()
    assert _codes(state, home, found) == []
    state.day = 30
    state, results = _run(state, 10, {home: OneShotSovereign(found)})

    assert _events(results, "settlement_founded")
    assert state.ruins == ()
    colony = next(item for item in state.civilizations[home].settlements if item.tile == ruin.tile)
    taker = state.civilizations[home]
    assert taker.stores[colony.settlement_id].capacity == ruin.store.capacity
    assert {item.settlement_id for item in taker.storehouses} >= {colony.settlement_id}
    assert [item.settlement_id for item in taker.walls] == [colony.settlement_id]
    validate_world(state)


def test_the_last_civilization_and_then_none_are_each_recorded_once() -> None:
    state, home, _, _ = treaty_world(distance=4)
    others = [item for item in sorted(state.civilizations) if item != home]
    for civilization_id in others:
        _kill(state, civilization_id)
    state, results = _run(state, 3)

    [last] = _events(results, "last_civilization")
    assert last.actor_id == str(home)
    [ending] = state.endings
    assert ending.kind is EndingKind.LAST_CIVILIZATION and ending.survivor_id == home
    assert ending.population == len(state.civilizations[home].population.living_ids)

    _kill(state, home)
    state, results = _run(state, 3)
    assert [item.kind for item in state.endings] == [
        EndingKind.LAST_CIVILIZATION,
        EndingKind.NO_CIVILIZATION,
    ]
    assert len(_events(results, "no_civilization")) == 1, "the world keeps turning, once"
    validate_world(state)
