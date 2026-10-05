"""Wall rings (rules version 3): section costs, defence shares, bombardment and salvage."""

from types import SimpleNamespace

import pytest
from logistics_helpers import OneShotSovereign, treaty_world
from pydantic import ValidationError
from test_allegiance import _colony
from test_peace import _make_peace

import sovereign_world.engine as engine
from sovereign_world.capabilities import CapabilityId
from sovereign_world.commands import (
    CommandEnvelope,
    DirectOrder,
    DirectOrderKind,
    build_council_report,
    validate_envelope,
)
from sovereign_world.config import RunManifest, WorldConfig
from sovereign_world.diplomacy import PeaceTerms
from sovereign_world.engine import advance_day
from sovereign_world.events import DomainEvent
from sovereign_world.hexmap import HexCoord
from sovereign_world.housing import HouseGrade, Housing
from sovereign_world.ids import EntityId
from sovereign_world.ranks import settlement_facts
from sovereign_world.resources import Resource
from sovereign_world.rings import (
    WallRing,
    WallSection,
    battered_ring,
    empty_ring,
    gate_sections,
    ring_defence_bp,
    ring_grade,
    ring_salvage,
    sheltered,
    tower_cap,
)
from sovereign_world.rng import StableRng
from sovereign_world.scripted import BaselineSovereign
from sovereign_world.state import WorldState, build_initial_state, state_hash, validate_world
from sovereign_world.territory import Settlement
from sovereign_world.townplan import Place, PlanStyle, TownPlanSpec, sections_of
from sovereign_world.walls import (
    BASIS,
    GRADES,
    WALL_GRADES,
    WallGrade,
    repair_materials,
    section_materials,
    section_person_days,
    section_repair_materials,
    section_repair_person_days,
    step_materials,
)

SID = EntityId("settlement:0000000001-0001")


def _ring(ring: int, grades: list[WallGrade | None], towers: int = 0) -> WallRing:
    """A ring along the plain plan's line (one gate, facing direction 0)."""
    doors = gate_sections(ring, (0,))
    sections = tuple(
        WallSection(
            grade=grade,
            strength=0 if grade is None else WALL_GRADES[grade].strength,
            gate=index in doors,
        )
        for index, grade in enumerate(grades)
    )
    return WallRing(settlement_id=SID, ring=ring, sections=sections, towers=towers, built_day=0)


def test_a_standard_ring_costs_what_walls_cost_before_plans() -> None:
    assert [sections_of(ring) for ring in range(1, 6)] == [6, 10, 14, 18, 22]
    for grade in GRADES:
        for start in (None, *GRADES[: GRADES.index(grade)]):
            ten = {
                resource: quantity * 10
                for resource, quantity in section_materials(start, grade).items()
            }
            assert ten == step_materials(start, grade)
        assert section_person_days(None, grade) * 10 == sum(
            WALL_GRADES[item].person_days for item in GRADES[: GRADES.index(grade) + 1]
        )
    assert section_materials(None, WallGrade.PALISADE) == {Resource.TIMBER: 4}
    assert section_person_days(None, WallGrade.EARTHWORK) == 3
    assert section_materials(WallGrade.DRYSTONE, WallGrade.FORTRESS) == {
        Resource.STONE: 40,
        Resource.TOOL: 3,
    }
    # Repair: a quarter of a section's share, rounded up; never more than the old whole repair.
    assert section_repair_materials(WallGrade.PALISADE) == {Resource.TIMBER: 1}
    assert section_repair_person_days(WallGrade.FORTRESS) == 8
    for grade in GRADES:
        for resource, quantity in section_repair_materials(grade).items():
            assert quantity * 4 >= WALL_GRADES[grade].materials[resource] // 10
            assert quantity <= repair_materials(grade)[resource]


def test_gates_face_their_directions() -> None:
    assert gate_sections(1, (0, 3)) == {0, 3}
    assert gate_sections(2, (0, 3)) == {0, 5}
    for ring in range(1, 6):
        assert len(gate_sections(ring, tuple(range(6)))) == 6
    ring = empty_ring(SID, 2, (0, 3), 4)
    assert ring.gates() == {0, 5} and ring.built == 0 and not ring.complete
    assert ring_grade(ring) is None and tower_cap(ring) == 0


def test_sections_and_rings_are_checked() -> None:
    with pytest.raises(ValidationError):
        WallSection(grade=None, strength=3)
    with pytest.raises(ValidationError):
        WallSection(grade=WallGrade.EARTHWORK, strength=11)
    with pytest.raises(ValidationError):
        WallSection(grade=WallGrade.EARTHWORK, strength=0)
    with pytest.raises(ValidationError):
        _ring(2, [None] * 9)
    with pytest.raises(ValidationError):
        _ring(2, [WallGrade.PALISADE] * 10, towers=3)


