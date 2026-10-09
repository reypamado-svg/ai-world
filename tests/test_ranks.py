"""Settlement and realm ranks (rules version 2)."""

from dataclasses import replace

import pytest

import sovereign_world.ranks as ranks
from sovereign_world.capabilities import CapabilityId, CapabilityRecord
from sovereign_world.commands import build_council_report
from sovereign_world.config import RunManifest, WorldConfig
from sovereign_world.diplomacy import ActiveTreaty, PeaceTerms, TreatyEndKind, TreatyKind
from sovereign_world.engine import TransitionResult, advance_day
from sovereign_world.housing import HouseGrade, Housing
from sovereign_world.ids import EntityId
from sovereign_world.institutions import Institution, InstitutionKind
from sovereign_world.ranks import (
    RealmRank,
    SettlementFacts,
    SettlementRank,
    next_settlement_rank,
    rules_over_others,
)
from sovereign_world.resources import Resource
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState, build_initial_state, validate_world
from sovereign_world.stores import StorehouseGrade
from sovereign_world.walls import WallGrade
from sovereign_world.war import Occupation

CONFIG = WorldConfig(seed=9, width=24, height=24)
CRAFTS = frozenset({CapabilityId.TIMBERCRAFT})


def _state(rules_version: int = 2) -> WorldState:
    return build_initial_state(RunManifest.new(CONFIG, "0.1.0", rules_version=rules_version))


def _run(state: WorldState, days: int) -> tuple[WorldState, list[TransitionResult]]:
    rng = StableRng(state.config.seed)
    results = []
    for _ in range(days):
        result = advance_day(state, rng)
        state = result.state
        results.append(result)
    return state, results


def _events(results, kind: str):
    return [event for result in results for event in result.events.events if event.kind == kind]


def _facts(**update) -> SettlementFacts:
    base = SettlementFacts(
        residents=300,
        houses=60,
        stone_houses=0,
        storehouse=StorehouseGrade.STOREHOUSE,
        walls=None,
        open_kinds=frozenset({InstitutionKind.HALL}),
    )
    return replace(base, **update)


def test_a_village_becomes_a_small_town_with_people_houses_a_hall_storehouse_and_craft() -> None:
    village = SettlementRank.VILLAGE
    assert next_settlement_rank(village, _facts(), CRAFTS) is SettlementRank.SMALL_TOWN
    assert next_settlement_rank(village, _facts(residents=299), CRAFTS) is village
    assert next_settlement_rank(village, _facts(houses=59), CRAFTS) is village
    assert next_settlement_rank(village, _facts(open_kinds=frozenset()), CRAFTS) is village
    granary = _facts(storehouse=StorehouseGrade.GRANARY)
    assert next_settlement_rank(village, granary, CRAFTS) is village
    assert next_settlement_rank(village, _facts(), frozenset()) is village


def test_a_rank_is_kept_down_to_seventy_percent_and_lost_one_step_at_a_time() -> None:
    small = SettlementRank.SMALL_TOWN
    assert next_settlement_rank(small, _facts(residents=225, houses=45), CRAFTS) is small
    assert next_settlement_rank(small, _facts(residents=195), CRAFTS) is SettlementRank.VILLAGE
    # Losing the hall loses the rank.
    hall_gone = _facts(open_kinds=frozenset())
    assert next_settlement_rank(small, hall_gone, CRAFTS) is SettlementRank.VILLAGE
    # A city that falls apart drops a single step per council.
    assert next_settlement_rank(SettlementRank.CITY, _facts(), CRAFTS) is SettlementRank.BIG_TOWN


def test_higher_ranks_need_walls_writing_institutions_and_stone() -> None:
    known = frozenset(
        {CapabilityId.TIMBERCRAFT, CapabilityId.WRITING, CapabilityId.ORGANIZED_LOGISTICS}
    )
    town = _facts(
        residents=1_000,
        houses=200,
        walls=WallGrade.PALISADE,
        open_kinds=frozenset({InstitutionKind.HALL, InstitutionKind.WORKSHOP}),
    )
    small = SettlementRank.SMALL_TOWN
    assert next_settlement_rank(small, town, known) is SettlementRank.TOWN
    assert next_settlement_rank(small, replace(town, walls=None), known) is small
    assert next_settlement_rank(small, town, CRAFTS) is small
    only_hall = replace(town, open_kinds=frozenset({InstitutionKind.HALL}))
    assert next_settlement_rank(small, only_hall, known) is small

    city = _facts(
        residents=8_000,
        houses=1_600,
        stone_houses=400,
        storehouse=StorehouseGrade.WAREHOUSE,
        walls=WallGrade.DRYSTONE,
        open_kinds=frozenset(
            {
                InstitutionKind.HALL,
                InstitutionKind.ARCHIVE,
                InstitutionKind.SCHOOL,
                InstitutionKind.WORKSHOP,
            }
        ),
    )
    big = SettlementRank.BIG_TOWN
    assert next_settlement_rank(big, city, known) is SettlementRank.CITY
    assert next_settlement_rank(big, replace(city, walls=WallGrade.PALISADE), known) is big
    # Below a quarter stone houses, a big town cannot rise, and falls back a step.
    short_of_stone = replace(city, stone_houses=300)
    assert next_settlement_rank(big, short_of_stone, known) is SettlementRank.TOWN
    no_archive = replace(
        city,
        open_kinds=frozenset(
            {
                InstitutionKind.HALL,
                InstitutionKind.HEALERS_HOUSE,
                InstitutionKind.SCHOOL,
                InstitutionKind.WORKSHOP,
            }
        ),
    )
    assert next_settlement_rank(big, no_archive, known) is big


