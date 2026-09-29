from logistics_helpers import move_home

from sovereign_world.capabilities import CapabilityRecord
from sovereign_world.commands import CommandEnvelope, DirectOrder, DirectOrderKind
from sovereign_world.config import RunManifest, WorldConfig
from sovereign_world.diplomacy import Contact, MissionStatus
from sovereign_world.engine import advance_day
from sovereign_world.hexmap import Terrain
from sovereign_world.ids import EntityId
from sovereign_world.resources import Resource
from sovereign_world.rng import StableRng
from sovereign_world.state import build_initial_state, state_hash


def _state(seed: int = 12):
    config = WorldConfig(seed=seed, width=48, height=48)
    manifest = RunManifest.new(config=config, engine_version="0.1.0")
    return build_initial_state(manifest)


def test_advance_day_consumes_food_and_increments_day() -> None:
    state = _state()
    before_food = sum(
        civilization.inventory.quantities[Resource.FOOD]
        for civilization in state.civilizations.values()
    )

    result = advance_day(state, StableRng(state.config.seed))

    after_food = sum(
        civilization.inventory.quantities[Resource.FOOD]
        for civilization in result.state.civilizations.values()
    )
    assert result.state.day == 1
    assert before_food - after_food == 128
    assert state.day == 0


def test_transition_is_independent_of_civilization_dict_insertion_order() -> None:
    first = _state()
    second = first.model_copy(
        update={"civilizations": dict(reversed(tuple(first.civilizations.items())))}
    )

    first_result = advance_day(first, StableRng(first.config.seed))
    second_result = advance_day(second, StableRng(second.config.seed))

    assert state_hash(first) == state_hash(second)
    assert state_hash(first_result.state) == state_hash(second_result.state)
    assert first_result.events.canonical_json() == second_result.events.canonical_json()


def test_hunger_accumulates_when_food_is_missing() -> None:
    state = _state()
    civilization_id = sorted(state.civilizations)[0]
    civilization = state.civilizations[civilization_id]
    empty = civilization.inventory.model_copy(update={"quantities": {}})
    state.civilizations[civilization_id] = civilization.model_copy(update={"inventory": empty})

    result = advance_day(state, StableRng(state.config.seed))

    population = result.state.civilizations[civilization_id].population
    assert all(person.nutrition_debt == 1 for person in population.people.values())
    assert any(event.kind == "food_shortage" for event in result.events.events)


def test_last_unrecorded_practitioner_death_forgets_capability() -> None:
    state = _state()
    civilization_id = sorted(state.civilizations)[0]
    civilization = state.civilizations[civilization_id]
    record = civilization.capabilities[0]
    practitioner = record.practitioner_ids[0]
    civilization.capabilities = (
        CapabilityRecord(
            capability=record.capability,
            practitioner_ids=(practitioner,),
            discovered_day=0,
        ),
    )
    civilization.population.people[practitioner].alive = False

    result = advance_day(state, StableRng(state.config.seed))

    assert not result.state.civilizations[civilization_id].capabilities
    assert "capability_forgotten" in {event.kind for event in result.events.events}


def test_validated_teaching_order_becomes_an_assignment() -> None:
    state = _state()
    civilization_id = sorted(state.civilizations)[0]
    civilization = state.civilizations[civilization_id]
    teacher = civilization.capabilities[0].practitioner_ids[0]
    apprentice = next(
        person_id
        for person_id in civilization.population.living_ids
        if person_id != teacher
    )

    class TeachingSovereign:
        def decide(self, report):
            return CommandEnvelope(
                schema_version=1,
                civilization_id=report.civilization_id,
                council_day=report.day,
                correlation_id=report.report_id,
                commands=(
                    DirectOrder(
                        command_id="order:teach",
                        kind=DirectOrderKind.START_TEACHING,
                        assignment_id=EntityId("teaching:1"),
                        teacher_id=teacher,
                        apprentice_id=apprentice,
                        capability=civilization.capabilities[0].capability,
                    ),
                ),
            )

    result = advance_day(
        state,
        StableRng(state.config.seed),
        sovereigns={civilization_id: TeachingSovereign()},
    )

    assignments = result.state.civilizations[civilization_id].teaching_assignments
    assert [assignment.assignment_id for assignment in assignments] == [EntityId("teaching:1")]


def test_expedition_creates_contact_only_when_it_reaches_foreign_settlement() -> None:
    state = _state()
    sender, foreign = sorted(state.civilizations)[:2]
    sender_state = state.civilizations[sender]
    explorer = sender_state.population.living_ids[0]
    origin = sender_state.population.people[explorer].location
    # A grassland neighbour is one day's walk, so contact happens on the first day.
    destination = next(
        tile
        for tile in state.world_map.neighbors(origin)
        if state.world_map.tile(tile).terrain is Terrain.GRASSLAND
    )
    move_home(state.civilizations[foreign], destination)

    class ExplorerSovereign:
        def decide(self, report):
            return CommandEnvelope(
                schema_version=1,
                civilization_id=report.civilization_id,
                council_day=report.day,
                correlation_id=report.report_id,
                commands=(
                    DirectOrder(
                        command_id="order:contact",
                        kind=DirectOrderKind.START_EXPEDITION,
                        expedition_id=EntityId("expedition:contact"),
                        explorer_ids=(explorer,),
                        route=(origin, destination),
                    ),
                ),
            )

    result = advance_day(
        state,
        StableRng(state.config.seed),
        sovereigns={sender: ExplorerSovereign()},
    )

    assert result.state.civilizations[sender].contacts[0].civilization_id == foreign
    assert result.state.civilizations[foreign].contacts == ()
    assert "foreign_settlement_sighted" in {event.kind for event in result.events.events}


def test_validated_message_is_dispatched_and_delivered_to_recipient_only() -> None:
    state = _state()
    sender, recipient = sorted(state.civilizations)[:2]
    sender_state = state.civilizations[sender]
    ambassador = sender_state.population.living_ids[0]
    origin = sender_state.population.people[ambassador].location
    destination = state.world_map.neighbors(origin)[0]
    move_home(state.civilizations[recipient], destination)
    sender_state.contacts = (
        Contact(
            civilization_id=recipient,
            settlement=destination,
            first_contact_day=0,
            last_seen_day=0,
        ),
    )

    class AmbassadorSovereign:
        def decide(self, report):
            return CommandEnvelope(
                schema_version=1,
                civilization_id=report.civilization_id,
                council_day=report.day,
                correlation_id=report.report_id,
                commands=(
                    DirectOrder(
                        command_id="order:message",
                        kind=DirectOrderKind.SEND_MESSAGE,
                        message_id=EntityId("message:peace"),
                        ambassador_id=ambassador,
                        recipient_civilization_id=recipient,
                        message_text="We seek peaceful contact.",
                        route=(origin, destination),
                    ),
                ),
            )

    result = advance_day(
        state,
        StableRng(state.config.seed),
        sovereigns={sender: AmbassadorSovereign()},
    )
    for _ in range(10):
        if result.state.diplomatic_missions[0].status is not MissionStatus.IN_TRANSIT:
            break
        result = advance_day(result.state, StableRng(result.state.config.seed))

    message = result.state.diplomatic_missions[0]
    assert message.status is MissionStatus.DELIVERED
    assert result.state.civilizations[recipient].received_messages == (message,)
    assert result.state.civilizations[sender].received_messages == ()

