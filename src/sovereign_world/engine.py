"""Atomic daily transition pipeline."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass

from sovereign_world.capabilities import KnowledgeState, TeachingAssignment, advance_knowledge_day
from sovereign_world.commands import (
    Decree,
    DirectOrder,
    DirectOrderKind,
    ProjectKind,
    build_council_report,
    validate_envelope,
)
from sovereign_world.events import DomainEvent, EventBatch, EventPhase
from sovereign_world.exploration import Expedition, advance_expeditions
from sovereign_world.ids import EntityId
from sovereign_world.people import advance_population_day
from sovereign_world.resources import InventoryDelta, Resource
from sovereign_world.rng import StableRng
from sovereign_world.scripted import Sovereign
from sovereign_world.state import WorldState, validate_world
from sovereign_world.work import ConstructionProject, WorkKind, WorkOrder, execute_work_day


@dataclass(frozen=True, slots=True)
class TransitionResult:
    state: WorldState
    events: EventBatch


def _event(
    state: WorldState,
    phase: EventPhase,
    kind: str,
    actor_id: str | None,
    subject_id: str | None = None,
    **payload: int | str | bool,
) -> DomainEvent:
    return DomainEvent(
        run_id=state.run_id,
        day=state.day,
        phase=phase,
        sequence=0,
        kind=kind,
        actor_id=actor_id,
        subject_id=subject_id,
        payload=payload,
    )


def _run_councils(
    state: WorldState,
    sovereigns: Mapping[EntityId, Sovereign],
) -> list[DomainEvent]:
    events: list[DomainEvent] = []
    if state.day % state.config.council_interval_days != 0:
        return events
    for civilization_id in sorted(sovereigns):
        if civilization_id not in state.civilizations:
            continue
        sovereign = sovereigns[civilization_id]
        try:
            envelope = sovereign.decide(build_council_report(state, civilization_id))
        except Exception as exception:
            events.append(
                _event(
                    state,
                    EventPhase.COMMAND,
                    "sovereign_unavailable",
                    str(civilization_id),
                    error_type=type(exception).__name__,
                )
            )
            continue
        validation = validate_envelope(envelope, state)
        decrees = state.active_decrees.setdefault(civilization_id, {})
        for command in validation.accepted:
            if isinstance(command, Decree):
                decrees[command.kind.value] = command.value
                decrees[f"{command.kind.value}_expires"] = state.day + command.duration_days
            elif (
                isinstance(command, DirectOrder)
                and command.kind is DirectOrderKind.START_PROJECT
                and command.project_id is not None
                and command.project_kind is not None
            ):
                civilization = state.civilizations[civilization_id]
                if command.project_id not in civilization.projects:
                    is_storage = command.project_kind is ProjectKind.STORAGE
                    resource = Resource.STONE if is_storage else Resource.TIMBER
                    quantity = 30 if is_storage else 40
                    if civilization.inventory.quantities.get(resource, 0) >= quantity:
                        civilization.inventory = civilization.inventory.apply_delta(
                            InventoryDelta(changes={resource: -quantity})
                        )
                        civilization.projects[command.project_id] = ConstructionProject(
                            project_id=command.project_id,
                            location=civilization.start_center,
                            required_materials={resource: quantity},
                            delivered_materials={resource: quantity},
                            required_labor_minutes=480 * len(command.worker_ids),
                        )
                        civilization.work_orders += (
                            WorkOrder(
                                order_id=EntityId(f"work:{command.project_id}"),
                                kind=WorkKind.CONSTRUCT,
                                worker_ids=command.worker_ids,
                                project_id=command.project_id,
                            ),
                        )
                        events.append(
                            _event(
                                state,
                                EventPhase.PROJECT,
                                "project_started",
                                str(civilization_id),
                                str(command.project_id),
                            )
                        )
            elif (
                isinstance(command, DirectOrder)
                and command.kind is DirectOrderKind.START_TEACHING
                and command.assignment_id is not None
                and command.teacher_id is not None
                and command.apprentice_id is not None
                and command.capability is not None
            ):
                civilization = state.civilizations[civilization_id]
                civilization.teaching_assignments = tuple(
                    sorted(
                        (*civilization.teaching_assignments, TeachingAssignment(
                            assignment_id=command.assignment_id,
                            teacher_id=command.teacher_id,
                            apprentice_id=command.apprentice_id,
                            capability=command.capability,
                            started_day=state.day,
                        )),
                        key=lambda assignment: assignment.assignment_id,
                    )
                )
            elif (
                isinstance(command, DirectOrder)
                and command.kind is DirectOrderKind.START_EXPEDITION
                and command.expedition_id is not None
                and command.explorer_ids
                and command.route
            ):
                civilization = state.civilizations[civilization_id]
                civilization.expeditions = tuple(
                    sorted(
                        (
                            *civilization.expeditions,
                            Expedition(
                                expedition_id=command.expedition_id,
                                explorer_ids=command.explorer_ids,
                                route=command.route,
                            ),
                        ),
                        key=lambda expedition: expedition.expedition_id,
                    )
                )
                events.append(
                    _event(
                        state,
                        EventPhase.MOVEMENT,
                        "expedition_started",
                        str(civilization_id),
                        str(command.expedition_id),
                    )
                )
            events.append(
                _event(
                    state,
                    EventPhase.COMMAND,
                    "command_accepted",
                    str(civilization_id),
                    command_id=command.command_id,
                )
            )
        for validation_error in validation.errors:
            events.append(
                _event(
                    state,
                    EventPhase.COMMAND,
                    "command_rejected",
                    str(civilization_id),
                    code=validation_error.code,
                )
            )
    return events


def advance_day(
    state: WorldState,
    rng: StableRng,
    *,
    sovereigns: Mapping[EntityId, Sovereign] | None = None,
) -> TransitionResult:
    candidate = state.model_copy(deep=False)
    candidate.civilizations = {
        civilization_id: civilization.model_copy(deep=True)
        for civilization_id, civilization in state.civilizations.items()
    }
    candidate.active_decrees = deepcopy(state.active_decrees)
    events: list[DomainEvent] = []
    if sovereigns is not None:
        events.extend(_run_councils(candidate, sovereigns))

    for civilization_id in sorted(candidate.civilizations):
        civilization = candidate.civilizations[civilization_id]
        expedition_result = advance_expeditions(
            civilization.expeditions,
            civilization.population.people,
            candidate.world_map,
            candidate.day,
            observations=civilization.observations,
        )
        civilization.expeditions = expedition_result.expeditions
        civilization.observations = expedition_result.observations
        civilization.known_tiles = tuple(
            observation.tile for observation in civilization.observations
        )
        civilization.population = civilization.population.model_copy(
            update={"people": expedition_result.people}
        )
        for tile in expedition_result.observed_tiles:
            events.append(
                _event(
                    candidate,
                    EventPhase.MOVEMENT,
                    "tile_observed",
                    str(civilization_id),
                    tile_q=tile.q,
                    tile_r=tile.r,
                )
            )
        for expedition_id in expedition_result.returned_ids:
            events.append(
                _event(
                    candidate,
                    EventPhase.MOVEMENT,
                    "expedition_returned",
                    str(civilization_id),
                    str(expedition_id),
                )
            )
        for expedition_id in expedition_result.failed_ids:
            events.append(
                _event(
                    candidate,
                    EventPhase.MOVEMENT,
                    "expedition_failed",
                    str(civilization_id),
                    str(expedition_id),
                )
            )

    for civilization_id in sorted(candidate.civilizations):
        civilization = candidate.civilizations[civilization_id]
        living_count = len(civilization.population.living_ids)
        decrees = candidate.active_decrees.get(civilization_id, {})
        reserve_days = decrees.get("food_reserve_target", 0)
        labor_priority = decrees.get("labor_priority", 0)
        current_food = civilization.inventory.quantities.get(Resource.FOOD, 0)
        target_food = living_count * reserve_days
        if living_count and labor_priority > 0 and current_food < target_food:
            capacity = civilization.inventory.capacity - civilization.inventory.total_units
            farm_capacity = sum(
                (candidate.world_map.tile(coord).soil // 200)
                + (2 if candidate.world_map.tile(coord).has_water else 0)
                for coord in civilization.known_tiles
            )
            produced = min(living_count, farm_capacity, target_food - current_food, capacity)
            if produced:
                civilization.inventory = civilization.inventory.apply_delta(
                    InventoryDelta(changes={Resource.FOOD: produced})
                )
                events.append(
                    _event(
                        candidate,
                        EventPhase.WORK,
                        "food_produced",
                        str(civilization_id),
                        units=produced,
                    )
                )
        available_food = civilization.inventory.quantities.get(Resource.FOOD, 0)
        consumed = min(living_count, available_food)
        if consumed:
            civilization.inventory = civilization.inventory.apply_delta(
                InventoryDelta(changes={Resource.FOOD: -consumed})
            )
        events.append(
            _event(
                candidate,
                EventPhase.CONSUMPTION,
                "food_consumed",
                str(civilization_id),
                units=consumed,
            )
        )
        if consumed < living_count:
            shortage = living_count - consumed
            for person_id in civilization.population.living_ids[consumed:]:
                civilization.population.people[person_id].nutrition_debt += 1
            events.append(
                _event(
                    candidate,
                    EventPhase.CONSUMPTION,
                    "food_shortage",
                    str(civilization_id),
                    people=shortage,
                )
            )

        work_result = execute_work_day(
            civilization.work_orders,
            civilization.population.people,
            civilization.inventory,
            civilization.projects,
        )
        civilization.inventory = work_result.inventory
        civilization.projects = work_result.projects
        for order_id in work_result.completed_order_ids:
            events.append(
                _event(candidate, EventPhase.WORK, "work_completed", str(order_id))
            )

        knowledge_result = advance_knowledge_day(
            KnowledgeState(
                records=civilization.capabilities,
                assignments=civilization.teaching_assignments,
            ),
            civilization.population.people,
            candidate.day,
        )
        civilization.capabilities = knowledge_result.knowledge.records
        civilization.teaching_assignments = knowledge_result.knowledge.assignments
        civilization.population = civilization.population.model_copy(
            update={"people": knowledge_result.people}
        )
        for capability in knowledge_result.learned:
            events.append(
                _event(
                    candidate,
                    EventPhase.WORK,
                    "capability_learned",
                    str(civilization_id),
                    capability=capability.value,
                )
            )
        for capability in knowledge_result.forgotten:
            events.append(
                _event(
                    candidate,
                    EventPhase.WORK,
                    "capability_forgotten",
                    str(civilization_id),
                    capability=capability.value,
                )
            )

        current_living = max(1, len(civilization.population.living_ids))
        food_days = civilization.inventory.quantities.get(Resource.FOOD, 0) // current_living
        growth_policy = candidate.active_decrees.get(civilization_id, {}).get(
            "population_growth_policy",
            0,
        )
        population_result = advance_population_day(
            civilization.population,
            day=candidate.day,
            rng=rng.stream(f"day:{candidate.day}:population:{civilization_id}"),
            food_days=food_days,
            shelter_slots=current_living + 64 if growth_policy > 0 else 0,
        )
        civilization.population = population_result.population
        mortality_knowledge_result = advance_knowledge_day(
            KnowledgeState(
                records=civilization.capabilities,
                assignments=civilization.teaching_assignments,
            ),
            civilization.population.people,
            candidate.day,
        )
        civilization.capabilities = mortality_knowledge_result.knowledge.records
        civilization.teaching_assignments = mortality_knowledge_result.knowledge.assignments
        civilization.population = civilization.population.model_copy(
            update={"people": mortality_knowledge_result.people}
        )
        for capability in mortality_knowledge_result.learned:
            events.append(
                _event(
                    candidate,
                    EventPhase.WORK,
                    "capability_learned",
                    str(civilization_id),
                    capability=capability.value,
                )
            )
        for capability in mortality_knowledge_result.forgotten:
            events.append(
                _event(
                    candidate,
                    EventPhase.WORK,
                    "capability_forgotten",
                    str(civilization_id),
                    capability=capability.value,
                )
            )
        for birth in population_result.births:
            events.append(
                _event(
                    candidate,
                    EventPhase.BIRTH,
                    "person_born",
                    str(civilization_id),
                    str(birth.person_id),
                )
            )
        for death in population_result.deaths:
            events.append(
                _event(
                    candidate,
                    EventPhase.DEATH,
                    "person_died",
                    str(civilization_id),
                    str(death.person_id),
                    cause=death.cause,
                )
            )
        for project_id in work_result.completed_project_ids:
            events.append(
                _event(
                    candidate,
                    EventPhase.PROJECT,
                    "building_completed",
                    str(civilization_id),
                    str(project_id),
                )
            )

    candidate.day += 1
    validate_world(candidate)
    return TransitionResult(state=candidate, events=EventBatch.assign_sequences(events))
