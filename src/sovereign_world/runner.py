"""Running a world forward, day by day: the one loop that writes a run (O4).

`run_days` is what `sovereign-world run` does: replay the latest verified state, then for each
day advance it under the run's sovereigns and save the day, with a checkpoint every 30 days and
at the end. A `RunControl` may hold the loop between days; holding changes nothing the run
records, because nothing about the pause reaches the world, its random streams or its journal.

Crash safety (sealed trial): each council is saved to the journal the moment it is held, before
its day. A run killed at any point is resumed by running it again: councils already saved for
the day under way are given back to their civilizations, so no model is asked twice about one
council, and the journal ends as an unbroken run's would. Saved councils that cannot be that
day's refuse the resume (`ResumeRefused`) before anything is asked or written. The first Ctrl+C
(`interruptible`) stops the loop after the day under way.

Two hooks let `sovereign-world run` show its progress: `on_council` hears each council the moment
it is saved (with the seconds since its day began), and `on_progress` each saved day (its new
councils, what the run has spent, how long the day took, and whether a checkpoint was saved).
Neither reaches the world, its random streams or its journal; the times they hear are measured
here and recorded nowhere. A pace (`pace_seconds`) waits between days; a stop ends the wait.

A sealed run (`seal.py`) is checked against its seal before anything else: a changed manifest,
seal, code or provider refuses it (`SealRefused`), hosted models are reached only at their
sealed addresses, and the loop stops at the seal's planned days.

`controlled` is the same loop driven from standard input, for an observer that started the
runner as its child (`sovereign-world run --controlled`, used by `observe --run-days`). It
starts paused and understands exactly two lines, `pause` and `resume`; anything else is
ignored. The end of its input stops it after the day in progress. It reports on standard
output, one line each: `start <day> <last_day>`, `paused`, `running`, `day <n>` after each
saved day, and `done <day>` once the checkpoint is saved. Nothing else is said: no hashes,
no settings and no secrets.
"""

from __future__ import annotations

import contextlib
import os
import signal
import sys
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass
from types import FrameType
from typing import TextIO

from sovereign_world.commands import council_day, crisis_council_due
from sovereign_world.config import RunManifest, SpendConfig
from sovereign_world.engine import advance_day
from sovereign_world.gateway.factory import build_sovereigns
from sovereign_world.gateway.records import (
    COUNCIL_RECORD,
    CouncilRecord,
    RecordsCouncils,
    ResumedCouncils,
    ResumeRefused,
    recorded_councils,
    split_resumed,
)
from sovereign_world.ids import EntityId
from sovereign_world.persistence import WorldStore
from sovereign_world.replay import replay_run
from sovereign_world.rng import StableRng
from sovereign_world.rulehash import rule_hash
from sovereign_world.scripted import Sovereign
from sovereign_world.seal import SealRefused, check_seal, stored_seal
from sovereign_world.spend import Tally, stop_reason, tally
from sovereign_world.state import WorldState

PAUSE = "pause"
RESUME = "resume"
CHECKPOINT_INTERVAL = 30
"""Days between checkpoints (the same as journal snapshots, by choice, not by need)."""
CRASH_ENV = "SOVEREIGN_WORLD_CRASH_AT"
"""Tests only: ``point:day[:n]`` ends the process at once at that point of the loop."""


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
                # A timed wait lets Ctrl+C through on every platform.
                self._changed.wait(timeout=0.5)
            return not self._stopped

    def wait_pace(self, seconds: float) -> bool:
        """Wait `seconds` between days, or less if stopped; whether the loop may go on."""
        deadline = time.monotonic() + seconds
        with self._changed:
            while not self._stopped:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                # Short steps, as in `wait_to_advance`, so Ctrl+C reaches it on Windows too.
                self._changed.wait(timeout=min(0.5, left))
            return not self._stopped


@dataclass(frozen=True)
class DayProgress:
    """One saved day, as `run` shows it: never recorded."""

    day: int
    councils: tuple[CouncilRecord, ...]
    """The councils held and saved for this day (none reused from an interrupted run)."""
    spent: Tally
    """What the run has spent in all, this day included."""
    elapsed_seconds: float
    checkpoint: bool
    """Whether a checkpoint was saved after this day."""


