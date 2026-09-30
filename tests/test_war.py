from logistics_helpers import (
    OneShotSovereign,
    clear_journey_id,
    clear_message_id,
    envelope,
    roll_matching_id,
    treaty_world,
)

from sovereign_world.armoury import KITS
from sovereign_world.commands import (
    DirectOrder,
    DirectOrderKind,
    build_council_report,
    validate_envelope,
)
from sovereign_world.diplomacy import TreatyEndKind
from sovereign_world.engine import TransitionResult, advance_day
from sovereign_world.hexmap import HexCoord, Terrain
from sovereign_world.ids import EntityId
from sovereign_world.logistics import Journey, JourneyKind, JourneyOutcome, JourneyPhase
from sovereign_world.resources import Resource
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState, validate_world
from sovereign_world.war import (
    ARMS,
    BATTLE_WON_POINTS,
    DRILL_CAP,
    War,
    WarObjective,
    defence_bonus_bp,
    estimate,
    fighter,
    fighting_strength,
    resolve_battle,
)


def _world():
    state, home, rival, route = treaty_world(distance=4)
    return state, home, rival, route


def _run(
    state: WorldState, days: int, sovereigns=None
) -> tuple[WorldState, list[TransitionResult]]:
    rng = StableRng(state.config.seed)
    results: list[TransitionResult] = []
    for _ in range(days):
        result = advance_day(state, rng, sovereigns=sovereigns)
        state = result.state
        results.append(result)
    return state, results


def _events(results: list[TransitionResult], kind: str):
    return [event for result in results for event in result.events.events if event.kind == kind]


def _war_party(
    state: WorldState,
    home: EntityId,
    rival: EntityId,
    route: tuple[HexCoord, ...],
    *,
    fighters: int,
    axes: int = 0,
    objective: WarObjective = WarObjective.RAID,
    journey: str | None = None,
) -> DirectOrder:
    return DirectOrder(
        command_id="march",
        kind=DirectOrderKind.SEND_WAR_PARTY,
        journey_id=EntityId(journey or clear_journey_id("raid", days=12)),
        recipient_civilization_id=rival,
        traveller_ids=state.civilizations[home].population.living_ids[:fighters],
        route=route,
        cargo={Resource.AXE: axes} if axes else {},
        war_objective=objective,
    )


def _send_away(state: WorldState, civilization_id: EntityId, keep: int, tile: HexCoord) -> None:
    """Leave only a few defenders at home; the rest are off at a distant tile."""
    people = state.civilizations[civilization_id].population.people
    for person_id in sorted(people)[keep:]:
        people[person_id].location = tile


def test_fighting_strength_follows_health_age_hunger_skill_and_arms() -> None:
    state, home, _, _ = _world()
    person = next(iter(state.civilizations[home].population.people.values())).model_copy(deep=True)
    person.health_bp = 10_000
    person.age_days = 30 * 365
    assert fighting_strength(person) == 100
    assert fighting_strength(person, KITS[Resource.AXE], attacking=False) == 150
    assert fighting_strength(person, KITS[Resource.AXE]) == 165, "axes favour attack"
    person.skills = {ARMS: 60}
    assert fighting_strength(person) == 160
    person.skills = {ARMS: 200}
    assert fighting_strength(person) == 160, "arms skill is capped at +60%"
    person.skills = {}
    person.nutrition_debt = 20
    assert fighting_strength(person) == 80
    person.nutrition_debt = 90
    assert fighting_strength(person) == 50, "hunger halves strength at most"
    person.nutrition_debt = 0
    person.age_days = 55 * 365
    assert fighting_strength(person) == 60
    person.age_days = 70 * 365
    assert fighting_strength(person) == 0, "the old do not fight"
    person.age_days = 10 * 365
    assert fighting_strength(person) == 0, "nor do children"


def test_defenders_gain_from_terrain_and_their_own_settlement() -> None:
    assert defence_bonus_bp(Terrain.GRASSLAND, settlement=False) == 10_000
    assert defence_bonus_bp(Terrain.FOREST, settlement=False) == 12_500
    assert defence_bonus_bp(Terrain.MOUNTAIN, settlement=True) == 18_750
    assert estimate(7) == 5 and estimate(8) == 10 and estimate(0) == 0


