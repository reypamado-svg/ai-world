"""The signed-in program adapters (the free trial): Claude Code's `claude` and OpenAI's `codex`,
asked through stand-ins that answer as the real programs document. Each call runs in an empty
folder that is removed after; the papers go in on standard input; the sign-in is never
replaced by an API key; failures and hangs become plain provider errors."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from fake_cli import QUIET, calls, install_fakes

from sovereign_world.config import SovereignConfig
from sovereign_world.gateway.claude_code_provider import ClaudeCodeProvider
from sovereign_world.gateway.codex_provider import CodexProvider
from sovereign_world.gateway.factory import provider_for
from sovereign_world.gateway.provider import (
    ModelRequest,
    ProviderTimeout,
    ProviderUnavailable,
)
from sovereign_world.ids import EntityId

SYSTEM = "You rule a small people. Reply with one JSON object."
PAPERS = "Council of day 0: wells, fields and the river — été 世界."
CIV = EntityId("civilization:0000000001")


def _request(timeout: float = 30.0) -> ModelRequest:
    return ModelRequest(system=SYSTEM, user=PAPERS, max_output_tokens=8000, timeout_seconds=timeout)


@pytest.fixture
def log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = install_fakes(tmp_path, monkeypatch)
    for name in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_FEDERATION_RULE_ID"):
        monkeypatch.setenv(name, "secret-not-to-be-seen")
    for name in ("CODEX_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.setenv(name, "secret-not-to-be-seen")
    monkeypatch.setenv("KEEP_ME", "1")
    return path


def _claude() -> ClaudeCodeProvider:
    provider = provider_for(CIV, SovereignConfig(provider="claude-code", model="claude-sonnet"))
    assert isinstance(provider, ClaudeCodeProvider)
    return provider


def _codex() -> CodexProvider:
    config = SovereignConfig(provider="codex", model="gpt-plus", effort="medium")
    provider = provider_for(CIV, config)
    assert isinstance(provider, CodexProvider)
    return provider


def test_claude_code_is_asked_once_with_the_charter_and_no_api_key(log: Path) -> None:
    reply = _claude().complete(_request())
    assert reply.text == QUIET
    assert reply.model == "claude-sonnet-fake"
    assert (reply.input_tokens, reply.output_tokens) == (1500, 50)
    [call] = calls(log)
    argv = call["argv"]
    assert isinstance(argv, list)
    assert argv[0] == "-p" and argv[2:5] == ["--safe-mode", "--system-prompt-file", argv[4]]
    assert argv[5:] == [
        "--tools",
        "",
        "--max-turns",
        "1",
        "--model",
        "claude-sonnet",
        "--output-format",
        "json",
        "--no-session-persistence",
    ]
    assert "--bare" not in argv
    assert call["system"] == SYSTEM and call["stdin"] == PAPERS
    assert call["files"] == ["system.md"]
    assert not Path(str(call["cwd"])).exists()
    assert call["seen"] == ["CODEX_API_KEY", "KEEP_ME", "OPENAI_API_KEY"]
    assert call["env"] == {"DISABLE_AUTOUPDATER": "1", "NO_COLOR": "1"}


def test_codex_is_asked_once_with_the_charter_and_no_api_key(log: Path) -> None:
    reply = _codex().complete(_request())
    assert reply.text == QUIET
    assert reply.model == "gpt-plus"
    assert (reply.input_tokens, reply.output_tokens) == (2000, 80)
    [call] = calls(log)
    argv = call["argv"]
    assert isinstance(argv, list)
    assert argv[:13] == [
        "exec",
        "-",
        "-m",
        "gpt-plus",
        "--json",
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
        "--ephemeral",
        "--ignore-user-config",
        "-C",
        call["cwd"],
        "-c",
    ]
    settings = [argv[index + 1] for index, word in enumerate(argv) if word == "-c"]
    assert settings[1:] == [
        "include_permissions_instructions=false",
        "include_apps_instructions=false",
        "include_environment_context=false",
        "project_doc_max_bytes=0",
        "features.shell_tool=false",
        'web_search="disabled"',
        'forced_login_method="chatgpt"',
        'model_reasoning_effort="medium"',
    ]
    assert settings[0].startswith("model_instructions_file=")
    assert call["system"] == SYSTEM and call["stdin"] == PAPERS
    assert call["files"] == ["system.md"]
    assert not Path(str(call["cwd"])).exists()
    seen = set(map(str, call["seen"]))  # type: ignore[call-overload]
    assert not seen & {"CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "OPENAI_API_KEY"}
    assert {"ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "KEEP_ME"} <= seen


@pytest.mark.parametrize("make", [_claude, _codex], ids=["claude-code", "codex"])
@pytest.mark.parametrize(
    ("mode", "says"),
    [
        ("limit", "usage limit reached"),
        ("auth", "not signed in"),
        ("notjson", "expected format|holds no text"),
        ("exit2", "failed \\(something broke\\)"),
        ("partial", "failed"),
        ("noturn", "did not complete|holds no text"),
    ],
)
def test_signed_in_program_failures_become_provider_errors(
    log: Path,
    monkeypatch: pytest.MonkeyPatch,
    make: Callable[[], ClaudeCodeProvider | CodexProvider],
    mode: str,
    says: str,
) -> None:
    monkeypatch.setenv("FAKE_CLI_MODE", mode)
    with pytest.raises(ProviderUnavailable, match=says) as raised:
        make().complete(_request())
    assert "secret-not-to-be-seen" not in str(raised.value)
    assert len(calls(log)) == 1, "a failed call is never retried"


def test_claude_code_failures_become_provider_errors(
    log: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CLI_MODE", "limit")
    with pytest.raises(ProviderUnavailable, match="session limit"):
        _claude().complete(_request())
    monkeypatch.setenv("PATH", str(log.parent / "nowhere"))
    with pytest.raises(ProviderUnavailable, match="the claude program is not on PATH"):
        _claude().complete(_request())


def test_codex_failures_become_provider_errors(log: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_CLI_MODE", "limit")
    with pytest.raises(ProviderUnavailable, match="try again at 5:02 PM"):
        _codex().complete(_request())
    monkeypatch.setenv("PATH", str(log.parent / "nowhere"))
    with pytest.raises(ProviderUnavailable, match="the codex program is not on PATH"):
        _codex().complete(_request())


def _gone(pid: int) -> bool:
    status = Path(f"/proc/{pid}/status")
    if not status.exists():
        return True
    return "State:\tZ" in status.read_text()


@pytest.mark.parametrize("make", [_claude, _codex], ids=["claude-code", "codex"])
def test_a_hung_program_is_stopped_with_its_children(
    log: Path,
    monkeypatch: pytest.MonkeyPatch,
    make: Callable[[], ClaudeCodeProvider | CodexProvider],
) -> None:
    monkeypatch.setenv("FAKE_CLI_MODE", "hang")
    started = time.monotonic()
    with pytest.raises(ProviderTimeout, match="no answer in time"):
        make().complete(_request(timeout=2))
    assert time.monotonic() - started < 10
    [call] = calls(log)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not (
        _gone(int(str(call["pid"]))) and _gone(int(str(call["child"])))
    ):
        time.sleep(0.1)
    assert _gone(int(str(call["pid"]))) and _gone(int(str(call["child"])))
    assert not Path(str(call["cwd"])).exists()


def test_status_names_the_version_and_the_sign_in(log: Path) -> None:
    assert _claude().status() == "claude 9.9.9 (fake); signed in with claude.ai"
    assert _codex().status() == "codex 9.9.9 (fake); Logged in using ChatGPT"
    assert calls(log) == [], "a status check asks no model"


def test_a_signed_in_program_must_name_its_model() -> None:
    for kind in ("claude-code", "codex"):
        with pytest.raises(ValueError, match="must be named"):
            provider_for(CIV, SovereignConfig(provider=kind))


def test_the_gateway_records_what_the_programs_answer(
    log: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sovereign_world.commands import build_council_report
    from sovereign_world.config import RunManifest, WorldConfig
    from sovereign_world.gateway.factory import budgets_of
    from sovereign_world.gateway.records import CouncilOutcome, CouncilRecord
    from sovereign_world.gateway.sovereign import GatewaySovereign
    from sovereign_world.state import build_initial_state

    manifest = RunManifest.new(WorldConfig(seed=21, width=24, height=24, civilizations=3), "0.2.0")
    state = build_initial_state(manifest)
    civilization = sorted(state.civilizations)[0]
    report = build_council_report(state, civilization)
    budgets = budgets_of(manifest.budgets)
    held: list[CouncilRecord] = []
    for make in (_claude, _codex):
        sovereign = GatewaySovereign(make(), prompt_version="council-7", budgets=budgets)
        sovereign.record_to(held.append)
        sovereign.decide(report)
    assert [record.outcome for record in held] == [CouncilOutcome.ACCEPTED] * 2
    assert [record.usage[0].model for record in held] == ["claude-sonnet-fake", "gpt-plus"]
    assert held[0].provider == f"claude-code:{CIV}" and held[1].provider == f"codex:{CIV}"
    monkeypatch.setenv("FAKE_CLI_MODE", "limit")
    sovereign = GatewaySovereign(_claude(), prompt_version="council-7", budgets=budgets)
    sovereign.record_to(held.append)
    sovereign.decide(report)
    assert held[-1].outcome is CouncilOutcome.UNAVAILABLE
    assert "usage limit reached" in held[-1].errors[0]
    logged = json.dumps(calls(log))
    assert "secret-not-to-be-seen" not in logged
    assert os.environ["ANTHROPIC_API_KEY"] == "secret-not-to-be-seen"


def test_a_failed_or_unfinished_codex_turn_is_never_taken_for_an_answer(
    log: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CLI_MODE", "partial")
    with pytest.raises(ProviderUnavailable, match="stream disconnected"):
        _codex().complete(_request())
    monkeypatch.setenv("FAKE_CLI_MODE", "noturn")
    with pytest.raises(ProviderUnavailable, match="the turn did not complete"):
        _codex().complete(_request())
    assert len(calls(log)) == 2
