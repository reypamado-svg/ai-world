"""Immutable configuration and signed run-manifest primitives."""

from __future__ import annotations

import hashlib
import json
from typing import Literal
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)

CURRENT_GENERATOR = 3
"""The world generator new runs use; see ``RunManifest.generator_version``."""
CURRENT_RULES = 3
"""The rules new runs use; see ``RunManifest.rules_version``."""
CURRENT_JOURNAL_FORMAT = 2
"""How new runs save their days; see ``RunManifest.journal_format``."""
ENGINE_VERSION = "0.2.0"
"""The engine version new runs record (``RunManifest.engine_version``)."""
COUNCIL_INTERVALS = (7, 14, 21, 28)
"""The days between regular councils a new world may choose (one to four weeks)."""
LEGACY_COUNCIL_INTERVAL = 30
"""The interval of every world made before it could be chosen; valid for them only."""
DEFAULT_CRISIS_GAP = 7


class WorldConfig(BaseModel):
    """Rules that define one Rules Laboratory world."""

    model_config = ConfigDict(frozen=True)

    seed: int
    width: int = Field(ge=24)
    height: int = Field(ge=24)
    civilizations: int = Field(default=4, ge=2, le=4)
    """How many civilizations the world starts with: two to four."""
    founders_per_civilization: int = Field(default=32, ge=32, le=32)
    council_interval_days: int = LEGACY_COUNCIL_INTERVAL
    """Days between regular councils: 7, 14, 21 or 28 for new worlds (`init` defaults to 28);
    30 for worlds made before it could be chosen."""
    crisis_gap_days: int = Field(default=DEFAULT_CRISIS_GAP, ge=0, le=365)
    """The fewest days between one civilization's crisis councils; 0 holds none. Left out of
    every hash at its default, so worlds made before it keep theirs."""

    @field_validator("council_interval_days")
    @classmethod
    def _known_interval(cls, value: int) -> int:
        if value not in (*COUNCIL_INTERVALS, LEGACY_COUNCIL_INTERVAL):
            raise ValueError(
                "council_interval_days must be 7, 14, 21 or 28 days (30 only for worlds made"
                " before the setting)"
            )
        return value

    @model_serializer(mode="wrap")
    def _omit_default_crisis_gap(self, handler: SerializerFunctionWrapHandler) -> object:
        dumped = handler(self)
        if isinstance(dumped, dict) and dumped.get("crisis_gap_days") == DEFAULT_CRISIS_GAP:
            dumped.pop("crisis_gap_days")
        return dumped


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
    prompt_version: str = "council-7"
    label: str = ""
    """Names the vendor behind a compatible endpoint (``gemini``, ``ollama``) in records and
    reports. Added for the sealed trial; left out of the manifest hash while empty."""


class BudgetConfig(BaseModel):
    """The same limits for every sovereign in a run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    state_chars: int = Field(default=36_000, ge=1_000)
    memory_chars: int = Field(default=16_000, ge=0)
    transcript_chars: int = Field(default=8_000, ge=0)
    transcript_turns: int = Field(default=3, ge=0, le=12)
    max_output_tokens: int = Field(default=8_000, ge=256)
    timeout_seconds: float = Field(default=300.0, gt=0)


class Price(BaseModel):
    """What a model's tokens cost, in US dollars per million."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_per_million_usd: float = Field(ge=0)
    output_per_million_usd: float = Field(ge=0)


class SpendConfig(BaseModel):
    """The run's hard spending cap (sealed trial), counted from every council's recorded usage.

    The run stops cleanly before a day whose councils could break any cap, keeping
    ``reserve_councils`` worst-case council rounds in hand. A reply from a model the price table
    does not list is priced at the table's dearest rate, or stops the run (``unknown_model``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_cost_usd: float | None = Field(default=None, gt=0)
    max_input_tokens: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)
    prices: dict[str, Price] = Field(default_factory=dict)
    """By model name, as the provider reports it."""
    unknown_model: Literal["highest", "refuse"] = "highest"
    reserve_councils: int = Field(default=1, ge=1, le=10)


_SOVEREIGN_ADDITIONS: dict[str, object] = {"label": ""}
"""Settings added to ``SovereignConfig`` after runs were recorded, and their defaults."""


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
    journal_format: int = Field(default=1, ge=1, le=CURRENT_JOURNAL_FORMAT)
    """How the run's days are saved. Format 1 saves the whole world each day and hashes it
    whole (hash version 1); format 2 saves snapshots and the changes between them, and
    hashes the world in parts (hash version 2). Runs from before formats used 1."""
    start_rotation: int = Field(default=0, ge=0)
    """Which start each civilization takes: civilization ``i`` gets the start (and with it the
    whole starting package: site, regional skill, founders) the generator chose ``(i + r) % n``-th.
    Balance calibration rotates it to separate a start's quality from who holds it; 0 is the
    world as generated, and keeps the manifest's hash."""

    spend: SpendConfig | None = None
    """The spending cap, if any (sealed trial); left out of the manifest's hash when unset."""

    @model_validator(mode="after")
    def _rotation_within_civilizations(self) -> RunManifest:
        if self.start_rotation >= self.config.civilizations:
            raise ValueError("start_rotation must be below the number of civilizations")
        return self

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
            journal_format=CURRENT_JOURNAL_FORMAT,
        )

    def content_hash(self) -> str:
        dumped = self.model_dump(mode="json")
        # Settings left at their defaults are omitted, so older manifests keep their hash.
        defaults: dict[str, object] = {
            "sovereigns": {},
            "budgets": BudgetConfig().model_dump(mode="json"),
            "parent_run_id": None,
            "forked_at_day": None,
            "generator_version": 1,
            "rules_version": 1,
            "journal_format": 1,
            "start_rotation": 0,
            "spend": None,
        }
        for key, default in defaults.items():
            if dumped.get(key) == default:
                dumped.pop(key)
        # Settings later added to each sovereign's entry, likewise omitted at their defaults.
        for entry in dumped.get("sovereigns", {}).values():
            for key, default in _SOVEREIGN_ADDITIONS.items():
                if entry.get(key) == default:
                    entry.pop(key)
        payload = json.dumps(dumped, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()
