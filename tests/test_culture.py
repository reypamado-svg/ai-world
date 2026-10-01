from logistics_helpers import treaty_world

from sovereign_world.commands import build_council_report
from sovereign_world.culture import (
    ASSIMILATED,
    FLUENT_ASSIMILATION,
    MONTHLY_ASSIMILATION,
    ancestry,
    assimilate,
    culture,
)
from sovereign_world.engine import TransitionResult, _change_allegiance, advance_day
from sovereign_world.ids import EntityId
from sovereign_world.people import CHILDHOOD_TONGUE, ScheduledBirth, Sex
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState


def _run(state: WorldState, days: int) -> tuple[WorldState, list[TransitionResult]]:
    rng = StableRng(state.config.seed)
    results: list[TransitionResult] = []
    for _ in range(days):
        result = advance_day(state, rng)
        state = result.state
        results.append(result)
    return state, results


def _events(results: list[TransitionResult], kind: str):
    return [event for result in results for event in result.events.events if event.kind == kind]


def _newcomers(state: WorldState, origin: EntityId, destination: EntityId, count: int = 2):
    people = state.civilizations[origin].population
    moving = tuple(
        person_id
        for person_id in people.living_ids
        if not any(birth.parent_ids[0] == person_id for birth in people.scheduled_births)
    )[:count]
    _change_allegiance(state, moving, origin, destination, "release")
    return moving


def test_founders_live_by_their_own_culture_and_ancestry() -> None:
    state, home, _, _ = treaty_world(distance=4)
    person = next(iter(state.civilizations[home].population.people.values()))
    assert culture(person) == home and ancestry(person) == (home,)
    assert person.culture is None and person.ancestry == ()


def test_newcomers_keep_their_culture_and_ancestry_and_going_home_ends_it() -> None:
    state, home, rival, _ = treaty_world(distance=4)
    moving = _newcomers(state, rival, home)
    people = state.civilizations[home].population.people
    for person_id in moving:
        person = people[person_id]
        assert person.civilization_id == home
        assert culture(person) == rival and ancestry(person) == (rival,)
        assert person.assimilation == 0
    _change_allegiance(state, moving[:1], home, rival, "release")
    back = state.civilizations[rival].population.people[moving[0]]
    assert back.culture is None and ancestry(back) == (rival,)


def test_assimilation_takes_years_and_goes_faster_in_the_language() -> None:
    state, home, rival, _ = treaty_world(distance=4)
    [stranger_id, speaker_id] = _newcomers(state, rival, home)
    people = state.civilizations[home].population.people
    stranger, speaker = people[stranger_id], people[speaker_id]
    speaker.languages = {home: 80}

    def months(person) -> int:
        count = 0
        while not assimilate(person):
            count += 1
        return count + 1

    assert months(stranger) == -(-ASSIMILATED // MONTHLY_ASSIMILATION)
    assert months(speaker) == -(-ASSIMILATED // (MONTHLY_ASSIMILATION + FLUENT_ASSIMILATION))
    assert culture(stranger) == home and ancestry(stranger) == (rival,), "ancestry is for life"


def test_the_engine_assimilates_monthly_and_records_it() -> None:
    state, home, rival, _ = treaty_world(distance=4)
    [newcomer] = _newcomers(state, rival, home, count=1)
    person = state.civilizations[home].population.people[newcomer]
    person.assimilation = ASSIMILATED - 1
    state.day = 29
    state, results = _run(state, 2)
    [event] = _events(results, "person_assimilated")
    assert event.subject_id == newcomer and event.payload["culture"] == str(rival)
    assert state.civilizations[home].population.people[newcomer].culture is None


def test_a_newcomer_mothers_child_is_of_its_birthplace_with_her_tongue_and_ancestry() -> None:
    state, home, rival, _ = treaty_world(distance=4)
    newcomers = _newcomers(state, rival, home, count=6)
    population = state.civilizations[home].population
    women = [
        person_id
        for person_id in newcomers
        if population.people[person_id].sex is Sex.FEMALE
        and population.people[person_id].age_days >= 16 * 365
    ]
    mother = women[0]
    father = next(
        person_id
        for person_id in population.living_ids
        if population.people[person_id].sex is Sex.MALE
        and population.people[person_id].culture is None
    )
    population = state.civilizations[home].population
    state.civilizations[home].population = population.model_copy(
        update={
            "scheduled_births": (
                *population.scheduled_births,
                ScheduledBirth(due_day=state.day, parent_ids=(mother, father)),
            )
        }
    )
    before = set(state.civilizations[home].population.people)
    state, _ = _run(state, 1)
    [child_id] = set(state.civilizations[home].population.people) - before
    child = state.civilizations[home].population.people[child_id]
    assert culture(child) == home and child.native_language is None
    assert ancestry(child) == tuple(sorted({home, rival}))
    assert set(child.languages) == {rival} and child.languages[rival] >= CHILDHOOD_TONGUE


def test_the_council_sees_how_blended_its_people_are() -> None:
    state, home, rival, _ = treaty_world(distance=4)
    moving = _newcomers(state, rival, home, count=3)
    report = build_council_report(state, home)
    living = len(state.civilizations[home].population.living_ids)
    assert report.cultures == {home: living - 3, rival: 3}
    assert report.ancestries[rival] == 3 and report.assimilating == len(moving)
