"""Standing defence orders (rules version 3): who stands in the line, a reserve, tower crews,
arms to veterans, and a store that must feed the defenders."""

import pytest
from logistics_helpers import OneShotSovereign, clear_journey_id, envelope, treaty_world
from pydantic import ValidationError

import sovereign_world.engine as engine
from sovereign_world.commands import (
    DirectOrder,
    DirectOrderKind,
    build_council_report,
    validate_envelope,
)
from sovereign_world.defence import (
    CRAFT_SKILLS,
    MIN_LINE,
    Arms,
    Crews,
    DefenceOrder,
    DefenceOrderSpec,
    Posture,
    is_craftsman,
)
from sovereign_world.engine import advance_day
from sovereign_world.gateway.prompt import PROMPT_VERSION, charter, defence_rule
from sovereign_world.hexmap import HexCoord
from sovereign_world.ids import EntityId
from sovereign_world.resources import Resource
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState, validate_world
from sovereign_world.war import ARMS, Fighter, WarObjective, morale_bp, resolve_battle


def _fighters(side: str, count: int, strength: int) -> list[Fighter]:
    return [
        Fighter(
            person_id=EntityId(f"person:{side}-{index:03d}"),
            civilization_id=EntityId(f"civilization:{side}"),
            strength=strength,
            health_bp=10_000,
            veteran=False,
            hungry=False,
        )
        for index in range(count)
    ]


def _battle(**extra: object):  # type: ignore[no-untyped-def]
    return resolve_battle(
        _fighters("a", 20, 120),
        _fighters("d", 12, 100),
        defence_bp=12_500,
        attacker_morale_bp=2_000,
        defender_morale_bp=3_000,
        rng=StableRng(5),
        stream="test",
        **extra,  # type: ignore[arg-type]
    )


def test_the_new_battle_hooks_change_nothing_unless_used() -> None:
    assert _battle(towers=2) == _battle(towers=2, tower_hits_bp=(1_500, 1_500))
    assert _battle() == _battle(defender_reserve=(), reserve_joins_round=3)
    assert _battle(towers=2).reserve_round == 0


def test_a_reserve_waits_and_then_steadies_the_line() -> None:
    reserve = tuple(_fighters("r", 4, 100))
    strong = resolve_battle(
        _fighters("a", 30, 160),
        _fighters("d", 10, 100),
        defence_bp=10_000,
        attacker_morale_bp=2_000,
        defender_morale_bp=3_000,
        rng=StableRng(7),
        stream="reserve",
        defender_reserve=reserve,
        reserve_joins_round=3,
    )
    assert 0 < strong.reserve_round <= 3
    # Against a weak party the line holds and the reserve is never needed, nor hurt.
    weak = resolve_battle(
        _fighters("a", 4, 60),
        _fighters("d", 10, 100),
        defence_bp=12_500,
        attacker_morale_bp=2_000,
        defender_morale_bp=3_000,
        rng=StableRng(7),
        stream="reserve",
        defender_reserve=reserve,
        reserve_joins_round=9,
    )
    assert not weak.attackers_won and weak.reserve_round == 0
    held = {item.person_id for item in reserve}
    assert not [item for item in weak.casualties if item.person_id in held]


def test_morale_falls_when_the_store_cannot_feed_the_defenders() -> None:
    line = _fighters("d", 10, 100)
    assert morale_bp(line, at_home=True, supplied=False) < morale_bp(
        line, at_home=True, supplied=True
    )


def test_the_defence_order_is_checked() -> None:
    spec = DefenceOrderSpec(posture=Posture.FIGHTERS, reserve_bp=1_000)
    assert DefenceOrderSpec() == DefenceOrderSpec.model_validate({})
    for bad in ({"reserve_bp": 5_001}, {"posture": "hide"}, {"towers": 2}):
        with pytest.raises(ValidationError):
            DefenceOrderSpec.model_validate(bad)
    state, home, rival, _ = treaty_world(distance=4, rules_version=3)
    [capital] = state.civilizations[home].settlements
    [foreign] = state.civilizations[rival].settlements

    def order(settlement_id: EntityId | None, command_id: str = "defend") -> DirectOrder:
        return DirectOrder(
            command_id=command_id,
            kind=DirectOrderKind.SET_DEFENCE,
            settlement_id=settlement_id,
            defence=spec,
        )

    def codes(*orders: DirectOrder) -> list[str]:
        result = validate_envelope(envelope(state, home, *orders), state)
        return [f"{item.code}: {item.message}" for item in result.errors]

    assert codes(order(capital.settlement_id)) == []
    assert codes(order(foreign.settlement_id)) == [
        "invalid_defence: only this civilization's own settlements are defended by its orders"
    ]
    assert codes(order(None)) == [
        "invalid_defence: a defence order names its settlement and gives its defence"
    ]
    assert codes(order(capital.settlement_id), order(capital.settlement_id, "again")) == [
        "invalid_defence: a settlement's defence is set once a council"
    ]
    older, older_home, _, _ = treaty_world(distance=4, rules_version=2)
    [older_capital] = older.civilizations[older_home].settlements
    result = validate_envelope(
        envelope(older, older_home, order(older_capital.settlement_id)), older
    )
    assert [item.code for item in result.errors] == ["invalid_defence"]
    assert (
        "defence" not in DirectOrder(command_id="x", kind=DirectOrderKind.ASSIGN_WORK).model_dump()
    )

    # The engine records it; the council's report shows it; it moves to no new owner.
    after = advance_day(
        state,
        StableRng(state.config.seed),
        sovereigns={home: OneShotSovereign(order(capital.settlement_id))},
    )
    recorded = after.state.civilizations[home].defence_orders[capital.settlement_id]
    assert recorded.spec() == spec and recorded.set_day == state.day
    [event] = [item for item in after.events.events if item.kind == "defence_set"]
    assert event.payload == {
        "posture": "fighters",
        "reserve_bp": 1_000,
        "tower_crews": "any",
        "arms_priority": "any",
    }
    assert build_council_report(after.state, home).defence_orders == {
        capital.settlement_id: recorded
    }
    validate_world(after.state)


