"""Defensive works (rules version 3 defence): a ditch, a moat, stakes and a citadel."""

from types import SimpleNamespace

import pytest
from logistics_helpers import OneShotSovereign, clear_journey_id, treaty_world
from test_defence import _fighters
from test_defence_placement import (
    _builders,
    _gates_east_and_west,
    _messages,
    _order,
    _ring,
    _run,
    _state,
)

import sovereign_world.commands as commands
import sovereign_world.engine as engine
from sovereign_world.commands import DirectOrder, DirectOrderKind, build_council_report
from sovereign_world.engine import advance_day
from sovereign_world.gateway.prompt import defence_rule
from sovereign_world.hexmap import HexCoord
from sovereign_world.ids import EntityId
from sovereign_world.resources import Resource
from sovereign_world.rings import (
    MOAT,
    STAKES_BP,
    Citadel,
    battered_citadel,
    bonus_behind_ditch,
    ring_for,
    ring_work,
    section_bonus,
    set_ring,
)
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState, validate_world
from sovereign_world.stores import set_store, store_at, store_id_at
from sovereign_world.townplan import Place
from sovereign_world.walls import BASIS, WALL_GRADES, DefenceWork, WallGrade
from sovereign_world.war import WarObjective, resolve_battle

LOW, HIGH = WallGrade.PALISADE, WallGrade.MORTARED
RAM, LADDER = {Resource.RAM: 1}, {Resource.LADDER: 1}


def test_a_ditch_or_a_moat_changes_what_engines_do() -> None:
    # A palisade is worth 12,500 bare, a mortared wall 16,000.
    table = {
        # ditch: (ram on low, ram on high, ladders on low, ladders on high)
        0: (10_000, 13_000, 11_250, 16_000),
        1: (11_250, 16_000, 11_250, 16_000),
        MOAT: (12_500, 13_000, 12_500, 16_000),
    }
    for ditch, expected in table.items():
        got = (
            bonus_behind_ditch(LOW, RAM, ditch),
            bonus_behind_ditch(HIGH, RAM, ditch),
            bonus_behind_ditch(LOW, LADDER, ditch),
            bonus_behind_ditch(HIGH, LADDER, ditch),
        )
        assert got == expected, ditch
        assert bonus_behind_ditch(LOW, {}, ditch) == 12_500
    # On a ring the ditch and the stakes reach every standing section; a gap stays a gap.
    ring = _ring([LOW] * 9 + [None], gates=(0,), ditch=1, stakes=True)
    none: frozenset[int] = frozenset()
    assert section_bonus(ring, 2, RAM, none) == 11_250 + STAKES_BP
    assert section_bonus(ring, 9, RAM, none) == BASIS


def test_rings_without_works_dump_as_before() -> None:
    ring = _ring([LOW] * 10)
    dumped = ring.model_dump(mode="json")
    assert "ditch" not in dumped and "stakes" not in dumped
    worked = ring.model_copy(update={"ditch": MOAT, "stakes": True}).model_dump(mode="json")
    assert worked["ditch"] == 2 and worked["stakes"] is True
    state, home, _ = _state()
    assert "citadels" not in state.model_dump(mode="json")["civilizations"][home]
    with pytest.raises(ValueError):
        Citadel(grade=WallGrade.EARTHWORK, strength=11, built_day=0)


