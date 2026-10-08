"""Stand-ins for Claude Code's `claude` and OpenAI's `codex`, put first on PATH: each answers as
the real program's documented output does, and logs what it was asked and which sign-in
variables it could see. Behaviour by ``FAKE_CLI_MODE``: ok, limit, auth, notjson, hang, exit2."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

QUIET = json.dumps({"commands": [], "rationale": "Wait and watch."})
WATCHED = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_FEDERATION_RULE_ID",
    "CODEX_API_KEY",
    "CODEX_ACCESS_TOKEN",
    "OPENAI_API_KEY",
    "KEEP_ME",
)

SCRIPT = r"""
import json, os, subprocess, sys, time
from pathlib import Path

program = Path(sys.argv[0]).name
args = sys.argv[1:]
mode = os.environ.get("FAKE_CLI_MODE", "ok")
if args[:1] == ["--version"]:
    print(f"{program} 9.9.9 (fake)")
    sys.exit(0)
if args[:2] in (["auth", "status"], ["login", "status"]):
    if program == "claude":
        print(json.dumps({"loggedIn": True, "authMethod": "claude.ai"}))
    else:
        print("Logged in using ChatGPT", file=sys.stderr)
    sys.exit(0)
stdin = sys.stdin.buffer.read().decode("utf-8")
if program == "claude":
    system_path = args[args.index("--system-prompt-file") + 1]
else:
    setting = next(a for a in args if a.startswith("model_instructions_file="))
    system_path = json.loads(setting.split("=", 1)[1])
log = os.environ.get("FAKE_CLI_LOG")
entry = {
    "program": program,
    "argv": args,
    "stdin": stdin,
    "system": Path(system_path).read_text(encoding="utf-8"),
    "cwd": os.getcwd(),
    "files": sorted(os.listdir(os.getcwd())),
    "seen": sorted(name for name in WATCHED if name in os.environ),
    "env": {name: os.environ.get(name) for name in ("DISABLE_AUTOUPDATER", "NO_COLOR")},
    "pid": os.getpid(),
}
if mode == "hang":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    entry["child"] = child.pid
if log:
    with open(log, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")
reply = os.environ.get("FAKE_CLI_REPLY", QUIET)
if mode == "hang":
    time.sleep(120)
if mode == "notjson":
    print("hello there")
    sys.exit(0)
if mode == "exit2":
    print("something broke", file=sys.stderr)
    sys.exit(2)
if program == "claude":
    if mode == "limit":
        print(json.dumps({"type": "result", "subtype": "success", "is_error": True,
                          "result": "You've hit your session limit · resets 3:45pm"}))
        sys.exit(1)
    if mode == "auth":
        print("Invalid API key · Please run /login")
        sys.exit(1)
    print(json.dumps({
        "type": "result", "subtype": "success", "is_error": False, "result": reply,
        "usage": {"input_tokens": 1000, "cache_creation_input_tokens": 200,
                  "cache_read_input_tokens": 300, "output_tokens": 50},
        "modelUsage": {"claude-haiku-fake": {"outputTokens": 3},
                       "claude-sonnet-fake": {"outputTokens": 50}},
        "total_cost_usd": 0.01, "num_turns": 1,
    }))
    sys.exit(0)
events = [{"type": "thread.started", "thread_id": "t"}, {"type": "turn.started"}]
if mode == "limit":
    events.append({"type": "turn.failed", "error": {"message":
        "You\u2019ve hit your usage limit. Upgrade to Pro or try again at 5:02 PM."}})
    code = 1
elif mode == "auth":
    events.append({"type": "error", "message": "Not logged in. Run codex login."})
    code = 1
else:
    events += [
        {"type": "error", "message": "Reconnecting... 1/5"},
        {"type": "item.completed", "item": {"id": "i1", "type": "reasoning", "text": "think"}},
        {"type": "item.completed", "item": {"id": "i2", "type": "agent_message", "text": reply}},
        {"type": "turn.completed", "usage": {"input_tokens": 2000, "cached_input_tokens": 500,
                                             "output_tokens": 80, "reasoning_output_tokens": 30}},
    ]
    code = 0
for event in events:
    print(json.dumps(event))
sys.exit(code)
"""


def install_fakes(root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Write the fake `claude` and `codex` into ``root/bin``, first on PATH; return the log."""
    folder = root / "bin"
    folder.mkdir(parents=True, exist_ok=True)
    body = f"#!{sys.executable}\nWATCHED = {WATCHED!r}\nQUIET = {QUIET!r}\n" + SCRIPT.lstrip("\n")
    for program in ("claude", "codex"):
        path = folder / program
        path.write_text(body, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    log = root / "fake-cli.jsonl"
    monkeypatch.setenv("PATH", f"{folder}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("FAKE_CLI_LOG", str(log))
    monkeypatch.delenv("FAKE_CLI_MODE", raising=False)
    return log


def calls(log: Path) -> list[dict[str, object]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
