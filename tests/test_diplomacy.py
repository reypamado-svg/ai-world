from sovereign_world.config import RunManifest, WorldConfig
from sovereign_world.diplomacy import (
    Contact,
    DiplomaticMessage,
    MissionStatus,
    advance_diplomacy_day,
)
from sovereign_world.ids import EntityId
from sovereign_world.rng import StableRng
from sovereign_world.state import build_initial_state


def _state():
    return build_initial_state(
        RunManifest.new(config=WorldConfig(seed=21, width=48, height=48), engine_version="0.1.0")
    )


def test_message_arrives_with_a_link_to_its_original_words() -> None:
    state = _state()
    sender, recipient = sorted(state.civilizations)[:2]
    ambassador = state.civilizations[sender].population.living_ids[0]
    origin = state.civilizations[sender].population.people[ambassador].location
    destination = state.world_map.neighbors(origin)[0]
    message = DiplomaticMessage(
        message_id=EntityId("message:arrival"),
        sender_civilization_id=sender,
        recipient_civilization_id=recipient,
        ambassador_id=ambassador,
        route=(origin, destination),
        source_text="We request peaceful contact.",
        departed_day=0,
    )

    missions = (message,)
    people = {sender: state.civilizations[sender].population.people}
    for day in range(10):
        result = advance_diplomacy_day(missions, people, day=day, rng=StableRng(99))
        missions = result.missions
        people = result.people_by_civilization
        if result.delivered:
            break
    delivered = missions[0]
    assert delivered.status is MissionStatus.DELIVERED
    assert delivered.source_text == "We request peaceful contact."
    assert delivered.delivered_text is not None
    assert result.delivered == (delivered,)


def test_dead_ambassador_loses_the_message() -> None:
    state = _state()
    sender, recipient = sorted(state.civilizations)[:2]
    ambassador = state.civilizations[sender].population.living_ids[0]
    people = state.civilizations[sender].population.people
    people[ambassador].alive = False
    origin = people[ambassador].location
    message = DiplomaticMessage(
        message_id=EntityId("message:lost"),
        sender_civilization_id=sender,
        recipient_civilization_id=recipient,
        ambassador_id=ambassador,
        route=(origin,),
        source_text="This never arrives.",
        departed_day=0,
    )

    result = advance_diplomacy_day(
        (message,), {sender: people}, day=0, rng=StableRng(99)
    )

    assert result.missions[0].status is MissionStatus.LOST
    assert result.delivered == ()


def test_contact_is_a_private_civilization_record() -> None:
    state = _state()
    state_center = state.civilizations[sorted(state.civilizations)[1]].start_center
    contact = Contact(
        civilization_id=EntityId("civilization:0000000002"),
        settlement=state_center,
        first_contact_day=3,
        last_seen_day=3,
    )

    assert contact.civilization_id == EntityId("civilization:0000000002")
    assert contact.settlement == state_center
