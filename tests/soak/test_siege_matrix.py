"""Seeded sieges: camps that bombard walls, then storm or go home, with daily invariants."""

import pytest
from logistics_helpers import treaty_world

from sovereign_world.commands import CommandEnvelope, CouncilReport, DirectOrder, DirectOrderKind
from sovereign_world.engine import advance_day
from sovereign_world.hexmap import HexCoord
from sovereign_world.ids import EntityId
from sovereign_world.resources import Resource
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState, state_hash, validate_world
from sovereign_world.walls import WALL_GRADES, WallGrade, Walls
from sovereign_world.war import SiegeEnd, WarObjective

DAYS = 70


class BesiegingSovereign:
    """Camp beside the enemy with a catapult, then storm or lift at the next council."""

    def __init__(self, enemy: EntityId, route: tuple[HexCoord, ...], *, storm: bool) -> None:
        self.enemy = enemy
        self.route = route
        self.storm = storm

    def decide(self, report: CouncilReport) -> CommandEnvelope:
        orders: list[DirectOrder] = []
        journey_id = EntityId(f"journey:{report.civilization_id}:siege")
        if report.day == 0:
            orders.append(
                DirectOrder(
                    command_id="siege",
                    kind=DirectOrderKind.SEND_WAR_PARTY,
                    journey_id=journey_id,
                    recipient_civilization_id=self.enemy,
                    traveller_ids=report.person_ids[:16],
                    route=self.route[:-1],
                    cargo={Resource.CATAPULT: 1, Resource.AXE: 8},
                    war_objective=WarObjective.BESIEGE,
                    extra_provisions=300,
                )
            )
        elif any(siege.active for siege in report.sieges):
            orders.append(
                DirectOrder(
                    command_id=f"end:{report.day}",
                    kind=(
                        DirectOrderKind.STORM_SETTLEMENT
                        if self.storm
                        else DirectOrderKind.LIFT_SIEGE
                    ),
                    journey_id=journey_id,
                    war_objective=WarObjective.RAID if self.storm else None,
                )
            )
        return CommandEnvelope(
            schema_version=1,
            civilization_id=report.civilization_id,
            council_day=report.day,
            correlation_id=report.report_id,
            commands=tuple(orders),
        )


class RepairingSovereign:
    """Mend the walls at every council they are damaged."""

    def decide(self, report: CouncilReport) -> CommandEnvelope:
        damaged = [
            walls for walls in report.walls if walls.strength < WALL_GRADES[walls.grade].strength
        ]
        orders = (
            (
                DirectOrder(
                    command_id=f"repair:{report.day}",
                    kind=DirectOrderKind.REPAIR_WALLS,
                    worker_ids=report.person_ids[-3:],
                ),
            )
            if damaged and not report.wall_jobs
            else ()
        )
        return CommandEnvelope(
            schema_version=1,
            civilization_id=report.civilization_id,
            council_day=report.day,
            correlation_id=report.report_id,
            commands=orders,
        )


def _prepare(seed: int) -> tuple[WorldState, EntityId, EntityId, tuple[HexCoord, ...]]:
    state, first, second, route = treaty_world(seed=seed, distance=3 + seed % 3)
    attacker = state.civilizations[first]
    attacker.inventory = attacker.inventory.model_copy(
        update={"quantities": {**attacker.inventory.quantities, Resource.CATAPULT: 1}}
    )
    defender = state.civilizations[second]
    grade = (WallGrade.EARTHWORK, WallGrade.PALISADE, WallGrade.DRYSTONE)[seed % 3]
    defender.walls = (
        Walls(
            settlement_id=defender.settlements[0].settlement_id,
            grade=grade,
            strength=WALL_GRADES[grade].strength,
            towers=min(seed % 3, WALL_GRADES[grade].towers),
            built_day=0,
        ),
    )
    return state, first, second, route


def _simulate(initial: WorldState, first: EntityId, second: EntityId, route, *, storm: bool):
    state = initial.model_copy(deep=True)
    sovereigns = {
        first: BesiegingSovereign(second, route, storm=storm),
        second: RepairingSovereign(),
    }
    rng = StableRng(state.config.seed)
    kinds: list[str] = []
    for _ in range(DAYS):
        before = {
            siege.settlement_tile
            for siege in state.sieges
            if siege.active and siege.defender_id == second
        }
        transition = advance_day(state, rng, sovereigns=sovereigns)
        state = transition.state
        kinds.extend(event.kind for event in transition.events.events)
        validate_world(state)
        for journey in state.journeys:
            if journey.sender_civilization_id == second and journey.departed_day == state.day - 1:
                assert journey.route[0] not in before, "nothing leaves a besieged settlement"
        for journey in state.journeys:
            if journey.encamped:
                assert journey.route_index == len(journey.route) - 1, "a camp never moves"
    return state, kinds


@pytest.mark.soak
@pytest.mark.parametrize("seed", range(8))
def test_seeded_sieges_hold_their_invariants_and_replay_exactly(seed: int) -> None:
    initial, first, second, route = _prepare(seed)
    storm = seed % 2 == 0

    final, kinds = _simulate(initial, first, second, route, storm=storm)
    rerun, rerun_kinds = _simulate(initial, first, second, route, storm=storm)

    assert "siege_began" in kinds
    assert "walls_damaged" in kinds or "walls_fell" in kinds, "the catapult found the walls"
    ends = [siege.end for siege in final.sieges]
    assert ends and all(end is not None for end in ends), "every siege ended"
    if SiegeEnd.STORMED in ends:
        assert any(battle.tile == route[-1] for battle in final.battles)
    elif not storm:
        assert SiegeEnd.RECALLED in ends or SiegeEnd.STARVED in ends
    assert state_hash(rerun) == state_hash(final)
    assert rerun_kinds == kinds
