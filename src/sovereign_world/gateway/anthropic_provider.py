"""A sovereign played by Claude, through the official Anthropic SDK.

The model is asked for plain JSON; the gateway reads and checks it, and repairs it once.
(The command schema has open-ended maps, such as a caravan's cargo, that structured output
cannot express.) Credentials are read by the SDK from the environment and go nowhere else.
"""

from __future__ import annotations

from typing import Any

from sovereign_world.gateway.provider import (
    ModelReply,
    ModelRequest,
    ProviderRefused,
    ProviderTimeout,
    ProviderUnavailable,
)

DEFAULT_MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
"""On a refusal, the API itself tries a fallback model chosen for the refusal's kind."""


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        effort: str = "high",
        max_retries: int = 2,
        fallbacks: bool = True,
        client: Any = None,
    ) -> None:
        self.model = model
        self.effort = effort
        self.fallbacks = fallbacks
        if client is None:
            import anthropic

            client = anthropic.Anthropic(max_retries=max_retries)
        self._client = client

    def complete(self, request: ModelRequest) -> ModelReply:
        import anthropic

        extra: dict[str, Any] = (
            {"betas": [FALLBACK_BETA], "fallbacks": "default"} if self.fallbacks else {}
        )
        try:
            response = self._client.beta.messages.create(
                model=self.model,
                max_tokens=request.max_output_tokens,
                system=request.system,
                messages=[{"role": "user", "content": request.user}],
                thinking={"type": "adaptive"},
                output_config={"effort": self.effort},
                timeout=request.timeout_seconds,
                **extra,
            )
        except anthropic.APITimeoutError as error:
            raise ProviderTimeout(str(error)) from error
        except (anthropic.APIConnectionError, anthropic.APIStatusError) as error:
            raise ProviderUnavailable(f"{type(error).__name__}: {error}") from error
        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None) if details is not None else None
            raise ProviderRefused(f"declined ({category or 'no category'})")
        text = "".join(block.text for block in response.content if block.type == "text")
        usage = response.usage
        return ModelReply(
            text=text,
            model=str(response.model),
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        )
