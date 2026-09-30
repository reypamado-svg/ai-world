from logistics_helpers import OneShotSovereign, clear_journey_id, envelope, treaty_world

import sovereign_world.engine as engine_module
from sovereign_world.capabilities import CapabilityId, CapabilityRecord
from sovereign_world.commands import (
    DirectOrder,
    DirectOrderKind,
    build_council_report,
    validate_envelope,
)
from sovereign_world.engine import TransitionResult, advance_day
from sovereign_world.hexmap import HexCoord
from sovereign_world.ids import EntityId
from sovereign_world.resources import Resource
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState, validate_world
from sovereign_world.walls import (
    WALL_GRADES,
    WallGrade,
    Walls,
    manned_towers,
    wall_bonus_after_engines,
)
from sovereign_world.war import Fighter, WarObjective, resolve_battle


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


def _grant(state: WorldState, civilization_id: EntityId, person_id, capability) -> None:
    civilization = state.civilizations[civilization_id]
    person = civilization.population.people[person_id]
    person.skills = {**person.skills, capability.value: 100}
    others = [item for item in civilization.capabilities if item.capability is not capability]
    civilization.capabilities = tuple(
        sorted(
            (
                *others,
                CapabilityRecord(
                    capability=capability, practitioner_ids=(person_id,), discovered_day=0
                ),
            ),
            key=lambda item: item.capability.value,
        )
    )


def _walls(state, civilization_id, grade, towers=0) -> None:
    civilization = state.civilizations[civilization_id]
    civilization.walls = (
        Walls(
            settlement_id=civilization.settlements[0].settlement_id,
            grade=grade,
            strength=WALL_GRADES[grade].strength,
            towers=towers,
            built_day=0,
        ),
    )


def _raise(builders, grade, command_id="walls") -> DirectOrder:
    return DirectOrder(
        command_id=command_id,
        kind=DirectOrderKind.BUILD_WALLS,
        worker_ids=builders,
        wall_grade=grade,
    )


def _towers(builders, count, command_id="towers") -> DirectOrder:
    return DirectOrder(
        command_id=command_id,
        kind=DirectOrderKind.BUILD_TOWERS,
        worker_ids=builders,
        tower_count=count,
    )


def test_engines_cut_low_walls_more_than_high_ones() -> None:
    ladder, ram = {Resource.LADDER: 1}, {Resource.RAM: 1}
    assert wall_bonus_after_engines(None, ladder) == 10_000
    assert wall_bonus_after_engines(WallGrade.PALISADE, {}) == 12_500
    assert wall_bonus_after_engines(WallGrade.PALISADE, ladder) == 11_250
    assert wall_bonus_after_engines(WallGrade.DRYSTONE, ram) == 10_000, "a ram breaches"
    assert wall_bonus_after_engines(WallGrade.MORTARED, ladder) == 16_000, "too high to scale"
    assert wall_bonus_after_engines(WallGrade.FORTRESS, ram) == 14_000, "a ram only halves it"
    assert manned_towers(6, 7) == 3, "each tower needs two defenders to man it"


