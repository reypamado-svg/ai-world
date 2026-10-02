"""Deterministic sovereign policies for simulation development and balance."""

from __future__ import annotations

from typing import Protocol

from sovereign_world.commands import (
    Command,
    CommandEnvelope,
    CouncilReport,
    Decree,
    DecreeKind,
    DirectOrder,
    DirectOrderKind,
    ProjectKind,
)
from sovereign_world.hexmap import HexCoord, Terrain
from sovereign_world.ids import EntityId


class Sovereign(Protocol):
    def decide(self, report: CouncilReport) -> CommandEnvelope: ...


def plan_baseline_commands(report: CouncilReport) -> tuple[Command, ...]:
    commands: tuple[Command, ...] = (
        Decree(
            command_id=f"decree:{report.day}:food",
            kind=DecreeKind.FOOD_RESERVE_TARGET,
            value=90,
            priority=100,
            duration_days=60,
        ),
        Decree(
            command_id=f"decree:{report.day}:labor",
            kind=DecreeKind.LABOR_PRIORITY,
            value=70,
            priority=80,
            duration_days=60,
        ),
        Decree(
            command_id=f"decree:{report.day}:growth",
            kind=DecreeKind.POPULATION_GROWTH_POLICY,
            value=1,
            priority=60,
            duration_days=60,
        ),
        DirectOrder(
            command_id=f"project:{report.day}:shelter",
            kind=DirectOrderKind.START_PROJECT,
            worker_ids=report.person_ids[:2],
            project_id=EntityId(f"project:shelter:{report.civilization_id}"),
            project_kind=ProjectKind.SHELTER,
            priority=90,
        ),
        DirectOrder(
            command_id=f"project:{report.day}:storage",
            kind=DirectOrderKind.START_PROJECT,
            worker_ids=report.person_ids[2:4],
            project_id=EntityId(f"project:storage:{report.civilization_id}"),
            project_kind=ProjectKind.STORAGE,
            priority=85,
        ),
    )
    if report.day != 0:
        return commands
    direction = -1 if report.start_center.q >= 4 else 1
    route = _survey_route(report, direction) or _survey_route(report, -direction)
    if route is None:
        return commands
    return (
        *commands,
        DirectOrder(
            command_id=f"expedition:{report.day}:survey",
            kind=DirectOrderKind.START_EXPEDITION,
            expedition_id=EntityId(f"expedition:survey:{report.civilization_id}"),
            explorer_ids=report.person_ids[4:6],
            route=route,
            priority=80,
        ),
    )


def _survey_route(report: CouncilReport, direction: int) -> tuple[HexCoord, ...] | None:
    """A straight survey of up to five tiles, stopping short of known water and of rivers
    known to be too deep to wade; None when not even one step is open."""
    terrain = dict(report.known_terrain)
    deep = {
        frozenset((view.tile, view.across)) for view in report.known_rivers if view.depth == "deep"
    }
    route = [report.start_center]
    for _step in range(5):
        here = route[-1]
        ahead = HexCoord(here.q + direction, here.r)
        if terrain.get(ahead) is Terrain.WATER or frozenset((here, ahead)) in deep:
            break
        route.append(ahead)
    return tuple(route) if len(route) > 1 else None


class BaselineSovereign:
    def decide(self, report: CouncilReport) -> CommandEnvelope:
        return CommandEnvelope(
            schema_version=1,
            civilization_id=report.civilization_id,
            council_day=report.day,
            correlation_id=report.report_id,
            commands=plan_baseline_commands(report)[:8],
        )
