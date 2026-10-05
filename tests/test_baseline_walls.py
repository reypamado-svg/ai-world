"""The scripted baseline walls its designed capital (rules version 3): earthwork, or a
palisade when someone idle knows timbercraft; repairs first; never below its reserve."""

from functools import cache

from sovereign_world.capabilities import CapabilityId
from sovereign_world.commands import (
    CouncilReport,
    DirectOrder,
    DirectOrderKind,
    build_council_report,
)
from sovereign_world.config import RunManifest, WorldConfig
from sovereign_world.engine import advance_day
from sovereign_world.events import DomainEvent
from sovereign_world.ids import EntityId
from sovereign_world.resources import Resource
from sovereign_world.rings import WallRing, empty_ring, ring_grade
from sovereign_world.rng import StableRng
from sovereign_world.scripted import (
    RESERVED_HANDS,
    WALL_CREW,
    WALL_RESERVE,
    BaselineSovereign,
    plan_baseline_commands,
)
from sovereign_world.state import WorldState, build_initial_state, validate_world
from sovereign_world.walls import WALL_GRADES, WallGrade, WallJob

CONFIG = WorldConfig(seed=21, width=24, height=24)
WALL_KINDS = {DirectOrderKind.BUILD_WALLS, DirectOrderKind.REPAIR_WALLS}


def _state(rules_version: int = 3) -> WorldState:
    return build_initial_state(RunManifest.new(CONFIG, "0.2.0", rules_version=rules_version))


def _run(state: WorldState, days: int) -> tuple[WorldState, list[DomainEvent]]:
    sovereigns = {key: BaselineSovereign() for key in state.civilizations}
    rng = StableRng(state.config.seed)
    events: list[DomainEvent] = []
    for _ in range(days):
        result = advance_day(state, rng, sovereigns=sovereigns)
        state = result.state
        events.extend(result.events.events)
    return state, events


@cache
def _designed() -> WorldState:
    """The baseline world after its day-30 council has designed every capital."""
    return _run(_state(), 31)[0]


def _walls(commands: tuple[object, ...]) -> list[DirectOrder]:
    return [item for item in commands if isinstance(item, DirectOrder) and item.kind in WALL_KINDS]


def _capital(report: CouncilReport) -> EntityId:
    return next(item.settlement_id for item in report.settlements if item.capital)


def _timbercraft(report: CouncilReport) -> bool:
    return any(
        person.skills.get(CapabilityId.TIMBERCRAFT.value, 0) > 0 for person in report.notable_people
    )


def _ring(report: CouncilReport, grades: list[WallGrade | None]) -> WallRing:
    sid = _capital(report)
    ring = empty_ring(sid, 2, (0, 3), 0)
    return ring.model_copy(
        update={
            "sections": tuple(
                item.model_copy(
                    update={
                        "grade": grade,
                        "strength": 0 if grade is None else WALL_GRADES[grade].strength,
                    }
                )
                for item, grade in zip(ring.sections, grades, strict=True)
            )
        }
    )


def test_no_walls_before_the_design_nor_under_older_rules() -> None:
    for rules_version in (1, 2, 3):
        state = _state(rules_version)
        for key in state.civilizations:
            assert not _walls(plan_baseline_commands(build_council_report(state, key)))


def test_a_designed_capital_is_walled_by_two_idle_hands() -> None:
    state = _designed()
    grades = {}
    for key in sorted(state.civilizations):
        report = build_council_report(state, key)
        commands = plan_baseline_commands(report)
        assert len(commands) <= 8
        [order] = _walls(commands)
        assert commands[-1] == order and order.kind is DirectOrderKind.BUILD_WALLS
        assert len(order.worker_ids) == WALL_CREW
        assert not set(order.worker_ids) & set(report.person_ids[:RESERVED_HANDS])
        idle = {
            person.person_id
            for person in report.notable_people
            if person.duty is None and person.settlement_id == _capital(report)
        }
        assert set(order.worker_ids) <= idle
        grades[key] = order.wall_grade
        expected = WallGrade.PALISADE if _timbercraft(report) else WallGrade.EARTHWORK
        assert order.wall_grade is expected
        assert plan_baseline_commands(report) == commands, "the same report, the same orders"
    assert WallGrade.PALISADE in grades.values() and WallGrade.EARTHWORK in grades.values()


