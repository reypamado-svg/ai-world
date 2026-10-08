"""A sovereign played by Claude through Claude Code (`claude`), signed in with the user's Claude
subscription.

Asked as `claude -p` with the charter as the whole system prompt, no tools, one turn, JSON
output and nothing saved; `--safe-mode` keeps the folder's settings, hooks, plugins and
CLAUDE.md out. (`--bare` is never used: it ignores the subscription sign-in.) The variables
that would make Claude Code use an API key or another account are left out of its
environment, it is told not to update itself mid-run, and the council's output budget is passed
as ``CLAUDE_CODE_MAX_OUTPUT_TOKENS`` (the request's limit, thinking included).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import MappingProxyType
from typing import Any

from sovereign_world.gateway.cli_provider import (
    CliProvider,
    Finished,
    classify,
    first_line,
)
from sovereign_world.gateway.provider import ModelReply, ModelRequest, ProviderUnavailable

INSTRUCTION = (
    "The council's papers are on standard input. Answer exactly as your instructions say:"
    " one JSON object and nothing else."
)
DROPPED = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_PROFILE",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_USE_FOUNDRY",
    }
)
"""Variables that would take Claude Code off the subscription sign-in."""


def _int(value: object) -> int:
    return value if isinstance(value, int) and value > 0 else 0


class ClaudeCodeProvider(CliProvider):
    program = "claude"
    dropped = DROPPED
    dropped_prefixes = ("ANTHROPIC_FEDERATION_",)
    added = MappingProxyType({"NO_COLOR": "1", "DISABLE_AUTOUPDATER": "1"})

    def added_for(self, request: ModelRequest) -> dict[str, str]:
        return {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(request.max_output_tokens)}

    def arguments(self, path: str, folder: Path, system_file: Path) -> list[str]:
        return [
            path,
            "-p",
            INSTRUCTION,
            "--safe-mode",
            "--system-prompt-file",
            str(system_file),
            "--tools",
            "",
            "--max-turns",
            "1",
            "--model",
            self.model,
            "--output-format",
            "json",
            "--no-session-persistence",
        ]

    def read(self, finished: Finished) -> ModelReply:
        try:
            data: Any = json.loads(finished.stdout.decode("utf-8", errors="replace"))
        except ValueError:
            data = None
        if not isinstance(data, dict):
            if finished.code != 0:
                said = first_line(finished.stderr) or first_line(finished.stdout)
                raise classify(self.name, self.program, said)
            raise ProviderUnavailable(f"{self.name}: the answer is not in the expected format")
        text = data.get("result")
        if data.get("is_error") or finished.code != 0:
            said = first_line(text if isinstance(text, str) else "") or first_line(
                str(data.get("subtype") or "")
            )
            raise classify(self.name, self.program, said or first_line(finished.stderr))
        if not isinstance(text, str):
            raise ProviderUnavailable(f"{self.name}: the answer holds no text")
        usage = data.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        return ModelReply(
            text=text,
            model=self._answering_model(data.get("modelUsage")),
            input_tokens=_int(usage.get("input_tokens"))
            + _int(usage.get("cache_creation_input_tokens"))
            + _int(usage.get("cache_read_input_tokens")),
            output_tokens=_int(usage.get("output_tokens")),
        )

    def _answering_model(self, models: object) -> str:
        """The model that wrote the most of the answer, as Claude Code names it."""
        if not isinstance(models, dict) or not models:
            return self.model

        def written(name: str) -> int:
            entry = models.get(name)
            return _int(entry.get("outputTokens")) if isinstance(entry, dict) else 0

        return str(max(sorted(models), key=written))

    def status_commands(self, path: str) -> list[list[str]]:
        return [[path, "--version"], [path, "auth", "status"]]

    def status_line(self, args: list[str], code: int, stdout: bytes) -> str:
        if args[1:] == ["auth", "status"]:
            try:
                data = json.loads(stdout.decode("utf-8", errors="replace"))
            except ValueError:
                return "not signed in" if code != 0 else ""
            method = data.get("authMethod") if isinstance(data, dict) else None
            return f"signed in with {method}" if code == 0 and method else "not signed in"
        return super().status_line(args, code, stdout)
