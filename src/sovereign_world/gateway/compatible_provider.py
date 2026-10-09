"""A sovereign played by a model on this computer or a second one, over the OpenAI-style
chat-completions HTTP format that Ollama, LM Studio, llama.cpp and vLLM all serve.

Only the prompt and the model's answer cross the connection. A second computer must be
reached over HTTPS, or over plain HTTP at a private address the run settings allow, and
always with a token. The token is read from the environment when a turn is played and is
never written into errors, records or the journal.
"""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import httpx

from sovereign_world.gateway.envelope import MAX_REPLY_BYTES, decoding_schema
from sovereign_world.gateway.provider import (
    ModelReply,
    ModelRequest,
    ProviderTimeout,
    ProviderUnavailable,
)

MAX_BODY_BYTES = 4 * MAX_REPLY_BYTES
"""A larger HTTP body is refused unread: it cannot hold a usable reply."""
RETRIED = frozenset({429, 500, 502, 503, 504})
SCHEMA_REFUSED = frozenset({400, 422, 500, 501})
"""Answers that may mean the server cannot hold a reply to the schema (a 500 only after its
retries): that call is made again for any JSON object. A refused token or an unknown model
is not one of them."""


def _plain_http_allowed(host: str, private_allowed: bool) -> bool:
    if host == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or (private_allowed and address.is_private)


class CompatibleProvider:
    """One adapter for both local sovereigns: the same computer, or a second one."""

    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        model: str,
        token_env: str | None = None,
        require_token: bool = False,
        allow_private_http: bool = False,
        max_retries: int = 1,
        client: httpx.Client | None = None,
        schema: Mapping[str, object] | None = None,
    ) -> None:
        parts = urlsplit(base_url)
        host = parts.hostname or ""
        if parts.scheme not in {"http", "https"} or not host:
            raise ValueError(f"{name}: the model's address must be an http(s) URL")
        if parts.scheme == "http" and not _plain_http_allowed(host, allow_private_http):
            raise ValueError(
                f"{name}: plain HTTP is allowed only to this computer or an allowed private address"
            )
        if require_token and not token_env:
            raise ValueError(f"{name}: a second computer is reached only with a token")
        if not model:
            raise ValueError(f"{name}: the model must be named")
        self.name = name
        self.model = model
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._token_env = token_env
        self._max_retries = max_retries
        self._client = client or httpx.Client()
        self._schema = dict(schema) if schema is not None else decoding_schema()
        self._schema_refused: str | None = None

    def _response_format(self, structured: bool) -> dict[str, object]:
        """The reply schema, so a model cannot write a command without its kind or a value
        the engine does not know; or, where the server refused it, any JSON object."""
        if not structured:
            return {"type": "json_object"}
        return {
            "type": "json_schema",
            "json_schema": {"name": "sovereign_reply", "schema": self._schema, "strict": True},
        }

    def status(self) -> str:
        """How the last reply was held to the schema (shown on the launch gate's probe line)."""
        if self._schema_refused is None:
            return "reply schema sent as json_schema"
        return f"json_object only: the server refused the reply schema ({self._schema_refused})"

    def _headers(self) -> dict[str, str]:
        if self._token_env is None:
            return {}
        token = os.environ.get(self._token_env)
        if not token:
            raise ProviderUnavailable(f"{self.name}: its token is not set in {self._token_env}")
        return {"Authorization": f"Bearer {token}"}

    def _post(self, request: ModelRequest) -> httpx.Response:
        headers = self._headers()
        response = self._send(request, headers, structured=True)
        if response.status_code in SCHEMA_REFUSED:
            # Asked again, for this call only, for any JSON object: a server that cannot take
            # the schema still plays, and one that failed once is held to it again next time.
            self._schema_refused = f"answered {response.status_code}"
            return self._send(request, headers, structured=False)
        self._schema_refused = None
        return response

    def _send(
        self, request: ModelRequest, headers: dict[str, str], *, structured: bool
    ) -> httpx.Response:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "max_tokens": request.max_output_tokens,
            "response_format": self._response_format(structured),
            "stream": False,
        }
        for attempt in range(self._max_retries + 1):
            try:
                response = self._client.post(
                    self._url, json=body, headers=headers, timeout=request.timeout_seconds
                )
            except httpx.TimeoutException as error:
                raise ProviderTimeout(f"{self.name}: no answer in time") from error
            except httpx.HTTPError as error:
                raise ProviderUnavailable(
                    f"{self.name}: could not reach the model ({type(error).__name__})"
                ) from error
            if response.status_code in RETRIED and attempt < self._max_retries:
                continue
            return response
        raise AssertionError("unreachable")

    def complete(self, request: ModelRequest) -> ModelReply:
        response = self._post(request)
        if response.status_code != 200:
            raise ProviderUnavailable(f"{self.name}: the model answered {response.status_code}")
        if len(response.content) > MAX_BODY_BYTES:
            raise ProviderUnavailable(f"{self.name}: the answer is too large to read")
        try:
            data: Any = response.json()
            message = data["choices"][0]["message"]
            content = message.get("content")
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as error:
            raise ProviderUnavailable(
                f"{self.name}: the answer is not in the expected format"
            ) from error
        if isinstance(content, list):
            # Some servers return the answer as a list of parts.
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        if not isinstance(content, str):
            raise ProviderUnavailable(f"{self.name}: the answer holds no text")
        usage = data.get("usage") if isinstance(data, dict) else None
        usage = usage if isinstance(usage, dict) else {}
        return ModelReply(
            text=content,
            model=str(data.get("model") or self.model),
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
        )
