"""A sovereign played by an OpenAI model, through the official OpenAI SDK.

There is no default model: the run's settings name it. The model is asked for a JSON
object; the gateway reads and checks it, and repairs it once. Credentials are read by the
SDK from the environment and go nowhere else.
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


class OpenAIProvider:
    name = "openai"

    def __init__(
        self,
        model: str,
        *,
        max_retries: int = 2,
        client: Any = None,
        base_url: str | None = None,
    ) -> None:
        """`base_url`, when given (a sealed run pins it), wins over OPENAI_BASE_URL."""
        if not model:
            raise ValueError("an OpenAI sovereign needs its model named in the run settings")
        self.model = model
        if client is None:
            import openai

            pinned: dict[str, Any] = {"base_url": base_url} if base_url else {}
            client = openai.OpenAI(max_retries=max_retries, **pinned)
        self._client = client

    def complete(self, request: ModelRequest) -> ModelReply:
        import openai

        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": request.system},
                    {"role": "user", "content": request.user},
                ],
                max_completion_tokens=request.max_output_tokens,
                response_format={"type": "json_object"},
                timeout=request.timeout_seconds,
            )
        except openai.APITimeoutError as error:
            raise ProviderTimeout(str(error)) from error
        except openai.APIError as error:
            raise ProviderUnavailable(f"{type(error).__name__}: {error}") from error
        if not response.choices:
            raise ProviderUnavailable("the reply held no choices")
        message = response.choices[0].message
        if getattr(message, "refusal", None):
            raise ProviderRefused(str(message.refusal))
        usage = getattr(response, "usage", None)
        return ModelReply(
            text=message.content or "",
            model=str(response.model),
            input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
        )
