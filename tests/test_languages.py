from logistics_helpers import treaty_world

from sovereign_world.commands import build_council_report
from sovereign_world.config import RunManifest, WorldConfig
from sovereign_world.diplomacy import DiplomaticMessage, MissionStatus, advance_diplomacy_day
from sovereign_world.engine import _change_allegiance, advance_day
from sovereign_world.ids import EntityId
from sovereign_world.languages import (
    FLUENT,
    GESTURE_FIDELITY,
    LEARNING_INTERVAL,
    LOST_WORD,
    WRITING,
    fidelity,
    fluency,
    learn,
    native,
    render,
    speaks,
)
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState, build_initial_state, validate_world

TEXT = "We ask for peace between our peoples and fair trade along the river road."


def _state() -> WorldState:
    return build_initial_state(
        RunManifest.new(config=WorldConfig(seed=21, width=48, height=48), engine_version="0.1.0")
    )


def _deliver(state: WorldState, envoy_id: EntityId) -> DiplomaticMessage:
    sender, recipient = sorted(state.civilizations)[:2]
    people = {sender: state.civilizations[sender].population.people}
    origin = people[sender][envoy_id].location
    missions = (
        DiplomaticMessage(
            message_id=EntityId("message:words"),
            sender_civilization_id=sender,
            recipient_civilization_id=recipient,
            ambassador_id=envoy_id,
            route=(origin, state.world_map.neighbors(origin)[0]),
            source_text=TEXT,
            departed_day=0,
        ),
    )
    for day in range(20):
        result = advance_diplomacy_day(
            missions, people, day=day, rng=StableRng(7), world_map=state.world_map
        )
        missions, people = result.missions, result.people_by_civilization
        if result.delivered:
            break
    [message] = missions
    assert message.status is MissionStatus.DELIVERED
    return message


def test_everyone_speaks_their_own_civilizations_language_and_no_other() -> None:
    state = _state()
    home, other = sorted(state.civilizations)[:2]
    person = next(iter(state.civilizations[home].population.people.values()))
    assert native(person) == home
    assert fluency(person, home) == 100 and speaks(person, home)
    assert fluency(person, other) == 0 and not speaks(person, other)


def test_fidelity_rests_on_fluency_with_gestures_below_and_writing_above() -> None:
    state = _state()
    home, other = sorted(state.civilizations)[:2]
    envoy = next(iter(state.civilizations[home].population.people.values()))
    assert fidelity(envoy, other) == GESTURE_FIDELITY
    assert fidelity(envoy, home) == 100
    envoy.languages = {other: 40}
    assert fidelity(envoy, other) == 40
    envoy.skills = {**envoy.skills, WRITING: 1}
    assert fidelity(envoy, other) == 70


def test_rendering_loses_words_but_keeps_their_places() -> None:
    whole = render(TEXT, 100, StableRng(1).stream("x"))
    assert whole == TEXT
    garbled = render(TEXT, 20, StableRng(1).stream("x")).split(" ")
    assert len(garbled) == len(TEXT.split(" "))
    assert LOST_WORD in garbled
    assert all(word in {LOST_WORD, *TEXT.split(" ")} for word in garbled)


def test_people_living_among_speakers_learn_their_language_slowly() -> None:
    state = _state()
    home, other = sorted(state.civilizations)[:2]
    learner = next(iter(state.civilizations[home].population.people.values()))
    speaker = next(iter(state.civilizations[other].population.people.values()))
    speaker.location = learner.location
    alone = [
        person
        for person in state.civilizations[home].population.people.values()
        if person.location != learner.location
    ]
    gained = learn([learner, speaker, *alone])
    assert gained >= 2, "each learns the other's tongue"
    assert learner.languages == {other: 1} and speaker.languages == {home: 1}
    assert all(other not in person.languages for person in alone)
    for _ in range(FLUENT - 1):
        learn([learner, speaker])
    assert speaks(learner, other)


def test_the_engine_teaches_every_few_days() -> None:
    state, home, rival, _ = treaty_world(distance=4)
    guest_id = state.civilizations[rival].population.living_ids[0]
    host_tile = state.civilizations[home].settlements[0].tile
    state.civilizations[rival].population.people[guest_id].location = host_tile
    rng = StableRng(state.config.seed)
    for _ in range(LEARNING_INTERVAL):
        state = advance_day(state, rng).state
    guest = state.civilizations[rival].population.people[guest_id]
    if guest.alive and guest.location == host_tile:
        assert guest.languages.get(home, 0) >= 1
    validate_world(state)


def test_a_stranger_garbles_a_message_and_a_fluent_envoy_carries_it_whole() -> None:
    state = _state()
    sender, recipient = sorted(state.civilizations)[:2]
    envoy_id = state.civilizations[sender].population.living_ids[0]
    first = _deliver(state.model_copy(deep=True), envoy_id)
    assert first.delivered_text != TEXT and LOST_WORD in (first.delivered_text or "")

    state.civilizations[sender].population.people[envoy_id].languages = {recipient: 100}
    fluent = _deliver(state, envoy_id)
    assert fluent.delivered_text == TEXT


def test_newcomers_keep_their_mother_tongue_and_the_council_knows_its_speakers() -> None:
    state, home, rival, _ = treaty_world(distance=4)
    moving = state.civilizations[rival].population.living_ids[:2]
    _change_allegiance(state, moving, rival, home, "release")
    people = state.civilizations[home].population.people
    for person_id in moving:
        assert people[person_id].native_language == rival
        assert fluency(people[person_id], rival) == 100
        assert fluency(people[person_id], home) == 0
    speakers = build_council_report(state, home).speakers
    assert speakers[rival] == tuple(sorted(moving))
    assert set(speakers[home]).isdisjoint(moving)
