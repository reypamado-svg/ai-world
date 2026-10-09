"""Settlement and realm ranks (rules version 2).

A settlement grows from village to small town, town, big town and city as its people, its
houses and its works grow; the civilization as a whole grows from chiefdom to kingdom and
empire. Ranks are weighed at each monthly council and move one step at a time. A rank is
earned in full but kept down to 70% of its people and houses, so a bad winter does not
cost a town its name; losing a required work, craft or building does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from sovereign_world.capabilities import CapabilityId
from sovereign_world.culture import culture
from sovereign_world.housing import HouseGrade
from sovereign_world.ids import EntityId
from sovereign_world.institutions import SEAT, InstitutionKind, serving_tiles
from sovereign_world.rings import ring_grade
from sovereign_world.stores import StorehouseGrade
from sovereign_world.stores import rank as storehouse_rank
from sovereign_world.walls import WallGrade

if TYPE_CHECKING:
    from sovereign_world.state import CivilizationState, WorldState


class SettlementRank(StrEnum):
    VILLAGE = "village"
    SMALL_TOWN = "small_town"
    TOWN = "town"
    BIG_TOWN = "big_town"
    CITY = "city"


class RealmRank(StrEnum):
    CHIEFDOM = "chiefdom"
    KINGDOM = "kingdom"
    EMPIRE = "empire"


SETTLEMENT_ORDER = tuple(SettlementRank)
REALM_ORDER = tuple(RealmRank)
KEEP_SHARE_PCT = 70
"""A rank is kept while its people and houses stay at this share of what earned it."""
STONE_WALLS = frozenset({WallGrade.DRYSTONE, WallGrade.MORTARED, WallGrade.FORTRESS})


@dataclass(frozen=True, slots=True)
class SettlementRequirement:
    residents: int
    houses: int
    storehouse: StorehouseGrade | None = None
    """The least grade of its best storehouse."""
    walls: bool = False
    stone_walls: bool = False
    capabilities_any: frozenset[CapabilityId] = frozenset()
    capabilities_all: frozenset[CapabilityId] = frozenset()
    other_kinds: int = 0
    """Open institutions of different kinds besides the hall."""
    institutions_all: frozenset[InstitutionKind] = frozenset()
    stone_house_share_pct: int = 0
    """The least share of its houses that are stone houses."""


SETTLEMENT_RANKS: dict[SettlementRank, SettlementRequirement] = {
    SettlementRank.SMALL_TOWN: SettlementRequirement(
        residents=300,
        houses=60,
        storehouse=StorehouseGrade.STOREHOUSE,
        capabilities_any=frozenset({CapabilityId.TIMBERCRAFT, CapabilityId.STONEWORKING}),
    ),
    SettlementRank.TOWN: SettlementRequirement(
        residents=1_000,
        houses=200,
        storehouse=StorehouseGrade.STOREHOUSE,
        walls=True,
        capabilities_all=frozenset({CapabilityId.WRITING}),
        other_kinds=1,
    ),
    SettlementRank.BIG_TOWN: SettlementRequirement(
        residents=3_000,
        houses=600,
        storehouse=StorehouseGrade.STOREHOUSE,
        walls=True,
        capabilities_all=frozenset({CapabilityId.WRITING}),
        capabilities_any=frozenset({CapabilityId.SURVEYING, CapabilityId.ORGANIZED_LOGISTICS}),
        other_kinds=2,
        stone_house_share_pct=25,
    ),
    SettlementRank.CITY: SettlementRequirement(
        residents=8_000,
        houses=1_600,
        storehouse=StorehouseGrade.WAREHOUSE,
        walls=True,
        stone_walls=True,
        capabilities_all=frozenset({CapabilityId.WRITING, CapabilityId.ORGANIZED_LOGISTICS}),
        other_kinds=3,
        institutions_all=frozenset({InstitutionKind.ARCHIVE}),
        stone_house_share_pct=25,
    ),
}
"""What each rank above village needs, besides a standing hall."""


@dataclass(frozen=True, slots=True)
class RealmRequirement:
    settlements: int
    least_rank: SettlementRank
    """At least one settlement of this rank or above."""
    people: int
    tiles: int
    capabilities_all: frozenset[CapabilityId] = frozenset()
    institutions_any: frozenset[InstitutionKind] = field(default_factory=frozenset)
    rule_over_others: bool = False


REALM_RANKS: dict[RealmRank, RealmRequirement] = {
    RealmRank.KINGDOM: RealmRequirement(
        settlements=3,
        least_rank=SettlementRank.TOWN,
        people=2_000,
        tiles=40,
        capabilities_all=frozenset({CapabilityId.WRITING}),
        institutions_any=frozenset({InstitutionKind.ARCHIVE, InstitutionKind.DIPLOMATIC_SERVICE}),
    ),
    RealmRank.EMPIRE: RealmRequirement(
        settlements=8,
        least_rank=SettlementRank.CITY,
        people=20_000,
        tiles=150,
        capabilities_all=frozenset({CapabilityId.ORGANIZED_LOGISTICS}),
        rule_over_others=True,
    ),
}
STOREHOUSE_RANK: dict[StorehouseGrade, SettlementRank] = {
    StorehouseGrade.WAREHOUSE: SettlementRank.SMALL_TOWN,
    StorehouseGrade.DEPOT: SettlementRank.TOWN,
}
"""The rank a settlement needs before it can raise a storehouse to this grade."""
TOLL_RANK = SettlementRank.SMALL_TOWN
"""A toll's takings go to a settlement of at least this rank."""
INSTITUTION_RANK: dict[InstitutionKind, SettlementRank] = {
    InstitutionKind.ARMOURY: SettlementRank.SMALL_TOWN,
    InstitutionKind.TRAINING_GROUNDS: SettlementRank.SMALL_TOWN,
}
"""Buildings a settlement can found only from this rank."""
INSTITUTION_SLOTS: dict[SettlementRank, int | None] = {
    SettlementRank.VILLAGE: 1,
    SettlementRank.SMALL_TOWN: 3,
    SettlementRank.TOWN: 4,
    SettlementRank.BIG_TOWN: 6,
    SettlementRank.CITY: None,
}
"""How many institutions besides its hall a settlement can keep; a city, any number."""
WAR_PARTY_LIMIT: dict[RealmRank, int] = {
    RealmRank.CHIEFDOM: 16,
    RealmRank.KINGDOM: 24,
    RealmRank.EMPIRE: 32,
}
"""The most fighters one war party can hold."""
TRIBUTE_RANK = RealmRank.KINGDOM
"""Only a realm of this rank can demand tribute in its peace terms."""
CIVIL_HALF_RATE_RANK = RealmRank.KINGDOM
"""From this realm rank, civil research without an open school or archive goes at half pace."""
WRITING_RANK = SettlementRank.SMALL_TOWN
"""Writing is worked out only where a settlement has grown to this rank."""
CITY_RESEARCH_BONUS = 1
"""Extra research points a day for each scholar working in a city."""
FOREIGN_SHARE_PCT = 10
"""Subjects of another culture making up this share count as rule over other peoples."""