CouncilHook = Callable[[CouncilRecord, float], None]
ProgressHook = Callable[[DayProgress], None]


class SpendCapReached(Exception):
    """The run stopped before a day its councils could take past the spending cap."""

    def __init__(self, reason: str, state: WorldState) -> None:
        super().__init__(reason)
        self.reason = reason
        self.state = state


def _crash_point() -> tuple[str, ...] | None:
    value = os.environ.get(CRASH_ENV)
    return tuple(value.split(":")) if value else None


def _crash_if(point: tuple[str, ...] | None, name: str, *numbers: int) -> None:
    """End the process with no clean-up, as a power cut would (tests of recovery only)."""
    if point is not None and point == (name, *map(str, numbers)):
        sys.stderr.write(f"crash hook: {':'.join(point)}\n")
        sys.stderr.flush()
        os._exit(137)


@contextlib.contextmanager
def interruptible(control: RunControl, *, out: TextIO | None = None) -> Iterator[None]:
    """The first Ctrl+C stops the run after the day under way; a second stops it at once."""
    previous = signal.getsignal(signal.SIGINT)
    pressed = False

    def handler(signum: int, frame: FrameType | None) -> None:
        nonlocal pressed
        if pressed:
            raise KeyboardInterrupt
        pressed = True
        control.stop()
        print(
            "stopping after the day under way; press Ctrl+C again to stop at once (running"
            " again then picks up from what was saved)",
            file=out or sys.stderr,
            flush=True,
        )

    signal.signal(signal.SIGINT, handler)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)


def run_days(
    store: WorldStore,
    days: int,
    *,
    control: RunControl | None = None,
    on_start: Callable[[WorldState, int], None] | None = None,
    on_day: Callable[[WorldState], None] | None = None,
    spend_limit_usd: float | None = None,
    on_council: CouncilHook | None = None,
    on_progress: ProgressHook | None = None,
    pace_seconds: float = 0.0,
) -> WorldState:
    """Advance the run's latest verified state by up to `days` days, saving each day and its
    councils, then a checkpoint. A control may hold or stop the loop between days; a run
    stopped before its first day saves nothing.

    With a spending cap (``manifest.spend``), the loop stops cleanly before any day that could
    take the run past it, saves a checkpoint, and raises ``SpendCapReached``. ``spend_limit_usd``
    may lower the cost cap for this session; it never raises it.

    ``pace_seconds`` waits that long after each saved day but the last; a stop ends the wait,
    and the loop then stops as it would between days."""
    crash = _crash_point()
    stored = store.manifest()
    seal = stored_seal(store)
    if seal is not None:
        # Before anything is replayed, asked or written: the run must still match its seal.
        check_seal(store, stored, seal, code_hash=rule_hash())
    manifest = _with_limit(stored, spend_limit_usd)
    state = replay_run(store)
    if seal is not None:
        remaining = seal.planned_days - state.day
        if remaining <= 0:
            raise SealRefused(
                f"the seal plans {seal.planned_days} days and the run stands at day {state.day}"
            )
        days = min(days, remaining)
    rng = StableRng(manifest.config.seed)
    records = recorded_councils(store)
    history, resumed = split_resumed(records, state.day)
    pinned = (
        {civilization: pin.base_url() for civilization, pin in seal.providers.items()}
        if seal is not None
        else None
    )
    sovereigns = build_sovereigns(manifest, state.civilizations, history=history, pinned=pinned)
    _check_resumed(resumed, state, sovereigns)
    # Every council saved counts once, those of the interrupted day included.
    spent = tally(records, manifest.spend)
    written: list[CouncilRecord] = []
    day_started = time.perf_counter()

    def save(record: CouncilRecord) -> None:
        store.append_record(COUNCIL_RECORD, record.model_dump(mode="json"))
        written.append(record)
        if on_council is not None:
            on_council(record, time.perf_counter() - day_started)
        _crash_if(crash, "after_council", record.day, len(written))

    for sovereign in sovereigns.values():
        if isinstance(sovereign, RecordsCouncils):
            sovereign.record_to(save)
            sovereign.resume_from(resumed)
    if on_start is not None:
        on_start(state, days)
    if pace_seconds > 0 and control is None:
        control = RunControl()
    advanced = 0
    for index in range(days):
        if control is not None and not control.wait_to_advance():
            break
        _crash_if(crash, "before_day", state.day)
        reason = stop_reason(manifest, spent)
        if reason is not None:
            if advanced:
                store.save_checkpoint(state)
            raise SpendCapReached(reason, state)
        written.clear()
        day_started = time.perf_counter()
        transition = advance_day(state, rng, sovereigns=sovereigns)
        _crash_if(crash, "after_councils", state.day)
        if resumed.refused is not None:
            raise ResumeRefused(resumed.refused)
        if resumed.pending():
            names = ", ".join(str(civ) for civ in resumed.pending())
            raise ResumeRefused(
                f"the saved councils of day {state.day} for {names} were not held again"
            )
        store.append_transition(transition.state, transition.events, previous=state)
        state = transition.state
        _crash_if(crash, "after_transition", state.day)
        spent.merge(tally(written, manifest.spend))
        advanced += 1
        if on_day is not None:
            on_day(state)
        checkpoint = state.day % CHECKPOINT_INTERVAL == 0
        if checkpoint:
            store.save_checkpoint(state)
            _crash_if(crash, "after_checkpoint", state.day)
        if on_progress is not None:
            on_progress(
                DayProgress(
                    day=state.day,
                    councils=tuple(written),
                    spent=deepcopy(spent),
                    elapsed_seconds=time.perf_counter() - day_started,
                    checkpoint=checkpoint,
                )
            )
        if pace_seconds > 0 and index < days - 1 and control is not None:
            control.wait_pace(pace_seconds)
    if advanced:
        store.save_checkpoint(state)
    return state


