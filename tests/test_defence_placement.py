"""Choosing where to build (rules version 3 defence): named sections, towers by section,
gatehouses, and attackers pressing the weakest section."""

from logistics_helpers import OneShotSovereign, envelope

from sovereign_world.capabilities import CapabilityId
from sovereign_world.commands import DirectOrder, DirectOrderKind, validate_envelope
from sovereign_world.config import RunManifest, WorldConfig
from sovereign_world.engine import advance_day
from sovereign_world.events import DomainEvent
from sovereign_world.ids import EntityId
from sovereign_world.resources import Resource
from sovereign_world.rings import (
    WallRing,
    WallSection,
    auto_towers,
    battered_ring,
    empty_ring,
    ring_defence_bp,
    tower_positions,
)
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState, build_initial_state, validate_world
from sovereign_world.walls import BASIS, WALL_GRADES, DefenceWork, WallGrade, WallJob

CONFIG = WorldConfig(seed=9, width=24, height=24)
SID = EntityId("settlement:0000000001-0001")


def _ring(
    grades: list[WallGrade | None], gates: tuple[int, ...] = (0, 3), **update: object
) -> WallRing:
    ring = empty_ring(SID, 2, gates, 0)
    sections = tuple(
        item.model_copy(
            update={
                "grade": grade,
                "strength": 0 if grade is None else WALL_GRADES[grade].strength,
            }
        )
        for item, grade in zip(ring.sections, grades, strict=True)
    )
    return ring.model_copy(update={"sections": sections, **update})


def _state(rules_version: int = 3) -> tuple[WorldState, EntityId, EntityId]:
    state = build_initial_state(RunManifest.new(CONFIG, "0.1.0", rules_version=rules_version))
    home = sorted(state.civilizations)[0]
    [capital] = state.civilizations[home].settlements
    return state, home, capital.settlement_id


def _gates_east_and_west(state: WorldState, home: EntityId, sid: EntityId) -> None:
    """The capital's plan with gates 0 and 3, as the test rings have them."""
    civilization = state.civilizations[home]
    plan = civilization.town_plans[sid]
    civilization.town_plans = {sid: plan.model_copy(update={"gates": (0, 3)})}


def _builders(state: WorldState, home: EntityId) -> tuple[EntityId, ...]:
    people = state.civilizations[home].population.people
    chosen = state.civilizations[home].population.living_ids[:2]
    for person_id in chosen:
        people[person_id].skills = {
            **people[person_id].skills,
            CapabilityId.TIMBERCRAFT.value: 1,
            CapabilityId.STONEWORKING.value: 1,
        }
    return tuple(chosen)


def _order(kind: DirectOrderKind, workers: tuple[EntityId, ...], **fields: object) -> DirectOrder:
    return DirectOrder(command_id="walls", kind=kind, worker_ids=workers, **fields)


def _messages(state: WorldState, home: EntityId, *orders: DirectOrder) -> list[str]:
    result = validate_envelope(envelope(state, home, *orders), state)
    return [f"{item.code}: {item.message}" for item in result.errors]


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


def test_named_sections_are_raised_in_the_order_given() -> None:
    state, home, sid = _state()
    workers = _builders(state, home)
    order = _order(
        DirectOrderKind.BUILD_WALLS, workers, wall_grade=WallGrade.EARTHWORK, section_ids=(7, 2)
    )
    assert _messages(state, home, order) == []
    state, events = _run(state, 4, {home: OneShotSovereign(order)})
    built = [event.payload["section"] for event in events if event.kind == "wall_section_built"]
    assert built == [7, 2]
    ring = state.civilizations[home].wall_rings[sid]
    assert [index for index, item in enumerate(ring.sections) if item.grade] == [2, 7]
    validate_world(state)


