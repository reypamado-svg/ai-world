"""Seeded wars fought with crafted kits, engines and researched doctrine: nothing is ever
made from nothing."""

import pytest
from logistics_helpers import treaty_world

from sovereign_world.armoury import ENGINES, KITS
from sovereign_world.capabilities import CapabilityId
from sovereign_world.commands import CommandEnvelope, CouncilReport, DirectOrder, DirectOrderKind
from sovereign_world.engine import advance_day
from sovereign_world.hexmap import HexCoord
from sovereign_world.ids import EntityId
from sovereign_world.logistics import JourneyKind
from sovereign_world.resources import Resource
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState, state_hash
from sovereign_world.war import WarObjective

DAYS = 75
GEAR = (*KITS, *ENGINES)


class ArmingSovereign:
    """Make slings and ladders and study spear formations at the first council, then raid."""

    def __init__(self, enemy: EntityId, route: tuple[HexCoord, ...], *, raids: bool) -> None:
        self.enemy = enemy
        self.route = route
        self.raids = raids

    def decide(self, report: CouncilReport) -> CommandEnvelope:
        people = report.person_ids
        if report.day == 0:
            orders = [
                DirectOrder(
                    command_id="slings",
                    kind=DirectOrderKind.CRAFT_EQUIPMENT,
                    worker_ids=people[:2],
                    craft_item=Resource.SLING,
                    craft_quantity=8,
                ),
                DirectOrder(
                    command_id="ladders",
                    kind=DirectOrderKind.CRAFT_EQUIPMENT,
                    worker_ids=people[2:4],
                    craft_item=Resource.LADDER,
                    craft_quantity=1,
                ),
                DirectOrder(
                    command_id="formations",
                    kind=DirectOrderKind.RESEARCH,
                    worker_ids=people[-4:],
                    research_topic=CapabilityId.SPEAR_FORMATIONS,
                    research_days=60,
                ),
            ]
        elif self.raids:
            council = report.day // 30
            gear = {
                resource: count
                for resource, count in report.inventory.items()
                if resource in (Resource.SLING, Resource.AXE, Resource.LADDER) and count
            }
            fighters = people[4 + council * 10 : 14 + council * 10]
            kits = min(gear.get(Resource.SLING, 0), len(fighters) - 2)
            cargo = {Resource.SLING: kits} if kits else {}
            if gear.get(Resource.LADDER):
                cargo[Resource.LADDER] = 1
            orders = [
                DirectOrder(
                    command_id=f"raid:{report.day}",
                    kind=DirectOrderKind.SEND_WAR_PARTY,
                    journey_id=EntityId(f"journey:{report.civilization_id}:raid:{council}"),
                    recipient_civilization_id=self.enemy,
                    traveller_ids=fighters,
                    route=self.route,
                    cargo=cargo,
                    war_objective=WarObjective.RAID,
                )
            ]
        else:
            orders = []
        return CommandEnvelope(
            schema_version=1,
            civilization_id=report.civilization_id,
            council_day=report.day,
            correlation_id=report.report_id,
            commands=tuple(orders),
        )


def _gear(state: WorldState, resource: Resource) -> int:
    """Items in stores, carried by war parties, or plundered."""
    stored = sum(
        civilization.inventory.quantities.get(resource, 0)
        for civilization in state.civilizations.values()
    )
    carried = sum(
        journey.cargo.get(resource, 0) + journey.plunder.get(resource, 0)
        for journey in state.journeys
        if journey.kind is JourneyKind.CAMPAIGN and journey.active
    )
    return stored + carried


def _simulate(initial: WorldState, first: EntityId, second: EntityId, route):
    state = initial.model_copy(deep=True)
    sovereigns = {
        first: ArmingSovereign(second, route, raids=True),
        second: ArmingSovereign(first, tuple(reversed(route)), raids=False),
    }
    rng = StableRng(state.config.seed)
    made = {resource: _gear(state, resource) for resource in GEAR}
    kinds: list[str] = []
    for _ in range(DAYS):
        transition = advance_day(state, rng, sovereigns=sovereigns)
        state = transition.state
        for event in transition.events.events:
            kinds.append(event.kind)
            if event.kind == "equipment_crafted":
                made[Resource(event.payload["item"])] += int(event.payload["quantity"])
        for resource in GEAR:
            assert 0 <= _gear(state, resource) <= made[resource], f"{resource} from nothing"
    return state, kinds


@pytest.mark.soak
@pytest.mark.parametrize("seed", range(8))
def test_seeded_armed_wars_never_make_gear_from_nothing(seed: int) -> None:
    initial, first, second, route = treaty_world(seed=seed, distance=2 + seed % 4)

    final, kinds = _simulate(initial, first, second, route)
    rerun, rerun_kinds = _simulate(initial, first, second, route)

    assert kinds.count("equipment_crafted") >= 2, "both sides armed themselves"
    assert kinds.count("research_completed") == 2, "both sides learned spear formations"
    assert "battle_joined" in kinds
    assert state_hash(rerun) == state_hash(final)
    assert rerun_kinds == kinds
