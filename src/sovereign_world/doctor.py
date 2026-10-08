"""Is this computer ready for the sealed trial? (slice J)

    sovereign-world doctor [--settings work\\trial.toml] [--root .] [--ollama URL] [--json]

One line per check, PASS, WARN, FAIL or SKIP, like the launch gate, for the computer rather
than a run: Python and uv, PowerShell, where the repository lives and on what drive, the
signed-in `claude` and `codex` programs, the variables that would replace their sign-ins, the
local Ollama model and its context, the GPU, disk, sleep settings and the clock. It reads and
asks; it writes nothing, and it names variables but never shows their values. Checks only
Windows can make are SKIP elsewhere.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from sovereign_world.config import SovereignConfig
from sovereign_world.gateway.claude_code_provider import ClaudeCodeProvider
from sovereign_world.gateway.codex_provider import CodexProvider
from sovereign_world.preflight import (
    DISK_FAIL_BYTES,
    DISK_WARN_BYTES,
    GATE_WRITTEN,
    SIGN_IN_HIJACKERS,
    Check,
    Status,
)

DOCTOR_CHECKS: tuple[tuple[str, str], ...] = (
    ("python", "Python 3.12 or later"),
    ("uv", "uv, the package tool"),
    ("powershell", "PowerShell 7.1 or later"),
    ("path", "where the repository lives"),
    ("drive", "a local NTFS drive"),
    ("claude", "Claude Code installed and signed in"),
    ("codex", "Codex installed and signed in"),
    ("keys", "no variable replaces a sign-in (names only)"),
    ("autoupdater", "Claude Code will not update itself mid-year"),
    ("ollama", "Ollama running, with the model pulled"),
    ("ollama_context", "Ollama's context holds a council"),
    ("gpu", "an NVIDIA card"),
    ("disk", "disk space for a year"),
    ("power", "the computer will not sleep"),
    ("clock", "the clock is plausible"),
)
"""Every check, in the order printed (`docs/sealed-trial-runbook.md` explains each)."""
DOCTOR_TITLES = dict(DOCTOR_CHECKS)
MIN_PYTHON = (3, 12)
MIN_POWERSHELL = (7, 1)
MIN_OLLAMA_CONTEXT = 32_768
"""A council's papers come to 25,000 to 30,000 tokens; a smaller context cuts them silently."""
DEFAULT_OLLAMA = "http://127.0.0.1:11434"
LONG_PATH = 60
WINDOWS_ONLY = "a Windows check"

Run = Callable[[Sequence[str]], tuple[int, str]]
Fetch = Callable[[str], bytes]
DriveInfo = Callable[[Path], tuple[bool, str]]


def _run(args: Sequence[str]) -> tuple[int, str]:
    try:
        done = subprocess.run(
            list(args),
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""
    return done.returncode, (done.stdout or done.stderr or "").strip()


def _fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=5) as answer:
        body: bytes = answer.read()
        return body


def _drive(path: Path) -> tuple[bool, str]:
    """Whether the path is on a fixed drive, and that drive's file system (Windows)."""
    import ctypes

    anchor = path.anchor or str(path)
    windll = ctypes.windll  # type: ignore[attr-defined]
    fixed = windll.kernel32.GetDriveTypeW(anchor) == 3
    name = ctypes.create_unicode_buffer(64)
    ok = windll.kernel32.GetVolumeInformationW(anchor, None, 0, None, None, None, name, 64)
    return bool(fixed), name.value if ok else ""


def _version(text: str) -> tuple[int, ...]:
    found = re.search(r"(\d+)\.(\d+)", text)
    return (int(found.group(1)), int(found.group(2))) if found else ()


def machine_checks(
    root: Path,
    sovereigns: Mapping[str, SovereignConfig] | None = None,
    *,
    ollama_url: str | None = None,
    environ: Mapping[str, str],
    which: Callable[[str], str | None] = shutil.which,
    run: Run = _run,
    fetch: Fetch = _fetch,
    disk_usage: Callable[[Path], tuple[int, int, int]] = shutil.disk_usage,
    now: Callable[[], float] = time.time,
    platform: str = sys.platform,
    drive: DriveInfo = _drive,
) -> list[Check]:
    """Every readiness check, in `DOCTOR_CHECKS` order. ``sovereigns`` (from the settings file)
    name the models; without them both programs and a local Ollama are checked."""
    configs = dict(sovereigns or {})
    windows = platform == "win32"
    kinds: set[str] = {str(config.provider) for config in configs.values()}
    checks = [
        _python(),
        _uv(which, run),
        _powershell(windows, which, run),
        _path(root),
        _drive_check(root, windows, drive),
        _program("claude", configs, kinds, environ),
        _program("codex", configs, kinds, environ),
        _keys(environ),
        _autoupdater(environ),
        _ollama(configs, kinds, ollama_url, fetch),
        _ollama_context(kinds, environ),
        _gpu(which, run),
        _disk(root, disk_usage),
        _power(windows, run),
        _clock(now),
    ]
    assert [check.id for check in checks] == [check_id for check_id, _ in DOCTOR_CHECKS]
    return [replace(check, label=DOCTOR_TITLES[check.id]) for check in checks]


