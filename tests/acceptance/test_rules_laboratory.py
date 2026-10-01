from pathlib import Path

from sovereign_world.config import RunManifest, WorldConfig
from sovereign_world.engine import advance_day
from sovereign_world.ids import EntityId
from sovereign_world.people import ScheduledBirth
from sovereign_world.persistence import WorldStore
from sovereign_world.replay import replay_run, verify_run
from sovereign_world.resources import Inventory, Resource
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState, build_initial_state, state_hash, validate_world
from sovereign_world.work import ConstructionProject, ProjectStatus, WorkKind, WorkOrder


def _prepare_multigenerational_fixture() -> tuple[RunManifest, WorldState]:
    manifest = RunManifest.new(
        WorldConfig(seed=73, width=24, height=24),
        engine_version="0.1.0",
    )
    state = build_initial_state(manifest)
    for civilization_id, civilization in state.civilizations.items():
        parents = civilization.population.living_ids[:2]
        civilization.population.scheduled_births = (
            ScheduledBirth(due_day=0, parent_ids=(parents[0], parents[1])),
        )
        quantities = dict(civilization.inventory.quantities)
        quantities[Resource.FOOD] = 300_000
        civilization.inventory = Inventory(capacity=400_000, quantities=quantities)

        shelter_id = EntityId(f"project:shelter:{civilization_id}")
        storage_id = EntityId(f"project:storage:{civilization_id}")
        for project_id in (shelter_id, storage_id):
            civilization.projects[project_id] = ConstructionProject(
                project_id=project_id,
                location=civilization.start_center,
                required_materials={Resource.TIMBER: 40},
                delivered_materials={Resource.TIMBER: 40},
                required_labor_minutes=480,
            )
        civilization.work_orders = (
            WorkOrder(
                order_id=EntityId(f"work:shelter:{civilization_id}"),
                kind=WorkKind.CONSTRUCT,
                worker_ids=(civilization.population.living_ids[0],),
                project_id=shelter_id,
            ),
            WorkOrder(
                order_id=EntityId(f"work:storage:{civilization_id}"),
                kind=WorkKind.CONSTRUCT,
                worker_ids=(civilization.population.living_ids[1],),
                project_id=storage_id,
            ),
        )
    return manifest, state


def test_second_generation_reaches_adulthood_with_completed_buildings(
    tmp_path: Path,
) -> None:
    manifest, state = _prepare_multigenerational_fixture()
    store = WorldStore.create(tmp_path, manifest, state)
    rng = StableRng(manifest.config.seed)
    last_events = None

    for _ in range(18 * 365):
        transition = advance_day(state, rng)
        state = transition.state
        last_events = transition.events

    validate_world(state)
    assert last_events is not None
    for civilization in state.civilizations.values():
        second_generation = [
            person
            for person in civilization.population.people.values()
            if person.parent_ids and person.alive and person.age_days >= 18 * 365
        ]
        assert second_generation
        assert (
            sum(
                project.status is ProjectStatus.COMPLETE
                for project in civilization.projects.values()
            )
            >= 2
        )
        assert all(quantity >= 0 for quantity in civilization.inventory.quantities.values())

    store.append_transition(state, last_events)
    store.save_checkpoint(state)
    replayed = replay_run(store, target_day=state.day)
    verification = verify_run(store)
    assert state_hash(replayed) == state_hash(state)
    assert verification.state_hash == state_hash(state)