def test_named_sections_are_checked() -> None:
    state, home, sid = _state()
    workers = _builders(state, home)
    civilization = state.civilizations[home]
    _gates_east_and_west(state, home, sid)
    civilization.wall_rings = {sid: _ring([WallGrade.PALISADE] * 3 + [None] * 7)}

    def walls(**fields: object) -> list[str]:
        defaults: dict[str, object] = {"wall_grade": WallGrade.PALISADE}
        return _messages(
            state, home, _order(DirectOrderKind.BUILD_WALLS, workers, **{**defaults, **fields})
        )

    assert walls(section_ids=(12,)) == ["invalid_walls: section 12 is not in this ring"]
    assert walls(section_ids=(4, 4)) == ["invalid_walls: each section is named once"]
    assert walls(section_ids=(4,), wall_sections=1) == [
        "invalid_walls: name the sections or count them, not both"
    ]
    assert walls(section_ids=(1,)) == ["invalid_walls: section 1 already stands at that grade"]
    repair = _order(DirectOrderKind.REPAIR_WALLS, workers, section_ids=(1,))
    assert _messages(state, home, repair) == ["invalid_walls: section 1 is not damaged"]
    older, older_home, _ = _state(2)
    old_workers = _builders(older, older_home)
    named = _order(
        DirectOrderKind.BUILD_WALLS, old_workers, wall_grade=WallGrade.EARTHWORK, section_ids=(1,)
    )
    assert _messages(older, older_home, named) == [
        "invalid_walls: this world's rules do not name wall sections"
    ]
    plain = _order(DirectOrderKind.BUILD_WALLS, workers, wall_grade=WallGrade.PALISADE)
    assert "section_ids" not in plain.model_dump() and "work" not in plain.model_dump()


def test_towers_stand_on_named_sections_or_go_to_the_gates_first() -> None:
    bare = _ring([WallGrade.PALISADE] * 10)
    assert bare.gates() == {0, 5}
    assert auto_towers(bare, 2) == (0, 5)
    assert auto_towers(_ring([WallGrade.PALISADE] * 10, gates=(0,)), 3) == (0, 5, 2)
    assert tower_positions(bare.model_copy(update={"towers": 2})) == (0, 5)

    state, home, sid = _state()
    workers = _builders(state, home)
    civilization = state.civilizations[home]
    _gates_east_and_west(state, home, sid)
    civilization.wall_rings = {sid: bare}
    civilization.inventory = civilization.inventory.model_copy(
        update={"quantities": {**civilization.inventory.quantities, Resource.TIMBER: 500}}
    )
    towers = _order(DirectOrderKind.BUILD_TOWERS, workers, section_ids=(2, 7))
    assert _messages(state, home, towers) == []
    assert _messages(
        state, home, _order(DirectOrderKind.BUILD_TOWERS, workers, section_ids=(1, 2, 3))
    ) == ["invalid_walls: this ring carries at most 2 towers"]
    state, events = _run(state, 12, {home: OneShotSovereign(towers)})
    ring = state.civilizations[home].wall_rings[sid]
    assert ring.tower_sections == (2, 7) and ring.towers == 2
    assert [event.payload["sections"] for event in events if event.kind == "towers_built"] == [
        "2",
        "7",
    ]
    validate_world(state)
    full = _order(DirectOrderKind.BUILD_TOWERS, workers, section_ids=(2,))
    assert "invalid_walls: section 2 already has a tower" in _messages(state, home, full)


def test_battering_takes_towers_from_the_highest_section_first() -> None:
    ring = _ring([WallGrade.DRYSTONE] * 10, towers=3, tower_sections=(2, 5, 8))
    # Nine hits bring section 0 down to palisade: the ring then carries two towers.
    after, _ = battered_ring(ring, 9)
    assert after.sections[0].grade is WallGrade.PALISADE
    assert after.tower_sections == (2, 5) and after.towers == 2
    # A section that falls to nothing loses its tower and its gatehouse.
    gate = _ring([WallGrade.EARTHWORK] * 10, towers=0)
    sections = list(gate.sections)
    sections[0] = sections[0].model_copy(update={"gatehouse": True})
    gate = gate.model_copy(update={"sections": tuple(sections)})
    fallen, _ = battered_ring(gate, 2)
    assert fallen.sections[0].grade is None and not fallen.sections[0].gatehouse


