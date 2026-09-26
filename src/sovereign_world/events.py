"""Canonical ordered domain events."""

from __future__ import annotations

import json
from collections.abc import Iterable
from enum import IntEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class EventPhase(IntEnum):
    COMMAND = 1
    MOVEMENT = 2
    CONSUMPTION = 3
    WORK = 4
    HEALTH = 5
    BIRTH = 6
    DEATH = 7
    PROJECT = 8
    REPORT = 9


class DomainEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: UUID
    day: int = Field(ge=0)
    phase: EventPhase
    sequence: int = Field(ge=0)
    kind: str
    actor_id: str | None
    subject_id: str | None
    payload: dict[str, Any] = Field(default_factory=dict)

    def sort_key(self) -> tuple[int, int, str, str, int]:
        return (
            self.day,
            int(self.phase),
            self.actor_id or "",
            self.subject_id or "",
            self.sequence,
        )

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )


class EventBatch(BaseModel):
    model_config = ConfigDict(frozen=True)

    events: tuple[DomainEvent, ...]

    @classmethod
    def assign_sequences(cls, events: Iterable[DomainEvent]) -> EventBatch:
        ordered = sorted(events, key=DomainEvent.sort_key)
        sequenced = tuple(
            event.model_copy(update={"sequence": index})
            for index, event in enumerate(ordered, start=1)
        )
        return cls(events=sequenced)

    def canonical_json(self) -> str:
        return "\n".join(event.canonical_json() for event in self.events)

