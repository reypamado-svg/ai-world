"""Builds each civilization's sovereign from the run's frozen settings."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from sovereign_world.config import BudgetConfig, RunManifest, SovereignConfig
from sovereign_world.gateway.memory import Budgets
from sovereign_world.gateway.prompt import PROMPT_VERSION
from sovereign_world.gateway.provider import ModelProvider
from sovereign_world.gateway.records import CouncilRecord
from sovereign_world.gateway.sovereign import GatewaySovereign, RecordingSovereign
from sovereign_world.ids import EntityId
from sovereign_world.scripted import BaselineSovereign, Sovereign


def budgets_of(config: BudgetConfig) -> Budgets:
    return Budgets(**config.model_dump())


def provider_for(
    civilization_id: EntityId, config: SovereignConfig, *, base_url: str | None = None
) -> ModelProvider:
    """The provider a civilization's settings name; its SDK is loaded only when used. A sealed
    run passes the hosted provider's pinned `base_url`, which the environment cannot change."""
    if config.provider == "anthropic":
        from sovereign_world.gateway.anthropic_provider import DEFAULT_MODEL, AnthropicProvider

        return AnthropicProvider(
            config.model or DEFAULT_MODEL,
            effort=config.effort,
            max_retries=config.max_retries,
            fallbacks=config.fallbacks,
            base_url=base_url,
        )
    if config.provider == "openai":
        from sovereign_world.gateway.openai_provider import OpenAIProvider

        return OpenAIProvider(config.model, max_retries=config.max_retries, base_url=base_url)
    if config.provider == "compatible":
        from sovereign_world.gateway.compatible_provider import CompatibleProvider

        return CompatibleProvider(
            name=f"compatible:{civilization_id}",
            base_url=config.base_url,
            model=config.model,
            token_env=config.token_env,
            require_token=config.require_token,
            allow_private_http=config.allow_private_http,
            max_retries=min(config.max_retries, 1),
        )
    if config.provider == "claude-code":
        from sovereign_world.gateway.claude_code_provider import ClaudeCodeProvider

        return ClaudeCodeProvider(name=f"claude-code:{civilization_id}", model=config.model)
    if config.provider == "codex":
        from sovereign_world.gateway.codex_provider import CodexProvider

        return CodexProvider(
            name=f"codex:{civilization_id}", model=config.model, effort=config.effort
        )
    raise ValueError(f"{civilization_id}: {config.provider} is not a model provider")


def build_sovereigns(
    manifest: RunManifest,
    civilization_ids: Iterable[EntityId],
    *,
    history: Iterable[CouncilRecord] = (),
    providers: dict[EntityId, ModelProvider] | None = None,
    pinned: Mapping[str, str] | None = None,
) -> dict[EntityId, Sovereign]:
    """Every civilization's sovereign, taking up the councils it has already held.

    `providers` replaces a civilization's provider, e.g. with a scripted one in tests.
    """
    past = list(history)
    budgets = budgets_of(manifest.budgets)
    sovereigns: dict[EntityId, Sovereign] = {}
    for civilization_id in sorted(civilization_ids):
        config = manifest.sovereigns.get(str(civilization_id), SovereignConfig())
        if config.provider == "baseline" and civilization_id not in (providers or {}):
            sovereigns[civilization_id] = RecordingSovereign(BaselineSovereign(), "baseline")
            continue
        if config.prompt_version != PROMPT_VERSION:
            raise ValueError(
                f"{civilization_id}: prompt {config.prompt_version} is not this engine's "
                f"{PROMPT_VERSION}; fork the run to change it"
            )
        provider = (providers or {}).get(civilization_id) or provider_for(
            civilization_id,
            config,
            base_url=(pinned or {}).get(str(civilization_id))
            if config.provider in ("anthropic", "openai")
            else None,
        )
        sovereign = GatewaySovereign(
            provider, prompt_version=config.prompt_version, budgets=budgets
        )
        sovereign.remember(record for record in past if record.civilization_id == civilization_id)
        sovereigns[civilization_id] = sovereign
    return sovereigns
