"""Wall rings (rules version 3): walls raised section by section along a settlement's plan.

A ring of radius r blocks is a square of side 2r+1 blocks; each section is two block-sides,
so a ring has `sections_of(r)` sections, and the gates are the sections facing the plan's
gate directions. A section costs a tenth of what a whole wall's grade step costs, so a
complete ring 2 costs exactly what walls cost before plans; a wider ring costs more and
shelters more houses. Each section has its own grade and strength: catapults batter the
weakest, and a fallen earthwork section leaves a gap.

The walls defend in proportion to the share of the ring built and the share of the
settlement's houses inside it. A complete ring counts as walls of its weakest section's
grade wherever walls are asked about (ranks, ruins, what spies see).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sovereign_world.ids import EntityId
from sovereign_world.resources import Resource
from sovereign_world.townplan import (
    DEFAULT_PLAN,
    GATE_SECTORS,
    MAX_RING,
    SHELTERED_HOUSES,
    sections_of,
)
from sovereign_world.walls import (
    BASIS,
    SECTION_SHARE,
    WALL_GRADES,
    WALL_HIT,
    WallGrade,
    WallJob,
    Walls,
    rank,
    section_materials,
    section_person_days,
    section_repair_materials,
    section_repair_person_days,
    tower_spec,
    wall_bonus_after_engines,
)

if TYPE_CHECKING:
    from sovereign_world.state import CivilizationState

SALVAGE_SHARE = 2
"""Pulling a ring down gives back half of what its sections and towers cost."""
MAX_SECTIONS = sections_of(MAX_RING)


def gate_sections(ring: int, gates: tuple[int, ...]) -> frozenset[int]:
    """The sections that are gates: those facing each gate direction (0-5)."""
    count = sections_of(ring)
    return frozenset(gate * count // GATE_SECTORS for gate in gates)


class WallSection(BaseModel):
    """One stretch of a ring: unbuilt (no grade), or standing at a grade and strength."""

    model_config = ConfigDict(frozen=True)

    grade: WallGrade | None = None
    strength: int = Field(default=0, ge=0)
    gate: bool = False

    @model_validator(mode="after")
    def within_grade(self) -> WallSection:
        if self.grade is None and self.strength:
            raise ValueError("an unbuilt section has no strength")
        if self.grade is not None and not 0 < self.strength <= WALL_GRADES[self.grade].strength:
            raise ValueError("a section's strength is within its grade's")
        return self


class WallRing(BaseModel):
    """A settlement's walls along its planned ring, section by section, with their towers."""

    model_config = ConfigDict(frozen=True)

    settlement_id: EntityId
    ring: int = Field(ge=1, le=MAX_RING)
    sections: tuple[WallSection, ...]
    towers: int = Field(default=0, ge=0)
    built_day: int = Field(ge=0)
    """The day a section last went up."""

    @model_validator(mode="after")
    def whole_ring(self) -> WallRing:
        if len(self.sections) != sections_of(self.ring):
            raise ValueError("a ring has its radius's number of sections")
        if self.towers > tower_cap(self):
            raise ValueError("a ring carries no more towers than its walls allow")
        return self

    @property
    def built(self) -> int:
        return sum(item.grade is not None for item in self.sections)

    @property
    def complete(self) -> bool:
        return self.built == len(self.sections)

    def gates(self) -> frozenset[int]:
        return frozenset(index for index, item in enumerate(self.sections) if item.gate)


def empty_ring(settlement_id: EntityId, ring: int, gates: tuple[int, ...], day: int) -> WallRing:
    doors = gate_sections(ring, gates)
    return WallRing(
        settlement_id=settlement_id,
        ring=ring,
        sections=tuple(WallSection(gate=index in doors) for index in range(sections_of(ring))),
        built_day=day,
    )


def weakest(ring: WallRing) -> WallGrade | None:
    """The lowest grade among the sections that stand, if any stand."""
    grades = [item.grade for item in ring.sections if item.grade is not None]
    return min(grades, key=rank) if grades else None


def ring_grade(ring: WallRing | None) -> WallGrade | None:
    """What a complete ring counts as: walls of its weakest section's grade."""
    if ring is None or not ring.complete:
        return None
    return weakest(ring)