def test_battles_are_deterministic_and_the_stronger_side_wins() -> None:
    state, home, rival, _ = _world()
    strong = [
        fighter(person, KITS[Resource.AXE])
        for person in list(state.civilizations[home].population.people.values())[:20]
    ]
    weak = [
        fighter(person, attacking=False)
        for person in list(state.civilizations[rival].population.people.values())[:5]
    ]

    def fight():
        return resolve_battle(
            strong,
            weak,
            defence_bp=10_000,
            attacker_morale_bp=1_500,
            defender_morale_bp=3_000,
            rng=StableRng(7),
            stream="test:battle",
        )

    first, second = fight(), fight()
    assert first == second
    assert first.attackers_won
    losers = {item.person_id for item in weak}
    loser_hits = [item for item in first.casualties if item.person_id in losers]
    assert len(loser_hits) >= 2, "the defenders broke and were pursued"
    assert all(item.damage > 0 for item in first.casualties)


def test_a_declaration_breaks_treaties_at_once_and_is_learned_on_delivery() -> None:
    state, home, rival, route = _world()
    herald = state.civilizations[home].population.living_ids[-1]
    declare = DirectOrder(
        command_id="declare",
        kind=DirectOrderKind.DECLARE_WAR,
        message_id=EntityId(clear_message_id("war", days=8)),
        ambassador_id=herald,
        recipient_civilization_id=rival,
        message_text="We come for your fields.",
        route=route,
    )
    state, results = _run(state, 1, {home: OneShotSovereign(declare)})

    [war] = state.wars
    assert war.declared and war.aggressor_id == home and war.defender_learned_day is None
    assert [treaty.end_kind for treaty in state.active_treaties] == [TreatyEndKind.BREACHED]
    assert _events(results, "war_declared")
    assert build_council_report(state, rival).wars == (), "the enemy has not heard yet"
    assert build_council_report(state, home).wars == (war,)

    again = declare.model_copy(
        update={
            "message_id": EntityId("message:again"),
            "ambassador_id": state.civilizations[home].population.living_ids[-2],
        }
    )
    state.day = 30
    errors = validate_envelope(envelope(state, home, again), state).errors
    assert [error.code for error in errors] == ["already_at_war"]
    state.day = 1

    state, results = _run(state, 8)
    assert _events(results, "war_learned")
    [war] = state.wars
    assert war.defender_learned_day is not None
    assert build_council_report(state, rival).wars == (war,)


def test_an_undeclared_raid_starts_a_war_and_carries_off_goods() -> None:
    state, home, rival, route = _world()
    _send_away(state, rival, 3, HexCoord(route[-1].q, route[-1].r - 3))
    food_before = state.civilizations[rival].inventory.quantities[Resource.FOOD]
    order = _war_party(state, home, rival, route, fighters=12, axes=6)
    assert validate_envelope(envelope(state, home, order), state).errors == ()

    state, results = _run(state, 12, {home: OneShotSovereign(order)})

    [war] = state.wars
    assert not war.declared and war.aggressor_id == home
    assert war.defender_learned_day is not None, "the victims know they were attacked"
    assert _events(results, "undeclared_attack")
    assert state.active_treaties[0].end_kind is TreatyEndKind.BREACHED
    [battle] = state.battles
    assert battle.winner_id == home
    [raided] = _events(results, "settlement_raided")
    assert raided.payload["units"] > 0
    assert state.civilizations[rival].inventory.quantities[Resource.FOOD] < food_before
    [party] = state.journeys
    assert party.outcome is JourneyOutcome.DELIVERED and not party.active
    assert party.plunder, "the raiders carried their spoils home"
    assert _events(results, "war_party_returned")
    survivors = [
        person
        for person_id in battle.attackers
        if (person := state.civilizations[home].population.people[person_id]).alive
    ]
    assert survivors
    assert all(person.skills.get(ARMS, 0) >= BATTLE_WON_POINTS for person in survivors)
    [report] = state.civilizations[home].war_reports
    assert report.won and report.own_fighters == 12
    validate_world(state)


def test_a_raid_on_a_full_settlement_is_routed_and_news_comes_home_late() -> None:
    state, home, rival, route = _world()
    order = _war_party(state, home, rival, route, fighters=4)

    state, _ = _run(state, 5, {home: OneShotSovereign(order)})

    [battle] = state.battles
    assert battle.winner_id == rival
    [party] = state.journeys
    assert party.outcome is JourneyOutcome.ROUTED and party.active
    assert state.civilizations[rival].war_reports, "the defenders told their council at once"
    assert state.civilizations[home].war_reports == (), "no word until survivors return"
    rival_report = state.civilizations[rival].war_reports[0]
    assert rival_report.won and rival_report.enemy_fighters_estimate == estimate(4)
    for casualty in battle.casualties:
        person = state.civilizations[casualty.civilization_id].population.people[casualty.person_id]
        assert not person.alive or person.health_bp < 10_000

    state, _ = _run(state, 10)
    [party] = state.journeys
    assert not party.active
    [report] = state.civilizations[home].war_reports
    assert not report.won and report.enemy_dead_seen == 0, "the beaten side counts no enemy dead"
    assert set(report.own_wounded) | set(report.own_dead) == {
        item.person_id for item in battle.casualties if item.civilization_id == home
    }


