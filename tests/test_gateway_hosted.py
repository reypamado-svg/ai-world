import json
import os
from types import SimpleNamespace

import anthropic
import httpx
import openai
import pytest
from logistics_helpers import treaty_world

from sovereign_world.commands import build_council_report
from sovereign_world.gateway.anthropic_provider import (
    DEFAULT_MODEL,
    FALLBACK_BETA,
    AnthropicProvider,
)
from sovereign_world.gateway.openai_provider import OpenAIProvider
from sovereign_world.gateway.provider import (
    ModelRequest,
    ProviderRefused,
    ProviderTimeout,
    ProviderUnavailable,
)
from sovereign_world.gateway.records import CouncilOutcome
from sovereign_world.gateway.sovereign import GatewaySovereign

REQUEST = ModelRequest(
    system="charter", user="papers", max_output_tokens=2_000, timeout_seconds=30.0
)
REPLY = json.dumps(
    {
        "commands": [{"command_id": "r", "kind": "food_reserve_target", "value": 70}],
        "rationale": "x",
    }
)
HTTP_REQUEST = httpx.Request("POST", "https://example.invalid")


class FakeAnthropic:
    """Stands in for the SDK client: records each call and plays back a response or error."""

    def __init__(self, outcome: object) -> None:
        self.calls: list[dict[str, object]] = []
        self.outcome = outcome
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _claude(text: str = REPLY, stop: str = "end_turn") -> SimpleNamespace:
    return SimpleNamespace(
        stop_reason=stop,
        stop_details=SimpleNamespace(category="cyber") if stop == "refusal" else None,
        content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text=text),
        ],
        model=DEFAULT_MODEL,
        usage=SimpleNamespace(input_tokens=100, output_tokens=20),
    )


def test_claude_is_asked_with_adaptive_thinking_fallbacks_and_the_turns_limits() -> None:
    client = FakeAnthropic(_claude())
    reply = AnthropicProvider(client=client, effort="medium").complete(REQUEST)

    assert reply.text == REPLY and reply.model == DEFAULT_MODEL and reply.input_tokens == 100
    [call] = client.calls
    assert call["model"] == "claude-opus-5-5" and call["max_tokens"] == 2_000
    assert call["system"] == "charter"
    assert call["messages"] == [{"role": "user", "content": "papers"}]
    assert call["thinking"] == {"type": "adaptive"}
    assert call["output_config"] == {"effort": "medium"}
    assert call["timeout"] == 30.0
    assert call["betas"] == [FALLBACK_BETA] and call["fallbacks"] == "default"
    assert "tools" not in call, "a sovereign is given no tools"


@pytest.mark.parametrize(
    ("outcome", "error"),
    [
        (anthropic.APITimeoutError(request=HTTP_REQUEST), ProviderTimeout),
        (anthropic.APIConnectionError(request=HTTP_REQUEST), ProviderUnavailable),
        (
            anthropic.RateLimitError(
                "slow down", response=httpx.Response(429, request=HTTP_REQUEST), body=None
            ),
            ProviderUnavailable,
        ),
        (_claude(stop="refusal"), ProviderRefused),
    ],
    ids=["timeout", "connection", "rate-limit", "refusal"],
)
def test_claude_failures_become_provider_errors(outcome: object, error: type) -> None:
    with pytest.raises(error):
        AnthropicProvider(client=FakeAnthropic(outcome)).complete(REQUEST)


def test_a_truncated_claude_reply_is_repaired_by_the_gateway() -> None:
    state, home, _, _ = treaty_world(distance=4)
    client = FakeAnthropic(_claude(text='{"commands": [', stop="max_tokens"))
    sovereign = GatewaySovereign(AnthropicProvider(client=client))
    assert sovereign.decide(build_council_report(state, home)).commands == ()
    [record] = sovereign.drain_records()
    assert record.outcome is CouncilOutcome.MALFORMED and len(client.calls) == 2


class FakeOpenAI:
    def __init__(self, outcome: object) -> None:
        self.calls: list[dict[str, object]] = []
        self.outcome = outcome
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _gpt(text: str | None = REPLY, refusal: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text, refusal=refusal))],
        model="named-in-settings",
        usage=SimpleNamespace(prompt_tokens=90, completion_tokens=15),
    )


def test_openai_is_asked_for_a_json_object_from_the_named_model() -> None:
    client = FakeOpenAI(_gpt())
    reply = OpenAIProvider("named-in-settings", client=client).complete(REQUEST)

    assert reply.text == REPLY and reply.output_tokens == 15
    [call] = client.calls
    assert call["model"] == "named-in-settings"
    assert call["messages"] == [
        {"role": "system", "content": "charter"},
        {"role": "user", "content": "papers"},
    ]
    assert call["response_format"] == {"type": "json_object"}
    assert call["max_completion_tokens"] == 2_000 and call["timeout"] == 30.0
    assert "tools" not in call


def test_openai_needs_its_model_named() -> None:
    with pytest.raises(ValueError):
        OpenAIProvider("", client=FakeOpenAI(_gpt()))


@pytest.mark.parametrize(
    ("outcome", "error"),
    [
        (openai.APITimeoutError(request=HTTP_REQUEST), ProviderTimeout),
        (openai.APIConnectionError(request=HTTP_REQUEST), ProviderUnavailable),
        (_gpt(text=None, refusal="I can't help with that."), ProviderRefused),
        (SimpleNamespace(choices=[], model="m", usage=None), ProviderUnavailable),
    ],
    ids=["timeout", "connection", "refusal", "empty"],
)
def test_openai_failures_become_provider_errors(outcome: object, error: type) -> None:
    with pytest.raises(error):
        OpenAIProvider("named-in-settings", client=FakeOpenAI(outcome)).complete(REQUEST)


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="needs ANTHROPIC_API_KEY")
def test_live_claude_council() -> None:
    state, home, _, _ = treaty_world(distance=4)
    sovereign = GatewaySovereign(AnthropicProvider())
    sovereign.decide(build_council_report(state, home))
    [record] = sovereign.drain_records()
    assert record.outcome in {CouncilOutcome.ACCEPTED, CouncilOutcome.REPAIRED}, record.errors


@pytest.mark.live
@pytest.mark.skipif(
    not (os.environ.get("OPENAI_API_KEY") and os.environ.get("SOVEREIGN_OPENAI_MODEL")),
    reason="needs OPENAI_API_KEY and SOVEREIGN_OPENAI_MODEL",
)
def test_live_openai_council() -> None:
    state, home, _, _ = treaty_world(distance=4)
    sovereign = GatewaySovereign(OpenAIProvider(os.environ["SOVEREIGN_OPENAI_MODEL"]))
    sovereign.decide(build_council_report(state, home))
    [record] = sovereign.drain_records()
    assert record.outcome in {CouncilOutcome.ACCEPTED, CouncilOutcome.REPAIRED}, record.errors
