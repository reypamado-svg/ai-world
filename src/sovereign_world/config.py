"""Immutable configuration and signed run-manifest primitives."""

from __future__ import annotations

import hashlib
import json
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

CURRENT_GENERATOR = 3
"""The world generator new runs use; see ``RunManifest.generator_version``."""
CURRENT_RULES = 2
"""The rules new runs use; see ``RunManifest.rules_version``."""


class WorldConfig(BaseModel):
    """Rules that define one Rules Laboratory world."""

    model_config = ConfigDict(frozen=True)

    seed: int
    width: int = Field(ge=24)
    height: int = Field(ge=24)
    civilizations: int = Field(default=4, ge=2, le=4)
    """How many civilizations the world starts with: two to four."""
    founders_per_civilization: int = Field(default=32, ge=32, le=32)
    council_interval_days: int = Field(default=30, ge=30, le=30)


ProviderKind = Literal["baseline", "anthropic", "openai", "compatible"]


class SovereignConfig(BaseModel):
    """Who plays one civilization: a scripted policy, or a model and how to reach it.

    Credentials are never stored here, only the name of the environment variable that
    holds a second computer's token.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: ProviderKind = "baseline"
    model: str = ""
    effort: str = "high"
    """Claude's effort level."""
    fallbacks: bool = True
    """Whether a refused Claude request is retried on a fallback model."""
    base_url: str = ""
    """For a local or second-computer model: its OpenAI-style address."""
    token_env: str | None = None
    require_token: bool = False
    allow_private_http: bool = False
    max_retries: int = Field(default=2, ge=0, le=5)
    prompt_version: str = "council-3"


class BudgetConfig(BaseModel):
    """The same limits for every sovereign in a run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    state_chars: int = Field(default=36_000, ge=1_000)
    memory_chars: int = Field(default=16_000, ge=0)
    transcript_chars: int = Field(default=8_000, ge=0)
    transcript_turns: int = Field(default=3, ge=0, le=12)
    max_output_tokens: int = Field(default=8_000, ge=256)
    timeout_seconds: float = Field(default=300.0, gt=0)


class RunManifest(BaseModel):
    """Versioned identity and locked configuration for one run."""

    model_config = ConfigDict(frozen=True)

    run_id: UUID
    engine_version: str
    config: WorldConfig
    sovereigns: dict[str, SovereignConfig] = Field(default_factory=dict)
    """Who plays each civilization, by its id; the rest are played by the baseline policy."""
    budgets: BudgetConfig = Field(default_factory=BudgetConfig)
    parent_run_id: UUID | None = None
    """The run this one was forked from, when its sovereigns or budgets were changed."""
    forked_at_day: int | None = Field(default=None, ge=0)
    generator_version: int = Field(default=1, ge=1, le=CURRENT_GENERATOR)
    """Which world generator built the map. Runs from before versions were recorded used 1."""
    rules_version: int = Field(default=1, ge=1, le=CURRENT_RULES)
    """Which rules the world runs under. Version 2 adds houses, ranks and civil research;
    runs from before versions were recorded used 1, and keep it when replayed."""

    @classmethod
    def new(
        cls, config: WorldConfig, engine_version: str, *, rules_version: int = CURRENT_RULES
    ) -> RunManifest:
        return cls(
            run_id=uuid4(),
            engine_version=engine_version,
            config=config,
            generator_version=CURRENT_GENERATOR,
            rules_version=rules_version,
        )

    def content_hash(self) -> str:
        dumped = self.model_dump(mode="json")
        # Settings left at their defaults are omitted, so older manifests keep their hash.
        defaults = {
            "sovereigns": {},
            "budgets": BudgetConfig().model_dump(mode="json"),
            "parent_run_id": None,
            "forked_at_day": None,
            "generator_version": 1,
            "rules_version": 1,
        }
        for key, default in defaults.items():
            if dumped.get(key) == default:
                dumped.pop(key)
        payload = json.dumps(dumped, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()
