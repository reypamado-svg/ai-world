from logistics_helpers import OneShotSovereign, envelope, treaty_world

from sovereign_world.armoury import CraftJob
from sovereign_world.capabilities import CapabilityId, CapabilityRecord
from sovereign_world.commands import (
    DirectOrder,
    DirectOrderKind,
    build_council_report,
    validate_envelope,
)
from sovereign_world.diplomacy import DiplomaticMessage, advance_diplomacy_day
from sovereign_world.engine import TransitionResult, advance_day
from sovereign_world.ids import EntityId
from sovereign_world.institutions import (
    INSTITUTIONS,
    SCHOOL_TEACHING_DAYS,
    Institution,
    InstitutionKind,
)
from sovereign_world.languages import LOST_WORD
from sovereign_world.resources import Resource
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState, validate_world
from sovereign_world.stores import put


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


def _codes(state: WorldState, civilization_id: EntityId, *orders: DirectOrder) -> list[str]:
    errors = validate_envelope(envelope(state, civilization_id, *orders), state).errors
    return [error.code for error in errors]


def _at_home(state: WorldState, civilization_id: EntityId) -> list[EntityId]:
    civilization = state.civilizations[civilization_id]
    capital = civilization.settlements[0].tile
    return [
        person_id
        for person_id in civilization.population.living_ids
        if civilization.population.people[person_id].location == capital
    ]


def _knowing(state: WorldState, civilization_id: EntityId, *capabilities: CapabilityId) -> None:
    """Give the civilization these capabilities, each practised by its first person at home."""
    civilization = state.civilizations[civilization_id]
    practitioner = _at_home(state, civilization_id)[0]
    person = civilization.population.people[practitioner]
    records = {record.capability: record for record in civilization.capabilities}
    for capability in capabilities:
        person.skills = {**person.skills, capability.value: 100}
        old = records.get(capability)
        records[capability] = CapabilityRecord(
            capability=capability,
            practitioner_ids=tuple(sorted({*(old.practitioner_ids if old else ()), practitioner})),
            discovered_day=0,
        )
    civilization.capabilities = tuple(
        records[item] for item in sorted(records, key=lambda item: item.value)
    )
    put(civilization, civilization.settlements[0].tile, {Resource.TIMBER: 200, Resource.STONE: 200})


def _open(
    state: WorldState, civilization_id: EntityId, kind: InstitutionKind, staff: tuple
) -> Institution:
    """An institution already standing at the capital, kept by these staff."""
    civilization = state.civilizations[civilization_id]
    capital = civilization.settlements[0]
    institution = Institution(
        institution_id=EntityId(f"institution:{capital.settlement_id}:{kind.value}"),
        kind=kind,
        settlement_id=capital.settlement_id,
        tile=capital.tile,
        staff_ids=tuple(sorted(staff)),
        founded_day=0,
        person_days_done=INSTITUTIONS[kind].person_days,
        opened_day=0,
    )
    civilization.institutions = tuple(
        sorted((*civilization.institutions, institution), key=lambda item: item.institution_id)
    )
    return institution


def _found(state, civilization_id, kind, workers) -> DirectOrder:
    return DirectOrder(
        command_id=f"found:{kind.value}",
        kind=DirectOrderKind.FOUND_INSTITUTION,
        institution_kind=kind,
        worker_ids=tuple(workers),
    )


def test_founding_needs_the_capability_one_site_and_the_materials() -> None:
    state, home, _, route = treaty_world(distance=4)
    founders = _at_home(state, home)[-2:]
    archive = _found(state, home, InstitutionKind.ARCHIVE, founders)
    assert _codes(state, home, archive) == ["missing_capability"]
    _knowing(state, home, CapabilityId.WRITING)
    assert _codes(state, home, archive) == []
    others = tuple(_at_home(state, home)[-4:-2])
    again = archive.model_copy(update={"command_id": "again", "worker_ids": others})
    assert _codes(state, home, archive, again) == ["invalid_institution"], (
        "one archive to a settlement"
    )
    elsewhere = state.civilizations[home].population.people[founders[0]]
    elsewhere.location = route[1]
    assert _codes(state, home, archive) == ["invalid_institution"]
    elsewhere.location = route[0]
    state.civilizations[home].inventory = state.civilizations[home].inventory.model_copy(
        update={"quantities": {Resource.FOOD: 500}}
    )
    assert _codes(state, home, archive) == ["insufficient_materials"]


