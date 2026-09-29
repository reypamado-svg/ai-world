from logistics_helpers import OneShotSovereign, clear_journey_id, envelope, treaty_world

from sovereign_world.armoury import (
    KITS,
    Kit,
    UnitType,
    crewed_engines,
    kit_assignment,
    settlement_bonus_after_engines,
)
from sovereign_world.commands import DirectOrder, DirectOrderKind, validate_envelope
from sovereign_world.engine import TransitionResult, advance_day
from sovereign_world.hexmap import HexCoord
from sovereign_world.ids import EntityId
from sovereign_world.logistics import JourneyKind, JourneyOutcome, journey_days
from sovereign_world.resources import Resource
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState, validate_world
from sovereign_world.war import (
    SETTLEMENT_DEFENCE_BP,
    Fighter,
    WarObjective,
    fighting_strength,
    resolve_battle,
)


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


def _stock(state: WorldState, civilization_id: EntityId, **items: int) -> None:
    civilization = state.civilizations[civilization_id]
    quantities = dict(civilization.inventory.quantities)
    quantities.update({Resource(name): count for name, count in items.items()})
    civilization.inventory = civilization.inventory.model_copy(update={"quantities": quantities})


def _levies(prefix: str, count: int, *, volley_bp: int = 0) -> list[Fighter]:
    return [
        Fighter(
            person_id=EntityId(f"person:{prefix}{index:03d}"),
            civilization_id=EntityId(f"civilization:{prefix}"),
            strength=100,
            health_bp=10_000,
            veteran=False,
            hungry=False,
            volley_bp=volley_bp,
        )
        for index in range(count)
    ]


def _battle(attackers, defenders, *, catapults: int = 0):
    return resolve_battle(
        attackers,
        defenders,
        defence_bp=10_000,
        attacker_morale_bp=2_000,
        defender_morale_bp=3_000,
        rng=StableRng(3),
        stream="test:armoury",
        catapults=catapults,
    )


def test_each_kit_makes_a_unit_with_its_own_strengths() -> None:
    state, home, _, _ = _world()
    person = next(iter(state.civilizations[home].population.people.values())).model_copy(
        deep=True
    )
    person.health_bp = 10_000
    person.age_days = 30 * 365

    def strength(resource: Resource, *, attacking: bool) -> int:
        return fighting_strength(person, KITS[resource], attacking=attacking)

    assert fighting_strength(person) == 100, "a levy"
    assert strength(Resource.SLING, attacking=True) == 90, "slingers are weak hand to hand"
    assert strength(Resource.SPEAR, attacking=True) == 140
    assert strength(Resource.SPEAR, attacking=False) == 175, "spears hold a line"
    assert strength(Resource.AXE, attacking=True) == 165, "axes favour attack"
    assert strength(Resource.BOW, attacking=True) == 90
    assert strength(Resource.BRONZE_ARMS, attacking=True) == 180
    assert fighting_strength(person, KITS[Resource.AXE], crewing=True) == 82, (
        "crews fight at half strength"
    )
    assert {kit.unit for kit in KITS.values()} == set(UnitType) - {UnitType.LEVY}


def test_the_best_kits_go_first_and_the_rest_fight_as_levies() -> None:
    fighters = [EntityId(f"person:{index}") for index in range(5)]
    issued = kit_assignment(fighters, {Resource.SLING: 1, Resource.AXE: 1, Resource.BRONZE_ARMS: 1})
    assert [issued[person_id].resource for person_id in fighters[:3]] == [
        Resource.BRONZE_ARMS,
        Resource.AXE,
        Resource.SLING,
    ]
    assert fighters[3] not in issued and fighters[4] not in issued


