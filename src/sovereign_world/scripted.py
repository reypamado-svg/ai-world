"""Deterministic sovereign policies for simulation development and balance."""

from __future__ import annotations

from typing import Protocol

from sovereign_world.capabilities import CapabilityId
from sovereign_world.commands import (
    Command,
    CommandEnvelope,
    CouncilReport,
    Decree,
    DecreeKind,
    DirectOrder,
    DirectOrderKind,
    PersonView,
    ProjectKind,
)
from sovereign_world.hexmap import HexCoord, Terrain
from sovereign_world.housing import HOUSE_GRADES, HOUSEHOLD, MAX_HOUSES_PER_ORDER, HouseGrade
from sovereign_world.ids import EntityId
from sovereign_world.institutions import InstitutionKind
from sovereign_world.resources import Resource
from sovereign_world.rings import damaged_sections, empty_ring
from sovereign_world.townplan import (
    STANDARD_RING,
    Place,
    PlanStyle,
    TownPlanSpec,
    is_default,
)
from sovereign_world.walls import (
    WALL_GRADES,
    WallGrade,
    rank,
    section_materials,
    section_repair_materials,
)


class Sovereign(Protocol):
    def decide(self, report: CouncilReport) -> CommandEnvelope: ...


def plan_baseline_commands(report: CouncilReport) -> tuple[Command, ...]:
    if report.rules_version >= 3:
        return _plan_rules_three(report)
    if report.rules_version >= 2:
        return _plan_rules_two(report)
    commands: tuple[Command, ...] = (
        Decree(
            command_id=f"decree:{report.day}:food",
            kind=DecreeKind.FOOD_RESERVE_TARGET,
            value=90,
            priority=100,
            duration_days=60,
        ),
        Decree(
            command_id=f"decree:{report.day}:labor",
            kind=DecreeKind.LABOR_PRIORITY,
            value=70,
            priority=80,
            duration_days=60,
        ),
        Decree(
            command_id=f"decree:{report.day}:growth",
            kind=DecreeKind.POPULATION_GROWTH_POLICY,
            value=1,
            priority=60,
            duration_days=60,
        ),
        DirectOrder(
            command_id=f"project:{report.day}:shelter",
            kind=DirectOrderKind.START_PROJECT,
            worker_ids=report.person_ids[:2],
            project_id=EntityId(f"project:shelter:{report.civilization_id}"),
            project_kind=ProjectKind.SHELTER,
            priority=90,
        ),
        DirectOrder(
            command_id=f"project:{report.day}:storage",
            kind=DirectOrderKind.START_PROJECT,
            worker_ids=report.person_ids[2:4],
            project_id=EntityId(f"project:storage:{report.civilization_id}"),
            project_kind=ProjectKind.STORAGE,
            priority=85,
        ),
    )
    if report.day != 0:
        return commands
    direction = -1 if report.start_center.q >= 4 else 1
    route = _survey_route(report, direction) or _survey_route(report, -direction)
    if route is None:
        return commands
    return (
        *commands,
        DirectOrder(
            command_id=f"expedition:{report.day}:survey",
            kind=DirectOrderKind.START_EXPEDITION,
            expedition_id=EntityId(f"expedition:survey:{report.civilization_id}"),
            explorer_ids=report.person_ids[4:6],
            route=route,
            priority=80,
        ),
    )