def test_works_are_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    state, home, sid = _state()
    workers = _builders(state, home)
    civilization = state.civilizations[home]
    _gates_east_and_west(state, home, sid)
    civilization.wall_rings = {sid: _ring([LOW] * 4 + [None] * 6)}

    def works(work: DefenceWork, **fields: object) -> list[str]:
        order = _order(DirectOrderKind.BUILD_WORKS, workers, work=work, **fields)
        return _messages(state, home, order)

    assert works(DefenceWork.DITCH) == [
        "invalid_works: a ditch is dug round a ring at least half built"
    ]
    assert works(DefenceWork.STAKES) == [
        "invalid_works: stakes are set round a ring at least half built"
    ]
    civilization.wall_rings = {sid: _ring([LOW] * 5 + [None] * 5)}
    assert works(DefenceWork.DITCH) == []
    assert works(DefenceWork.DITCH, section_ids=(1,)) == [
        "invalid_works: a ditch is not raised on named sections"
    ]
    assert works(DefenceWork.DITCH, wall_grade=LOW) == [
        "invalid_works: only a citadel names a grade"
    ]
    assert works(DefenceWork.MOAT) == ["invalid_works: a moat is flooded from a ditch"]
    civilization.wall_rings = {sid: _ring([LOW] * 5 + [None] * 5, ditch=1, stakes=True)}
    assert works(DefenceWork.DITCH) == ["invalid_works: this ring already has a ditch"]
    assert works(DefenceWork.STAKES) == ["invalid_works: stakes already stand round this ring"]
    monkeypatch.setattr(commands, "water_near", lambda *args: False)
    assert works(DefenceWork.MOAT) == [
        "invalid_works: a moat needs water on or beside the settlement"
    ]
    monkeypatch.setattr(commands, "water_near", lambda *args: True)
    assert works(DefenceWork.MOAT) == []
    civilization.wall_rings = {sid: _ring([LOW] * 5 + [None] * 5, ditch=MOAT)}
    assert works(DefenceWork.MOAT) == ["invalid_works: this ring already has a moat"]

    # Stakes need someone who knows timbercraft.
    people = civilization.population.people
    for person_id in workers:
        people[person_id].skills = {
            key: value for key, value in people[person_id].skills.items() if key != "timbercraft"
        }
    assert works(DefenceWork.STAKES) == [
        "unqualified_worker: this work needs someone who knows timbercraft"
    ]

    # A citadel: a grade, a keep at the centre, a complete ring, and only one.
    workers = _builders(state, home)
    _stock(state, home, stone=0)
    assert works(DefenceWork.CITADEL) == ["invalid_works: a citadel names the grade of its walls"]
    assert works(DefenceWork.CITADEL, wall_grade=LOW) == [
        "invalid_works: a citadel is raised round a keep at the centre"
    ]
    plan = civilization.town_plans[sid]
    civilization.town_plans = {sid: plan.model_copy(update={"keep": Place.CENTRE})}
    assert works(DefenceWork.CITADEL, wall_grade=LOW) == [
        "invalid_works: a citadel stands inside a complete ring of radius 2 or more"
    ]
    civilization.wall_rings = {sid: _ring([LOW] * 10)}
    assert works(DefenceWork.CITADEL, wall_grade=LOW) == []
    assert works(DefenceWork.CITADEL, wall_grade=WallGrade.DRYSTONE) == [
        "insufficient_materials: not enough stone for the walls"
    ]
    civilization.citadels = {sid: Citadel(grade=LOW, strength=25, built_day=0)}
    assert works(DefenceWork.CITADEL, wall_grade=LOW) == [
        "invalid_works: this settlement already has a citadel"
    ]


def test_what_works_cost() -> None:
    ring = _ring([LOW] * 6 + [None] * 4)

    def cost(work: DefenceWork, target: WallGrade | None = None):  # type: ignore[no-untyped-def]
        return ring_work(ring, target=target, repair=False, towers=0, count=None, work=work)

    pieces, grades, materials = cost(DefenceWork.DITCH)
    assert pieces == tuple(range(10)) and materials == {}
    assert grades == tuple(item.grade for item in ring.sections)
    assert cost(DefenceWork.STAKES)[2] == {Resource.TIMBER: 20}
    pieces, grades, materials = cost(DefenceWork.CITADEL, LOW)
    assert pieces == (0, 1, 2, 3) and grades == (LOW,) * 4
    assert materials == {Resource.TIMBER: 16}
    assert cost(DefenceWork.CITADEL, WallGrade.DRYSTONE)[2] == {
        Resource.TIMBER: 16,
        Resource.STONE: 32,
    }


def _stock(state: WorldState, home: EntityId, **quantities: int) -> None:
    civilization = state.civilizations[home]
    civilization.inventory = civilization.inventory.model_copy(
        update={
            "quantities": {
                **civilization.inventory.quantities,
                **{Resource(key): value for key, value in quantities.items()},
            }
        }
    )


def _walled_capital(**ring: object) -> tuple[WorldState, EntityId, EntityId]:
    """A capital with a keep at the centre inside a complete palisade ring, and timber."""
    state, home, sid = _state()
    civilization = state.civilizations[home]
    _gates_east_and_west(state, home, sid)
    plan = civilization.town_plans[sid]
    civilization.town_plans = {sid: plan.model_copy(update={"keep": Place.CENTRE})}
    civilization.wall_rings = {sid: _ring([LOW] * 10, **ring)}
    _stock(state, home, timber=500)
    return state, home, sid


