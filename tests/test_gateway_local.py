import json
from collections.abc import Callable

import httpx
import pytest
from logistics_helpers import treaty_world

from sovereign_world.commands import build_council_report
from sovereign_world.gateway.compatible_provider import MAX_BODY_BYTES, CompatibleProvider
from sovereign_world.gateway.provider import ModelRequest, ProviderTimeout, ProviderUnavailable
from sovereign_world.gateway.records import CouncilOutcome
from sovereign_world.gateway.sovereign import GatewaySovereign

REQUEST = ModelRequest(
    system="charter", user="papers", max_output_tokens=1_500, timeout_seconds=20.0
)
REPLY = json.dumps(
    {
        "commands": [{"command_id": "r", "kind": "food_reserve_target", "value": 70}],
        "rationale": "x",
    }
)
TOKEN = "secret-token-1234"
OVERSIZED = b"x" * (MAX_BODY_BYTES + 1)


def _answer(content: object = REPLY, choices: int = 1) -> dict[str, object]:
    return {
        "model": "local-model",
        "choices": [{"message": {"role": "assistant", "content": content}}] * choices,
        "usage": {"prompt_tokens": 50, "completion_tokens": 10},
    }


def _provider(
    handler: Callable[[httpx.Request], httpx.Response], **settings: object
) -> tuple[CompatibleProvider, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    options: dict[str, object] = {
        "name": "same-pc",
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "local-model",
    }
    options.update(settings)
    client = httpx.Client(transport=httpx.MockTransport(record))
    return CompatibleProvider(client=client, **options), seen  # type: ignore[arg-type]


def test_the_same_computer_is_asked_in_the_openai_format_without_a_token() -> None:
    provider, seen = _provider(lambda request: httpx.Response(200, json=_answer()))
    reply = provider.complete(REQUEST)

    assert reply.text == REPLY and reply.model == "local-model" and reply.output_tokens == 10
    [request] = seen
    assert str(request.url) == "http://127.0.0.1:11434/v1/chat/completions"
    assert "authorization" not in request.headers
    body = json.loads(request.content)
    assert body["messages"][0] == {"role": "system", "content": "charter"}
    assert body["max_tokens"] == 1_500 and body["stream"] is False
    assert "tools" not in body


def test_a_second_computer_needs_a_token_and_never_shows_it(monkeypatch) -> None:
    monkeypatch.setenv("SECOND_PC_TOKEN", TOKEN)
    provider, seen = _provider(
        lambda request: httpx.Response(200, json=_answer()),
        name="second-pc",
        base_url="https://second-pc.lan:8443/v1",
        token_env="SECOND_PC_TOKEN",
        require_token=True,
    )
    state, home, _, _ = treaty_world(distance=4)
    sovereign = GatewaySovereign(provider)
    sovereign.decide(build_council_report(state, home))

    assert seen[0].headers["authorization"] == f"Bearer {TOKEN}"
    [record] = sovereign.drain_records()
    assert TOKEN not in record.model_dump_json()

    monkeypatch.delenv("SECOND_PC_TOKEN")
    with pytest.raises(ProviderUnavailable) as raised:
        provider.complete(REQUEST)
    assert TOKEN not in str(raised.value) and len(seen) == 1, "no call without the token"


@pytest.mark.parametrize(
    ("settings", "allowed"),
    [
        ({"base_url": "http://localhost:1234/v1"}, True),
        ({"base_url": "https://models.example.com/v1"}, True),
        ({"base_url": "http://models.example.com/v1"}, False),
        ({"base_url": "http://192.168.1.20:8000/v1"}, False),
        ({"base_url": "http://192.168.1.20:8000/v1", "allow_private_http": True}, True),
        ({"base_url": "ftp://192.168.1.20/v1"}, False),
        ({"base_url": "https://pc/v1", "require_token": True}, False),
        ({"model": ""}, False),
    ],
)
def test_where_a_model_may_be_reached(settings: dict[str, object], allowed: bool) -> None:
    if allowed:
        _provider(lambda request: httpx.Response(200, json=_answer()), **settings)
    else:
        with pytest.raises(ValueError):
            _provider(lambda request: httpx.Response(200, json=_answer()), **settings)


def _raise(error: Exception) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    return handler


@pytest.mark.parametrize(
    ("handler", "error"),
    [
        (_raise(httpx.ReadTimeout("slow")), ProviderTimeout),
        (_raise(httpx.ConnectError("gone")), ProviderUnavailable),
        (lambda request: httpx.Response(401, json={}), ProviderUnavailable),
        (lambda request: httpx.Response(500, json={}), ProviderUnavailable),
        (
            lambda request: httpx.Response(200, content=b"x" * (MAX_BODY_BYTES + 1)),
            ProviderUnavailable,
        ),
        (lambda request: httpx.Response(200, content=b"not json"), ProviderUnavailable),
        (lambda request: httpx.Response(200, json={"answer": REPLY}), ProviderUnavailable),
        (lambda request: httpx.Response(200, json=_answer(content=None)), ProviderUnavailable),
    ],
    ids=[
        "timeout",
        "disconnect",
        "unauthorized",
        "server-error",
        "oversized",
        "not-json",
        "new-format",
        "no-text",
    ],
)
def test_failures_become_provider_errors(handler, error) -> None:
    provider, _ = _provider(handler)
    with pytest.raises(error):
        provider.complete(REQUEST)


def test_a_busy_server_is_tried_once_more() -> None:
    answers = iter([httpx.Response(503, json={}), httpx.Response(200, json=_answer())])
    provider, seen = _provider(lambda request: next(answers))
    assert provider.complete(REQUEST).text == REPLY and len(seen) == 2


def test_answers_in_parts_or_duplicated_still_give_one_reply() -> None:
    parts = [{"type": "text", "text": REPLY[:10]}, {"type": "text", "text": REPLY[10:]}]
    provider, _ = _provider(lambda request: httpx.Response(200, json=_answer(content=parts)))
    assert provider.complete(REQUEST).text == REPLY
    provider, _ = _provider(lambda request: httpx.Response(200, json=_answer(choices=2)))
    assert provider.complete(REQUEST).text == REPLY


def test_a_dropped_second_computer_costs_its_civilization_only_the_turn() -> None:
    state, home, _, _ = treaty_world(distance=4)
    provider, _ = _provider(_raise(httpx.ConnectError("gone")))
    sovereign = GatewaySovereign(provider)
    assert sovereign.decide(build_council_report(state, home)).commands == ()
    [record] = sovereign.drain_records()
    assert record.outcome is CouncilOutcome.UNAVAILABLE
