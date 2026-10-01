from logistics_helpers import OneShotSovereign, envelope, treaty_world

from sovereign_world.armoury import KITS, kit_assignment
from sovereign_world.capabilities import CapabilityId, CapabilityRecord
from sovereign_world.commands import DirectOrder, DirectOrderKind, validate_envelope
from sovereign_world.engine import TransitionResult, advance_day
from sovereign_world.ids import EntityId
from sovereign_world.resources import Resource
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState, validate_world
from sovereign_world.war import ARMS, WarObjective, fighting_strength


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


def _research(
    state: WorldState, civilization_id: EntityId, topic: CapabilityId, scholars: int, days: int
) -> DirectOrder:
    return DirectOrder(
        command_id=f"study:{topic.value}",
        kind=DirectOrderKind.RESEARCH,
        worker_ids=state.civilizations[civilization_id].population.living_ids[:scholars],
        research_topic=topic,
        research_days=days,
    )


def _grant(state: WorldState, civilization_id: EntityId, capability: CapabilityId) -> None:
    civilization = state.civilizations[civilization_id]
    master = civilization.population.living_ids[-1]
    person = civilization.population.people[master]
    person.skills = {**person.skills, capability.value: 100}
    civilization.capabilities = tuple(
        sorted(
            (
                *civilization.capabilities,
                CapabilityRecord(
                    capability=capability, practitioner_ids=(master,), discovered_day=0
                ),
            ),
            key=lambda record: record.capability.value,
        )
    )


def _codes(state: WorldState, civilization_id: EntityId, *orders: DirectOrder) -> list[str]:
    errors = validate_envelope(envelope(state, civilization_id, *orders), state).errors
    return [error.code for error in errors]


def test_research_needs_its_prerequisites_and_something_new_to_learn() -> None:
    state, home, _, _ = _world()
    assert _codes(state, home, _research(state, home, CapabilityId.ARCHERY, 2, 30)) == [
        "invalid_research"
    ], "archery needs timbercraft first"
    assert _codes(state, home, _research(state, home, CapabilityId.CULTIVATION, 2, 30)) == [
        "invalid_research"
    ], "cultivation is not a research topic"
    formations = _research(state, home, CapabilityId.SPEAR_FORMATIONS, 2, 30)
    assert _codes(state, home, formations) == []
    twice = formations.model_copy(
        update={
            "command_id": "again",
            "worker_ids": state.civilizations[home].population.living_ids[2:4],
        }
    )
    assert _codes(state, home, formations, twice) == ["invalid_research"]
    _grant(state, home, CapabilityId.TIMBERCRAFT)
    assert _codes(state, home, _research(state, home, CapabilityId.ARCHERY, 2, 30)) == []
    _grant(state, home, CapabilityId.SPEAR_FORMATIONS)
    assert _codes(state, home, formations) == ["invalid_research"], "already known"


def test_scholars_discover_a_topic_point_by_point_and_writers_work_faster() -> None:
    state, home, _, _ = _world()
    order = _research(state, home, CapabilityId.SPEAR_FORMATIONS, 4, 60)
    scribe = state.civilizations[home].population.people[order.worker_ids[0]]
    scribe.skills = {**scribe.skills, CapabilityId.WRITING.value: 50}

    state, results = _run(state, 23, {home: OneShotSovereign(order)})

    [done] = _events(results, "research_completed")
    assert done.day == 22, "4 scholars and a writer make 9 points a day: 200 in 23 days"
    civilization = state.civilizations[home]
    [record] = [
        item
        for item in civilization.capabilities
        if item.capability is CapabilityId.SPEAR_FORMATIONS
    ]
    assert record.practitioner_ids == tuple(sorted(order.worker_ids))
    people = civilization.population.people
    assert all(
        people[person_id].skills[CapabilityId.SPEAR_FORMATIONS.value] == 100
        for person_id in order.worker_ids
    )
    assert civilization.research == () and civilization.research_points == {}
    validate_world(state)


def test_unfinished_research_keeps_its_points_for_later() -> None:
    state, home, _, _ = _world()
    order = _research(state, home, CapabilityId.SPEAR_FORMATIONS, 2, 10)
    state, results = _run(state, 12, {home: OneShotSovereign(order)})
    [paused] = _events(results, "research_paused")
    assert paused.payload["points"] == 20
    assert state.civilizations[home].research_points == {CapabilityId.SPEAR_FORMATIONS: 20}
    assert not _events(results, "research_completed")


