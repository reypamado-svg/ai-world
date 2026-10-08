"""A sovereign played by ChatGPT through OpenAI's Codex (`codex exec`), signed in with the user's
ChatGPT plan.

Asked as `codex exec -` with the charter replacing Codex's own instructions
(`model_instructions_file`), a read-only sandbox, no shell, no web search, no AGENTS.md, no
environment context, no saved session and no user config, reading the papers from standard
input and answering as JSON events. The answer is the last agent message of a turn that
completed, from a program that exited 0; a turn that failed, or never completed, is no answer,
whatever it said before. The tokens are the completed turn's. The variables that would make
Codex use an API key instead of the ChatGPT sign-in are left out of its environment. Codex takes
no output limit; its reasoning effort is the lever, and the spending cap reserves a ceiling.
"""

from __future__ import annotations

import json
from pathlib import Path

from sovereign_world.gateway.cli_provider import (
    CliProvider,
    Finished,
    classify,
    first_line,
)
from sovereign_world.gateway.provider import ModelReply, ProviderUnavailable

DROPPED = frozenset({"CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "OPENAI_API_KEY", "OPENAI_BASE_URL"})
"""Variables that would take Codex off the ChatGPT sign-in."""


def _toml(value: str) -> str:
    """A TOML string (JSON's escapes are TOML's), for a ``-c key=value`` override."""
    return json.dumps(value)


class CodexProvider(CliProvider):
    program = "codex"
    dropped = DROPPED

    def arguments(self, path: str, folder: Path, system_file: Path) -> list[str]:
        overrides = {
            "model_instructions_file": _toml(str(system_file)),
            "include_permissions_instructions": "false",
            "include_apps_instructions": "false",
            "include_environment_context": "false",
            "project_doc_max_bytes": "0",
            "features.shell_tool": "false",
            "web_search": _toml("disabled"),
            "forced_login_method": _toml("chatgpt"),
            **({"model_reasoning_effort": _toml(self.effort)} if self.effort else {}),
        }
        settings = [part for key, value in overrides.items() for part in ("-c", f"{key}={value}")]
        return [
            path,
            "exec",
            "-",
            "-m",
            self.model,
            "--json",
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "--ephemeral",
            "--ignore-user-config",
            "-C",
            str(folder),
            *settings,
        ]

    def read(self, finished: Finished) -> ModelReply:
        text: str | None = None
        failure = ""
        completed = failed = False
        tokens_in = tokens_out = 0
        for line in finished.stdout.decode("utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            kind = event.get("type")
            item = event.get("item")
            if (
                kind == "item.completed"
                and isinstance(item, dict)
                and item.get("type") == "agent_message"
                and isinstance(item.get("text"), str)
            ):
                text = item["text"]
            elif kind == "turn.completed":
                completed = True
                usage = event.get("usage")
                usage = usage if isinstance(usage, dict) else {}
                tokens_in = max(0, int(usage.get("input_tokens") or 0))
                tokens_out = max(0, int(usage.get("output_tokens") or 0))
            elif kind == "turn.failed":
                failed = True
                error = event.get("error")
                message = error.get("message") if isinstance(error, dict) else None
                failure = first_line(str(message or "")) or failure
            elif kind == "error" and not failure:
                # Kept only to explain a failure: Codex also reports errors it retries.
                failure = first_line(str(event.get("message") or ""))
        if text is None or failed or not completed or finished.code != 0:
            said = failure if failed or finished.code != 0 else ""
            said = said or first_line(finished.stderr)
            if failed or finished.code != 0:
                raise classify(self.name, self.program, said)
            if text is None:
                raise ProviderUnavailable(f"{self.name}: the answer holds no text")
            raise ProviderUnavailable(f"{self.name}: the turn did not complete")
        return ModelReply(
            text=text, model=self.model, input_tokens=tokens_in, output_tokens=tokens_out
        )

    def status_commands(self, path: str) -> list[list[str]]:
        return [[path, "--version"], [path, "login", "status"]]

    def status_line(self, args: list[str], code: int, stdout: bytes) -> str:
        if args[1:] == ["login", "status"]:
            return first_line(stdout, 80) if code == 0 else "not signed in"
        return super().status_line(args, code, stdout)