def test_engines_need_crews_and_breach_a_settlements_advantage() -> None:
    engines = {Resource.CATAPULT: 1, Resource.RAM: 1, Resource.LADDER: 2}
    assert crewed_engines(20, engines) == engines
    assert crewed_engines(7, engines) == {Resource.CATAPULT: 1}, "the heaviest are crewed first"
    assert crewed_engines(3, {Resource.LADDER: 2}) == {Resource.LADDER: 1}
    assert settlement_bonus_after_engines(SETTLEMENT_DEFENCE_BP, {}) == 12_500
    assert settlement_bonus_after_engines(SETTLEMENT_DEFENCE_BP, {Resource.LADDER: 1}) == 11_250
    assert settlement_bonus_after_engines(SETTLEMENT_DEFENCE_BP, {Resource.RAM: 1}) == 10_000


def test_an_opening_volley_can_break_a_line_before_it_closes() -> None:
    outcome = _battle(_levies("a", 5, volley_bp=10_000), _levies("d", 5))
    assert outcome.rounds == 0 and outcome.attackers_won, "five sure shots felled five"
    assert _battle(_levies("a", 5), _levies("d", 5)).rounds > 0


def test_catapults_bombard_defenders_before_every_round() -> None:
    few, many = _levies("a", 3), _levies("d", 10)
    assert not _battle(few, many).attackers_won
    assert _battle(few, many, catapults=10).attackers_won, "the engines broke the defence"


def test_the_armoury_needs_knowledge_materials_and_days_of_work() -> None:
    state, home, _, _ = _world()
    civilization = state.civilizations[home]
    workers = civilization.population.living_ids[:2]
    order = DirectOrder(
        command_id="spears",
        kind=DirectOrderKind.CRAFT_EQUIPMENT,
        worker_ids=workers,
        craft_item=Resource.SPEAR,
        craft_quantity=4,
    )
    errors = validate_envelope(envelope(state, home, order), state).errors
    assert [error.code for error in errors] == ["unqualified_worker"], "spears need timbercraft"

    smith = civilization.population.people[workers[0]]
    smith.skills = {**smith.skills, "timbercraft": 200}
    assert validate_envelope(envelope(state, home, order), state).errors == ()
    greedy = order.model_copy(update={"craft_item": Resource.RAM, "craft_quantity": 100})
    errors = validate_envelope(envelope(state, home, greedy), state).errors
    assert [error.code for error in errors] == ["insufficient_materials"]

    timber = civilization.inventory.quantities[Resource.TIMBER]
    state, results = _run(state, 1, {home: OneShotSovereign(order)})
    assert _events(results, "craft_started")
    assert state.civilizations[home].inventory.quantities[Resource.TIMBER] == timber - 8
    busy = DirectOrder(
        command_id="march",
        kind=DirectOrderKind.SEND_WAR_PARTY,
        journey_id=EntityId("journey:busy"),
        recipient_civilization_id=EntityId("civilization:0000000002"),
        traveller_ids=workers,
        route=(civilization.start_center, civilization.start_center),
        war_objective=WarObjective.RAID,
    )
    state.day = 30
    codes = [error.code for error in validate_envelope(envelope(state, home, busy), state).errors]
    assert "traveller_unavailable" in codes, "armourers stay at their benches"
    state.day = 1

    state, results = _run(state, 4)
    [crafted] = _events(results, "equipment_crafted")
    assert crafted.payload["quantity"] == 4
    assert state.civilizations[home].inventory.quantities[Resource.SPEAR] == 4
    assert state.civilizations[home].craft_jobs == ()


def test_heavy_engines_slow_a_war_party() -> None:
    state, home, rival, route = _world()
    assert journey_days(JourneyKind.CAMPAIGN, state.world_map, route) == 8
    assert journey_days(JourneyKind.CAMPAIGN, state.world_map, route, heavy=True) == 12

    def arrival(cargo: dict[Resource, int]) -> int:
        world, _, _, _ = _world()
        _stock(world, home, ram=1, ladder=1)
        order = DirectOrder(
            command_id="march",
            kind=DirectOrderKind.SEND_WAR_PARTY,
            journey_id=EntityId(clear_journey_id("slow", days=10)),
            recipient_civilization_id=rival,
            traveller_ids=world.civilizations[home].population.living_ids[:8],
            route=route[:4],
            cargo=cargo,
            war_objective=WarObjective.ATTACK,
        )
        assert validate_envelope(envelope(world, home, order), world).errors == ()
        world, _ = _run(world, 10, {home: OneShotSovereign(order)})
        [party] = world.journeys
        return party.arrived_day

    assert arrival({Resource.LADDER: 1}) == 2, "ladders are light"
    assert arrival({Resource.RAM: 1}) == 4, "a ram adds half again to every tile"


