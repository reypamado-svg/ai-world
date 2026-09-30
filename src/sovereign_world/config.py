"""Immutable configuration and signed run-manifest primitives."""

from __future__ import annotations

import hashlib
import json
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class WorldConfig(BaseModel):
    """Rules that define one Rules Laboratory world."""

    model_config = ConfigDict(frozen=True)

    seed: int
    width: int = Field(ge=24)
    height: int = Field(ge=24)
    civilizations: int = Field(default=4, ge=4, le=4)
    founders_per_civilization: int = Field(default=32, ge=32, le=32)
    council_interval_days: int = Field(default=30, ge=30, le=30)


class RunManifest(BaseModel):
    """Versioned identity and locked configuration for one run."""

    model_config = ConfigDict(frozen=True)

    run_id: UUID
    engine_version: str
    config: WorldConfig

    @classmethod
    def new(cls, config: WorldConfig, engine_version: str) -> RunManifest:
        return cls(run_id=uuid4(), engine_version=engine_version, config=config)

    def content_hash(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()