def test_the_baseline_waits_for_its_job_its_reserve_and_its_hands() -> None:
    state = _designed()
    key = next(
        key for key in sorted(state.civilizations) if _timbercraft(build_council_report(state, key))
    )
    report = build_council_report(state, key)
    sid = _capital(report)
    job = WallJob(
        job_id=EntityId("wall-job:test"),
        settlement_id=sid,
        tile=next(item.tile for item in report.settlements if item.capital),
        worker_ids=report.person_ids[:1],
        start_grade=None,
        target=WallGrade.EARTHWORK,
        started_day=report.day,
    )
    assert not _walls(plan_baseline_commands(report.model_copy(update={"wall_jobs": (job,)})))

    # Ten palisade sections take 40 timber; the store keeps its reserve after paying.
    def with_timber(timber: int) -> CouncilReport:
        stores = {
            key: {**value, Resource.TIMBER: timber} if key == sid else value
            for key, value in report.stores.items()
        }
        inventory = {**report.inventory, Resource.TIMBER: timber}
        return report.model_copy(update={"stores": stores, "inventory": inventory})

    assert not _walls(plan_baseline_commands(with_timber(WALL_RESERVE + 39)))
    [order] = _walls(plan_baseline_commands(with_timber(WALL_RESERVE + 40)))
    assert order.wall_grade is WallGrade.PALISADE

    # One spare idle person is not a crew.
    spare = [
        person
        for person in report.notable_people
        if person.duty is None
        and person.settlement_id == sid
        and person.person_id not in report.person_ids[:RESERVED_HANDS]
    ]
    few = tuple(item for item in report.notable_people if item not in spare[1:])
    assert not _walls(plan_baseline_commands(report.model_copy(update={"notable_people": few})))


def test_an_earthwork_ring_is_raised_a_palisade_one_left_and_a_battered_one_mended() -> None:
    state = _designed()
    reports = [build_council_report(state, key) for key in sorted(state.civilizations)]
    skilled = next(report for report in reports if _timbercraft(report))
    unskilled = next(report for report in reports if not _timbercraft(report))

    def orders(report: CouncilReport, ring: WallRing) -> list[DirectOrder]:
        update = {"wall_rings": {_capital(report): ring}}
        return _walls(plan_baseline_commands(report.model_copy(update=update)))

    [raise_] = orders(skilled, _ring(skilled, [WallGrade.EARTHWORK] * 10))
    assert raise_.kind is DirectOrderKind.BUILD_WALLS and raise_.wall_grade is WallGrade.PALISADE
    assert orders(skilled, _ring(skilled, [WallGrade.PALISADE] * 10)) == []
    assert orders(unskilled, _ring(unskilled, [WallGrade.EARTHWORK] * 10)) == []

    battered = _ring(skilled, [WallGrade.PALISADE] * 10)
    sections = list(battered.sections)
    sections[4] = sections[4].model_copy(update={"strength": 5})
    battered = battered.model_copy(update={"sections": tuple(sections)})
    [mend] = orders(skilled, battered)
    assert mend.kind is DirectOrderKind.REPAIR_WALLS
    # Nobody who can mend a palisade: the ring is raised instead, here to nothing new.
    battered_there = battered.model_copy(update={"settlement_id": _capital(unskilled)})
    assert orders(unskilled, battered_there) == []


def test_the_baseline_walls_every_capital_within_four_months() -> None:
    state, events = _run(_state(), 105)
    started = [event for event in events if event.kind == "wall_work_started"]
    assert [event.day for event in started] == [60] * len(state.civilizations)
    assert not [event for event in events if event.kind == "walls_unfunded"]
    assert not [event for event in events if "reject" in event.kind]
    validate_world(state)
    for key, civilization in state.civilizations.items():
        [capital] = [item for item in civilization.settlements if item.capital]
        ring = civilization.wall_rings[capital.settlement_id]
        assert ring.complete, key
        report = build_council_report(state, key)
        expected = WallGrade.PALISADE if _timbercraft(report) else WallGrade.EARTHWORK
        assert ring_grade(ring) is expected
        assert civilization.wall_jobs == ()
        assert civilization.inventory.quantities.get(Resource.TIMBER, 0) >= WALL_RESERVE
        assert report.population is not None and report.population.hungry == 0
        assert len(civilization.population.living_ids) == 32