def _python() -> Check:
    found = sys.version_info[:2]
    shown = f"{found[0]}.{found[1]}"
    if found < MIN_PYTHON:
        return Check("python", "FAIL", f"{shown}; install Python 3.12")
    return Check("python", "PASS", shown)


def _uv(which: Callable[[str], str | None], run: Run) -> Check:
    path = which("uv")
    if path is None:
        return Check("uv", "WARN", "not found; install it with `winget install astral-sh.uv`")
    code, text = run([path, "--version"])
    return Check("uv", "PASS", text.splitlines()[0] if code == 0 and text else "found")


def _powershell(windows: bool, which: Callable[[str], str | None], run: Run) -> Check:
    if not windows:
        return Check("powershell", "SKIP", WINDOWS_ONLY)
    path = which("pwsh")
    if path is None:
        return Check("powershell", "FAIL", "pwsh not found; install PowerShell 7")
    code, text = run([path, "-NoProfile", "-Command", "$PSVersionTable.PSVersion.ToString()"])
    found = _version(text) if code == 0 else ()
    if not found or found < MIN_POWERSHELL:
        return Check("powershell", "FAIL", f"{text or 'unknown'}; 7.1 or later is needed")
    return Check("powershell", "PASS", text)


def _path(root: Path) -> Check:
    text = str(root.resolve())
    if text.startswith(("\\\\", "//")):
        return Check("path", "FAIL", "on a network share; use a folder on this computer")
    if any(part.lower().startswith("onedrive") for part in Path(text).parts):
        return Check("path", "FAIL", "inside OneDrive, which syncs files as they change; move it")
    if " " in text or len(text) > LONG_PATH:
        return Check("path", "WARN", f"{text} (a short path without spaces is safer)")
    return Check("path", "PASS", text)


def _drive_check(root: Path, windows: bool, drive: DriveInfo) -> Check:
    if not windows:
        return Check("drive", "SKIP", WINDOWS_ONLY)
    try:
        fixed, system = drive(root.resolve())
    except Exception as error:
        return Check("drive", "WARN", f"could not be read ({type(error).__name__})")
    if not fixed:
        return Check("drive", "FAIL", "not a fixed local drive")
    if system.upper() != "NTFS":
        return Check("drive", "FAIL", f"{system or 'unknown'} file system; NTFS is needed")
    return Check("drive", "PASS", "fixed, NTFS")


def _program(
    program: str,
    configs: Mapping[str, SovereignConfig],
    kinds: set[str],
    environ: Mapping[str, str],
) -> Check:
    kind = "claude-code" if program == "claude" else "codex"
    if configs and kind not in kinds:
        return Check(program, "SKIP", "not in the settings")
    model = next((c.model for c in configs.values() if c.provider == kind and c.model), "check")
    provider_class = ClaudeCodeProvider if program == "claude" else CodexProvider
    provider = provider_class(name="doctor", model=model, environ=environ)
    if provider.location() is None:
        return Check(program, "FAIL", f"`{program}` not found on PATH; install it (section 1)")
    status = provider.status() or ""
    if "not signed in" in status or not status:
        return Check(program, "FAIL", f"{status or 'no answer'}; sign in (section 1)")
    expected = "claude.ai" if program == "claude" else "chatgpt"
    if expected not in status.lower():
        return Check(program, "WARN", f"{status} (not the plan's sign-in?)")
    return Check(program, "PASS", status)


def _keys(environ: Mapping[str, str]) -> Check:
    present = sorted(
        name
        for names in SIGN_IN_HIJACKERS.values()
        for name in names
        if environ.get(name) and name in environ
    )
    if present:
        return Check(
            "keys",
            "WARN",
            f"{', '.join(present)} set: the programs leave them out, but remove them",
        )
    return Check("keys", "PASS", "none set")