def test_founders_raise_the_building_then_keep_it_and_do_no_other_work() -> None:
    state, home, _, _ = treaty_world(distance=4)
    _knowing(state, home, CapabilityId.HERBAL_CARE)
    founders = _at_home(state, home)[-2:]
    timber = state.civilizations[home].inventory.quantities[Resource.TIMBER]
    order = _found(state, home, InstitutionKind.HEALERS_HOUSE, founders)
    state, results = _run(state, 12, {home: OneShotSovereign(order)})

    assert _events(results, "institution_founded") and _events(results, "institution_opened")
    [house] = state.civilizations[home].institutions
    assert house.built and house.staff_ids == tuple(sorted(founders))
    days = -(-INSTITUTIONS[InstitutionKind.HEALERS_HOUSE].person_days // 2)
    assert house.opened_day == days - 1
    assert state.civilizations[home].inventory.quantities[Resource.TIMBER] <= timber - 30
    state.day = 30
    assert build_council_report(state, home).institutions == (house,)
    drill = DirectOrder(command_id="drill", kind=DirectOrderKind.DRILL, worker_ids=(founders[0],))
    assert _codes(state, home, drill) == ["person_travelling"], "staff do nothing else"
    validate_world(state)


def test_staff_can_be_replaced_by_people_standing_at_the_institution() -> None:
    state, home, _, _ = treaty_world(distance=4)
    people = _at_home(state, home)
    house = _open(state, home, InstitutionKind.WORKSHOP, (people[-1],))
    order = DirectOrder(
        command_id="staff",
        kind=DirectOrderKind.STAFF_INSTITUTION,
        institution_id=house.institution_id,
        worker_ids=(people[-1], people[-2]),
    )
    assert _codes(state, home, order) == []
    state.day = 30
    state, results = _run(state, 1, {home: OneShotSovereign(order)})
    assert _events(results, "institution_staffed")
    [after] = state.civilizations[home].institutions
    assert after.staff_ids == tuple(sorted((people[-1], people[-2])))


def test_an_open_archive_keeps_what_its_practitioners_knew() -> None:
    state, home, _, _ = treaty_world(distance=4)
    _knowing(state, home, CapabilityId.WRITING)
    civilization = state.civilizations[home]
    [scribe] = next(
        record.practitioner_ids
        for record in civilization.capabilities
        if record.capability is CapabilityId.WRITING
    )
    keeper = _at_home(state, home)[-1]
    _open(state, home, InstitutionKind.ARCHIVE, (keeper,))
    unarchived = state.model_copy(deep=True)
    unarchived.civilizations[home].institutions = ()
    for world in (state, unarchived):
        person = world.civilizations[home].population.people[scribe]
        person.alive, person.death_day, person.health_bp = False, 0, 0

    kept, results = _run(state, 2)
    lost, lost_results = _run(unarchived, 2)

    def knows(world: WorldState) -> bool:
        return any(
            record.capability is CapabilityId.WRITING
            for record in world.civilizations[home].capabilities
        )

    assert knows(kept) and not _events(results, "capability_forgotten")
    assert not knows(lost) and _events(lost_results, "capability_forgotten")


def test_a_school_halves_teaching_and_lets_a_teacher_take_two_apprentices() -> None:
    state, home, _, _ = treaty_world(distance=4)
    _knowing(state, home, CapabilityId.WRITING)
    people = _at_home(state, home)
    teacher = next(
        record.practitioner_ids[0]
        for record in state.civilizations[home].capabilities
        if record.capability is CapabilityId.WRITING
    )
    pupils = [person_id for person_id in people if person_id != teacher][:2]

    def teach(index: int) -> DirectOrder:
        return DirectOrder(
            command_id=f"teach:{index}",
            kind=DirectOrderKind.START_TEACHING,
            assignment_id=EntityId(f"teaching:{index}"),
            teacher_id=teacher,
            apprentice_id=pupils[index],
            capability=CapabilityId.WRITING,
        )

    assert _codes(state, home, teach(0), teach(1)) == ["teacher_busy"]
    _open(state, home, InstitutionKind.SCHOOL, (people[-1],))
    assert _codes(state, home, teach(0), teach(1)) == []
    state, _ = _run(state, 1, {home: OneShotSovereign(teach(0), teach(1))})
    assignments = state.civilizations[home].teaching_assignments
    assert [item.required_days for item in assignments] == [SCHOOL_TEACHING_DAYS] * 2
    state, _ = _run(state, SCHOOL_TEACHING_DAYS)
    people_now = state.civilizations[home].population.people
    assert all(people_now[pupil].skills.get("writing", 0) > 0 for pupil in pupils)


def test_a_healers_house_doubles_recovery_at_its_settlement() -> None:
    state, home, _, _ = treaty_world(distance=4)
    people = _at_home(state, home)
    patient = people[0]
    state.civilizations[home].population.people[patient].health_bp = 5_000
    healed = state.model_copy(deep=True)
    _open(healed, home, InstitutionKind.HEALERS_HOUSE, (people[-1],))

    plain, _ = _run(state, 1)
    cared, _ = _run(healed, 1)

    gain = plain.civilizations[home].population.people[patient].health_bp - 5_000
    cared_gain = cared.civilizations[home].population.people[patient].health_bp - 5_000
    assert gain > 0 and cared_gain == 2 * gain


def test_a_workshop_speeds_crafting_by_a_quarter() -> None:
    state, home, _, _ = treaty_world(distance=4)
    people = _at_home(state, home)
    capital = state.civilizations[home].settlements[0].tile
    job = CraftJob(
        job_id=EntityId("craft:spears"),
        item=Resource.TOOL,
        quantity=1,
        worker_ids=(people[0],),
        workshop=capital,
        started_day=0,
        person_days_needed=100,
    )
    state.civilizations[home].craft_jobs = (job,)
    workshop = state.model_copy(deep=True)
    _open(workshop, home, InstitutionKind.WORKSHOP, (people[-1],))

    plain, _ = _run(state, 8)
    faster, _ = _run(workshop, 8)

    [slow_job] = plain.civilizations[home].craft_jobs
    [fast_job] = faster.civilizations[home].craft_jobs
    assert slow_job.person_days_done == 8
    assert fast_job.person_days_done == 10


def test_a_diplomatic_service_briefs_envoys_and_its_staff_study_tongues() -> None:
    state, home, rival, route = treaty_world(distance=4)
    envoy = _at_home(state, home)[0]
    text = " ".join(f"word{index}" for index in range(400))
    message = DiplomaticMessage(
        message_id=EntityId("message:briefed"),
        sender_civilization_id=home,
        recipient_civilization_id=rival,
        ambassador_id=envoy,
        route=route[:2],
        source_text=text[:1_000],
        departed_day=0,
    )
    people = {home: state.civilizations[home].population.people}

    def delivered(briefed: frozenset) -> str:
        missions = (message,)
        for day in range(20):
            result = advance_diplomacy_day(
                missions,
                people,
                day=day,
                rng=StableRng(3),
                world_map=state.world_map,
                briefed=briefed,
            )
            missions = result.missions
            if result.delivered:
                return result.delivered[0].delivered_text or ""
        raise AssertionError("never delivered")

    plain = delivered(frozenset()).split(" ").count(LOST_WORD)
    briefed = delivered(frozenset({home})).split(" ").count(LOST_WORD)
    assert briefed < plain

    clerk = _at_home(state, home)[-1]
    _open(state, home, InstitutionKind.DIPLOMATIC_SERVICE, (clerk,))
    state, _ = _run(state, 10)
    assert state.civilizations[home].population.people[clerk].languages.get(rival, 0) >= 2


def test_an_institution_goes_with_its_settlement_and_unbuilt_ones_with_their_founders() -> None:
    state, home, _, _ = treaty_world(distance=4)
    _knowing(state, home, CapabilityId.TIMBERCRAFT)
    people = _at_home(state, home)
    founder = people[-1]
    order = _found(state, home, InstitutionKind.WORKSHOP, (founder,))
    state, _ = _run(state, 1, {home: OneShotSovereign(order)})
    timber = state.civilizations[home].inventory.quantities[Resource.TIMBER]
    person = state.civilizations[home].population.people[founder]
    person.alive, person.death_day, person.health_bp = False, state.day, 0
    state, results = _run(state, 1)
    assert _events(results, "institution_abandoned")
    assert state.civilizations[home].institutions == ()
    assert state.civilizations[home].inventory.quantities[Resource.TIMBER] >= timber + 30

    house = _open(state, home, InstitutionKind.WORKSHOP, (people[0],))
    state.civilizations[home].institutions = (
        house.model_copy(update={"settlement_id": EntityId("settlement:gone")}),
    )
    state, results = _run(state, 1)
    assert _events(results, "institution_lost")
    assert state.civilizations[home].institutions == ()
    validate_world(state)