def tower_cap(ring: WallRing) -> int:
    """Towers scale with the ring: the weakest standing grade's towers per ten sections."""
    grade = weakest(ring)
    if grade is None:
        return 0
    return WALL_GRADES[grade].towers * len(ring.sections) // SECTION_SHARE


def sheltered(ring: int, houses: int) -> tuple[int, int]:
    """Houses inside a ring of this radius and outside it."""
    inside = min(houses, SHELTERED_HOUSES[ring])
    return inside, houses - inside


def ring_defence_bp(ring: WallRing | None, engines: dict[Resource, int], houses: int) -> int:
    """What the walls add to the home defence: each standing section its share of its grade's
    bonus (after the attackers' engines), scaled by the share of houses inside the ring."""
    if ring is None or not ring.built:
        return BASIS
    extra = sum(
        wall_bonus_after_engines(item.grade, engines) - BASIS
        for item in ring.sections
        if item.grade is not None
    ) // len(ring.sections)
    inside, outside = sheltered(ring.ring, houses)
    if outside:
        extra = extra * inside // houses
    return BASIS + extra


def battered_ring(ring: WallRing, hits: int) -> tuple[WallRing, list[tuple[int, WallGrade | None]]]:
    """The ring after catapult hits, and each section hit with the grade it was left at.

    Every hit lands on the most battered standing section (the lowest strength, the lowest
    index on ties), so bombardment is decided without a draw. A section whose strength runs
    out falls to the grade below at full strength; earthwork that falls leaves a gap. Towers
    beyond what the weakest standing grade carries fall with it.
    """
    sections = list(ring.sections)
    hit: list[tuple[int, WallGrade | None]] = []
    for _ in range(hits):
        standing = [index for index, item in enumerate(sections) if item.grade is not None]
        if not standing:
            break
        index = min(standing, key=lambda item: (sections[item].strength, item))
        section = sections[index]
        assert section.grade is not None
        strength = section.strength - WALL_HIT
        if strength > 0:
            sections[index] = section.model_copy(update={"strength": strength})
        else:
            below = rank(section.grade) - 1
            grade = None if below == 0 else tuple(WallGrade)[below - 1]
            sections[index] = WallSection(
                grade=grade,
                strength=0 if grade is None else WALL_GRADES[grade].strength,
                gate=section.gate,
            )
        hit.append((index, sections[index].grade))
    after = ring.model_copy(update={"sections": tuple(sections), "towers": 0})
    return after.model_copy(update={"towers": min(ring.towers, tower_cap(after))}), hit


def ring_salvage(ring: WallRing) -> dict[Resource, int]:
    """Half of what the ring's standing sections and towers cost, rounded down."""
    total: dict[Resource, int] = {}
    for item in ring.sections:
        if item.grade is not None:
            for resource, quantity in section_materials(None, item.grade).items():
                total[resource] = total.get(resource, 0) + quantity
    grade = weakest(ring)
    if grade is not None and ring.towers:
        for resource, quantity in tower_spec(grade).materials.items():
            total[resource] = total.get(resource, 0) + quantity * ring.towers
    return {
        resource: quantity // SALVAGE_SHARE
        for resource, quantity in sorted(total.items())
        if quantity // SALVAGE_SHARE
    }


def ring_for(civilization: CivilizationState, settlement_id: EntityId, day: int) -> WallRing:
    """A settlement's ring as it stands, or an unbuilt one along its plan's line."""
    ring = civilization.wall_rings.get(settlement_id)
    if ring is not None:
        return ring
    plan = civilization.town_plans.get(settlement_id) or DEFAULT_PLAN
    return empty_ring(settlement_id, plan.wall_ring, plan.gates, day)


def set_ring(civilization: CivilizationState, ring: WallRing) -> None:
    civilization.wall_rings = dict(
        sorted({**civilization.wall_rings, ring.settlement_id: ring}.items())
    )


def sections_to_raise(ring: WallRing, target: WallGrade, count: int | None) -> tuple[int, ...]:
    """The sections an order raises to `target`: the weakest first, then in ring order."""
    below = sorted(
        (index for index, item in enumerate(ring.sections) if rank(item.grade) < rank(target)),
        key=lambda index: (rank(ring.sections[index].grade), index),
    )
    return tuple(below if count is None else below[:count])