def test_towers_scale_with_the_ring() -> None:
    assert tower_cap(_ring(2, [WallGrade.PALISADE] * 10)) == 2
    assert tower_cap(_ring(5, [WallGrade.FORTRESS] * 22)) == 13
    assert tower_cap(_ring(1, [WallGrade.DRYSTONE] * 6)) == 1
    mixed = _ring(2, [WallGrade.FORTRESS] * 9 + [WallGrade.PALISADE])
    assert tower_cap(mixed) == 2 and ring_grade(mixed) is WallGrade.PALISADE


def test_defence_follows_the_share_built_and_the_houses_sheltered() -> None:
    half = _ring(2, [WallGrade.PALISADE] * 5 + [None] * 5)
    assert ring_defence_bp(half, {}, 100) == BASIS + 1_250
    whole = _ring(2, [WallGrade.PALISADE] * 10)
    assert ring_defence_bp(whole, {}, 384) == BASIS + 2_500
    assert ring_defence_bp(whole, {}, 600) == BASIS + 1_600  # 384 of 600 houses: 64 %
    assert ring_defence_bp(None, {}, 50) == BASIS
    assert ring_defence_bp(empty_ring(SID, 2, (0,), 0), {}, 50) == BASIS
    # A ram breaches low walls whatever the ring.
    assert ring_defence_bp(whole, {Resource.RAM: 1}, 50) == BASIS
    assert sheltered(2, 600) == (384, 216)
    assert sheltered(1, 20) == (20, 0)


def test_catapults_batter_the_weakest_section() -> None:
    ring = _ring(2, [WallGrade.FORTRESS] * 10, towers=6)
    after, hit = battered_ring(ring, 20)
    assert [index for index, _ in hit] == [0] * 20
    assert after.sections[0].grade is WallGrade.MORTARED
    assert after.sections[0].strength == WALL_GRADES[WallGrade.MORTARED].strength
    assert after.sections[1:] == ring.sections[1:]
    assert after.towers == 4  # mortared walls carry four towers a standard ring
    # The breach takes the next hit too: it is still the most battered.
    again, hit = battered_ring(after, 1)
    assert hit == [(0, WallGrade.MORTARED)] and again.sections[0].strength == 65

    earth = _ring(1, [WallGrade.EARTHWORK] * 6)
    after, hit = battered_ring(earth, 3)
    assert hit == [(0, WallGrade.EARTHWORK), (0, None), (1, WallGrade.EARTHWORK)]
    assert after.sections[0].grade is None and not after.complete
    assert ring_grade(after) is None
    nothing, hit = battered_ring(empty_ring(SID, 1, (0,), 0), 3)
    assert hit == [] and nothing.built == 0


def test_pulling_a_ring_down_gives_back_half() -> None:
    ring = _ring(2, [WallGrade.DRYSTONE] * 3 + [None] * 7)
    assert ring_salvage(ring) == {Resource.STONE: 12, Resource.TIMBER: 6}
    towered = _ring(2, [WallGrade.PALISADE] * 10, towers=2)
    assert ring_salvage(towered) == {Resource.TIMBER: 30}
    assert ring_salvage(empty_ring(SID, 3, (0,), 0)) == {}


# ---------------------------------------------------------------- in the engine (rules 3)

CONFIG = WorldConfig(seed=9, width=24, height=24)


def _state(rules_version: int = 3) -> WorldState:
    return build_initial_state(RunManifest.new(CONFIG, "0.1.0", rules_version=rules_version))


def _home(state: WorldState) -> tuple[EntityId, Settlement]:
    home = sorted(state.civilizations)[0]
    [capital] = state.civilizations[home].settlements
    return home, capital


def _builders(state: WorldState, home: EntityId, count: int = 2) -> tuple[EntityId, ...]:
    people = state.civilizations[home].population.people
    chosen = state.civilizations[home].population.living_ids[:count]
    for person_id in chosen:
        people[person_id].skills = {
            **people[person_id].skills,
            CapabilityId.TIMBERCRAFT.value: 1,
            CapabilityId.STONEWORKING.value: 1,
        }
    return tuple(chosen)


def _walls(workers: tuple[EntityId, ...], grade: WallGrade, **update: object) -> DirectOrder:
    return DirectOrder(
        command_id="walls",
        kind=DirectOrderKind.BUILD_WALLS,
        worker_ids=workers,
        wall_grade=grade,
        **update,
    )


