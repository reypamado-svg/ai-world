"""Typed entity identifiers allocated in deterministic sequence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import NewType

EntityId = NewType("EntityId", str)


@dataclass(slots=True)
class IdAllocator:
    """Allocate stable, human-readable identifiers within one namespace."""

    namespace: str
    next_sequence: int = 1

    def __post_init__(self) -> None:
        if not self.namespace or ":" in self.namespace:
            raise ValueError("namespace must be non-empty and cannot contain ':'")
        if self.next_sequence < 1:
            raise ValueError("next_sequence must be positive")

    def allocate(self) -> EntityId:
        value = EntityId(f"{self.namespace}:{self.next_sequence:010d}")
        self.next_sequence += 1
        return value