def damaged_sections(ring: WallRing) -> tuple[int, ...]:
    return tuple(
        index
        for index, item in enumerate(ring.sections)
        if item.grade is not None and item.strength < WALL_GRADES[item.grade].strength
    )


def job_costs(job: WallJob) -> tuple[int, ...]:
    """Person-days for each section a ring job works on, in order."""
    if job.target is not None:
        return tuple(section_person_days(grade, job.target) for grade in job.section_grades)
    return tuple(
        section_repair_person_days(grade) for grade in job.section_grades if grade is not None
    )


def sections_done(job: WallJob) -> int:
    done, spent = 0, 0
    for cost in job_costs(job):
        spent += cost
        if job.person_days_done < spent:
            break
        done += 1
    return done


def ring_job_done(job: WallJob) -> bool:
    if job.sections:
        return sections_done(job) == len(job.sections)
    return job.towers_built() == job.towers


def unspent(job: WallJob) -> dict[Resource, int]:
    """What a ring job took for the work it has not yet done."""
    left: dict[Resource, int] = {}
    if job.sections:
        for grade in job.section_grades[sections_done(job) :]:
            if job.target is not None:
                cost = section_materials(grade, job.target)
            else:
                assert grade is not None
                cost = section_repair_materials(grade)
            for resource, quantity in cost.items():
                left[resource] = left.get(resource, 0) + quantity
    elif job.start_grade is not None:
        remaining = job.towers - job.towers_built()
        for resource, quantity in tower_spec(job.start_grade).materials.items():
            left[resource] = left.get(resource, 0) + quantity * remaining
    return {resource: quantity for resource, quantity in sorted(left.items()) if quantity}


def ruin_walls(ring: WallRing | None) -> Walls | None:
    """What a complete ring leaves standing in a ruin: walls of its weakest grade."""
    grade = ring_grade(ring)
    if ring is None or grade is None:
        return None
    return Walls(
        settlement_id=ring.settlement_id,
        grade=grade,
        strength=min(item.strength for item in ring.sections if item.grade is grade),
        towers=min(ring.towers, WALL_GRADES[grade].towers),
        built_day=ring.built_day,
    )


def ring_from_ruin(ring: WallRing, walls: Walls, day: int) -> WallRing:
    """The ruin's walls taken up along a resettled town's line, every section alike."""
    raised = ring.model_copy(
        update={
            "sections": tuple(
                item.model_copy(update={"grade": walls.grade, "strength": walls.strength})
                for item in ring.sections
            ),
            "towers": 0,
            "built_day": day,
        }
    )
    return raised.model_copy(update={"towers": min(walls.towers, tower_cap(raised))})


def ring_work(
    ring: WallRing,
    *,
    target: WallGrade | None,
    repair: bool,
    towers: int,
    count: int | None,
) -> tuple[tuple[int, ...], tuple[WallGrade | None, ...], dict[Resource, int]]:
    """The sections a wall order works on, their grades now, and what it takes from the store.

    Raising takes the weakest sections first (`count` of them, or every one below the
    target); repair takes every damaged section; towers take no section.
    """
    if repair:
        chosen = damaged_sections(ring)
    elif target is not None:
        chosen = sections_to_raise(ring, target, count)
    else:
        chosen = ()
    grades = tuple(ring.sections[index].grade for index in chosen)
    total: dict[Resource, int] = {}
    grade = weakest(ring)
    if towers and grade is None:
        # Towers stand on walls; with none standing there is nothing to take.
        costs = []
    elif towers:
        assert grade is not None
        costs = [
            {
                resource: quantity * towers
                for resource, quantity in tower_spec(grade).materials.items()
            }
        ]
    elif repair:
        costs = [section_repair_materials(grade) for grade in grades if grade is not None]
    else:
        assert target is not None or not chosen
        costs = [section_materials(grade, target) for grade in grades if target is not None]
    for cost in costs:
        for resource, quantity in cost.items():
            total[resource] = total.get(resource, 0) + quantity
    return (
        chosen,
        grades,
        {resource: total[resource] for resource in sorted(total) if total[resource]},
    )
