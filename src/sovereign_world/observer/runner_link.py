"""The observer's link to a runner it started (O4).

`sovereign-world observe DIR --run-days N` starts `sovereign-world run DIR --days N
--controlled` as its child and holds it over a private pipe: the observer writes only
`pause` and `resume`, and reads the runner's reports (`start`, `paused`, `running`,
`day <n>`, `done <n>`). The pipe carries no data about the run, and the runner's environment
is the observer's without the observer's token, so the runner never sees it. The runner is the
only writer of the run; the observer keeps reading it as before and never builds a store.

When the observer closes the pipe (it is stopping), the runner finishes the day under way,
saves a checkpoint and exits. When the run's history changes under the runner (its journal
cut back or replaced), the observer terminates it at once instead: its world and its journal
tail belong to the old history, so it must save nothing more, not even a checkpoint.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from sovereign_world.observer import TOKEN_ENV

STARTING = "starting"
PAUSED = "paused"
RUNNING = "running"
DONE = "done"
EXITED = "exited"
STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class RunnerState:
    phase: str
    """starting, paused, running, done (reached its last day), stopped (by the observer: the
    run's history changed under it) or exited (stopped otherwise)."""
    day: int | None = None
    """The last day it saved (or the day it started from)."""
    last_day: int | None = None
    """The day it will stop at."""
    exit_code: int | None = None

    def record(self) -> dict[str, object]:
        return {
            "phase": self.phase,
            "day": self.day,
            "last_day": self.last_day,
            "exit_code": self.exit_code,
        }


def _days(days: int) -> tuple[str, ...]:
    return ("--days", str(days), "--controlled")


class Runner(Protocol):
    def pause(self) -> None: ...

    def resume(self) -> None: ...

    def state(self) -> RunnerState: ...

    def terminate(self) -> None: ...

    def close(self) -> None: ...


def child_environment(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """The runner's environment: the observer's own, without the observer's token. AI keys
    and other settings pass through unchanged, as they would to `sovereign-world run`."""
    source = os.environ if environ is None else environ
    return {key: value for key, value in source.items() if key != TOKEN_ENV}


class RunnerLink:
    """A runner started as this process's child, held over its standard input."""

    def __init__(
        self,
        process: subprocess.Popen[str],
        on_change: Callable[[], None] | None = None,
    ) -> None:
        self._process = process
        self._on_change = on_change
        self._lock = threading.Lock()
        self._state = RunnerState(STARTING)
        self._wants_running = False
        self._stopped = False
        self._reader = threading.Thread(target=self._read, name="runner-reports", daemon=True)
        self._reader.start()

    @classmethod
    def spawn(
        cls,
        root: Path,
        days: int,
        *,
        on_change: Callable[[], None] | None = None,
        python: str = sys.executable,
        environ: Mapping[str, str] | None = None,
    ) -> RunnerLink:
        process = subprocess.Popen(
            [python, "-m", "sovereign_world.cli", "run", str(root), *_days(days)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            text=True,
            env=child_environment(environ),
        )
        return cls(process, on_change)

    def _read(self) -> None:
        stdout = self._process.stdout
        assert stdout is not None
        for raw in stdout:
            words = raw.split()
            if not words:
                continue
            with self._lock:
                state = self._state
                if words[0] == "start" and len(words) == 3:
                    state = replace(state, day=int(words[1]), last_day=int(words[2]))
                elif words[0] == "paused":
                    state = replace(state, phase=PAUSED)
                elif words[0] == "running":
                    state = replace(state, phase=RUNNING)
                elif words[0] == "day" and len(words) == 2:
                    state = replace(state, day=int(words[1]))
                elif words[0] == "done" and len(words) == 2:
                    day = int(words[1])
                    finished = state.last_day is not None and day >= state.last_day
                    state = replace(state, day=day, phase=DONE if finished else EXITED)
                if self._stopped:
                    # Reports already on their way when it was stopped do not undo that.
                    state = replace(state, phase=STOPPED)
                self._state = state
            self._changed()
        code = self._process.wait()
        with self._lock:
            phase = self._state.phase if self._state.phase in (DONE, STOPPED) else EXITED
            self._state = replace(self._state, phase=phase, exit_code=code)
        self._changed()

    def _changed(self) -> None:
        if self._on_change is not None:
            self._on_change()

    def _send(self, word: str) -> None:
        stdin = self._process.stdin
        if self._stopped or stdin is None or stdin.closed or self._process.poll() is not None:
            return
        try:
            stdin.write(word + "\n")
            stdin.flush()
        except (BrokenPipeError, ValueError):
            pass

    def pause(self) -> None:
        with self._lock:
            if not self._wants_running:
                return
            self._wants_running = False
        self._send("pause")

    def resume(self) -> None:
        with self._lock:
            if self._wants_running:
                return
            self._wants_running = True
        self._send("resume")

    def state(self) -> RunnerState:
        with self._lock:
            return self._state

    def terminate(self) -> None:
        """Stop the runner now, saving nothing: the run's history changed under it, so its
        world and journal tail are the old history's and any write would corrupt the new one.
        Returns at once; the reader thread reports the exit as usual."""
        with self._lock:
            self._wants_running = False
            self._stopped = True
            self._state = replace(self._state, phase=STOPPED)
        if self._process.poll() is None:
            self._process.terminate()

    def close(self, timeout: float = 600.0) -> None:
        """Stop the runner after the day under way (closing its input), then wait for it."""
        stdin = self._process.stdin
        if stdin is not None and not stdin.closed:
            with contextlib.suppress(BrokenPipeError):
                stdin.close()
        try:
            self._process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._process.terminate()
            self._process.wait(timeout=30)
        self._reader.join(timeout=5)