def test_gatehouses_fortify_the_gates() -> None:
    state, home, sid = _state()
    workers = _builders(state, home)
    civilization = state.civilizations[home]
    _gates_east_and_west(state, home, sid)
    civilization.wall_rings = {sid: _ring([WallGrade.PALISADE] * 10)}
    timber = civilization.inventory.quantities.get(Resource.TIMBER, 0)
    works = _order(
        DirectOrderKind.BUILD_WORKS, workers, work=DefenceWork.GATEHOUSE, section_ids=(0, 5)
    )
    assert _messages(state, home, works) == []
    not_gate = works.model_copy(update={"section_ids": (1,)})
    assert _messages(state, home, not_gate) == ["invalid_works: section 1 is not a standing gate"]
    state, events = _run(state, 11, {home: OneShotSovereign(works)})
    ring = state.civilizations[home].wall_rings[sid]
    assert ring.sections[0].gatehouse and ring.sections[5].gatehouse
    assert [event.payload["section"] for event in events if event.kind == "gatehouse_built"] == [
        0,
        5,
    ]
    [started] = [event for event in events if event.kind == "works_started"]
    assert started.payload == {"work": "gatehouse", "sections": "0,5"}
    # Two gatehouses on a palisade cost what two wooden towers cost.
    assert state.civilizations[home].inventory.quantities.get(Resource.TIMBER, 0) <= timber - 20
    validate_world(state)
    again = works.model_copy(update={"section_ids": (0,)})
    assert "invalid_works: section 0 already has a gatehouse" in _messages(state, home, again)
    older, older_home, _ = _state(2)
    old_works = works.model_copy(update={"worker_ids": _builders(older, older_home)})
    assert _messages(older, older_home, old_works) == [
        "invalid_works: this world's rules have no defensive works"
    ]


def test_attackers_press_the_weakest_section() -> None:
    bare = _ring([WallGrade.PALISADE] * 10)
    # Gates at 0 and 5 are worth 11,500, the rest 12,500: mean 12,300, weakest 11,500.
    assert ring_defence_bp(bare, {}, 50, assault=True) == BASIS + 1_900
    assert ring_defence_bp(bare, {}, 50) == BASIS + 2_500, "the old rule is unchanged"
    sections = tuple(
        item.model_copy(update={"gatehouse": True}) if item.gate else item for item in bare.sections
    )
    fortified = bare.model_copy(update={"sections": sections})
    assert ring_defence_bp(fortified, {}, 50, assault=True) == BASIS + 2_500
    # Towers on the gates: the gates 12,000, the four beside them 13,000, the rest 12,500.
    towered = bare.model_copy(update={"towers": 2, "tower_sections": (0, 5)})
    assert ring_defence_bp(towered, {}, 50, assault=True) == BASIS + 2_300
    # A gap is the assault point: half the ring built is worth far less than before.
    half = _ring([WallGrade.PALISADE] * 5 + [None] * 5)
    assert ring_defence_bp(half, {}, 50, assault=True) < ring_defence_bp(half, {}, 50)


def test_rings_and_jobs_without_the_new_fields_dump_as_before() -> None:
    ring = _ring([WallGrade.PALISADE] * 10, towers=2)
    dumped = ring.model_dump(mode="json")
    assert "tower_sections" not in dumped
    assert all("gatehouse" not in item for item in dumped["sections"])
    job = WallJob(
        job_id=EntityId("job"),
        settlement_id=SID,
        tile=build_initial_state(RunManifest.new(CONFIG, "0.1.0")).world_map.tiles[0].coord,
        worker_ids=(),
        start_grade=None,
        target=WallGrade.EARTHWORK,
        started_day=0,
    )
    assert "work" not in job.model_dump(mode="json")
    assert WallSection(grade=WallGrade.EARTHWORK, strength=10, gate=True, gatehouse=True)
