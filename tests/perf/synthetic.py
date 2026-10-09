"""Grow a fresh world to a chosen population, deterministically, for benchmarks.

The extra people are ordinary members of each civilization: a mix of
children and adults living at the capital, with skills like the founders'
and food enough for a year. They exist only to measure how the engine's
cost grows with population; nothing here is used by the simulation itself.
"""

from __future__ import annotations

from uuid import UUID

from sovereign_world.config import RunManifest, WorldConfig
from sovereign_world.people import Person, Sex, birth_person_id
from sovereign_world.resources import Inventory, Resource
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState, build_initial_state
from sovereign_world.stores import (
    FOUNDING_GRADE,
    Storehouse,
    founding_capacity,
    founding_storehouses,
)

RUN_ID = UUID("00000000-0000-4000-8000-0000000000be")


FOUNDING_GRANARIES = 5
"""Granaries each grown capital starts with: a real capital's few, not one per 5,000 food."""


def grown_world(
    people: int, *, seed: int = 21, size: int = 48, generator: int = 2, rules: int = 1
) -> WorldState:
    """A day-0 world whose civilizations together hold about `people` living people."""
    config = WorldConfig(seed=seed, width=size, height=size)
    manifest = RunManifest(
        run_id=RUN_ID,
        engine_version="bench",
        config=config,
        generator_version=generator,
        rules_version=rules,
    )
    state = build_initial_state(manifest)
    rng = StableRng(seed).stream("bench:grow")
    per_civilization = max(0, people // len(state.civilizations) - config.founders_per_civilization)
    for civilization in state.civilizations.values():
        population = civilization.population
        founders = list(population.people.values())
        mothers = [person for person in founders if person.sex is Sex.FEMALE]
        fathers = [person for person in founders if person.sex is Sex.MALE]
        template = founders[0].skills
        for _ in range(per_civilization):
            person_id = birth_person_id(civilization.civilization_id, population.next_sequence)
            population.next_sequence += 1
            age_days = int(rng.integers(0, 60 * 365))
            child = age_days < 18 * 365
            parents = (
                (
                    mothers[int(rng.integers(0, len(mothers)))].person_id,
                    fathers[int(rng.integers(0, len(fathers)))].person_id,
                )
                if child
                else ()
            )
            population.people[person_id] = Person(
                person_id=person_id,
                civilization_id=civilization.civilization_id,
                sex=Sex.FEMALE if rng.integers(0, 2) else Sex.MALE,
                birth_day=-age_days,
                age_days=age_days,
                location=civilization.start_center,
                parent_ids=parents,
                health_bp=int(rng.integers(8_000, 10_001)),
                skills={name: int(rng.integers(100, 701)) for name in template},
            )
        # A year of food for everyone. The capital's room is set to hold it directly, in a
        # few granaries: one granary per 5,000 food would give 1,800 per civilization at
        # 100K and swamp every copy, save and report with storehouses no real run builds.
        goods = dict(civilization.inventory.quantities)
        goods[Resource.FOOD] = goods.get(Resource.FOOD, 0) + per_civilization * 365
        total = sum(goods.values())
        capital = civilization.settlements[0].settlement_id
        civilization.inventory = Inventory(capacity=founding_capacity(total), quantities=goods)
        civilization.storehouses = tuple(
            Storehouse(
                storehouse_id=f"storehouse:{capital}:{number:04d}",  # type: ignore[arg-type]
                settlement_id=capital,
                grade=FOUNDING_GRADE,
                built_day=0,
            )
            for number in range(1, min(FOUNDING_GRANARIES, founding_storehouses(total)) + 1)
        )
    return state