def at_least(rank: SettlementRank, floor: SettlementRank) -> bool:
    return SETTLEMENT_ORDER.index(rank) >= SETTLEMENT_ORDER.index(floor)


def realm_at_least(rank: RealmRank, floor: RealmRank) -> bool:
    return REALM_ORDER.index(rank) >= REALM_ORDER.index(floor)


def settlement_rank(civilization: CivilizationState, settlement_id: EntityId) -> SettlementRank:
    return civilization.ranks_reached.get(settlement_id, SettlementRank.VILLAGE)


@dataclass(frozen=True, slots=True)
class SettlementFacts:
    """What a settlement has, as the ranks weigh it."""

    residents: int
    houses: int
    stone_houses: int
    storehouse: StorehouseGrade | None
    walls: WallGrade | None
    open_kinds: frozenset[InstitutionKind]


def settlement_facts(
    civilization: CivilizationState,
    settlement_id: EntityId,
    residents: int,
    open_at: dict[InstitutionKind, set[EntityId]],
) -> SettlementFacts:
    housing = civilization.housing.get(settlement_id)
    grades = [
        item.grade for item in civilization.storehouses if item.settlement_id == settlement_id
    ]
    walls = next(
        (item.grade for item in civilization.walls if item.settlement_id == settlement_id),
        ring_grade(civilization.wall_rings.get(settlement_id)),
    )
    return SettlementFacts(
        residents=residents,
        houses=0 if housing is None else housing.count,
        stone_houses=0 if housing is None else housing.houses.get(HouseGrade.STONE_HOUSE, 0),
        storehouse=max(grades, key=storehouse_rank, default=None),
        walls=walls,
        open_kinds=frozenset(kind for kind, places in open_at.items() if settlement_id in places),
    )


def meets(
    requirement: SettlementRequirement,
    facts: SettlementFacts,
    known: frozenset[CapabilityId],
    *,
    keeping: bool,
) -> bool:
    """Whether a settlement earns (or, `keeping`, still holds) a rank."""
    share = KEEP_SHARE_PCT if keeping else 100
    if facts.residents * 100 < requirement.residents * share:
        return False
    if facts.houses * 100 < requirement.houses * share:
        return False
    if SEAT not in facts.open_kinds:
        return False
    if requirement.storehouse is not None and storehouse_rank(facts.storehouse) < storehouse_rank(
        requirement.storehouse
    ):
        return False
    if requirement.walls and facts.walls is None:
        return False
    if requirement.stone_walls and facts.walls not in STONE_WALLS:
        return False
    if requirement.capabilities_any and not requirement.capabilities_any & known:
        return False
    if not requirement.capabilities_all <= known:
        return False
    if len(facts.open_kinds - {SEAT}) < requirement.other_kinds:
        return False
    if not requirement.institutions_all <= facts.open_kinds:
        return False
    return facts.stone_houses * 100 >= requirement.stone_house_share_pct * max(facts.houses, 1)