def test_war_parties_may_be_larger_than_sixteen_but_need_fit_fighters() -> None:
    state, home, rival, route = _world()
    big = _war_party(state, home, rival, route, fighters=24, journey="journey:big")
    assert validate_envelope(envelope(state, home, big), state).errors == ()

    child = state.civilizations[home].population.people[
        state.civilizations[home].population.living_ids[0]
    ]
    child.age_days = 8 * 365
    errors = validate_envelope(envelope(state, home, big), state).errors
    assert [error.code for error in errors] == ["unfit_fighter"]
    child.age_days = 30 * 365

    too_many_axes = _war_party(state, home, rival, route, fighters=2, axes=3, journey="journey:x")
    errors = validate_envelope(envelope(state, home, too_many_axes), state).errors
    assert [error.code for error in errors] == ["invalid_cargo"]


def test_drill_raises_arms_slowly_to_its_cap_and_commits_the_drillers() -> None:
    state, home, rival, route = _world()
    drillers = state.civilizations[home].population.living_ids[:4]
    drill = DirectOrder(
        command_id="drill",
        kind=DirectOrderKind.DRILL,
        worker_ids=drillers,
        drill_days=25,
    )
    state, results = _run(state, 1, {home: OneShotSovereign(drill)})
    assert _events(results, "drill_started")
    march = _war_party(state, home, rival, route, fighters=4, journey="journey:busy")
    state.day = 30
    errors = validate_envelope(envelope(state, home, march), state).errors
    assert "traveller_unavailable" in [error.code for error in errors]
    state.day = 1

    state, results = _run(state, 24)
    assert _events(results, "drill_completed")
    people = state.civilizations[home].population.people
    assert all(people[person_id].skills.get(ARMS, 0) == 5 for person_id in drillers)
    assert state.civilizations[home].drills == ()

    for person_id in drillers:
        people[person_id].skills = {**people[person_id].skills, ARMS: DRILL_CAP}
    state.civilizations[home].drills = ()
    state, _ = _run(state, 1, {home: OneShotSovereign(drill)})
    state, _ = _run(state, 10)
    assert all(people[person_id].skills.get(ARMS, 0) == DRILL_CAP for person_id in drillers), (
        "drill alone never passes its cap"
    )


def test_a_war_party_ambushes_an_enemy_convoy_and_seizes_its_goods() -> None:
    state, home, rival, route = _world()
    convoy_id = roll_matching_id(
        "journey:convoy", "logistics", lambda roll: 60 <= roll < 900, days=3
    )
    carriers = tuple(sorted(state.civilizations[rival].population.living_ids[:2]))
    back = tuple(reversed(route))
    for person_id in carriers:
        state.civilizations[rival].population.people[person_id].location = back[2]
    convoy = Journey(
        journey_id=EntityId(convoy_id),
        kind=JourneyKind.SHIPMENT,
        treaty_id=EntityId("treaty:trade"),
        sender_civilization_id=rival,
        recipient_civilization_id=home,
        traveller_ids=carriers,
        route=back,
        cargo={Resource.STONE: 40},
        carrying_cargo=True,
        provisions_packed=20,
        provisions=20,
        departed_day=0,
        route_index=2,
    )
    state.journeys = (convoy,)
    state.wars = (
        War(
            war_id=EntityId("war:test"),
            aggressor_id=home,
            defender_id=rival,
            started_day=0,
            declared=True,
            defender_learned_day=0,
        ),
    )
    order = _war_party(state, home, rival, route[:3], fighters=6, objective=WarObjective.ATTACK)

    state, results = _run(state, 3, {home: OneShotSovereign(order)})

    [ambush] = _events(results, "convoy_ambushed")
    assert ambush.payload["units"] == 40
    robbed = next(item for item in state.journeys if item.journey_id == convoy.journey_id)
    assert robbed.outcome is JourneyOutcome.AMBUSHED
    assert robbed.phase is not JourneyPhase.OUTBOUND and not robbed.carrying_cargo
    party = next(item for item in state.journeys if item.kind is JourneyKind.CAMPAIGN)
    assert party.plunder == {Resource.STONE: 40}
