from logistics_helpers import OneShotSovereign, clear_journey_id, envelope, treaty_world
from test_peace import _make_peace

from sovereign_world.capabilities import CapabilityId
from sovereign_world.commands import DirectOrder, DirectOrderKind, validate_envelope
from sovereign_world.diplomacy import PeaceTerms, TreatyKind
from sovereign_world.engine import TransitionResult, _next_settlement_id, advance_day
from sovereign_world.ids import EntityId
from sovereign_world.people import SETTLING_DAYS
from sovereign_world.resources import Inventory, Resource
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState, validate_world
from sovereign_world.stores import Storehouse, StorehouseGrade
from sovereign_world.territory import Settlement
from sovereign_world.walls import WALL_GRADES, WallGrade, Walls
from sovereign_world.war import Drill


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


def _colony(state: WorldState, owner: EntityId, tile, *, people: int) -> Settlement:
    """A second settlement of the rival's, with residents, a store, a granary and walls."""
    civilization = state.civilizations[owner]
    colony = Settlement(
        settlement_id=_next_settlement_id(state, owner),
        civilization_id=owner,
        tile=tile,
        founded_day=0,
    )
    civilization.settlements = (*civilization.settlements, colony)
    for person_id in civilization.population.living_ids[-people:]:
        civilization.population.people[person_id].location = tile
    civilization.stores = {
        colony.settlement_id: Inventory(
            capacity=7_000, quantities={Resource.FOOD: 500, Resource.STONE: 40}
        )
    }
    civilization.storehouses = tuple(
        sorted(
            (
                *civilization.storehouses,
                Storehouse(
                    storehouse_id=EntityId(f"storehouse:{colony.settlement_id}:01"),
                    settlement_id=colony.settlement_id,
                    grade=StorehouseGrade.GRANARY,
                    built_day=0,
                ),
            ),
            key=lambda item: item.storehouse_id,
        )
    )
    civilization.walls = (
        Walls(
            settlement_id=colony.settlement_id,
            grade=WallGrade.PALISADE,
            strength=WALL_GRADES[WallGrade.PALISADE].strength,
            built_day=0,
        ),
    )
    return colony


def test_migrants_keep_their_lives_but_a_quarter_of_their_skill_waits_a_year() -> None:
    state, home, rival, route = treaty_world(TreatyKind.MIGRATION, distance=4)
    migrant = state.civilizations[home].population.living_ids[-1]
    person = state.civilizations[home].population.people[migrant]
    person.skills = {**person.skills, CapabilityId.TIMBERCRAFT.value: 80}
    order = DirectOrder(
        command_id="move",
        kind=DirectOrderKind.DISPATCH_MIGRATION,
        journey_id=EntityId(clear_journey_id("move", days=12)),
        treaty_id=EntityId("treaty:migration"),
        recipient_civilization_id=rival,
        traveller_ids=(migrant,),
        route=route,
    )
    state, _ = _run(state, 8, {home: OneShotSovereign(order)})

    arrived = state.civilizations[rival].population.people[migrant]
    [change] = arrived.allegiances
    assert (change.from_civilization_id, change.to_civilization_id, change.reason) == (
        home,
        rival,
        "migration",
    )
    assert arrived.skills[CapabilityId.TIMBERCRAFT.value] == 60
    assert arrived.held_skills[CapabilityId.TIMBERCRAFT.value] == 20
    assert arrived.settled_day == change.day + SETTLING_DAYS

    state.day = change.day + SETTLING_DAYS - 1
    state, _ = _run(state, 2)
    settled = state.civilizations[rival].population.people[migrant]
    assert settled.skills[CapabilityId.TIMBERCRAFT.value] == 80, "the year is up"
    assert settled.held_skills == {} and settled.settled_day is None


def test_a_capital_or_a_strangers_settlement_cannot_be_ceded() -> None:
    state, home, rival, route = treaty_world(distance=4)
    capital = state.civilizations[rival].settlements[0].settlement_id
    offer = DirectOrder(
        command_id="offer",
        kind=DirectOrderKind.OFFER_TREATY,
        treaty_id=EntityId("treaty:p"),
        treaty_kind=TreatyKind.PEACE,
        peace_terms=PeaceTerms(truce_days=60, ceded_settlement=capital),
        message_id=EntityId("message:m"),
        ambassador_id=state.civilizations[home].population.living_ids[0],
        recipient_civilization_id=rival,
        message_text="Peace.",
        route=route,
    )
    assert "invalid_treaty" in _codes(state, home, offer)
    colony = _colony(state, rival, route[2], people=4)
    fine = offer.model_copy(
        update={"peace_terms": PeaceTerms(truce_days=60, ceded_settlement=colony.settlement_id)}
    )
    assert _codes(state, home, fine) == []


def test_a_ceded_settlement_changes_hands_with_its_store_buildings_walls_and_people() -> None:
    state, home, rival, route = treaty_world(distance=6)
    colony = _colony(state, rival, route[3], people=6)
    residents = tuple(
        person_id
        for person_id, person in state.civilizations[rival].population.people.items()
        if person.location == colony.tile
    )
    drilling = state.civilizations[rival].population.living_ids[0]
    state.civilizations[rival].drills = (
        Drill(
            drill_id=EntityId("drill:colony"),
            person_ids=(residents[0], drilling),
            started_day=0,
            days=30,
        ),
    )
    smith = state.civilizations[rival].population.people[residents[0]]
    smith.skills = {**smith.skills, CapabilityId.SIEGECRAFT.value: 100}

    state, results = _make_peace(
        state, home, rival, PeaceTerms(truce_days=60, ceded_settlement=colony.settlement_id)
    )

    [ceded] = _events(results, "settlement_ceded")
    assert ceded.payload == {"to": str(home), "people": len(residents)}
    giver, taker = state.civilizations[rival], state.civilizations[home]
    assert colony.settlement_id not in {item.settlement_id for item in giver.settlements}
    [moved] = [item for item in taker.settlements if item.settlement_id == colony.settlement_id]
    assert moved.civilization_id == home and not moved.capital
    assert taker.stores[colony.settlement_id].quantities[Resource.STONE] == 40
    assert {item.settlement_id for item in taker.storehouses} >= {colony.settlement_id}
    assert [item.settlement_id for item in taker.walls] == [colony.settlement_id]
    assert giver.walls == () and colony.settlement_id not in giver.stores
    for person_id in residents:
        assert person_id in taker.population.people and person_id not in giver.population.people
        assert taker.population.people[person_id].allegiances[-1].reason == "cession"
    [drill] = giver.drills
    assert drill.person_ids == (drilling,), "the colonist left the rival's drill"
    assert any(item.capability is CapabilityId.SIEGECRAFT for item in taker.capabilities), (
        "the colonists brought their knowledge"
    )
    validate_world(state)

    state, _ = _run(state, 20)
    assert state.territory.owner_of()[colony.tile] == home, "the settlement anchors its new owner"
    assert _next_settlement_id(state, rival).endswith("-0003"), "no id is ever reused"