def _set(state: WorldState, civilization_id: EntityId, **spec: object) -> EntityId:
    [capital] = [item for item in state.civilizations[civilization_id].settlements if item.capital]
    order = DefenceOrder(
        **DefenceOrderSpec.model_validate(spec).model_dump(),
        settlement_id=capital.settlement_id,
        set_day=state.day,
    )
    state.civilizations[civilization_id].defence_orders = {capital.settlement_id: order}
    return capital.settlement_id


def _no_crafts(civilization) -> None:  # type: ignore[no-untyped-def]
    """Founders of some peoples all know a craft; start from nobody knowing one."""
    civilization.capabilities = tuple(
        record
        for record in civilization.capabilities
        if record.capability.value not in CRAFT_SKILLS
    )
    for person in civilization.population.people.values():
        person.skills = {
            key: value for key, value in person.skills.items() if key not in CRAFT_SKILLS
        }


def test_the_order_decides_the_line_the_reserve_and_who_stays_back() -> None:
    state, _, rival, _ = treaty_world(distance=4, rules_version=3)
    civilization = state.civilizations[rival]
    [capital] = civilization.settlements
    home_side, _ = engine._defenders(state, rival, capital.tile)
    people = civilization.population.people
    _no_crafts(civilization)
    plain = engine._arrange_defence(state, rival, capital.tile, home_side)
    assert plain[1:] == (home_side, [], [])

    # Two practised fighters are too few: everyone fights after all; four are enough.
    for person_id in home_side[:2]:
        people[person_id].skills = {**people[person_id].skills, ARMS: 12}
    _set(state, rival, posture="fighters")
    assert engine._arrange_defence(state, rival, capital.tile, home_side)[1] == home_side
    for person_id in home_side[2:MIN_LINE]:
        people[person_id].skills = {**people[person_id].skills, ARMS: 15}
    _, line, reserve, back = engine._arrange_defence(state, rival, capital.tile, home_side)
    assert line == home_side[:MIN_LINE] and reserve == [] and back == home_side[MIN_LINE:]

    # Craftsmen kept back; a fifth of the rest, the least practised, wait in reserve.
    for person_id in home_side[-3:]:
        people[person_id].skills = {**people[person_id].skills, "stoneworking": 1}
    assert is_craftsman(people[home_side[-1]].skills)
    _set(state, rival, posture="craftsmen_back", reserve_bp=2_000)
    _, line, reserve, back = engine._arrange_defence(state, rival, capital.tile, home_side)
    assert back == home_side[-3:]
    standing = home_side[:-3]
    assert len(reserve) == -(-len(standing) * 2_000 // 10_000)
    assert sorted(line + reserve) == standing
    assert all(people[item].skills.get(ARMS, 0) == 0 for item in reserve)


def test_craftsmen_kept_back_are_not_hurt_when_the_town_is_raided() -> None:
    state, home, rival, route = treaty_world(distance=4, rules_version=3)
    civilization = state.civilizations[rival]
    people = civilization.population.people
    _no_crafts(civilization)
    # A few defenders at home, three of them craftsmen; the rest are away.
    away = HexCoord(route[-1].q, route[-1].r - 3)
    for person_id in sorted(people)[8:]:
        people[person_id].location = away
    craftsmen = sorted(people)[:3]
    for person_id in craftsmen:
        people[person_id].skills = {**people[person_id].skills, "timbercraft": 1}
    _set(state, rival, posture="craftsmen_back", tower_crews="drilled", arms_priority="veterans")
    raid = DirectOrder(
        command_id="march",
        kind=DirectOrderKind.SEND_WAR_PARTY,
        journey_id=EntityId(clear_journey_id("raid", days=12)),
        recipient_civilization_id=rival,
        traveller_ids=state.civilizations[home].population.living_ids[:12],
        route=route,
        cargo={Resource.AXE: 6},
        war_objective=WarObjective.RAID,
    )
    rng = StableRng(state.config.seed)
    events = []
    sovereigns = {home: OneShotSovereign(raid)}
    for _ in range(12):
        result = advance_day(state, rng, sovereigns=sovereigns)
        state = result.state
        events.extend(result.events.events)
    [battle] = state.battles
    assert not set(craftsmen) & set(battle.defenders)
    assert not {item.person_id for item in battle.casualties} & set(craftsmen)
    [held] = [item for item in events if item.kind == "held_back"]
    assert held.payload == {"count": 3, "posture": "craftsmen_back"}
    assert all(state.civilizations[rival].population.people[item].alive for item in craftsmen)
    validate_world(state)


def test_council_seven_tells_rules_three_councils_how_a_town_is_defended() -> None:
    from test_town_plan_councils import _report

    assert PROMPT_VERSION == "council-7"
    rule = defence_rule()
    assert rule in charter(_report(3)) and rule not in charter(_report(2))
    for word in ("set_defence", "craftsmen_back", "reserve_bp", "drilled", "veterans"):
        assert word in rule
    for value in (Posture, Crews, Arms):
        for item in value:
            assert item.value in rule
    assert "15% of the time" in rule and "20% of the time" in rule
    for word in ("section_ids", "build_works", "gatehouse", "5% harder", "10% weaker"):
        assert word in rule
