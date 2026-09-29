from logistics_helpers import treaty_world

from sovereign_world.diplomacy import TreatyKind
from sovereign_world.engine import advance_day
from sovereign_world.hexmap import HexCoord
from sovereign_world.ids import EntityId
from sovereign_world.logistics import Journey, JourneyKind
from sovereign_world.people import (
    FED_HEALTH_GAIN_BP,
    FERTILE_HEALTH_BP,
    HUNGER_GRACE_DAYS,
    UNFED_HEALTH_LOSS_BP,
    Person,
    Sex,
    _eligible_pairs,
    _mortality_threshold,
    go_hungry,
    recover,
)
from sovereign_world.resources import Inventory, Resource
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState


def _person(**changes) -> Person:
    base = dict(
        person_id=EntityId("person:0000000001"),
        civilization_id=EntityId("civilization:0000000001"),
        sex=Sex.FEMALE,
        birth_day=-9_000,
        age_days=9_000,
        location=HexCoord(0, 0),
    )
    return Person(**{**base, **changes})


def _set_food(state: WorldState, civilization_id: EntityId, units: int) -> None:
    storehouse = state.civilizations[civilization_id].inventory
    state.civilizations[civilization_id].inventory = Inventory(
        capacity=storehouse.capacity,
        quantities={**storehouse.quantities, Resource.FOOD: units},
    )


def test_reserves_absorb_the_first_days_of_hunger() -> None:
    assert _mortality_threshold(_person(nutrition_debt=HUNGER_GRACE_DAYS))[0] == 0
    threshold, cause = _mortality_threshold(_person(nutrition_debt=HUNGER_GRACE_DAYS + 15))
    assert (threshold, cause) == (15_000, "malnutrition")


def test_hunger_wastes_quickly_and_heals_slowly() -> None:
    person = _person(health_bp=10_000)
    for _ in range(30):
        go_hungry(person)
    assert person.nutrition_debt == 30
    assert person.health_bp == 10_000 - 30 * UNFED_HEALTH_LOSS_BP

    debts = []
    for _ in range(5):
        recover(person)
        debts.append(person.nutrition_debt)
    assert debts == [15, 7, 3, 1, 0], "acute danger fades within days"
    assert person.health_bp == 7_000 + 5 * FED_HEALTH_GAIN_BP, "wasting rebuilds slowly"

    for _ in range(1_000):
        recover(person)
    assert person.health_bp == 10_000
    for _ in range(200):
        go_hungry(person)
    assert person.health_bp == 0


def test_half_rations_waste_the_body_without_acute_danger() -> None:
    person = _person(health_bp=10_000)
    for day in range(168):
        if day % 2:
            recover(person)
        else:
            go_hungry(person)
        assert _mortality_threshold(person)[0] == 0
    assert 0 < person.health_bp < FERTILE_HEALTH_BP, "wasted and infertile, but alive"


def test_the_wasted_cannot_conceive() -> None:
    father = _person(person_id=EntityId("person:0000000002"), sex=Sex.MALE)
    for health, expected in ((FERTILE_HEALTH_BP - 1, 0), (FERTILE_HEALTH_BP, 1)):
        mother = _person(health_bp=health)
        pairs = _eligible_pairs({mother.person_id: mother, father.person_id: father})
        assert len(pairs) == expected
    wasted_father = father.model_copy(update={"health_bp": FERTILE_HEALTH_BP - 1})
    mother = _person()
    assert _eligible_pairs({mother.person_id: mother, father.person_id: wasted_father}) == []


def test_scarce_food_is_shared_with_the_hungriest_first() -> None:
    state, civilization_id, _, _ = treaty_world(None)
    living = state.civilizations[civilization_id].population.living_ids
    rng = StableRng(state.config.seed)
    for _ in range(12):
        _set_food(state, civilization_id, len(living) // 2)
        state = advance_day(state, rng).state
        people = state.civilizations[civilization_id].population.people
        assert max(people[person_id].nutrition_debt for person_id in living) <= 1

    people = state.civilizations[civilization_id].population.people
    assert all(people[person_id].alive for person_id in living)
    wasting = {people[person_id].health_bp for person_id in living}
    assert max(wasting) < 10_000, "everyone shares the shortage"


def test_famine_survivors_recover_once_food_returns() -> None:
    state, civilization_id, _, _ = treaty_world(None)
    rng = StableRng(state.config.seed)
    for _ in range(20):
        _set_food(state, civilization_id, 0)
        state = advance_day(state, rng).state
    people = state.civilizations[civilization_id].population.people
    survivors = [person_id for person_id in people if people[person_id].alive]
    assert all(people[person_id].nutrition_debt >= 20 for person_id in survivors)
    health_after_famine = {person_id: people[person_id].health_bp for person_id in survivors}

    _set_food(state, civilization_id, 50_000)
    for _ in range(6):
        state = advance_day(state, rng).state

    people = state.civilizations[civilization_id].population.people
    still_alive = [person_id for person_id in survivors if people[person_id].alive]
    assert len(still_alive) >= len(survivors) - 2, "the danger passes once food returns"
    assert all(people[person_id].nutrition_debt == 0 for person_id in still_alive)
    assert all(
        people[person_id].health_bp == health_after_famine[person_id] + 6 * FED_HEALTH_GAIN_BP
        for person_id in still_alive
    )


def test_travellers_heal_when_fed_and_waste_when_foraging_fails() -> None:
    state, sender, recipient, route = treaty_world(TreatyKind.MIGRATION)
    migrants = state.civilizations[sender].population.living_ids[-2:]
    people = state.civilizations[sender].population.people
    for person_id in migrants:
        people[person_id].nutrition_debt = 8
        people[person_id].health_bp = 9_000
    state.journeys = (
        Journey(
            journey_id=EntityId("journey:recovering"),
            kind=JourneyKind.MIGRATION,
            treaty_id=EntityId("treaty:migration"),
            sender_civilization_id=sender,
            recipient_civilization_id=recipient,
            traveller_ids=migrants,
            route=route,
            provisions_packed=20,
            provisions=20,
            departed_day=0,
        ),
    )

    state = advance_day(state, StableRng(state.config.seed)).state

    people = state.civilizations[sender].population.people
    for person_id in migrants:
        assert people[person_id].nutrition_debt == 4
        assert people[person_id].health_bp == 9_000 + FED_HEALTH_GAIN_BP
