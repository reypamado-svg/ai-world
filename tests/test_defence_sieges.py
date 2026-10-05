"""Active defence (rules version 3): a town's own catapults, fire at a siege camp, a sally
covered from the walls, and how long a besieged store lasts."""

from collections import Counter
from typing import Any

import pytest
from logistics_helpers import OneShotSovereign, clear_journey_id, treaty_world
from test_defence import _battle, _fighters
from test_sieges import _encamped, _events, _run, _stock

import sovereign_world.engine as engine
from sovereign_world.commands import DirectOrder, DirectOrderKind, build_council_report
from sovereign_world.defence import COUNTER_BATTERY_DRAWS
from sovereign_world.engine import advance_day
from sovereign_world.gateway.prompt import defence_rule
from sovereign_world.hexmap import HexCoord
from sovereign_world.ids import EntityId
from sovereign_world.resources import Resource
from sovereign_world.rings import empty_ring, facing_sections, gate_sections, ring_for, set_ring
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState, validate_world
from sovereign_world.stores import set_store, store_at, store_id_at
from sovereign_world.walls import WALL_GRADES, WallGrade
from sovereign_world.war import PURSUIT_BP, WarObjective, resolve_battle


class CountingRng(StableRng):
    """Counts the draws made on each stream."""

    def __init__(self, root_seed: int) -> None:
        super().__init__(root_seed)
        self.draws: Counter[str] = Counter()

    def stream(self, name: str) -> Any:
        generator = super().stream(name)
        draws = self.draws

        class Counted:
            def integers(self, *args: Any, **kwargs: Any) -> Any:
                draws[name] += 1
                return generator.integers(*args, **kwargs)

            def __getattr__(self, attribute: str) -> Any:
                return getattr(generator, attribute)

        return Counted()


def test_the_new_hooks_change_nothing_unless_used() -> None:
    assert _battle() == _battle(
        defender_catapults=0,
        attacker_cover_bp=0,
        attacker_pursuit_bp=PURSUIT_BP,
        defender_pursuit_bp=PURSUIT_BP,
    )


def _fight(attackers: int, a_strength: int, defenders: int, **extra: Any):  # type: ignore[no-untyped-def]
    return resolve_battle(
        _fighters("a", attackers, a_strength),
        _fighters("d", defenders, 100),
        defence_bp=10_000,
        attacker_morale_bp=2_000,
        defender_morale_bp=3_000,
        rng=StableRng(3),
        stream="d3",
        **extra,
    )


def test_defender_catapults_and_cover_turn_a_battle() -> None:
    assert _fight(10, 120, 6).attackers_won
    assert not _fight(10, 120, 6, defender_catapults=8).attackers_won
    assert not _fight(4, 60, 10).attackers_won
    assert _fight(4, 60, 10, attacker_cover_bp=40_000).attackers_won


