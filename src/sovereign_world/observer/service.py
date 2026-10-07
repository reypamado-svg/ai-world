"""A run, served live to the observer (O3).

`RunService` owns one `RunReader` and answers with the same bytes the static run export
writes, for days as soon as they are saved. A follower thread polls the journal every half
second and walks new days in order, projecting each, so a person's number (their place in
the id table) is exactly the one the export gives them. A day the walk has not reached yet
is not ready.

The reader is not thread-safe, so everything that touches it holds one lock. When the
journal is cut back or replaced the reader's history epoch goes up; the service then drops
every cache and walks the run again from its first day. Each answer carries the epoch it was
made under, so a client can tell a new history from a longer one.

Nothing here writes to the run: the reader opens the database read-only and follows the
journal by byte offset, and `WorldStore` is never constructed. A new history also stops a
runner the service holds, so the runner writes nothing more either.
"""

from __future__ import annotations

import json
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sovereign_world.commands import build_council_report
from sovereign_world.ids import EntityId
from sovereign_world.observer.changes import record_changes, routes
from sovereign_world.observer.chronicle import chronicle_entries
from sovereign_world.observer.perspective import perspective_record
from sovereign_world.observer.projection import project_day
from sovereign_world.observer.reader import RunReader
from sovereign_world.observer.run_export import (
    day_record,
    encode_json,
    manifest_record,
    people_bytes,
)
from sovereign_world.observer.runner_link import Runner
from sovereign_world.observer.terrain_export import TerrainBundle, terrain_bundle
from sovereign_world.state import build_initial_state

LOOKAHEAD_DAYS = 3
"""How many days a runner may save beyond the day the page shows."""
MAX_LOOKAHEAD = 30
POLL_SECONDS = 0.5
CACHE_DAYS = 64
"""Days whose bytes are kept, most recently used first."""


class NotReady(LookupError):
    """The day is saved but the walk has not reached it yet."""


@dataclass(frozen=True, slots=True)
class Served:
    """An answer's bytes and the history epoch they belong to."""

    epoch: int
    body: bytes


class NoRunner(LookupError):
    """The run is being followed, not run: there is no runner to hold."""


@dataclass(slots=True)
class Control:
    """What the page asked of the runner: paused or playing, how far ahead it may run, and
    the day the page shows. Only pause and resume ever reach the runner."""

    paused: bool = True
    lookahead: int = LOOKAHEAD_DAYS
    shown: int | None = None

    def record(self) -> dict[str, object]:
        return {"paused": self.paused, "lookahead": self.lookahead, "shown": self.shown}


@dataclass(slots=True)
class _Runner:
    link: Runner | None = None
    control: Control = field(default_factory=Control)