def _check_resumed(
    resumed: ResumedCouncils, state: WorldState, sovereigns: Mapping[EntityId, Sovereign]
) -> None:
    """Refuse, before anything is asked or written, saved councils that cannot be the day under
    way's: the civilization must be alive, recorded and sitting in council that day."""
    if resumed.day is None:
        return
    regular = council_day(state)
    for civilization_id in resumed.pending():
        sovereign = sovereigns.get(civilization_id)
        civilization = state.civilizations.get(civilization_id)
        sits = (
            sovereign is not None
            and isinstance(sovereign, RecordsCouncils)
            and civilization is not None
            and civilization.eliminated_day is None
            and (
                regular
                or (
                    bool(getattr(sovereign, "crisis_councils", False))
                    and crisis_council_due(state, civilization_id)
                )
            )
        )
        if not sits:
            raise ResumeRefused(
                f"the journal holds a council of {civilization_id} for day {state.day}, but"
                f" {civilization_id} holds no council that day; the journal was changed or"
                " belongs to another history"
            )


def _with_limit(manifest: RunManifest, limit_usd: float | None) -> RunManifest:
    """The manifest with this session's lower cost cap, if one was given."""
    if limit_usd is None:
        return manifest
    spend = manifest.spend or SpendConfig()
    cap = limit_usd if spend.max_cost_usd is None else min(limit_usd, spend.max_cost_usd)
    return manifest.model_copy(update={"spend": spend.model_copy(update={"max_cost_usd": cap})})


def controlled(
    store: WorldStore,
    days: int,
    *,
    commands: TextIO = sys.stdin,
    out: TextIO = sys.stdout,
    spend_limit_usd: float | None = None,
    control: RunControl | None = None,
    pace_seconds: float = 0.0,
) -> WorldState:
    """`run_days` held by `pause` and `resume` lines on `commands`, reporting on `out`. A run
    stopped by its spending cap says `stopped <day> spend_cap` instead of `done <day>`."""
    control = control or RunControl()
    control.pause()
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

    def started(state: WorldState, planned: int) -> None:
        say(f"start {state.day} {state.day + planned}")
        say("paused")
        threading.Thread(target=listen, name="runner-commands", daemon=True).start()

    try:
        state = run_days(
            store,
            days,
            control=control,
            on_start=started,
            on_day=lambda day: say(f"day {day.day}"),
            spend_limit_usd=spend_limit_usd,
            pace_seconds=pace_seconds,
        )
    except SpendCapReached as stop:
        say(f"stopped {stop.state.day} spend_cap")
        raise
    say(f"done {state.day}")
    return state