def next_settlement_rank(
    current: SettlementRank, facts: SettlementFacts, known: frozenset[CapabilityId]
) -> SettlementRank:
    """One step up if the next rank is earned, one down if this one is no longer held."""
    index = SETTLEMENT_ORDER.index(current)
    if index + 1 < len(SETTLEMENT_ORDER):
        above = SETTLEMENT_ORDER[index + 1]
        if meets(SETTLEMENT_RANKS[above], facts, known, keeping=False):
            return above
    if current is not SettlementRank.VILLAGE and not meets(
        SETTLEMENT_RANKS[current], facts, known, keeping=True
    ):
        return SETTLEMENT_ORDER[index - 1]
    return current


def institutions_open(
    civilization: CivilizationState, away: set[EntityId]
) -> dict[InstitutionKind, set[EntityId]]:
    """For each kind, the settlements where one is open."""
    tiles_to_settlement = {item.tile: item.settlement_id for item in civilization.settlements}
    return {
        kind: {
            tiles_to_settlement[tile]
            for tile in serving_tiles(civilization, kind, away)
            if tile in tiles_to_settlement
        }
        for kind in InstitutionKind
    }


def rules_over_others(state: WorldState, civilization_id: EntityId) -> bool:
    """Tribute received, a settlement occupied or ceded to it, or many foreign subjects."""
    civilization = state.civilizations[civilization_id]
    own = {item.settlement_id for item in civilization.settlements}
    for treaty in state.active_treaties:
        terms = treaty.terms
        if terms is None or civilization_id not in {
            treaty.proposer_civilization_id,
            treaty.recipient_civilization_id,
        }:
            continue
        if treaty.in_force and terms.tribute_payer == treaty.counterparty(civilization_id):
            return True
        if terms.ceded_settlement in own:
            return True
    if any(
        item.ended_day is None and item.occupier_id == civilization_id for item in state.occupations
    ):
        return True
    free = [
        person
        for person in civilization.population.people.values()
        if person.alive and person.captive_of is None
    ]
    foreign = sum(culture(person) != civilization_id for person in free)
    return bool(free) and foreign * 100 >= FOREIGN_SHARE_PCT * len(free)


def realm_meets(
    requirement: RealmRequirement,
    state: WorldState,
    civilization_id: EntityId,
    settlement_ranks: dict[EntityId, SettlementRank],
    open_at: dict[InstitutionKind, set[EntityId]],
    *,
    keeping: bool,
) -> bool:
    civilization = state.civilizations[civilization_id]
    share = KEEP_SHARE_PCT if keeping else 100
    known = frozenset(record.capability for record in civilization.capabilities)
    if len(civilization.settlements) * 100 < requirement.settlements * share:
        return False
    if not any(at_least(rank, requirement.least_rank) for rank in settlement_ranks.values()):
        return False
    people = sum(
        person.alive and person.captive_of is None
        for person in civilization.population.people.values()
    )
    if people * 100 < requirement.people * share:
        return False
    tiles = sum(item.civilization_id == civilization_id for item in state.territory.owners)
    if tiles * 100 < requirement.tiles * share:
        return False
    if not requirement.capabilities_all <= known:
        return False
    if requirement.institutions_any and not any(
        open_at.get(kind) for kind in requirement.institutions_any
    ):
        return False
    # Rule over other peoples raises an empire; once ruled, it stays history.
    return keeping or not requirement.rule_over_others or rules_over_others(state, civilization_id)


def next_realm_rank(
    current: RealmRank,
    state: WorldState,
    civilization_id: EntityId,
    settlement_ranks: dict[EntityId, SettlementRank],
    open_at: dict[InstitutionKind, set[EntityId]],
) -> RealmRank:
    index = REALM_ORDER.index(current)
    if index + 1 < len(REALM_ORDER):
        above = REALM_ORDER[index + 1]
        if realm_meets(
            REALM_RANKS[above], state, civilization_id, settlement_ranks, open_at, keeping=False
        ):
            return above
    if current is not RealmRank.CHIEFDOM and not realm_meets(
        REALM_RANKS[current], state, civilization_id, settlement_ranks, open_at, keeping=True
    ):
        return REALM_ORDER[index - 1]
    return current