class RunService:
    def __init__(
        self,
        root: Path,
        *,
        poll_seconds: float = POLL_SECONDS,
        cache_days: int = CACHE_DAYS,
        chunk_tiles: int = 8,
        lookahead: int = LOOKAHEAD_DAYS,
    ) -> None:
        self.root = root
        self.poll_seconds = poll_seconds
        self.cache_days = cache_days
        self.chunk_tiles = chunk_tiles
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._runner = _Runner(control=Control(lookahead=lookahead))
        self._reader = RunReader(root)
        self._reset()

    def _reset(self) -> None:
        """Start the walk again (on opening, and after the history changed)."""
        self.epoch = self._reader.history_epoch
        self._numbers: dict[str, int] = {}
        self._ordered: list[str] = []
        self._walked: list[int] = []
        self._civilizations: tuple[str, ...] = ()
        self._cache: OrderedDict[int, tuple[bytes, bytes]] = OrderedDict()
        self._chronicles: OrderedDict[int, bytes] = OrderedDict()
        self._routes: OrderedDict[int, bytes] = OrderedDict()
        self._perspectives: OrderedDict[tuple[int, int], bytes] = OrderedDict()
        self._terrain: TerrainBundle | None = None
        # A new history: the page starts again, so the runner waits for it.
        self._runner.control.shown = None
        self._gate()

    # ------------------------------------------------------------ following the run
    def start(self) -> None:
        """Follow the run in the background until `stop`."""
        if self._thread is None:
            self._thread = threading.Thread(target=self._follow, name="run-follower", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None
        link = self._runner.link
        if link is not None:
            link.close()

    # ------------------------------------------------------------ the runner
    def attach(self, link: Runner) -> None:
        """Hold a runner from now on (it starts paused and stays so until the page plays)."""
        with self._lock:
            self._runner.link = link
            self._gate()

    def runner_changed(self) -> None:
        """The runner reported something (a saved day, a phase): look again and re-gate."""
        with self._lock:
            self.poll()
            self._gate()

    def _gate(self) -> None:
        """Let the runner go on only while the page plays and is close enough behind it."""
        link = self._runner.link
        if link is None:
            return
        control = self._runner.control
        latest = self._reader.days()[-1]
        may_run = (
            not control.paused
            and control.shown is not None
            and latest - control.shown < control.lookahead
        )
        if may_run:
            link.resume()
        else:
            link.pause()

    def set_control(
        self,
        *,
        paused: bool | None = None,
        lookahead: int | None = None,
        shown: int | None = None,
    ) -> Served:
        """The page plays or pauses, changes the lookahead, or shows another day."""
        with self._lock:
            if self._runner.link is None:
                raise NoRunner("this run is followed, not run, by this observer")
            if lookahead is not None and not 1 <= lookahead <= MAX_LOOKAHEAD:
                raise ValueError(f"lookahead must be 1 to {MAX_LOOKAHEAD} days")
            if shown is not None and shown not in self._reader.days():
                raise KeyError(f"day {shown} is not saved in this run")
            control = self._runner.control
            if paused is not None:
                control.paused = paused
            if lookahead is not None:
                control.lookahead = lookahead
            if shown is not None:
                control.shown = shown
            self._gate()
            return Served(self.epoch, encode_json(self._control_record()))

    def control(self) -> Served:
        with self._lock:
            if self._runner.link is None:
                raise NoRunner("this run is followed, not run, by this observer")
            return Served(self.epoch, encode_json(self._control_record()))

    def _control_record(self) -> dict[str, object]:
        link = self._runner.link
        return {
            "runner": None if link is None else link.state().record(),
            "control": self._runner.control.record() if link is not None else None,
        }

    def _follow(self) -> None:
        while not self._stop.is_set():
            try:
                progressed = self.step()
            except Exception:
                # A half-written or bad record: the reader stops before it; look again later.
                progressed = False
            if not progressed:
                self._stop.wait(self.poll_seconds)

    def poll(self) -> None:
        """Read what the run saved since the last look."""
        with self._lock:
            self._reader.refresh()
            if self._reader.history_epoch != self.epoch:
                self._reset()
                self._retire()

    def _retire(self) -> None:
        """A new history: stop the runner for good. Its world and its store's journal tail are
        the old history's, so its next day or checkpoint would corrupt the new one. It stays
        attached, so the page can say why it stopped."""
        link = self._runner.link
        if link is not None:
            link.terminate()

    def step(self) -> bool:
        """Look for new records, then walk one day; whether a day was walked."""
        with self._lock:
            self.poll()
            pending = self._pending()
            if not pending:
                return False
            self._walk(pending[0])
            self._gate()
            return True

    def walk_all(self) -> None:
        """Walk every saved day now (tests, and short runs)."""
        while self.step():
            pass

    def _pending(self) -> list[int]:
        walked = set(self._walked)
        return [day for day in self._reader.days() if day not in walked]

    def _walk(self, day: int) -> None:
        self._walked.append(day)
        self._remember(day, self._project(day))

    def _project(self, day: int) -> tuple[bytes, bytes]:
        view = project_day(self._reader.state_at(day))
        self._civilizations = self._civilizations or view.civilizations
        numbers = []
        for person_id in view.people.ids:
            number = self._numbers.get(person_id)
            if number is None:
                number = self._numbers[person_id] = len(self._ordered)
                self._ordered.append(person_id)
            numbers.append(number)
        record = day_record(view, state_hash=self._reader.recorded_hash(day))
        return encode_json(record), people_bytes(view, numbers)

    def _keep(self, cache: OrderedDict[Any, bytes], day: Any, body: bytes) -> None:
        cache[day] = body
        cache.move_to_end(day)
        while len(cache) > self.cache_days:
            cache.popitem(last=False)

    def _remember(self, day: int, files: tuple[bytes, bytes]) -> None:
        self._cache[day] = files
        self._cache.move_to_end(day)
        while len(self._cache) > self.cache_days:
            self._cache.popitem(last=False)

    # ------------------------------------------------------------ answers
    def status(self) -> Served:
        with self._lock:
            saved = self._reader.days()
            body = {
                "history_epoch": self.epoch,
                "saved_days": [saved[0], saved[-1]],
                "saved": len(saved),
                "ready": len(self._walked),
                "ready_through": self._walked[-1] if self._walked else None,
                "people": len(self._ordered),
                "following": self._thread is not None,
                **self._control_record(),
            }
            return Served(self.epoch, encode_json(body))

    def seal(self) -> Served:
        """Whether the run is sealed, and its seal's public parts, with whether its signature
        verifies by the key it names."""
        from sovereign_world.seal import Seal, SealRefused

        with self._lock:
            document = self._reader.seal_document()
            if document is None:
                return Served(self.epoch, encode_json({"sealed": False}))
            seal = Seal.model_validate(document)
            try:
                seal.verify_signature()
                valid = True
            except SealRefused:
                valid = False
            record = {
                "sealed": True,
                "fingerprint": seal.fingerprint,
                "signature_valid": valid,
                **seal.model_dump(mode="json"),
            }
            return Served(self.epoch, encode_json(record))

    def manifest(self) -> Served:
        with self._lock:
            reader = self._reader
            record = manifest_record(
                reader.manifest(),
                journal_format=reader.journal_format,
                history_epoch=self.epoch,
                civilizations=self._civilizations,
                days=tuple(self._walked),
                saved=reader.days(),
            )
            return Served(self.epoch, encode_json(record))

    def days(self) -> Served:
        with self._lock:
            return Served(self.epoch, encode_json(list(self._walked)))

    def ids(self, start: int = 0) -> Served:
        """The id table from a place on: what a client that holds `start` ids lacks."""
        if start < 0:
            raise ValueError("start must not be negative")
        with self._lock:
            return Served(self.epoch, encode_json(self._ordered[start:]))

    def _day_files(self, day: int) -> tuple[bytes, bytes]:
        if day not in self._reader.days():
            raise KeyError(f"day {day} is not saved in this run")
        if day not in self._walked:
            raise NotReady(f"day {day} is not ready yet")
        files = self._cache.get(day)
        if files is None:
            files = self._project(day)
        self._remember(day, files)
        return files

    def day(self, day: int) -> Served:
        with self._lock:
            return Served(self.epoch, self._day_files(day)[0])

    def people(self, day: int) -> Served:
        with self._lock:
            return Served(self.epoch, self._day_files(day)[1])

    def changes(self, day: int, start: int) -> Served:
        """What turns the record of day `start` into day `day`'s (see `changes`)."""
        with self._lock:
            after = json.loads(self._day_files(day)[0])
            before = json.loads(self._day_files(start)[0])
            return Served(self.epoch, encode_json(record_changes(before, after)))

    def routes(self, day: int) -> Served:
        """The day's parties on the road and their routes."""
        with self._lock:
            self._day_files(day)
            body = self._routes.get(day)
            if body is None:
                body = encode_json(routes(self._reader.state_at(day)))
            self._keep(self._routes, day, body)
            return Served(self.epoch, body)

    def perspective(self, day: int, civ: int) -> Served:
        """What civilization number `civ` knows on a saved day: its council report for that
        day, as `perspective_record` serializes it. This is the one place a report is built
        from a day's world; the record is made from the report alone."""
        with self._lock:
            self._day_files(day)
            if not 0 <= civ < len(self._civilizations):
                raise KeyError(f"no civilization number {civ} in this run")
            body = self._perspectives.get((day, civ))
            if body is None:
                state = self._reader.state_at(day)
                report = build_council_report(state, EntityId(self._civilizations[civ]))
                body = encode_json(perspective_record(report))
            self._keep(self._perspectives, (day, civ), body)
            return Served(self.epoch, body)

    def chronicle(self, day: int) -> Served:
        """A saved day's events, each placed where the record puts it (see `chronicle`)."""
        with self._lock:
            saved = self._reader.days()
            if day not in saved:
                raise KeyError(f"day {day} is not saved in this run")
            body = self._chronicles.get(day)
            if body is None:
                place = saved.index(day)
                before = self._reader.state_at(saved[place - 1]) if place > 0 else None
                today = self._reader.state_at(day)
                entries = chronicle_entries(self._reader.events_at(day), today, before)
                body = encode_json({"day": day, "events": entries})
            self._keep(self._chronicles, day, body)
            return Served(self.epoch, body)

    def terrain(self, path: str) -> Served:
        """One terrain file of the run's own world, as the terrain export writes it."""
        with self._lock:
            if self._terrain is None:
                manifest = self._reader.manifest()
                start = self._reader.state_at(self._reader.days()[0])
                expected = build_initial_state(manifest).world_map.content_hash()
                # A run whose land was edited (a scenario) is shown as it was recorded; any
                # other run's terrain is byte for byte the terrain export's.
                edited = start.world_map.content_hash() != expected
                self._terrain = terrain_bundle(
                    manifest, self.chunk_tiles, start.world_map if edited else None
                )
            body = self._terrain.files.get(path)
            if body is None:
                raise KeyError(f"no terrain file {path}")
            return Served(self.epoch, body)

    def describe(self) -> dict[str, Any]:
        """For the command line: what is being served."""
        with self._lock:
            manifest = self._reader.manifest()
            saved = self._reader.days()
            return {
                "run_id": str(manifest.run_id),
                "seed": manifest.config.seed,
                "size": f"{manifest.config.width}x{manifest.config.height}",
                "days": f"{saved[0]}..{saved[-1]}",
            }