def test_a_routed_side_chased_at_half_the_rate_loses_less() -> None:
    def losses(outcome) -> int:  # type: ignore[no-untyped-def]
        return len(outcome.casualties) + len(outcome.captured)

    # Forty weak attackers break; the pursuit falls on 4 of them, or on 2.
    usual = _fight(40, 20, 30)
    halved = _fight(40, 20, 30, attacker_pursuit_bp=PURSUIT_BP // 2)
    assert not usual.attackers_won and not halved.attackers_won
    assert usual.rounds == halved.rounds
    assert losses(usual) - losses(halved) in (1, 2)
    weak = {"defence_bp": 10_000, "attacker_morale_bp": 2_000, "defender_morale_bp": 3_000}
    beaten = [
        resolve_battle(
            _fighters("a", 40, 200),
            _fighters("d", 40, 20),
            **weak,
            rng=StableRng(3),
            stream="d3",
            defender_pursuit_bp=rate,
        )
        for rate in (PURSUIT_BP, PURSUIT_BP // 2)
    ]
    assert all(item.attackers_won for item in beaten)
    assert 1 <= losses(beaten[0]) - losses(beaten[1]) <= 2


def test_facing_sections_split_the_ring_and_hold_its_gates() -> None:
    for radius in range(1, 6):
        ring = empty_ring(EntityId("settlement:x"), radius, (0,), 0)
        facing = [facing_sections(ring, direction) for direction in range(6)]
        assert sorted(index for item in facing for index in item) == list(range(len(ring.sections)))
        for gate in range(6):
            assert gate_sections(radius, (gate,)) <= facing[gate]
    centre = HexCoord(5, 5)
    for index, neighbour in enumerate(centre.neighbors()):
        assert centre.direction_to(neighbour) == index
    with pytest.raises(ValueError):
        centre.direction_to(HexCoord(9, 9))


def _world() -> tuple[WorldState, EntityId, EntityId, tuple[HexCoord, ...]]:
    return treaty_world(distance=4, rules_version=3)


def _siege_draws(state: WorldState, days: int = 1) -> tuple[WorldState, Counter[str], list]:  # type: ignore[type-arg]
    rng = CountingRng(state.config.seed)
    events = []
    for _ in range(days):
        result = advance_day(state, rng)
        state = result.state
        events.extend(result.events.events)
    return (
        state,
        Counter({key: value for key, value in rng.draws.items() if ":siege:" in key}),
        events,
    )


def test_a_town_with_catapults_fires_at_the_camp_every_day() -> None:
    state, home, rival, route = _world()
    _stock(state, rival, catapult=2)
    state, _, camp = _encamped(state, home, rival, route, fighters=16)
    assert state.sieges[0].defender_learned_day is not None
    people = state.civilizations[home].population.people
    before = {item: people[item].health_bp for item in camp.traveller_ids}
    state, draws, events = _siege_draws(state)
    [(_, count)] = draws.items()
    assert count == 2 * COUNTER_BATTERY_DRAWS
    after = state.civilizations[home].population.people
    struck = [item for item in camp.traveller_ids if after[item].health_bp < before[item]]
    bombarded = [event for event in events if event.kind == "camp_bombarded"]
    if bombarded:
        [event] = bombarded
        assert event.payload["catapults"] == 2 and event.payload["hits"] == len(struck)
        assert event.payload["dead"] == sum(not after[item].alive for item in struck)
    else:
        assert not struck
    validate_world(state)

    # A town without catapults opens no siege stream; nor does one under rules 2.
    state, home, rival, route = _world()
    state, _, _ = _encamped(state, home, rival, route, fighters=16)
    assert not _siege_draws(state)[1]
    older, home, rival, route = treaty_world(distance=4, rules_version=2)
    _stock(older, rival, catapult=2)
    older, _, _ = _encamped(older, home, rival, route, fighters=16)
    assert not _siege_draws(older)[1]


def test_the_camps_own_catapults_draw_first() -> None:
    state, home, rival, route = _world()
    _stock(state, home, catapult=1)
    _stock(state, rival, catapult=1)
    state, _, _ = _encamped(state, home, rival, route, fighters=16, cargo={Resource.CATAPULT: 1})
    _, draws, events = _siege_draws(state)
    [(_, count)] = draws.items()
    camp_hit = any(
        event.kind in ("wall_section_damaged", "wall_section_fell", "person_wounded")
        and event.actor_id == str(home)
        for event in events
    ) or any(
        event.kind == "person_died"
        and event.payload.get("cause") == "bombardment"
        and event.actor_id == str(home)
        for event in events
    )
    # The camp's catapult draws once, and twice more if it hits people in an unwalled town.
    assert count in (1 + COUNTER_BATTERY_DRAWS, 3 + COUNTER_BATTERY_DRAWS)
    assert (count == 3 + COUNTER_BATTERY_DRAWS) == camp_hit


def _raid_home(rules_version: int, catapults: int) -> tuple[WorldState, list, EntityId]:  # type: ignore[type-arg]
    state, home, rival, route = treaty_world(distance=4, rules_version=rules_version)
    _stock(state, rival, catapult=catapults)
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
    _stock(state, home, axe=6)
    rng = StableRng(state.config.seed)
    events = []
    sovereigns = {home: OneShotSovereign(raid)}
    for _ in range(12):
        result = advance_day(state, rng, sovereigns=sovereigns)
        state = result.state
        events.extend(result.events.events)
        if state.battles:
            break
    return state, events, rival


def test_stored_catapults_join_a_battle_at_home() -> None:
    state, events, rival = _raid_home(3, 1)
    [battle] = state.battles
    [manned] = [event for event in events if event.kind == "engines_manned"]
    assert manned.payload == {"catapults": 1, "crew": 6}
    assert manned.actor_id == str(rival) and manned.subject_id == str(battle.battle_id)
    older, older_events, _ = _raid_home(2, 1)
    assert older.battles and not [item for item in older_events if item.kind == "engines_manned"]
    _, plain_events, _ = _raid_home(3, 0)
    assert not [item for item in plain_events if item.kind == "engines_manned"]


def _towered(state: WorldState, rival: EntityId, camp: HexCoord) -> int:
    """A complete palisade ring with its towers on the sections facing the camp."""
    civilization = state.civilizations[rival]
    [capital] = civilization.settlements
    ring = ring_for(civilization, capital.settlement_id, state.day)
    facing = sorted(facing_sections(ring, capital.tile.direction_to(camp)))[:2]
    ring = ring.model_copy(
        update={
            "sections": tuple(
                item.model_copy(
                    update={
                        "grade": WallGrade.PALISADE,
                        "strength": WALL_GRADES[WallGrade.PALISADE].strength,
                    }
                )
                for item in ring.sections
            ),
            "towers": len(facing),
            "tower_sections": tuple(facing),
        }
    )
    set_ring(civilization, ring)
    return len(facing)


def _sally(towered: bool, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    state, home, rival, route = _world()
    state, _, camp = _encamped(state, home, rival, route, fighters=8)
    towers = _towered(state, rival, camp.route[-1]) if towered else 0
    calls: list[dict[str, Any]] = []
    real = engine.resolve_battle

    def spy(*args: Any, **kwargs: Any):  # type: ignore[no-untyped-def]
        calls.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(engine, "resolve_battle", spy)
    defenders = state.civilizations[rival].population.living_ids
    sally = DirectOrder(
        command_id="sally",
        kind=DirectOrderKind.SEND_WAR_PARTY,
        journey_id=EntityId(clear_journey_id("sally", start_day=30, days=12)),
        recipient_civilization_id=home,
        traveller_ids=defenders[:10],
        route=tuple(reversed(route))[:2],
        war_objective=WarObjective.ATTACK,
    )
    state.day = 30
    state, results = _run(state, 3, {rival: OneShotSovereign(sally)})
    assert state.battles
    return towers, calls, _events(results, "sally_covered")


def test_a_sally_is_covered_by_the_towers_facing_the_camp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    towers, calls, covered = _sally(True, monkeypatch)
    assert towers >= 1
    [event] = covered
    assert event.payload == {"towers": towers, "pursuit_bp": PURSUIT_BP // 2}
    [call] = calls
    # Whichever side struck first, the towers' fire is on the sally's side.
    assert call["attacker_cover_bp"] == towers * 750 or call["tower_hits_bp"] == (750,) * towers
    bare_towers, bare_calls, bare_covered = _sally(False, monkeypatch)
    assert bare_towers == 0 and not bare_covered
    [bare] = bare_calls
    assert bare["attacker_cover_bp"] == 0 and bare["tower_hits_bp"] == ()
    assert bare["attacker_pursuit_bp"] == bare["defender_pursuit_bp"] == PURSUIT_BP


def _set_food(state: WorldState, rival: EntityId, food: int) -> None:
    civilization = state.civilizations[rival]
    tile = civilization.settlements[0].tile
    held = store_at(civilization, tile)
    set_store(
        civilization,
        store_id_at(civilization, tile),
        held.model_copy(update={"quantities": {**held.quantities, Resource.FOOD: food}}),
    )


def test_a_besieged_council_sees_how_long_its_store_lasts() -> None:
    state, home, rival, route = _world()
    state, _, _ = _encamped(state, home, rival, route, fighters=8)
    _set_food(state, rival, 500)
    [capital] = state.civilizations[rival].settlements
    report = build_council_report(state, rival)
    residents = report.housing[capital.settlement_id].residents
    assert report.siege_days_of_food == {capital.settlement_id: 500 // residents}
    assert "siege_days_of_food" not in build_council_report(state, home).model_dump()
    older, home, rival, route = treaty_world(distance=4, rules_version=2)
    older, _, _ = _encamped(older, home, rival, route, fighters=8)
    assert "siege_days_of_food" not in build_council_report(older, rival).model_dump()


def test_a_starving_besieged_town_fights_with_less_resolve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    morale: dict[int, int] = {}
    real = engine.resolve_battle
    for food in (0, 5_000):
        state, home, rival, route = _world()
        _stock(state, home, axe=8)
        state, _, camp = _encamped(state, home, rival, route, fighters=16, cargo={Resource.AXE: 8})
        _set_food(state, rival, food)
        calls: list[dict[str, Any]] = []

        def spy(*args: Any, calls: list[dict[str, Any]] = calls, **kwargs: Any):  # type: ignore[no-untyped-def]
            calls.append(kwargs)
            return real(*args, **kwargs)

        monkeypatch.setattr(engine, "resolve_battle", spy)
        storm = DirectOrder(
            command_id="storm",
            kind=DirectOrderKind.STORM_SETTLEMENT,
            journey_id=camp.journey_id,
            war_objective=WarObjective.RAID,
        )
        state.day = 30
        _run(state, 3, {home: OneShotSovereign(storm)})
        morale[food] = calls[0]["defender_morale_bp"]
    assert morale[0] < morale[5_000]


def test_the_charter_tells_councils_of_active_defence() -> None:
    rule = defence_rule()
    for words in (
        "Catapults in a settlement's store",
        "fire at the camp every day",
        "7.5% of the time",
        "chased half as far",
        "siege_days_of_food",
    ):
        assert words in rule
