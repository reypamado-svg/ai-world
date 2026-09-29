"""Real consecutive days of derived territory: anchored settlements, thresholds, no flicker."""

from collections import Counter

import pytest

from sovereign_world.commands import (
    CommandEnvelope,
    CouncilReport,
    DirectOrder,
    DirectOrderKind,
)
from sovereign_world.config import RunManifest, WorldConfig
from sovereign_world.engine import advance_day
from sovereign_world.hexmap import HexCoord, Terrain
from sovereign_world.ids import EntityId
from sovereign_world.rng import StableRng
from sovereign_world.roads import RoadGrade, rank
from sovereign_world.scripted import BaselineSovereign, plan_baseline_commands
from sovereign_world.state import WorldState, build_initial_state, state_hash
from sovereign_world.territory import LOSE_THRESHOLD, TAKE_THRESHOLD

DAYS = 90


class ExpandingSovereign:
    """Baseline orders, settlers sent to the nearest open known site at each council, and
    road crews sent to raise a track to the first colony once it stands."""

    def decide(self, report: CouncilReport) -> CommandEnvelope:
        orders = [*plan_baseline_commands(report), *self._roads(report)]
        founding = self._found(report) if report.day else None
        if founding is not None:
            orders.append(founding)
        return CommandEnvelope(
            schema_version=1,
            civilization_id=report.civilization_id,
            council_day=report.day,
            correlation_id=report.report_id,
            commands=tuple(orders[:8]),
        )

    @staticmethod
    def _foreign(report: CouncilReport) -> set[HexCoord]:
        return {
            view.tile
            for view in report.observed_control
            if view.owner not in {None, report.civilization_id}
        }

    @staticmethod
    def _routes(
        report: CouncilReport, avoid: set[HexCoord]
    ) -> dict[HexCoord, tuple[HexCoord, ...]]:
        """Shortest routes over known land from home, in a fixed order."""
        land = {tile for tile, terrain in report.known_terrain if terrain is not Terrain.WATER}
        home = report.start_center
        routes = {home: (home,)}
        frontier = [home]
        while frontier:
            tile = frontier.pop(0)
            for neighbor in sorted(tile.neighbors()):
                if neighbor in land and neighbor not in avoid and neighbor not in routes:
                    routes[neighbor] = (*routes[tile], neighbor)
                    frontier.append(neighbor)
        return routes

    def _roads(self, report: CouncilReport) -> list[DirectOrder]:
        """Three small crews, since a council cannot see which mothers are expecting."""
        colonies = [settlement for settlement in report.settlements if not settlement.capital]
        if not colonies:
            return []
        route = self._routes(report, self._foreign(report)).get(colonies[0].tile)
        if route is None:
            return []
        return [
            DirectOrder(
                command_id=f"road:{report.day}:{crew}",
                kind=DirectOrderKind.BUILD_ROAD,
                journey_id=EntityId(f"journey:{report.civilization_id}:road:{report.day}:{crew}"),
                traveller_ids=report.person_ids[6 + 3 * crew : 9 + 3 * crew],
                route=route,
                road_grade=RoadGrade.TRACK,
            )
            for crew in range(3)
        ]

    def _found(self, report: CouncilReport) -> DirectOrder | None:
        taken = {settlement.tile for settlement in report.settlements} | {
            contact.settlement for contact in report.contacts
        }
        foreign = self._foreign(report)
        routes = self._routes(report, set())
        sites = sorted(
            (len(route), tile)
            for tile, route in routes.items()
            if tile not in foreign and all(tile.distance(other) >= 3 for other in taken)
        )
        if not sites:
            return None
        target = sites[0][1]
        return DirectOrder(
            command_id=f"found:{report.day}",
            kind=DirectOrderKind.FOUND_SETTLEMENT,
            journey_id=EntityId(f"journey:{report.civilization_id}:found:{report.day}"),
            traveller_ids=report.person_ids[-4:],
            route=routes[target],
        )


def _simulate(initial: WorldState) -> tuple[WorldState, list[str]]:
    state = initial.model_copy(deep=True)
    rng = StableRng(state.config.seed)
    sovereigns: dict[EntityId, BaselineSovereign | ExpandingSovereign] = {
        civilization_id: ExpandingSovereign() if index % 2 == 0 else BaselineSovereign()
        for index, civilization_id in enumerate(sorted(state.civilizations))
    }
    changes: Counter[HexCoord] = Counter()
    kinds: list[str] = []
    grades: dict[HexCoord, int] = {}
    for _ in range(DAYS):
        transition = advance_day(state, rng, sovereigns=sovereigns)
        state = transition.state
        for road in state.roads:
            assert state.world_map.tile(road.tile).terrain is not Terrain.WATER
            assert rank(road.grade) >= grades.get(road.tile, 0), "a road never loses grade"
            grades[road.tile] = rank(road.grade)
        assert set(grades) == {road.tile for road in state.roads}, "roads never vanish"
        owners = state.territory.owner_of()
        held = state.territory.held_by_tile()
        settlement_tiles = {
            settlement.tile: civilization.civilization_id
            for civilization in state.civilizations.values()
            for settlement in civilization.settlements
        }
        for tile, civilization_id in settlement_tiles.items():
            inhabited = any(
                person.alive and person.location == tile
                for person in state.civilizations[civilization_id].population.people.values()
            )
            if inhabited:
                assert owners.get(tile) == civilization_id, "an inhabited settlement is its own"
        for event in transition.events.events:
            kinds.append(event.kind)
            if event.kind not in {"control_gained", "control_lost"}:
                continue
            tile = HexCoord(int(event.payload["q"]), int(event.payload["r"]))
            changes[tile] += 1
            value = held.get(tile, {}).get(str(event.actor_id), 0)
            if event.kind == "control_gained" and tile not in settlement_tiles:
                assert value >= TAKE_THRESHOLD or "from" in event.payload
            if event.kind == "control_lost" and tile not in settlement_tiles:
                assert value < LOSE_THRESHOLD or owners.get(tile) is not None
    assert max(changes.values(), default=0) <= 2, "a tile flickered between owners"
    return state, kinds


@pytest.mark.soak
@pytest.mark.parametrize("seed", range(16))
def test_derived_territory_is_stable_and_deterministic(seed: int) -> None:
    initial = build_initial_state(
        RunManifest.new(WorldConfig(seed=seed, width=24, height=24), engine_version="0.1.0")
    )

    final, kinds = _simulate(initial)
    rerun, rerun_kinds = _simulate(initial)

    assert "control_gained" in kinds
    assert "settlers_dispatched" in kinds, "the expanding sovereigns sent settlers"
    assert "road_built" in kinds, "the colonists' kin built a road to them"
    assert all(
        any(owner.civilization_id == civilization_id for owner in final.territory.owners)
        for civilization_id in final.civilizations
    )
    assert state_hash(rerun) == state_hash(final)
    assert rerun_kinds == kinds