def _small_town_ready(state: WorldState, monkeypatch: pytest.MonkeyPatch) -> EntityId:
    """Make the first capital meet a small town's needs, at a size a test world can reach."""
    monkeypatch.setitem(
        ranks.SETTLEMENT_RANKS,
        SettlementRank.SMALL_TOWN,
        replace(ranks.SETTLEMENT_RANKS[SettlementRank.SMALL_TOWN], residents=30, houses=6),
    )
    home = sorted(state.civilizations)[0]
    civilization = state.civilizations[home]
    [capital] = civilization.settlements
    civilization.storehouses = tuple(
        item.model_copy(update={"grade": StorehouseGrade.STOREHOUSE})
        for item in civilization.storehouses
    )
    civilization.capabilities = tuple(
        sorted(
            {
                *civilization.capabilities,
                CapabilityRecord(
                    capability=CapabilityId.STONEWORKING, practitioner_ids=(), discovered_day=0
                ),
            },
            key=lambda record: record.capability.value,
        )
    )
    clerk = civilization.population.living_ids[0]
    civilization.institutions = (
        Institution(
            institution_id=EntityId("institution:hall"),
            kind=InstitutionKind.HALL,
            settlement_id=capital.settlement_id,
            tile=capital.tile,
            staff_ids=(clerk,),
            founded_day=0,
            person_days_done=20,
            opened_day=0,
        ),
    )
    return home


