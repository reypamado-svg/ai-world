"""The one interface every model provider implements, and a scripted stand-in for tests."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ModelRequest:
    """What the gateway sends a model: instructions, the council's papers, and limits."""

    system: str
    user: str
    max_output_tokens: int
    timeout_seconds: float
    purpose: str = "turn"
    """`turn` for a council's first call, `repair` for its one second chance."""


@dataclass(frozen=True, slots=True)
class ModelReply:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


class ProviderError(Exception):
    """A provider could not give an answer this turn."""


class ProviderTimeout(ProviderError):
    """The model took longer than the turn allows."""


class ProviderUnavailable(ProviderError):
    """The model could not be reached, or its service failed."""


class ProviderRefused(ProviderError):
    """The model declined to answer."""


class ModelProvider(Protocol):
    name: str
    model: str

    def complete(self, request: ModelRequest) -> ModelReply: ...


Scripted = str | ProviderError | Callable[[ModelRequest], str]


@dataclass
class ScriptedProvider:
    """Answers from a fixed script, one entry per call; used to test the gateway."""

    script: Sequence[Scripted]
    name: str = "scripted"
    model: str = "scripted-model"
    requests: list[ModelRequest] = field(default_factory=list)
    input_tokens: int = 0
    """Tokens each reply reports, as a real provider's would (for spending tests)."""
    output_tokens: int = 0
    answering_model: str | None = None
    """The model each reply says answered, when not the configured one (a fallback)."""

    def complete(self, request: ModelRequest) -> ModelReply:
        self.requests.append(request)
        index = len(self.requests) - 1
        if index >= len(self.script):
            raise ProviderUnavailable("the script has no more replies")
        entry = self.script[index]
        if isinstance(entry, ProviderError):
            raise entry
        text = entry(request) if callable(entry) else entry
        return ModelReply(
            text=text,
            model=self.answering_model or self.model,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
        )