def _plan_rules_two(report: CouncilReport) -> tuple[Command, ...]:
    """Rules version 2: the same steady policy, building houses instead of one shelter, and
    raising a hall at the capital on the first day."""
    commands: list[Command] = [
        Decree(
            command_id=f"decree:{report.day}:food",
            kind=DecreeKind.FOOD_RESERVE_TARGET,
            value=90,
            priority=100,
            duration_days=60,
        ),
        Decree(
            command_id=f"decree:{report.day}:labor",
            kind=DecreeKind.LABOR_PRIORITY,
            value=70,
            priority=80,
            duration_days=60,
        ),
        Decree(
            command_id=f"decree:{report.day}:growth",
            kind=DecreeKind.POPULATION_GROWTH_POLICY,
            value=1,
            priority=60,
            duration_days=60,
        ),
        Decree(
            command_id=f"decree:{report.day}:housing",
            kind=DecreeKind.HOUSING_POLICY,
            value=SPARE_ROOM_PCT,
            priority=60,
            duration_days=60,
        ),
        Decree(
            command_id=f"decree:{report.day}:materials",
            kind=DecreeKind.MATERIALS_RESERVE_TARGET,
            value=MATERIALS_TARGET,
            priority=60,
            duration_days=60,
        ),
    ]
    # On the first day the housing policy starts the first house; the council's eight
    # orders go to the hall and the survey instead.
    shelter = _house_order(report) if report.day else None
    if shelter is not None:
        commands.append(shelter)
    commands.append(
        DirectOrder(
            command_id=f"project:{report.day}:storage",
            kind=DirectOrderKind.START_PROJECT,
            worker_ids=report.person_ids[2:4],
            project_id=EntityId(f"project:storage:{report.civilization_id}"),
            project_kind=ProjectKind.STORAGE,
            priority=85,
        )
    )
    # A hall at the capital, raised on the first day, or as soon as the store can pay.
    if not any(item.kind is InstitutionKind.HALL for item in report.institutions):
        commands.append(
            DirectOrder(
                command_id=f"institution:{report.day}:hall",
                kind=DirectOrderKind.FOUND_INSTITUTION,
                institution_kind=InstitutionKind.HALL,
                worker_ids=report.person_ids[6:7],
                priority=70,
            )
        )
    if report.day != 0:
        return tuple(commands)
    direction = -1 if report.start_center.q >= 4 else 1
    route = _survey_route(report, direction) or _survey_route(report, -direction)
    if route is not None:
        commands.append(
            DirectOrder(
                command_id=f"expedition:{report.day}:survey",
                kind=DirectOrderKind.START_EXPEDITION,
                expedition_id=EntityId(f"expedition:survey:{report.civilization_id}"),
                explorer_ids=report.person_ids[4:6],
                route=route,
                priority=80,
            )
        )
    return tuple(commands)


def _plan_rules_three(report: CouncilReport) -> tuple[Command, ...]:
    """Rules version 3: the rules-2 policy, then a design for the capital while it still
    has the plain plan; once it is designed, walls along its ring. Either goes last, so it
    waits for a council with an order to spare."""
    commands = _plan_rules_two(report)
    capital = next((item for item in report.settlements if item.capital), None)
    if capital is None:
        return commands
    plan = report.town_plans.get(capital.settlement_id)
    if plan is None:
        return commands
    if not is_default(plan):
        walls = _wall_order(report, capital.settlement_id)
        return commands if walls is None else (*commands, walls)
    land = report.land.get(capital.settlement_id)
    watered = land is not None and land.watered
    design = DirectOrder(
        command_id=f"plan:{report.day}:capital",
        kind=DirectOrderKind.PLAN_SETTLEMENT,
        settlement_id=capital.settlement_id,
        town_plan=TownPlanSpec(
            style=PlanStyle.RINGED,
            keep=Place.CENTRE,
            market=Place.BY_STORE,
            craft_quarter=Place.BY_WATER if watered else Place.BY_STORE,
            wall_ring=STANDARD_RING,
            gates=(0, 3),
        ),
    )
    return (*commands, design)


WALL_CREW = 2
"""Builders the baseline sets to its walls, as many as to a house."""
BASELINE_WALL = WallGrade.PALISADE
"""The highest grade the baseline raises: stone walls need skills and tools it never gets."""
RESERVED_HANDS = 7
"""The first people the baseline's other orders name (houses, storage, survey, hall); its
wall crew is drawn from the rest, so no two orders in a council claim one person."""


def _wall_order(report: CouncilReport, settlement_id: EntityId) -> DirectOrder | None:
    """Rules version 3: walls along the capital's designed ring. Mend damaged sections the
    crew can mend, else raise every section to palisade (earthwork when no crew member knows
    timbercraft), in one job, keeping half the materials target in store."""
    if any(job.settlement_id == settlement_id for job in report.wall_jobs):
        return None
    reserved = set(report.person_ids[:RESERVED_HANDS])

    def knows(person: PersonView, capability: CapabilityId | None) -> bool:
        return capability is None or person.skills.get(capability.value, 0) > 0

    crew = sorted(
        (
            person
            for person in report.notable_people
            if person.duty is None
            and person.settlement_id == settlement_id
            and person.person_id not in reserved
        ),
        key=lambda person: (not knows(person, CapabilityId.TIMBERCRAFT), person.person_id),
    )[:WALL_CREW]
    if len(crew) < WALL_CREW:
        return None
    plan = report.town_plans[settlement_id]
    ring = report.wall_rings.get(settlement_id) or empty_ring(
        settlement_id, plan.wall_ring, plan.gates, report.day
    )
    store = report.stores.get(settlement_id, report.inventory)

    def affordable(cost: dict[Resource, int]) -> bool:
        return all(
            store.get(resource, 0) - quantity >= WALL_RESERVE for resource, quantity in cost.items()
        )

    def total(costs: list[dict[Resource, int]]) -> dict[Resource, int]:
        out: dict[Resource, int] = {}
        for cost in costs:
            for resource, quantity in cost.items():
                out[resource] = out.get(resource, 0) + quantity
        return out

    def order(kind: DirectOrderKind, grade: WallGrade | None = None) -> DirectOrder:
        return DirectOrder(
            command_id=f"walls:{report.day}:capital",
            kind=kind,
            worker_ids=tuple(person.person_id for person in crew),
            wall_grade=grade,
            priority=75,
        )

    damaged = [ring.sections[index].grade for index in damaged_sections(ring)]
    if damaged and all(
        any(knows(person, WALL_GRADES[grade].capability) for person in crew)
        for grade in damaged
        if grade is not None
    ):
        cost = total([section_repair_materials(grade) for grade in damaged if grade is not None])
        if affordable(cost):
            return order(DirectOrderKind.REPAIR_WALLS)
    target = BASELINE_WALL if knows(crew[0], CapabilityId.TIMBERCRAFT) else WallGrade.EARTHWORK
    below = [item.grade for item in ring.sections if rank(item.grade) < rank(target)]
    if not below or not affordable(total([section_materials(grade, target) for grade in below])):
        return None
    return order(DirectOrderKind.BUILD_WALLS, target)