def _autoupdater(environ: Mapping[str, str]) -> Check:
    if environ.get("DISABLE_AUTOUPDATER") == "1":
        return Check("autoupdater", "PASS", "DISABLE_AUTOUPDATER=1")
    return Check("autoupdater", "WARN", "DISABLE_AUTOUPDATER is not 1 (section 1)")


def _ollama(
    configs: Mapping[str, SovereignConfig],
    kinds: set[str],
    ollama_url: str | None,
    fetch: Fetch,
) -> Check:
    if configs and "compatible" not in kinds:
        return Check("ollama", "SKIP", "not in the settings")
    local = next((c for c in configs.values() if c.provider == "compatible"), None)
    base = ollama_url or (local.base_url if local and local.base_url else DEFAULT_OLLAMA)
    root = base.rstrip("/").removesuffix("/v1")
    try:
        version = json.loads(fetch(f"{root}/api/version")).get("version", "?")
        tags = json.loads(fetch(f"{root}/api/tags"))
    except Exception as error:
        return Check("ollama", "FAIL", f"no answer at {root} ({type(error).__name__}); start it")
    names = sorted(str(entry.get("name", "")) for entry in tags.get("models", []))
    if local is None or not local.model:
        return Check("ollama", "PASS", f"version {version}; models: {', '.join(names) or 'none'}")
    wanted = local.model
    if wanted not in names and f"{wanted}:latest" not in names:
        return Check("ollama", "FAIL", f"{wanted} is not pulled; `ollama pull {wanted}`")
    return Check("ollama", "PASS", f"version {version}; {wanted} pulled")


def _ollama_context(kinds: set[str], environ: Mapping[str, str]) -> Check:
    if kinds and "compatible" not in kinds:
        return Check("ollama_context", "SKIP", "not in the settings")
    text = environ.get("OLLAMA_CONTEXT_LENGTH", "")
    if not text.isdigit():
        return Check(
            "ollama_context",
            "FAIL",
            f"OLLAMA_CONTEXT_LENGTH is not set; set it to {MIN_OLLAMA_CONTEXT} and restart Ollama",
        )
    if int(text) < MIN_OLLAMA_CONTEXT:
        return Check(
            "ollama_context", "FAIL", f"{text}; {MIN_OLLAMA_CONTEXT} or more holds a council"
        )
    return Check("ollama_context", "PASS", text)


def _gpu(which: Callable[[str], str | None], run: Run) -> Check:
    path = which("nvidia-smi")
    if path is None:
        return Check("gpu", "WARN", "nvidia-smi not found; Ollama would run on the processor")
    code, text = run([path, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
    if code != 0 or not text:
        return Check("gpu", "WARN", "nvidia-smi did not answer")
    return Check("gpu", "PASS", text.splitlines()[0].strip())


def _disk(root: Path, disk_usage: Callable[[Path], tuple[int, int, int]]) -> Check:
    free = disk_usage(root)[2]
    shown = f"{free / 2**30:.1f} GiB free"
    status: Status = (
        "FAIL" if free < DISK_FAIL_BYTES else "WARN" if free < DISK_WARN_BYTES else "PASS"
    )
    return Check("disk", status, shown)


def _power(windows: bool, run: Run) -> Check:
    if not windows:
        return Check("power", "SKIP", WINDOWS_ONLY)
    awake: list[str] = []
    for setting in ("STANDBYIDLE", "HIBERNATEIDLE"):
        code, text = run(["powercfg", "/query", "SCHEME_CURRENT", "SUB_SLEEP", setting])
        found = re.search(r"Current AC Power Setting Index:\s*0x([0-9a-fA-F]+)", text)
        if code != 0 or found is None:
            return Check("power", "WARN", "powercfg did not answer; check by hand (section 6)")
        if int(found.group(1), 16) != 0:
            awake.append(setting.lower().removesuffix("idle"))
    if awake:
        return Check("power", "WARN", f"{' and '.join(awake)} on mains; turn off (section 6)")
    return Check("power", "PASS", "sleep and hibernate off on mains")


def _clock(now: Callable[[], float]) -> Check:
    today = datetime.fromtimestamp(now(), UTC).date()
    if today < GATE_WRITTEN:
        return Check("clock", "FAIL", f"{today} is before this check was written")
    return Check("clock", "PASS", str(today))
