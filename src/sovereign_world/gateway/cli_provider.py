"""A sovereign played by a vendor's own program on this computer, signed in with the user's
subscription (Claude Code's `claude`, OpenAI's `codex`), asked one council at a time.

Each call runs the program once, in a new empty folder that is removed afterwards: the charter
is written to a file in that folder, the council's papers go in on standard input, and the
answer comes back on standard output. The program is found on PATH. Its environment is this
one's without the variables that would make it use an API key instead of the sign-in, so a
run never spends on an API by accident. Nothing here reads a key; a failure's message is the
program's first line, classified (a usage limit, no sign-in), and never carries a secret.

No call is retried here: a retry after a usage limit would spend more of the allowance. The
gateway's one repair call is the second chance. A program that takes an output limit is given the
council's budget (Claude Code, through its environment); one that does not (Codex) is reserved a
fixed ceiling by the spending cap (``spend.OUTPUT_CEILING_TOKENS``).
"""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from sovereign_world.gateway.envelope import MAX_REPLY_BYTES
from sovereign_world.gateway.provider import (
    ModelReply,
    ModelRequest,
    ProviderTimeout,
    ProviderUnavailable,
)

MAX_OUTPUT_BYTES = 4 * MAX_REPLY_BYTES
"""Output longer than this is refused unread: it cannot hold a usable reply."""
STATUS_TIMEOUT_SECONDS = 15.0
SYSTEM_FILE = "system.md"
Which = Callable[[str], str | None]
LIMIT_WORDS = ("usage limit", "session limit", "weekly limit", "rate limit", "hit your")
"""How the programs say an allowance is used up (a turn limit is not one)."""


@dataclass(frozen=True)
class Finished:
    """What a program run left: its exit code and its output."""

    code: int
    stdout: bytes
    stderr: bytes


def child_environment(
    environ: Mapping[str, str],
    dropped: frozenset[str],
    dropped_prefixes: tuple[str, ...] = (),
    added: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """This environment without the variables that would take the program off its sign-in."""
    env = {
        name: value
        for name, value in environ.items()
        if name.upper() not in dropped and not name.upper().startswith(dropped_prefixes)
    }
    env.update(added or {})
    return env


def _kill_tree(process: subprocess.Popen[bytes]) -> None:
    """End the program and everything it started."""
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(process.pid)],
            capture_output=True,
            check=False,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            process.kill()


def run_program(
    args: Sequence[str],
    stdin: bytes,
    *,
    timeout: float,
    cwd: Path,
    env: Mapping[str, str],
    name: str,
) -> Finished:
    """Run the program once; on timeout, kill it with its children and say so."""
    options: dict[str, object] = {}
    if sys.platform == "win32":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    try:
        process = subprocess.Popen(
            list(args),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=dict(env),
            **options,  # type: ignore[call-overload]
        )
    except OSError as error:
        raise ProviderUnavailable(
            f"{name}: could not start {Path(args[0]).name} ({type(error).__name__})"
        ) from error
    try:
        stdout, stderr = process.communicate(stdin, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        _kill_tree(process)
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.communicate(timeout=10)
        raise ProviderTimeout(f"{name}: no answer in time") from error
    return Finished(process.returncode, stdout, stderr)


def first_line(data: bytes | str, limit: int = 200) -> str:
    text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data
    for line in text.splitlines():
        if line.strip():
            return line.strip()[:limit]
    return ""


def classify(name: str, program: str, text: str) -> ProviderUnavailable:
    """A plain failure from the program's own words: a usage limit, no sign-in, or as said."""
    lowered = text.lower()
    if any(words in lowered for words in LIMIT_WORDS):
        return ProviderUnavailable(f"{name}: usage limit reached ({text})")
    if any(word in lowered for word in ("login", "log in", "sign in", "signed in", "auth")):
        return ProviderUnavailable(f"{name}: not signed in; run {program} and sign in ({text})")
    return ProviderUnavailable(f"{name}: {program} failed ({text or 'no detail'})")


class CliProvider:
    """What the two programs share; each subclass says how to ask and how to read the answer."""

    program = ""
    """The program's name on PATH."""
    dropped: frozenset[str] = frozenset()
    dropped_prefixes: tuple[str, ...] = ()
    added: Mapping[str, str] = MappingProxyType({"NO_COLOR": "1"})

    def __init__(
        self,
        *,
        name: str,
        model: str,
        effort: str = "",
        which: Which = shutil.which,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        if not model:
            raise ValueError(f"{name}: the model must be named")
        self.name = name
        self.model = model
        self.effort = effort
        self._which = which
        self._environ = environ

    def environment(self, request: ModelRequest | None = None) -> dict[str, str]:
        return child_environment(
            os.environ if self._environ is None else self._environ,
            self.dropped,
            self.dropped_prefixes,
            {**self.added, **(self.added_for(request) if request is not None else {})},
        )

    def added_for(self, request: ModelRequest) -> dict[str, str]:
        """Variables that carry this call's limits to the program (none by default)."""
        return {}

    def location(self) -> str | None:
        """Where the program is, or None when it is not on PATH."""
        return self._which(self.program)

    def arguments(self, path: str, folder: Path, system_file: Path) -> list[str]:
        raise NotImplementedError

    def read(self, finished: Finished) -> ModelReply:
        raise NotImplementedError

    def complete(self, request: ModelRequest) -> ModelReply:
        path = self.location()
        if path is None:
            raise ProviderUnavailable(f"{self.name}: the {self.program} program is not on PATH")
        with tempfile.TemporaryDirectory(
            prefix="sovereign-council-", ignore_cleanup_errors=True
        ) as folder:
            here = Path(folder)
            system_file = here / SYSTEM_FILE
            system_file.write_text(request.system, encoding="utf-8")
            finished = run_program(
                self.arguments(path, here, system_file),
                request.user.encode("utf-8"),
                timeout=request.timeout_seconds,
                cwd=here,
                env=self.environment(request),
                name=self.name,
            )
        if len(finished.stdout) > MAX_OUTPUT_BYTES:
            raise ProviderUnavailable(f"{self.name}: the answer is too large to read")
        return self.read(finished)

    def status_commands(self, path: str) -> list[list[str]]:
        return [[path, "--version"]]

    def status(self) -> str | None:
        """The program's version and sign-in, in a few words, or None when they cannot be read.
        Never raises: the probe's real call is the test."""
        path = self.location()
        if path is None:
            return None
        parts: list[str] = []
        for args in self.status_commands(path):
            try:
                done = subprocess.run(
                    args,
                    capture_output=True,
                    timeout=STATUS_TIMEOUT_SECONDS,
                    env=self.environment(),
                    check=False,
                    stdin=subprocess.DEVNULL,
                )
            except (OSError, subprocess.TimeoutExpired):
                continue
            line = self.status_line(args, done.returncode, done.stdout or done.stderr)
            if line:
                parts.append(line)
        return "; ".join(parts) or None

    def status_line(self, args: list[str], code: int, stdout: bytes) -> str:
        return first_line(stdout, 80) if code == 0 else ""
