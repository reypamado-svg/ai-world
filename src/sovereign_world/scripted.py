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
from sovereign_world.ids import EntityId


class Sovereign(Protocol):
    def decide(self, report: CouncilReport) -> CommandEnvelope: ...


def plan_baseline_commands(report: CouncilReport) -> tuple[Command, ...]:
    return (
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


class BaselineSovereign:
    def decide(self, report: CouncilReport) -> CommandEnvelope:
        return CommandEnvelope(
            schema_version=1,
            civilization_id=report.civilization_id,
            council_day=report.day,
            correlation_id=report.report_id,
            commands=plan_baseline_commands(report)[:8],
        )