def _codes(state: WorldState, home: EntityId, *orders: DirectOrder) -> list[str]:
    envelope = CommandEnvelope(
        schema_version=2,
        civilization_id=home,
        council_day=state.day,
        correlation_id="test",
        commands=orders,
    )
    return [error.code for error in validate_envelope(envelope, state).errors]


def _run(
    state: WorldState, days: int, sovereigns: dict[EntityId, object] | None = None
) -> tuple[WorldState, list[DomainEvent]]:
    rng = StableRng(state.config.seed)
    events: list[DomainEvent] = []
    for _ in range(days):
        result = advance_day(state, rng, sovereigns=sovereigns or {})
        state = result.state
        events.extend(result.events.events)
    return state, events


def _timber(state: WorldState, home: EntityId) -> int:
    return state.civilizations[home].inventory.quantities.get(Resource.TIMBER, 0)


def test_a_ring_goes_up_section_by_section() -> None:
    state = _state()
    home, capital = _home(state)
    workers = _builders(state, home)
    order = _walls(workers, WallGrade.EARTHWORK)
    assert _codes(state, home, order) == []
    # Ten earthwork sections of three person-days each: two builders, fifteen days.
    state, events = _run(state, 16, {home: OneShotSovereign(order)})
    built = [event for event in events if event.kind == "wall_section_built"]
    assert [event.payload["section"] for event in built] == list(range(10))
    assert [event.payload["standing"] for event in built] == list(range(1, 11))
    assert len({event.day for event in built}) >= 7, "sections stand one at a time"
    ring = state.civilizations[home].wall_rings[capital.settlement_id]
    assert ring.complete and ring_grade(ring) is WallGrade.EARTHWORK
    assert ring.gates() == gate_sections(2, (0,))
    assert state.civilizations[home].walls == ()
    assert not state.civilizations[home].wall_jobs
    validate_world(state)
    report = build_council_report(state, home)
    assert report.wall_rings[capital.settlement_id] == ring
    assert (
        settlement_facts(state.civilizations[home], capital.settlement_id, 0, {}).walls
        is WallGrade.EARTHWORK
    )


def test_a_council_raises_some_sections_and_redesigns_at_a_cost() -> None:
    state = _state()
    home, capital = _home(state)
    workers = _builders(state, home)
    timber = _timber(state, home)
    order = _walls(workers, WallGrade.PALISADE, wall_sections=5)
    state, events = _run(state, 1, {home: OneShotSovereign(order)})
    # Five palisade sections take four timber each, up front.
    assert _timber(state, home) <= timber - 20
    job = state.civilizations[home].wall_jobs[0]
    assert job.sections == (0, 1, 2, 3, 4) and job.section_grades == (None,) * 5
    assert job.model_dump(mode="json")["sections"] == [0, 1, 2, 3, 4]
    state, events = _run(state, 29)
    ring = state.civilizations[home].wall_rings[capital.settlement_id]
    assert ring.built == 5 and not ring.complete and ring_grade(ring) is None
    assert ring_defence_bp(ring, {}, 7) == BASIS + 1_250

    # Widening the ring pulls the old one down: half the timber comes back.
    plan = DirectOrder(
        command_id="plan",
        kind=DirectOrderKind.PLAN_SETTLEMENT,
        settlement_id=capital.settlement_id,
        town_plan=TownPlanSpec(style="ringed", keep="centre", wall_ring=3, gates=(0,)),
    )
    assert state.day == 30
    state, events = _run(state, 1, {home: OneShotSovereign(plan)})
    [salvaged] = [event for event in events if event.kind == "walls_salvaged"]
    assert salvaged.payload["sections"] == 5 and salvaged.payload["timber"] == 10
    assert capital.settlement_id not in state.civilizations[home].wall_rings
    validate_world(state)


def test_redesign_stops_wall_work_and_returns_its_materials() -> None:
    state = _state()
    home, capital = _home(state)
    workers = _builders(state, home)
    order = _walls(workers, WallGrade.PALISADE)
    state, _ = _run(state, 1, {home: OneShotSovereign(order)})
    state, _ = _run(state, 29)
    assert state.civilizations[home].wall_jobs
    standing = state.civilizations[home].wall_rings[capital.settlement_id].built
    assert 0 < standing < 10
    plan = DirectOrder(
        command_id="plan",
        kind=DirectOrderKind.PLAN_SETTLEMENT,
        settlement_id=capital.settlement_id,
        town_plan=TownPlanSpec(style="open", keep="edge", wall_ring=2, gates=(0, 3)),
    )
    state, events = _run(state, 1, {home: OneShotSovereign(plan)})
    [salvaged] = [event for event in events if event.kind == "walls_salvaged"]
    assert salvaged.payload["jobs_stopped"] == 1
    # Half of what the standing sections cost, and all of what the rest would have.
    assert salvaged.payload["sections"] == standing
    assert salvaged.payload["timber"] == 4 * standing // 2 + 4 * (10 - standing)
    assert not state.civilizations[home].wall_jobs
    validate_world(state)


