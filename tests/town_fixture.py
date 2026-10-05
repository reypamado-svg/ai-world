"""A small rules-3 run whose first capital is designed and walled, for the observer.

On day 0 the council of the first civilization lays its capital out as a ringed town (keep
at the centre, market by the store, gates east and west) and sets four builders to raise six
palisade sections; by day 18 most of them stand and the rest are a planned line. The second
civilization's capital is fortified from day 0: a complete palisade ring with towers and
gatehouses on its two gates, a ditch, stakes and a citadel, and a standing defence order.
The export of days 0 and 18 is committed as `observer/tests/fixtures/run-town`.
"""

from __future__ import annotations

from pathlib import Path

from logistics_helpers import OneShotSovereign

from sovereign_world.capabilities import CapabilityId
from sovereign_world.commands import DirectOrder, DirectOrderKind
from sovereign_world.config import RunManifest, WorldConfig
from sovereign_world.defence import DefenceOrder
from sovereign_world.engine import advance_day
from sovereign_world.persistence import WorldStore
from sovereign_world.rings import DITCH, Citadel, empty_ring
from sovereign_world.rng import StableRng
from sovereign_world.scripted import BASELINE_DEFENCE
from sovereign_world.state import CivilizationState, build_initial_state
from sovereign_world.townplan import Place, PlanStyle, TownPlan, TownPlanSpec
from sovereign_world.walls import WALL_GRADES, WallGrade

DAYS = 18
EXPORTED = (0, DAYS)


def record_town(root: Path) -> None:
    manifest = RunManifest.model_validate(
        {
            "run_id": "00000000-0000-0000-0000-00000000007a",
            "config": WorldConfig(seed=21, width=24, height=24),
            "engine_version": "0.2.0",
            "generator_version": 3,
            "rules_version": 3,
            "journal_format": 2,
        }
    )
    state = build_initial_state(manifest)
    home = sorted(state.civilizations)[0]
    civilization = state.civilizations[home]
    [capital] = civilization.settlements
    builders = civilization.population.living_ids[:4]
    for person_id in builders:
        person = civilization.population.people[person_id]
        person.skills = {**person.skills, CapabilityId.TIMBERCRAFT.value: 1}
    _fortify(state.civilizations[sorted(state.civilizations)[1]])
    store = WorldStore.create(root, manifest, state)
    orders = (
        DirectOrder(
            command_id="design",
            kind=DirectOrderKind.PLAN_SETTLEMENT,
            settlement_id=capital.settlement_id,
            town_plan=TownPlanSpec(
                style=PlanStyle.RINGED,
                keep=Place.CENTRE,
                market=Place.BY_STORE,
                shrine=Place.EDGE,
                wall_ring=2,
                gates=(0, 3),
            ),
        ),
        DirectOrder(
            command_id="palisade",
            kind=DirectOrderKind.BUILD_WALLS,
            worker_ids=tuple(builders),
            wall_grade=WallGrade.PALISADE,
            wall_sections=6,
        ),
    )
    sovereigns = {home: OneShotSovereign(*orders)}
    rng = StableRng(state.config.seed)
    previous = state
    for _ in range(DAYS):
        transition = advance_day(previous, rng, sovereigns=sovereigns)
        store.append_transition(transition.state, transition.events, previous=previous)
        previous = transition.state


def _fortify(civilization: CivilizationState) -> None:
    """A capital with every defensive work a council can raise on a palisade ring."""
    [capital] = civilization.settlements
    sid = capital.settlement_id
    civilization.town_plans = {
        sid: TownPlan(
            settlement_id=sid,
            planned_day=0,
            style=PlanStyle.RINGED,
            keep=Place.CENTRE,
            market=Place.BY_STORE,
            wall_ring=2,
            gates=(0, 3),
        )
    }
    strength = WALL_GRADES[WallGrade.PALISADE].strength
    ring = empty_ring(sid, 2, (0, 3), 0)
    sections = tuple(
        item.model_copy(
            update={"grade": WallGrade.PALISADE, "strength": strength, "gatehouse": item.gate}
        )
        for item in ring.sections
    )
    civilization.wall_rings = {
        sid: ring.model_copy(
            update={
                "sections": sections,
                "towers": 2,
                "tower_sections": (0, 5),
                "ditch": DITCH,
                "stakes": True,
            }
        )
    }
    civilization.citadels = {sid: Citadel(grade=WallGrade.PALISADE, strength=strength, built_day=0)}
    civilization.defence_orders = {
        sid: DefenceOrder(settlement_id=sid, set_day=0, **BASELINE_DEFENCE.model_dump())
    }