def test_ranks_rise_at_the_monthly_council(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _state()
    home = _small_town_ready(state, monkeypatch)
    [capital] = state.civilizations[home].settlements
    # Day 0 is a council day: the rank is weighed and rises at once.
    state, results = _run(state, 1)
    [changed] = _events(results, "settlement_rank_changed")
    assert changed.payload == {"rank": "small_town", "previous": "village"}
    assert state.civilizations[home].ranks_reached == {
        capital.settlement_id: SettlementRank.SMALL_TOWN
    }
    report = build_council_report(state, home)
    assert report.ranks == {capital.settlement_id: SettlementRank.SMALL_TOWN}
    assert report.realm_rank is RealmRank.CHIEFDOM
    dumped = state.model_dump(mode="json")["civilizations"][home]
    assert dumped["ranks_reached"] == {capital.settlement_id: "small_town"}
    assert "realm_rank_reached" not in dumped, "a chiefdom is left out"
    validate_world(state)

    # Without the hall's clerk the hall is shut, and at the next council the rank is lost.
    civilization = state.civilizations[home]
    civilization.institutions = tuple(
        item.model_copy(update={"staff_ids": ()}) for item in civilization.institutions
    )
    state, results = _run(state, 29)
    assert _events(results, "settlement_rank_changed") == [], "weighed only on council days"
    state, results = _run(state, 1)
    [lost] = _events(results, "settlement_rank_changed")
    assert lost.payload == {"rank": "village", "previous": "small_town"}
    assert state.civilizations[home].ranks_reached == {}


def test_older_worlds_have_no_ranks(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _state(1)
    home = _small_town_ready(state, monkeypatch)
    state, results = _run(state, 31)
    assert _events(results, "settlement_rank_changed") == []
    report = build_council_report(state, home)
    assert report.ranks == {} and report.realm_rank is None
    assert {"ranks", "realm_rank"}.isdisjoint(report.model_dump(mode="json"))


def test_a_kingdom_needs_settlements_people_land_writing_and_an_archive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state()
    home = sorted(state.civilizations)[0]
    civilization = state.civilizations[home]
    [capital] = civilization.settlements
    tiles = sum(item.civilization_id == home for item in state.territory.owners)
    monkeypatch.setitem(
        ranks.REALM_RANKS,
        RealmRank.KINGDOM,
        replace(
            ranks.REALM_RANKS[RealmRank.KINGDOM],
            settlements=1,
            people=30,
            tiles=max(tiles, 1),
        ),
    )
    everyone = {capital.settlement_id: SettlementRank.TOWN}
    archive = {kind: set() for kind in InstitutionKind}
    archive[InstitutionKind.ARCHIVE] = {capital.settlement_id}
    known_writing = (
        *civilization.capabilities,
        CapabilityRecord(capability=CapabilityId.WRITING, practitioner_ids=(), discovered_day=0),
    )
    chiefdom = RealmRank.CHIEFDOM
    state, _ = _run(state, 1)  # territory is drawn on the first day
    tiles = sum(item.civilization_id == home for item in state.territory.owners)
    monkeypatch.setitem(
        ranks.REALM_RANKS,
        RealmRank.KINGDOM,
        replace(ranks.REALM_RANKS[RealmRank.KINGDOM], tiles=tiles),
    )
    assert ranks.next_realm_rank(chiefdom, state, home, everyone, archive) is chiefdom
    state.civilizations[home].capabilities = known_writing
    assert ranks.next_realm_rank(chiefdom, state, home, everyone, archive) is RealmRank.KINGDOM
    villages = {capital.settlement_id: SettlementRank.SMALL_TOWN}
    assert ranks.next_realm_rank(chiefdom, state, home, villages, archive) is chiefdom
    nothing = {kind: set() for kind in InstitutionKind}
    assert ranks.next_realm_rank(chiefdom, state, home, everyone, nothing) is chiefdom


def test_rule_over_other_peoples() -> None:
    state = _state()
    ruler, other = sorted(state.civilizations)[:2]
    assert not rules_over_others(state, ruler)

    tribute = ActiveTreaty(
        treaty_id=EntityId("treaty:peace"),
        proposer_civilization_id=ruler,
        recipient_civilization_id=other,
        kind=TreatyKind.PEACE,
        offered_day=0,
        activated_day=0,
        terms=PeaceTerms(
            truce_days=60,
            tribute_payer=other,
            tribute={Resource.FOOD: 10},
            tribute_payments=3,
        ),
    )
    state.active_treaties = (tribute,)
    assert rules_over_others(state, ruler)
    assert not rules_over_others(state, other), "paying tribute is not ruling"
    ended = tribute.model_copy(
        update={"ended_day": 5, "end_kind": TreatyEndKind.CANCELLED, "ended_by": other}
    )
    state.active_treaties = (ended,)
    assert not rules_over_others(state, ruler)
    state.active_treaties = ()

    [held] = state.civilizations[other].settlements
    state.occupations = (
        Occupation(
            occupation_id=EntityId("occupation:1"),
            journey_id=EntityId("journey:1"),
            occupier_id=ruler,
            owner_id=other,
            settlement_id=held.settlement_id,
            tile=held.tile,
            started_day=0,
        ),
    )
    assert rules_over_others(state, ruler)
    state.occupations = ()

    people = state.civilizations[ruler].population.people
    for person_id in state.civilizations[ruler].population.living_ids[:4]:
        people[person_id].culture = other
    assert rules_over_others(state, ruler), "4 of 32 (12%) live by another culture"
    people[state.civilizations[ruler].population.living_ids[0]].culture = None
    assert not rules_over_others(state, ruler), "3 of 32 is under 10%"


def test_ranks_stand_only_in_their_own_settlements() -> None:
    state, _ = _run(_state(), 1)
    first, second = sorted(state.civilizations)[:2]
    [foreign] = state.civilizations[second].settlements
    state.civilizations[first].ranks_reached = {foreign.settlement_id: SettlementRank.TOWN}
    with pytest.raises(ValueError, match="own settlements"):
        validate_world(state)
    [own] = state.civilizations[first].settlements
    state.civilizations[first].ranks_reached = {own.settlement_id: SettlementRank.VILLAGE}
    with pytest.raises(ValueError, match="own settlements"):
        validate_world(state)


def test_houses_built_count_toward_rank() -> None:
    facts = ranks.settlement_facts(
        _state().civilizations[sorted(_state().civilizations)[0]],
        EntityId("settlement:none"),
        0,
        {},
    )
    assert facts.houses == 0 and facts.storehouse is None
    state = _state()
    home = sorted(state.civilizations)[0]
    [capital] = state.civilizations[home].settlements
    state.civilizations[home].housing = {
        capital.settlement_id: Housing(houses={HouseGrade.HUT: 4, HouseGrade.STONE_HOUSE: 2})
    }
    facts = ranks.settlement_facts(state.civilizations[home], capital.settlement_id, 32, {})
    assert facts.houses == 6 and facts.stone_houses == 2