def test_works_are_built_and_recorded() -> None:
    def build(work: DefenceWork, days: int, **fields: object):  # type: ignore[no-untyped-def]
        # Councils sit monthly, so each work is ordered at day 0 of its own world.
        state, home, sid = _walled_capital(ditch=1 if work is not DefenceWork.DITCH else 0)
        order = _order(DirectOrderKind.BUILD_WORKS, _builders(state, home), work=work, **fields)
        assert _messages(state, home, order) == []
        return (*_run(state, days, {home: OneShotSovereign(order)}), home, sid)

    # A ditch is 30 person-days for two builders; it is dug only when it is all done.
    state, events, home, sid = build(DefenceWork.DITCH, 16)
    [started] = [event for event in events if event.kind == "works_started"]
    assert started.payload == {"work": "ditch", "sections": ",".join(map(str, range(10)))}
    [built] = [event for event in events if event.kind == "works_built"]
    assert built.payload == {"work": "ditch"}
    assert state.civilizations[home].wall_rings[sid].ditch == 1
    assert not state.civilizations[home].wall_jobs
    # Stakes are 10 person-days and 20 timber; the ditch is kept.
    state, events, home, sid = build(DefenceWork.STAKES, 6)
    ring = state.civilizations[home].wall_rings[sid]
    assert ring.stakes and ring.ditch == 1
    assert state.civilizations[home].inventory.quantities[Resource.TIMBER] <= 480
    validate_world(state)
    # A palisade citadel is 36 person-days.
    state, events, home, sid = build(DefenceWork.CITADEL, 19, wall_grade=LOW)
    citadel = state.civilizations[home].citadels[sid]
    assert (citadel.grade, citadel.strength) == (LOW, WALL_GRADES[LOW].strength)
    assert 0 < citadel.built_day < state.day
    assert [event.payload for event in events if event.kind == "works_built"] == [
        {"work": "citadel"}
    ]
    assert build_council_report(state, home).citadels == {sid: citadel}
    validate_world(state)


def test_catapults_turn_on_the_citadel_once_no_wall_stands() -> None:
    citadel = Citadel(grade=LOW, strength=WALL_GRADES[LOW].strength, built_day=0)
    assert battered_citadel(citadel, 4) == citadel.model_copy(update={"strength": 5})
    assert battered_citadel(citadel, 5).grade is WallGrade.EARTHWORK  # type: ignore[union-attr]
    assert battered_citadel(citadel, 7) is None

    state, home, sid = _state()
    civilization = state.civilizations[home]
    _gates_east_and_west(state, home, sid)
    civilization.wall_rings = {sid: _ring([WallGrade.EARTHWORK] + [None] * 9)}
    civilization.citadels = {sid: citadel}
    [capital] = civilization.settlements
    siege = SimpleNamespace(
        defender_id=home,
        besieger_id=sorted(state.civilizations)[1],
        settlement_id=sid,
        settlement_tile=capital.tile,
    )
    # While a section stands, the walls take the hits.
    events = engine._bombard(state, siege, 2, None)  # type: ignore[arg-type]
    assert [event.kind for event in events] == ["wall_section_fell"]
    assert civilization.citadels[sid] == citadel
    events = engine._bombard(state, siege, 2, None)  # type: ignore[arg-type]
    assert [(event.kind, event.payload) for event in events] == [
        ("citadel_damaged", {"grade": "palisade", "strength": 15})
    ]
    events = engine._bombard(state, siege, 3, None)  # type: ignore[arg-type]
    assert [event.kind for event in events] == ["citadel_fell"]
    assert civilization.citadels[sid].grade is WallGrade.EARTHWORK
    engine._bombard(state, siege, 2, None)  # type: ignore[arg-type]
    assert sid not in civilization.citadels


