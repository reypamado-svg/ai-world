"""Atomic daily transition pipeline."""

from __future__ import annotations

from dataclasses import dataclass

from sovereign_world.events import DomainEvent, EventBatch, EventPhase
from sovereign_world.people import advance_population_day
from sovereign_world.resources import InventoryDelta, Resource
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState, validate_world
from sovereign_world.work import execute_work_day


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


def advance_day(state: WorldState, rng: StableRng) -> TransitionResult:
    candidate = state.model_copy(deep=True)
    events: list[DomainEvent] = []

    for civilization_id in sorted(candidate.civilizations):
        civilization = candidate.civilizations[civilization_id]
        living_count = len(civilization.population.living_ids)
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

        current_living = max(1, len(civilization.population.living_ids))
        food_days = civilization.inventory.quantities.get(Resource.FOOD, 0) // current_living
        population_result = advance_population_day(
            civilization.population,
            day=candidate.day,
            rng=rng.stream(f"day:{candidate.day}:population:{civilization_id}"),
            food_days=food_days,
            shelter_slots=current_living + 64,
        )
        civilization.population = population_result.population
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