def test_war_party_gear_needs_crews_and_one_kit_per_fighter() -> None:
    state, home, rival, route = _world()
    _stock(state, home, catapult=1, spear=10)

    def codes(cargo: dict[Resource, int], fighters: int) -> list[str]:
        order = DirectOrder(
            command_id="march",
            kind=DirectOrderKind.SEND_WAR_PARTY,
            journey_id=EntityId("journey:gear"),
            recipient_civilization_id=rival,
            traveller_ids=state.civilizations[home].population.living_ids[:fighters],
            route=route,
            cargo=cargo,
            war_objective=WarObjective.RAID,
        )
        errors = validate_envelope(envelope(state, home, order), state).errors
        return [error.code for error in errors]

    assert codes({Resource.CATAPULT: 1}, 6) == []
    assert codes({Resource.CATAPULT: 1}, 5) == ["invalid_cargo"], "a catapult needs six hands"
    assert codes({Resource.SPEAR: 7}, 6) == ["invalid_cargo"], "one kit per fighter"
    assert codes({Resource.TIMBER: 1}, 6) == ["invalid_cargo"], "only war gear"


def test_a_routed_party_abandons_its_engines_and_its_dead_lose_their_kits() -> None:
    state, home, rival, route = _world()
    _stock(state, home, ladder=1, spear=4)
    order = DirectOrder(
        command_id="march",
        kind=DirectOrderKind.SEND_WAR_PARTY,
        journey_id=EntityId(clear_journey_id("doomed", days=12)),
        recipient_civilization_id=rival,
        traveller_ids=state.civilizations[home].population.living_ids[:4],
        route=route,
        cargo={Resource.LADDER: 1, Resource.SPEAR: 4},
        war_objective=WarObjective.RAID,
    )

    state, results = _run(state, 12, {home: OneShotSovereign(order)})

    [battle] = state.battles
    assert battle.winner_id == rival
    [abandoned] = _events(results, "engines_abandoned")
    assert abandoned.payload["taken"] is True
    assert state.civilizations[rival].inventory.quantities.get(Resource.LADDER) == 1
    [party] = state.journeys
    assert party.outcome is JourneyOutcome.ROUTED and not party.active
    people = state.civilizations[home].population.people
    survivors = sum(people[person_id].alive for person_id in party.traveller_ids)
    assert state.civilizations[home].inventory.quantities.get(Resource.SPEAR, 0) == survivors
    assert state.civilizations[home].inventory.quantities.get(Resource.LADDER, 0) == 0
    validate_world(state)


def test_home_defenders_take_up_kits_from_their_store() -> None:
    state, home, rival, route = _world()
    fields = HexCoord(route[-1].q, route[-1].r - 3)
    people = state.civilizations[rival].population.people
    for person_id in sorted(people)[6:]:
        people[person_id].location = fields

    def outcome(defender_spears: int) -> str:
        world = state.model_copy(deep=True)
        _stock(world, rival, axe=0, spear=defender_spears)
        order = DirectOrder(
            command_id="march",
            kind=DirectOrderKind.SEND_WAR_PARTY,
            journey_id=EntityId(clear_journey_id("probe", days=6)),
            recipient_civilization_id=rival,
            traveller_ids=world.civilizations[home].population.living_ids[:12],
            route=route,
            war_objective=WarObjective.RAID,
        )
        world, _ = _run(world, 6, {home: OneShotSovereign(order)})
        [battle] = world.battles
        return battle.winner_id

    assert outcome(0) == home, "twelve raiders beat six unarmed villagers"
    assert outcome(6) == rival, "the same villagers with spears hold"
    assert Kit  # the kit type is exported for sovereign code