def test_walls_and_towers_turn_the_odds_against_attackers() -> None:
    def fighters(prefix: str, count: int) -> list[Fighter]:
        return [
            Fighter(
                person_id=EntityId(f"person:{prefix}{index:02d}"),
                civilization_id=EntityId(f"civilization:{prefix}"),
                strength=100,
                health_bp=10_000,
                veteran=False,
                hungry=False,
            )
            for index in range(count)
        ]

    def wins(defence_bp: int, towers: int) -> int:
        return sum(
            resolve_battle(
                fighters("a", 16),
                fighters("d", 10),
                defence_bp=defence_bp,
                attacker_morale_bp=2_000,
                defender_morale_bp=3_000,
                rng=StableRng(7),
                stream=f"trial:{trial}",
                towers=towers,
            ).attackers_won
            for trial in range(200)
        )

    open_village = wins(12_500, 0)
    palisade = wins(12_500 * 12_500 // 10_000, 2)
    fortress = wins(12_500 * 18_000 // 10_000, 5)
    assert open_village > palisade > fortress
    assert open_village >= 150 and fortress <= 50


def test_the_engine_fights_with_the_walls_and_towers_the_settlement_has(monkeypatch) -> None:
    seen: list[dict] = []
    real = engine_module.resolve_battle

    def spy(attackers, defenders, **kwargs):
        seen.append({"defence_bp": kwargs["defence_bp"], "towers": kwargs["towers"]})
        return real(attackers, defenders, **kwargs)

    monkeypatch.setattr(engine_module, "resolve_battle", spy)
    state, home, rival, route = _world()
    people = state.civilizations[rival].population.people
    for person_id in sorted(people)[5:]:
        people[person_id].location = HexCoord(route[-1].q, route[-1].r - 3)
    _walls(state, rival, WallGrade.FORTRESS, towers=6)
    raiders = state.civilizations[home]
    raiders.inventory = raiders.inventory.model_copy(
        update={"quantities": {**raiders.inventory.quantities, Resource.RAM: 1}}
    )
    raid = DirectOrder(
        command_id="march",
        kind=DirectOrderKind.SEND_WAR_PARTY,
        journey_id=EntityId(clear_journey_id("raid", days=12)),
        recipient_civilization_id=rival,
        traveller_ids=state.civilizations[home].population.living_ids[:12],
        route=route,
        cargo={Resource.RAM: 1},
        war_objective=WarObjective.RAID,
    )
    _run(state, 8, {home: OneShotSovereign(raid)})

    [battle] = seen
    assert battle["towers"] == 2, "five defenders man only two of the six towers"
    assert battle["defence_bp"] == 10_000 * 14_000 // 10_000, (
        "the ram takes the settlement's own bonus and halves the fortress wall's"
    )


def test_walls_rise_grade_by_grade_with_the_craft_each_grade_needs() -> None:
    state, home, _, _ = _world()
    civilization = state.civilizations[home]
    builders = civilization.population.living_ids[:6]
    palisade = _raise(builders, WallGrade.PALISADE)
    assert _codes(state, home, palisade) == ["unqualified_worker"], "a palisade is timbercraft"
    _grant(state, home, builders[0], CapabilityId.TIMBERCRAFT)
    assert _codes(state, home, palisade) == []
    assert _codes(
        state,
        home,
        palisade,
        _raise(civilization.population.living_ids[6:8], WallGrade.EARTHWORK, "again"),
    ) == ["invalid_walls"], "one wall job at a time in a settlement"
    assert _codes(state, home, _towers(builders, 1)) == ["invalid_walls"], "no walls yet"
    timber = civilization.inventory.quantities[Resource.TIMBER]

    state, results = _run(state, 16, {home: OneShotSovereign(palisade)})

    [built] = _events(results, "walls_built")
    [raised] = _events(results, "walls_raised")
    assert (built.payload["grade"], built.day) == ("earthwork", 4), "30 person-days by six"
    assert (raised.payload["grade"], raised.day) == ("palisade", 14), "60 more person-days"
    civilization = state.civilizations[home]
    assert civilization.inventory.quantities[Resource.TIMBER] == timber - 40
    [walls] = civilization.walls
    assert walls.grade is WallGrade.PALISADE
    assert walls.strength == WALL_GRADES[WallGrade.PALISADE].strength
    assert civilization.wall_jobs == ()
    assert build_council_report(state, home).walls == civilization.walls
    assert _codes(state, home, _raise(builders, WallGrade.EARTHWORK)) == ["invalid_walls"]
    validate_world(state)


def test_towers_stand_on_walls_up_to_the_grade_s_limit() -> None:
    state, home, _, _ = _world()
    civilization = state.civilizations[home]
    builders = civilization.population.living_ids[:2]
    _walls(state, home, WallGrade.PALISADE)
    assert _codes(state, home, _towers(builders, 3)) == ["invalid_walls"], "a palisade holds 2"
    order = _towers(builders, 2)
    assert _codes(state, home, order) == ["unqualified_worker"]
    _grant(state, home, builders[1], CapabilityId.TIMBERCRAFT)
    timber = civilization.inventory.quantities[Resource.TIMBER]

    state, results = _run(state, 11, {home: OneShotSovereign(order)})

    assert [event.day for event in _events(results, "towers_built")] == [4, 9]
    civilization = state.civilizations[home]
    assert civilization.walls[0].towers == 2
    assert civilization.inventory.quantities[Resource.TIMBER] == timber - 20


def test_stone_walls_need_stone_tools_and_fortification() -> None:
    state, home, _, _ = _world()
    civilization = state.civilizations[home]
    builders = civilization.population.living_ids[:4]
    _walls(state, home, WallGrade.DRYSTONE)
    mortared = _raise(builders, WallGrade.MORTARED)
    assert _codes(state, home, mortared) == ["unqualified_worker"]
    _grant(state, home, builders[0], CapabilityId.FORTIFICATION)
    assert _codes(state, home, mortared) == ["insufficient_materials"], "no tools in store"
    civilization.inventory = civilization.inventory.model_copy(
        update={"quantities": {**civilization.inventory.quantities, Resource.TOOL: 10}}
    )
    assert _codes(state, home, mortared) == []


def test_unused_wall_materials_return_when_every_builder_dies() -> None:
    state, home, _, _ = _world()
    civilization = state.civilizations[home]
    builders = civilization.population.living_ids[:2]
    _grant(state, home, builders[0], CapabilityId.TIMBERCRAFT)
    timber = civilization.inventory.quantities[Resource.TIMBER]
    state, _ = _run(state, 2, {home: OneShotSovereign(_raise(builders, WallGrade.PALISADE))})
    assert state.civilizations[home].inventory.quantities[Resource.TIMBER] == timber - 40
    for person_id in builders:
        person = state.civilizations[home].population.people[person_id]
        person.alive = False
        person.death_day = state.day

    state, results = _run(state, 1)

    assert _events(results, "wall_work_stopped")
    civilization = state.civilizations[home]
    assert civilization.inventory.quantities[Resource.TIMBER] == timber
    assert civilization.walls == () and civilization.wall_jobs == ()