def test_scholars_stay_at_their_desks() -> None:
    state, home, rival, route = _world()
    order = _research(state, home, CapabilityId.DRILL_DOCTRINE, 4, 30)
    state, _ = _run(state, 1, {home: OneShotSovereign(order)})
    march = DirectOrder(
        command_id="march",
        kind=DirectOrderKind.SEND_WAR_PARTY,
        journey_id=EntityId("journey:scholars"),
        recipient_civilization_id=rival,
        traveller_ids=order.worker_ids,
        route=route,
        war_objective=WarObjective.RAID,
    )
    state.day = 30
    assert "traveller_unavailable" in _codes(state, home, march)


def test_spear_formations_strengthen_a_defending_line() -> None:
    state, home, _, _ = _world()
    person = next(iter(state.civilizations[home].population.people.values())).model_copy(deep=True)
    person.health_bp = 10_000
    person.age_days = 30 * 365
    fighters = [person.person_id]
    plain = kit_assignment(fighters, {Resource.SPEAR: 1})[person.person_id]
    drilled = kit_assignment(fighters, {Resource.SPEAR: 1}, formations=True)[person.person_id]
    assert plain == KITS[Resource.SPEAR]
    assert fighting_strength(person, plain, attacking=False) == 175
    assert fighting_strength(person, drilled, attacking=False) == 192
    assert fighting_strength(person, drilled, attacking=True) == 140, "only on defence"


def test_drill_doctrine_teaches_twice_as_fast_and_further() -> None:
    state, home, _, _ = _world()
    _grant(state, home, CapabilityId.DRILL_DOCTRINE)
    drillers = state.civilizations[home].population.living_ids[:3]
    drill = DirectOrder(
        command_id="drill", kind=DirectOrderKind.DRILL, worker_ids=drillers, drill_days=25
    )
    state, _ = _run(state, 26, {home: OneShotSovereign(drill)})
    people = state.civilizations[home].population.people
    assert all(people[person_id].skills.get(ARMS, 0) == 10 for person_id in drillers)

    for person_id in drillers:
        people[person_id].skills = {**people[person_id].skills, ARMS: 25}
    state.day = 30
    state, _ = _run(state, 20, {home: OneShotSovereign(drill)})
    people = state.civilizations[home].population.people
    assert all(people[person_id].skills.get(ARMS, 0) == 30 for person_id in drillers), (
        "the doctrine's cap is 30"
    )


def test_military_logistics_lets_fighters_carry_more() -> None:
    state, home, rival, route = _world()
    long_route = route + tuple(reversed(route))[1:] + route[1:]
    march = DirectOrder(
        command_id="march",
        kind=DirectOrderKind.SEND_WAR_PARTY,
        journey_id=EntityId("journey:far"),
        recipient_civilization_id=rival,
        traveller_ids=state.civilizations[home].population.living_ids[:2],
        route=long_route,
        cargo={Resource.AXE: 2},
        war_objective=WarObjective.RAID,
        extra_provisions=50,
    )
    assert _codes(state, home, march) == ["cargo_over_capacity"]
    _grant(state, home, CapabilityId.MILITARY_LOGISTICS)
    assert _codes(state, home, march) == []
    state, _ = _run(state, 1, {home: OneShotSovereign(march)})
    [party] = state.journeys
    assert party.carry_per_person == 60


def test_discoveries_are_forgotten_when_every_practitioner_dies() -> None:
    state, home, _, _ = _world()
    _grant(state, home, CapabilityId.SIEGECRAFT)
    [record] = [
        item
        for item in state.civilizations[home].capabilities
        if item.capability is CapabilityId.SIEGECRAFT
    ]
    for person_id in record.practitioner_ids:
        person = state.civilizations[home].population.people[person_id]
        person.alive = False
        person.death_day = 0

    state, results = _run(state, 1)

    assert not any(
        item.capability is CapabilityId.SIEGECRAFT
        for item in state.civilizations[home].capabilities
    )
    [forgotten] = _events(results, "capability_forgotten")
    assert forgotten.payload["capability"] == CapabilityId.SIEGECRAFT.value