def test_wall_orders_are_checked_against_the_ring() -> None:
    state = _state()
    home, capital = _home(state)
    workers = _builders(state, home)
    towers = DirectOrder(
        command_id="towers",
        kind=DirectOrderKind.BUILD_TOWERS,
        worker_ids=workers,
        tower_count=1,
    )
    repair = DirectOrder(command_id="repair", kind=DirectOrderKind.REPAIR_WALLS, worker_ids=workers)
    assert _codes(state, home, towers) == ["invalid_walls"]
    assert _codes(state, home, repair) == ["invalid_walls"]
    civilization = state.civilizations[home]
    civilization.wall_rings = {capital.settlement_id: _ring(2, [WallGrade.PALISADE] * 10)}
    assert _codes(state, home, _walls(workers, WallGrade.PALISADE)) == ["invalid_walls"]
    assert _codes(state, home, towers) == []
    assert _codes(state, home, towers.model_copy(update={"tower_count": 3})) == ["invalid_walls"]
    unskilled = civilization.population.living_ids[5:7]
    assert _codes(state, home, _walls(unskilled, WallGrade.DRYSTONE)) == ["unqualified_worker"]
    civilization.inventory = civilization.inventory.model_copy(
        update={"quantities": {**civilization.inventory.quantities, Resource.STONE: 7}}
    )
    assert _codes(state, home, _walls(workers, WallGrade.DRYSTONE, wall_sections=1)) == [
        "insufficient_materials"
    ]
    # Sections are a planned town's; older rules refuse them.
    older = _state(2)
    old_home, _ = _home(older)
    old_workers = _builders(older, old_home)
    order = _walls(old_workers, WallGrade.EARTHWORK, wall_sections=3)
    assert _codes(older, old_home, order) == ["invalid_walls"]
    assert _codes(older, old_home, order.model_copy(update={"wall_sections": None})) == []
    assert "wall_sections" not in order.model_copy(update={"wall_sections": None}).model_dump()


def test_catapults_batter_a_ring_in_the_engine() -> None:
    state = _state()
    home, capital = _home(state)
    civilization = state.civilizations[home]
    civilization.wall_rings = {capital.settlement_id: _ring(2, [WallGrade.EARTHWORK] * 10)}
    siege = SimpleNamespace(
        defender_id=home,
        besieger_id=sorted(state.civilizations)[1],
        settlement_id=capital.settlement_id,
        settlement_tile=capital.tile,
    )
    events = engine._bombard(state, siege, 2, None)  # type: ignore[arg-type]
    assert [event.kind for event in events] == ["wall_section_fell"]
    assert events[0].payload == {"section": 0, "grade": "none", "strength": 0}
    ring = civilization.wall_rings[capital.settlement_id]
    assert ring.sections[0].grade is None and ring.built == 9


