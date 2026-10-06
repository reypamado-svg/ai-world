"""Running a world forward, day by day: the one loop that writes a run (O4).

`run_days` is what `sovereign-world run` does: replay the latest verified state, then for each
day advance it under the run's sovereigns, save the day and the councils held, and save a
checkpoint at the end. A `RunControl` may hold the loop between days; holding changes nothing
the run records, because nothing about the pause reaches the world, its random streams or its
journal.

`controlled` is the same loop driven from standard input, for an observer that started the
runner as its child (`sovereign-world run --controlled`, used by `observe --run-days`). It
starts paused and understands exactly two lines, `pause` and `resume`; anything else is
ignored. The end of its input stops it after the day in progress. It reports on standard
output, one line each: `start <day> <last_day>`, `paused`, `running`, `day <n>` after each
saved day, and `done <day>` once the checkpoint is saved. Nothing else is said: no hashes,
no settings and no secrets.
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from typing import TextIO

from sovereign_world.engine import advance_day
from sovereign_world.gateway.factory import build_sovereigns
from sovereign_world.gateway.records import journal_councils, recorded_councils
from sovereign_world.persistence import WorldStore
from sovereign_world.replay import replay_run
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState

PAUSE = "pause"
RESUME = "resume"


class RunControl:
    """Whether the loop may start its next day. Pausing takes effect between days."""

    def __init__(self, *, paused: bool = False) -> None:
        self._changed = threading.Condition()
        self._paused = paused
        self._stopped = False

    @property
    def paused(self) -> bool:
        with self._changed:
            return self._paused

    @property
    def stopped(self) -> bool:
        with self._changed:
            return self._stopped

    def pause(self) -> None:
        with self._changed:
            self._paused = True
            self._changed.notify_all()

    def resume(self) -> None:
        with self._changed:
            self._paused = False
            self._changed.notify_all()

    def stop(self) -> None:
        with self._changed:
            self._stopped = True
            self._changed.notify_all()

    def wait_to_advance(self) -> bool:
        """Block while paused; whether the loop may go on (False once stopped)."""
        with self._changed:
            while self._paused and not self._stopped:
                self._changed.wait()
            return not self._stopped


def run_days(
    store: WorldStore,
    days: int,
    *,
    control: RunControl | None = None,
    on_start: Callable[[WorldState], None] | None = None,
    on_day: Callable[[WorldState], None] | None = None,
) -> WorldState:
    """Advance the run's latest verified state by up to `days` days, saving each day and its
    councils, then a checkpoint. A control may hold or stop the loop between days; a run
    stopped before its first day saves nothing."""
    manifest = store.manifest()
    state = replay_run(store)
    rng = StableRng(manifest.config.seed)
    sovereigns = build_sovereigns(manifest, state.civilizations, history=recorded_councils(store))
    if on_start is not None:
        on_start(state)
    advanced = 0
    for _ in range(days):
        if control is not None and not control.wait_to_advance():
            break
        transition = advance_day(state, rng, sovereigns=sovereigns)
        store.append_transition(transition.state, transition.events, previous=state)
        state = transition.state
        journal_councils(store, sovereigns.values())
        advanced += 1
        if on_day is not None:
            on_day(state)
    if advanced:
        store.save_checkpoint(state)
    return state


def controlled(
    store: WorldStore,
    days: int,
    *,
    commands: TextIO = sys.stdin,
    out: TextIO = sys.stdout,
) -> WorldState:
    """`run_days` held by `pause` and `resume` lines on `commands`, reporting on `out`."""
    control = RunControl(paused=True)
    speaking = threading.Lock()

    def say(line: str) -> None:
        with speaking:
            print(line, file=out, flush=True)

    def listen() -> None:
        for raw in commands:
            word = raw.strip()
            if word == PAUSE and not control.paused:
                control.pause()
                say("paused")
            elif word == RESUME and control.paused:
                control.resume()
                say("running")
        control.stop()

    def started(state: WorldState) -> None:
        say(f"start {state.day} {state.day + days}")
        say("paused")
        threading.Thread(target=listen, name="runner-commands", daemon=True).start()

    state = run_days(
        store,
        days,
        control=control,
        on_start=started,
        on_day=lambda day: say(f"day {day.day}"),
    )
    say(f"done {state.day}")
    return state
