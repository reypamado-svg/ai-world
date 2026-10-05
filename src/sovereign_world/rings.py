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

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)

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
    HIGH_WALLS,
    SECTION_SHARE,
    WALL_GRADES,
    WALL_HIT,
    DefenceWork,
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
DITCH = 1
MOAT = 2
"""A ring's `ditch`: dug, or flooded."""


def gate_sections(ring: int, gates: tuple[int, ...]) -> frozenset[int]:
    """The sections that are gates: those facing each gate direction (0-5)."""
    count = sections_of(ring)
    return frozenset(gate * count // GATE_SECTORS for gate in gates)


def facing_sections(ring: WallRing, direction: int) -> frozenset[int]:
    """The sections facing a hex direction (0-5): each section faces the direction nearest
    to it round the ring, so a gate section faces its own gate's direction."""
    n = len(ring.sections)
    return frozenset(
        index
        for index in range(n)
        if (2 * index * GATE_SECTORS + n) // (2 * n) % GATE_SECTORS == direction
    )


class WallSection(BaseModel):
    """One stretch of a ring: unbuilt (no grade), or standing at a grade and strength."""

    model_config = ConfigDict(frozen=True)

    grade: WallGrade | None = None
    strength: int = Field(default=0, ge=0)
    gate: bool = False
    gatehouse: bool = False
    """Rules version 3: the gate is fortified, no weaker than the wall beside it."""

    @model_serializer(mode="wrap")
    def _omit_gatehouse(self, handler: SerializerFunctionWrapHandler) -> object:
        dumped = handler(self)
        if isinstance(dumped, dict) and not self.gatehouse:
            dumped.pop("gatehouse", None)
        return dumped

    @model_validator(mode="after")
    def within_grade(self) -> WallSection:
        if self.gatehouse and (not self.gate or self.grade is None):
            raise ValueError("a gatehouse stands in a standing gate section")
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
    tower_sections: tuple[int, ...] = ()
    """Rules version 3: the sections the towers stand on, one each."""
    ditch: int = Field(default=0, ge=0, le=MOAT)
    """Rules version 3: 1 if a ditch is dug round the ring, 2 if it is flooded as a moat."""
    stakes: bool = False
    """Rules version 3: stakes stand round the ring until the next battle at home."""

    @model_serializer(mode="wrap")
    def _omit_placement(self, handler: SerializerFunctionWrapHandler) -> object:
        dumped = handler(self)
        if isinstance(dumped, dict):
            if not self.tower_sections:
                dumped.pop("tower_sections", None)
            if not self.ditch:
                dumped.pop("ditch", None)
            if not self.stakes:
                dumped.pop("stakes", None)
        return dumped

    @model_validator(mode="after")
    def whole_ring(self) -> WallRing:
        if len(self.sections) != sections_of(self.ring):
            raise ValueError("a ring has its radius's number of sections")
        if self.tower_sections:
            placed = self.tower_sections
            if len(placed) != self.towers or placed != tuple(sorted(set(placed))):
                raise ValueError("each tower stands on its own section, in order")
            if any(
                index >= len(self.sections) or self.sections[index].grade is None
                for index in placed
            ):
                raise ValueError("towers stand on standing sections")
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


class Citadel(BaseModel):
    """Rules version 3: a walled keep at a settlement's centre, which the defenders fall back
    to when the town is lost."""

    model_config = ConfigDict(frozen=True)

    grade: WallGrade
    strength: int = Field(gt=0)
    built_day: int = Field(ge=0)

    @model_validator(mode="after")
    def within_grade(self) -> Citadel:
        if self.strength > WALL_GRADES[self.grade].strength:
            raise ValueError("a citadel's strength is within its grade's")
        return self


CITADEL_PIECES = 4
"""A citadel's walls cost four sections of their grade, raised from nothing."""
CITADEL_PLUNDER_BP = 5_000
"""Rules version 3: the share of a settlement's store raiders can carry off while its people
hold the citadel."""
DITCH_PERSON_DAYS = 3
MOAT_PERSON_DAYS = 6
STAKES_PERSON_DAYS = 1
STAKES_TIMBER = 2
"""Per section of the ring."""


def work_cost(work: DefenceWork, grade: WallGrade | None) -> tuple[dict[Resource, int], int]:
    """One piece of a work: its materials and person-days. A gatehouse is a tower of its
    section's grade; a ditch, a moat and stakes are dug or set per section of the ring; a
    citadel is four pieces of its grade, each a section raised from nothing."""
    if work is DefenceWork.DITCH:
        return {}, DITCH_PERSON_DAYS
    if work is DefenceWork.MOAT:
        return {}, MOAT_PERSON_DAYS
    if work is DefenceWork.STAKES:
        return {Resource.TIMBER: STAKES_TIMBER}, STAKES_PERSON_DAYS
    if grade is None:
        return {}, 0
    if work is DefenceWork.GATEHOUSE:
        spec = tower_spec(grade)
        return dict(spec.materials), spec.person_days
    return section_materials(None, grade), section_person_days(None, grade)


def battered_citadel(citadel: Citadel, hits: int) -> Citadel | None:
    """A citadel after catapult hits: it falls a grade as its strength runs out, and an
    earthwork citadel that falls is gone."""
    current: Citadel | None = citadel
    for _ in range(hits):
        if current is None:
            break
        strength = current.strength - WALL_HIT
        if strength > 0:
            current = current.model_copy(update={"strength": strength})
            continue
        below = rank(current.grade) - 1
        current = (
            None
            if below == 0
            else current.model_copy(
                update={
                    "grade": tuple(WallGrade)[below - 1],
                    "strength": WALL_GRADES[tuple(WallGrade)[below - 1]].strength,
                }
            )
        )
    return current


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


def auto_towers(ring: WallRing, count: int, taken: tuple[int, ...] = ()) -> tuple[int, ...]:
    """Where `count` more towers go when nobody says: the gates first, then each on the
    standing section farthest along the ring from the towers already placed (the lowest
    index on ties), so they spread evenly."""
    n = len(ring.sections)
    placed = list(taken)
    free = [
        index
        for index, item in enumerate(ring.sections)
        if item.grade is not None and index not in placed
    ]
    chosen: list[int] = []

    def gap(index: int) -> int:
        others = placed + chosen
        if not others:
            return n
        return min(min(abs(index - other), n - abs(index - other)) for other in others)

    for _ in range(count):
        options = [index for index in free if index not in chosen]
        if not options:
            break
        gates = [index for index in options if ring.sections[index].gate]
        pick = gates[0] if gates else max(options, key=lambda index: (gap(index), -index))
        chosen.append(pick)
    return tuple(chosen)


def tower_positions(ring: WallRing) -> tuple[int, ...]:
    """The sections the ring's towers stand on: where they were placed, or where they would
    have gone had nobody said."""
    if ring.tower_sections or not ring.towers:
        return ring.tower_sections
    return tuple(sorted(auto_towers(ring, ring.towers)))


TOWER_COVER_BP = 500
"""Rules version 3: a section a tower stands on, or stands beside, is that much harder."""
GATE_WEAKNESS_BP = 1_000
"""Rules version 3: what an unfortified gate takes off its section."""
STAKES_BP = 500
"""Rules version 3: what stakes add to every standing section in the next battle at home."""


def bonus_behind_ditch(grade: WallGrade, engines: dict[Resource, int], ditch: int) -> int:
    """A grade's bonus after the attackers' engines, behind a ditch or a moat.

    Without one, as `wall_bonus_after_engines`. Across a ditch a ram cannot reach the wall
    and serves only as ladders would: low walls lose half their worth, high ones nothing.
    Across a moat no ladder can be set, and a ram brought over does nothing to low walls and
    halves high ones.
    """
    if not ditch:
        return wall_bonus_after_engines(grade, engines)
    extra = WALL_GRADES[grade].defence_bp - BASIS
    high = grade in HIGH_WALLS
    ram = bool(engines.get(Resource.RAM))
    ladders = bool(engines.get(Resource.LADDER))
    if ditch >= MOAT:
        if ram and high:
            extra //= 2
    elif (ram or ladders) and not high:
        extra //= 2
    return BASIS + extra


def section_bonus(
    ring: WallRing, index: int, engines: dict[Resource, int], towers: frozenset[int]
) -> int:
    """One section's worth to the defenders: its grade's bonus after the attackers' engines
    (behind any ditch or moat), harder under a tower's cover and behind stakes, weaker at an
    unfortified gate; a gap is worth nothing."""
    section = ring.sections[index]
    if section.grade is None:
        return BASIS
    n = len(ring.sections)
    bonus = bonus_behind_ditch(section.grade, engines, ring.ditch)
    if {index, (index - 1) % n, (index + 1) % n} & towers:
        bonus += TOWER_COVER_BP
    if ring.stakes:
        bonus += STAKES_BP
    if section.gate and not section.gatehouse:
        bonus -= GATE_WEAKNESS_BP
    return max(bonus, BASIS)


def sheltered(ring: int, houses: int) -> tuple[int, int]:
    """Houses inside a ring of this radius and outside it."""
    inside = min(houses, SHELTERED_HOUSES[ring])
    return inside, houses - inside


def ring_defence_bp(
    ring: WallRing | None, engines: dict[Resource, int], houses: int, *, assault: bool = False
) -> int:
    """What the walls add to the home defence: each standing section its share of its grade's
    bonus (after the attackers' engines), scaled by the share of houses inside the ring.

    With `assault` (rules version 3 defence), the attackers also pick the weakest section,
    so the walls are worth the mean of the sections' worth and the weakest one's, halved.
    """
    if ring is None or not ring.built:
        return BASIS
    if assault:
        towers = frozenset(tower_positions(ring))
        worth = [section_bonus(ring, index, engines, towers) for index in range(len(ring.sections))]
        extra = (sum(worth) // len(worth) + min(worth)) // 2 - BASIS
    else:
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
    after = ring.model_copy(update={"sections": tuple(sections), "towers": 0, "tower_sections": ()})
    cap = tower_cap(after)
    if not ring.tower_sections:
        return after.model_copy(update={"towers": min(ring.towers, cap)}), hit
    # Placed towers on a fallen gap come down; beyond the cap, the highest-numbered go first.
    kept = [index for index in ring.tower_sections if sections[index].grade is not None][:cap]
    return after.model_copy(update={"towers": len(kept), "tower_sections": tuple(kept)}), hit


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
    if job.work is not None:
        return tuple(work_cost(job.work, grade)[1] for grade in job.section_grades)
    if job.towers:
        assert job.start_grade is not None
        return tuple(tower_spec(job.start_grade).person_days for _ in job.section_grades)
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


def piece_cost(job: WallJob, grade: WallGrade | None) -> dict[Resource, int]:
    """What one section's share of a ring job took from the store."""
    if job.towers:
        assert job.start_grade is not None
        return dict(tower_spec(job.start_grade).materials)
    if job.work is not None:
        return work_cost(job.work, grade)[0]
    if job.target is not None:
        return section_materials(grade, job.target)
    assert grade is not None
    return section_repair_materials(grade)


def unspent(job: WallJob) -> dict[Resource, int]:
    """What a ring job took for the work it has not yet done."""
    left: dict[Resource, int] = {}
    if job.sections:
        for grade in job.section_grades[sections_done(job) :]:
            cost = piece_cost(job, grade)
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
    named: tuple[int, ...] = (),
    work: DefenceWork | None = None,
    placed: bool = False,
) -> tuple[tuple[int, ...], tuple[WallGrade | None, ...], dict[Resource, int]]:
    """The sections a wall order works on, their grades now, and what it takes from the store.

    Raising takes the weakest sections first (`count` of them, or every one below the
    target); repair takes every damaged section; towers take no section. With `named`
    sections (rules version 3 defence), exactly those, in that order. With `placed`, towers
    are put on sections: the named ones, or where they would go by themselves. A gatehouse
    is raised on the named sections; a ditch, a moat or stakes round every section; a
    citadel in four pieces of `target`'s grade.
    """
    if work is DefenceWork.CITADEL:
        pieces = tuple(range(CITADEL_PIECES))
        costs = [work_cost(work, target)[0] for _ in pieces]
        return pieces, tuple(target for _ in pieces), _summed(costs)
    if work is not None and work is not DefenceWork.GATEHOUSE:
        every = tuple(range(len(ring.sections)))
        grades_now = tuple(item.grade for item in ring.sections)
        return every, grades_now, _summed([work_cost(work, grade)[0] for grade in grades_now])
    if named and (repair or target is not None or towers or work is not None):
        chosen = named
    elif repair:
        chosen = damaged_sections(ring)
    elif target is not None:
        chosen = sections_to_raise(ring, target, count)
    elif towers and placed:
        chosen = tuple(sorted(auto_towers(ring, towers, tower_positions(ring))))
    else:
        chosen = ()
    grades = tuple(ring.sections[index].grade for index in chosen)
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
    elif work is not None:
        costs = [work_cost(work, grade)[0] for grade in grades]
    elif repair:
        costs = [section_repair_materials(grade) for grade in grades if grade is not None]
    else:
        assert target is not None or not chosen
        costs = [section_materials(grade, target) for grade in grades if target is not None]
    return chosen, grades, _summed(costs)


def _summed(costs: list[dict[Resource, int]]) -> dict[Resource, int]:
    total: dict[Resource, int] = {}
    for cost in costs:
        for resource, quantity in cost.items():
            total[resource] = total.get(resource, 0) + quantity
    return {resource: total[resource] for resource in sorted(total) if total[resource]}