def test_the_keep_the_hill_and_the_houses_inside_change_the_defence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state()
    home, capital = _home(state)
    civilization = state.civilizations[home]
    hall_open: set[HexCoord] = set()
    monkeypatch.setattr(engine, "serving_tiles", lambda *args: hall_open)

    def defence() -> tuple[int, int, int]:
        return engine._planned_defence(state, home, capital.tile, {}, 10, 10_000)

    assert defence() == (0, 10_000, 12_500)
    plan = civilization.town_plans[capital.settlement_id]
    civilization.town_plans = {
        capital.settlement_id: plan.model_copy(update={"keep": Place.CENTRE})
    }
    assert defence() == (0, 10_000, 12_500), "a closed hall adds nothing"
    hall_open.add(capital.tile)
    assert defence() == (0, 10_000, 13_000)
    civilization.town_plans = {
        capital.settlement_id: plan.model_copy(update={"style": PlanStyle.HILL_FORT})
    }
    assert defence()[1] == 10_500
    civilization.wall_rings = {capital.settlement_id: _ring(2, [WallGrade.PALISADE] * 10, towers=2)}
    houses = civilization.housing[capital.settlement_id].count
    assert houses <= 384
    assert defence() == (2, 10_500, 12_500 * 12_500 // BASIS)


def test_a_craft_quarter_by_the_water_and_a_market_by_the_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state()
    home, capital = _home(state)
    civilization = state.civilizations[home]
    monkeypatch.setattr(engine, "serving_tiles", lambda *args: {capital.tile})
    plan = civilization.town_plans[capital.settlement_id]
    state.day = 3
    assert engine._workshop_bonus(state, home, capital.tile, 2, set()) == 0
    civilization.town_plans = {
        capital.settlement_id: plan.model_copy(update={"craft_quarter": Place.BY_WATER})
    }
    assert engine._workshop_bonus(state, home, capital.tile, 2, set()) == 2
    state.day = 4
    assert engine._workshop_bonus(state, home, capital.tile, 2, set()) == 0

    state = _state()
    home, capital = _home(state)
    room = state.civilizations[home].inventory.capacity
    market = DirectOrder(
        command_id="plan",
        kind=DirectOrderKind.PLAN_SETTLEMENT,
        settlement_id=capital.settlement_id,
        town_plan=TownPlanSpec(
            style="grid", keep="edge", market="by_store", wall_ring=2, gates=(0,)
        ),
    )
    state, events = _run(state, 1, {home: OneShotSovereign(market)})
    assert state.civilizations[home].inventory.capacity == room + 2_000
    assert not [event for event in events if event.kind == "walls_salvaged"]


def test_a_ceded_town_keeps_its_ring_and_a_fallen_one_leaves_its_walls() -> None:
    state, home, rival, route = treaty_world(distance=6, rules_version=3)
    colony = _colony(state, rival, route[3], people=6)
    # The helper walls the colony whole, the older way; a planned town has a ring instead.
    state.civilizations[rival].walls = ()
    ring = _ring(2, [WallGrade.PALISADE] * 9 + [WallGrade.EARTHWORK], towers=0)
    ring = ring.model_copy(update={"settlement_id": colony.settlement_id})
    giver = state.civilizations[rival]
    giver.wall_rings = dict(sorted({**giver.wall_rings, colony.settlement_id: ring}.items()))
    state, _ = _make_peace(
        state, home, rival, PeaceTerms(truce_days=60, ceded_settlement=colony.settlement_id)
    )
    assert colony.settlement_id not in state.civilizations[rival].wall_rings
    assert state.civilizations[home].wall_rings[colony.settlement_id] == ring
    validate_world(state)

    # The fallen people's capital, walled all round, is left a ruin with walls of its weakest
    # section's grade.
    fallen = state.civilizations[rival]
    [capital] = fallen.settlements
    fallen.wall_rings = {
        capital.settlement_id: ring.model_copy(update={"settlement_id": capital.settlement_id})
    }
    for person in fallen.population.people.values():
        person.alive = False
        person.death_day = state.day
    state = advance_day(state, StableRng(state.config.seed)).state
    assert state.civilizations[rival].wall_rings == {}
    [ruin] = [item for item in state.ruins if item.tile == capital.tile]
    assert ruin.walls is not None and ruin.walls.grade is WallGrade.EARTHWORK


def test_rules_three_runs_repeat_exactly() -> None:
    start = _state()

    def run() -> list[str]:
        state = start.model_copy(deep=True)
        sovereigns = {key: BaselineSovereign() for key in state.civilizations}
        hashes = []
        rng = StableRng(state.config.seed)
        for _ in range(40):
            state = advance_day(state, rng, sovereigns=sovereigns).state
            hashes.append(state_hash(state))
        validate_world(state)
        return hashes

    assert run() == run()


def test_a_storm_burns_the_houses_beyond_the_wall_line_first() -> None:
    state = _state()
    home, capital = _home(state)
    civilization = state.civilizations[home]
    civilization.housing = {capital.settlement_id: Housing(houses={HouseGrade.HUT: 400})}
    [event] = engine._lose_houses(state, home, capital.settlement_id, "stormed")
    # A quarter of 400 burn; 16 stood beyond a ring that shelters 384.
    assert event.payload == {"cause": "stormed", "count": 100, "outside": 16}
    older = _state(2)
    old_home, old_capital = _home(older)
    older.civilizations[old_home].housing = {
        old_capital.settlement_id: Housing(houses={HouseGrade.HUT: 400})
    }
    [event] = engine._lose_houses(older, old_home, old_capital.settlement_id, "stormed")
    assert event.payload == {"cause": "stormed", "count": 100}