def _raided(*, citadel: bool) -> tuple[WorldState, list, EntityId]:  # type: ignore[type-arg]
    """A strong raid on a staked town held by a handful of defenders."""
    state, home, rival, route = treaty_world(distance=4, rules_version=3)
    civilization = state.civilizations[rival]
    [capital] = civilization.settlements
    sid = capital.settlement_id
    plan = civilization.town_plans[sid]
    civilization.town_plans = {sid: plan.model_copy(update={"keep": Place.CENTRE})}
    ring = ring_for(civilization, sid, state.day)
    ring = ring.model_copy(
        update={
            "sections": tuple(
                item.model_copy(update={"grade": WallGrade.EARTHWORK, "strength": 10})
                for item in ring.sections
            ),
            "stakes": True,
        }
    )
    set_ring(civilization, ring)
    # A store small enough that the raiders could carry all of it off.
    held = store_at(civilization, capital.tile)
    set_store(
        civilization,
        store_id_at(civilization, capital.tile),
        held.model_copy(update={"quantities": {**held.quantities, Resource.FOOD: 1_000}}),
    )
    if citadel:
        civilization.citadels = {sid: Citadel(grade=WallGrade.EARTHWORK, strength=10, built_day=0)}
    people = civilization.population.people
    away = HexCoord(route[-1].q, route[-1].r - 3)
    for person_id in sorted(people)[4:]:
        people[person_id].location = away
    raid = DirectOrder(
        command_id="march",
        kind=DirectOrderKind.SEND_WAR_PARTY,
        journey_id=EntityId(clear_journey_id("raid", days=12)),
        recipient_civilization_id=rival,
        traveller_ids=state.civilizations[home].population.living_ids[:16],
        route=route,
        cargo={Resource.AXE: 8},
        war_objective=WarObjective.RAID,
    )
    rng = StableRng(state.config.seed)
    events = []
    sovereigns = {home: OneShotSovereign(raid)}
    for _ in range(12):
        result = advance_day(state, rng, sovereigns=sovereigns)
        state = result.state
        events.extend(result.events.events)
        if any(event.kind == "settlement_raided" for event in result.events.events):
            break
    return state, events, rival


def test_beaten_defenders_with_a_refuge_are_not_pursued() -> None:
    for seed in range(4):
        outcomes = [
            resolve_battle(
                _fighters("a", 30, 160),
                _fighters("d", 10, 100),
                defence_bp=10_000,
                attacker_morale_bp=2_000,
                defender_morale_bp=3_000,
                rng=StableRng(seed),
                stream="refuge",
                defender_refuge=refuge,
            )
            for refuge in (False, True)
        ]
        pursued, sheltered = outcomes
        assert pursued.attackers_won and sheltered.attackers_won
        assert pursued.captured and not sheltered.captured
        assert sheltered.rounds == pursued.rounds
        assert len(sheltered.casualties) <= len(pursued.casualties)


def test_stakes_are_spent_and_a_citadel_shelters_the_beaten_defenders() -> None:
    state, events, rival = _raided(citadel=True)
    [battle] = state.battles
    assert battle.winner_id != rival
    assert battle.captured == ()
    kinds = [event.kind for event in events]
    assert "stakes_cleared" in kinds and "fell_back_to_citadel" in kinds
    [capital] = state.civilizations[rival].settlements
    assert not state.civilizations[rival].wall_rings[capital.settlement_id].stakes
    # Raiders carried off at most half of what the store held of anything they took (the
    # food left is eaten into later that day, so it is compared with the bare raid below).
    [party] = [item for item in state.journeys if item.plunder]
    store = store_at(state.civilizations[rival], capital.tile).quantities
    assert party.plunder[Resource.TIMBER] and party.plunder[Resource.AXE]
    assert all(
        store.get(key, 0) >= taken for key, taken in party.plunder.items() if key != Resource.FOOD
    )
    validate_world(state)

    # Without the citadel the same raid carries off more of the store.
    bare, bare_events, _ = _raided(citadel=False)
    [bare_battle] = bare.battles
    assert bare_battle.winner_id != rival
    assert "fell_back_to_citadel" not in [event.kind for event in bare_events]
    bare_plunder = next(item for item in bare.journeys if item.plunder).plunder
    assert party.plunder[Resource.FOOD] < bare_plunder[Resource.FOOD]


def test_the_charter_tells_councils_of_the_works() -> None:
    rule = defence_rule()
    for words in ("ditch", "moat", "Stakes", "citadel", "16 timber and 36 person-days", "50%"):
        assert words in rule
