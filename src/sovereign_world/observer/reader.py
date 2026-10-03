"""Reading a saved run for the observer, in either journal format (Phase 5 S5).

`RunReader` answers what the observer asks of a run: which days are saved, the world on any
of them, that day's events, and the councils held. It never writes. A format-2 journal is
rebuilt from the whole-world snapshot at or before the day asked for, through the days'
changes; reading days in order rebuilds each from the one before.

This is the skeleton the O2 reader is built on: it keeps the journal's records in memory,
which suits the runs the observer is tested on; O2 decides what to page out.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from sovereign_world.events import DomainEvent
from sovereign_world.gateway.records import COUNCIL_RECORD, CouncilRecord
from sovereign_world.journal import StateCursor, decompress
from sovereign_world.persistence import JournalRecord, WorldStore
from sovereign_world.replay import recorded_state
from sovereign_world.state import WorldState


class RunReader:
    def __init__(self, root: Path) -> None:
        self.store = WorldStore(root)
        self._records: list[JournalRecord] = []
        self._transitions: dict[int, JournalRecord] = {}
        self._start: WorldState | None = None
        self._cursor = StateCursor()
        self.refresh()

    @property
    def journal_format(self) -> int:
        return self.store.journal_format

    def refresh(self) -> tuple[int, ...]:
        """Read records saved since the last look; returns the days they added."""
        known = len(self._records)
        self._records = list(self.store.iter_records())
        added: list[int] = []
        for record in self._records[known:]:
            if record.type == "transition":
                day = int(record.payload["day"])
                self._transitions[day] = record
                added.append(day)
        return tuple(added)

    def _first_checkpoint(self) -> WorldState:
        """The day the journal follows: day 0, or the day a fork began."""
        if self._start is None:
            first = min(self._transitions, default=None)
            self._start = (
                self.store.load_checkpoint()
                if first is None
                else self.store.load_checkpoint(at_or_before=first - 1)
            )
        return self._start

    def days(self) -> tuple[int, ...]:
        return (self._first_checkpoint().day, *sorted(self._transitions))

    def state_at(self, day: int) -> WorldState:
        if day == self._first_checkpoint().day:
            return self._first_checkpoint()
        if day not in self._transitions:
            raise KeyError(f"day {day} is not saved in this run")
        record = self._transitions[day]
        if self.journal_format == 1:
            return recorded_state(record.payload)
        cursor = self._cursor
        if (
            cursor.state is None
            or not cursor.state.day < day
            or self._snapshot_between(cursor.state.day, day)
        ):
            # Start again from the last whole world at or before the day.
            base = max(
                (
                    saved
                    for saved, item in self._transitions.items()
                    if saved <= day and "state_gzip_base64" in item.payload
                ),
                default=None,
            )
            if base is None:
                cursor.load_snapshot(self._first_checkpoint())
            else:
                cursor.load_snapshot(recorded_state(self._transitions[base].payload))
        assert cursor.state is not None
        while cursor.state.day < day:
            following = self._transitions.get(cursor.state.day + 1)
            if following is None or "delta_gzip_base64" not in following.payload:
                raise RuntimeError(f"day {cursor.state.day + 1} cannot be rebuilt in order")
            cursor.apply_delta(decompress(following.payload["delta_gzip_base64"]))
        return cursor.state

    def _snapshot_between(self, after: int, through: int) -> bool:
        """Whether a whole world is saved after one day and by another: quicker to load."""
        return any(
            "state_gzip_base64" in self._transitions[day].payload
            for day in range(after + 1, through + 1)
            if day in self._transitions
        )

    def iter_days(self) -> Iterator[WorldState]:
        for day in self.days():
            yield self.state_at(day)

    def events_at(self, day: int) -> tuple[DomainEvent, ...]:
        record = self._transitions.get(day)
        if record is None:
            return ()
        text = str(record.payload["events"])
        return tuple(DomainEvent.model_validate_json(line) for line in text.splitlines())

    def councils(self) -> tuple[CouncilRecord, ...]:
        return tuple(
            CouncilRecord.model_validate(record.payload)
            for record in self._records
            if record.type == COUNCIL_RECORD
        )