SPARE_ROOM_PCT = 10
"""The baseline keeps a tenth of its capital's room spare."""
MATERIALS_TARGET = 300
"""Timber each settlement gathers toward, and half as much stone."""
WALL_RESERVE = MATERIALS_TARGET // 2
"""Timber (or stone) the baseline keeps in store after paying for walls, for its houses."""


def _house_order(report: CouncilReport) -> DirectOrder | None:
    """Houses enough to bring the capital back to its spare room, as far as its store allows."""
    if report.house_jobs:
        return None
    capital = next((item for item in report.settlements if item.capital), None)
    view = None if capital is None else report.housing.get(capital.settlement_id)
    if capital is None or view is None:
        return None
    wanted = view.residents * (100 + SPARE_ROOM_PCT) // 100 + 1 - view.slots
    if wanted <= 0:
        return None
    count = min(MAX_HOUSES_PER_ORDER, -(-wanted // HOUSEHOLD))
    store = report.stores.get(capital.settlement_id, report.inventory)
    # The best house the store can pay for at least one of; huts when stone is short.
    grade = next(
        (
            item
            for item in _grades_down_from(view.buildable)
            if all(
                store.get(resource, 0) >= quantity
                for resource, quantity in HOUSE_GRADES[item].materials.items()
            )
        ),
        None,
    )
    if grade is None:
        return None
    for resource, quantity in HOUSE_GRADES[grade].materials.items():
        count = min(count, store.get(resource, 0) // quantity)
    return DirectOrder(
        command_id=f"project:{report.day}:shelter",
        kind=DirectOrderKind.START_PROJECT,
        worker_ids=report.person_ids[:2],
        project_id=EntityId(f"project:houses:{report.civilization_id}:{report.day}"),
        project_kind=ProjectKind.SHELTER,
        house_count=count,
        house_grade=grade,
        priority=90,
    )


def _grades_down_from(best: HouseGrade) -> tuple[HouseGrade, ...]:
    """The best grade and those below it, best first; a hut is always known."""
    if best is HouseGrade.STONE_HOUSE:
        return (HouseGrade.STONE_HOUSE, HouseGrade.HUT)
    if best is HouseGrade.HOUSE:
        return (HouseGrade.HOUSE, HouseGrade.HUT)
    return (HouseGrade.HUT,)


def _survey_route(report: CouncilReport, direction: int) -> tuple[HexCoord, ...] | None:
    """A straight survey of up to five tiles, stopping short of known water and of rivers
    known to be too deep to wade; None when not even one step is open."""
    terrain = dict(report.known_terrain)
    deep = {
        frozenset((view.tile, view.across)) for view in report.known_rivers if view.depth == "deep"
    }
    route = [report.start_center]
    for _step in range(5):
        here = route[-1]
        ahead = HexCoord(here.q + direction, here.r)
        if terrain.get(ahead) is Terrain.WATER or frozenset((here, ahead)) in deep:
            break
        route.append(ahead)
    return tuple(route) if len(route) > 1 else None


class BaselineSovereign:
    def decide(self, report: CouncilReport) -> CommandEnvelope:
        return CommandEnvelope(
            schema_version=1,
            civilization_id=report.civilization_id,
            council_day=report.day,
            correlation_id=report.report_id,
            commands=plan_baseline_commands(report)[:8],
        )
