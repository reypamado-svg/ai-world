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
journal by byte offset, and `WorldStore` is never constructed.
"""

from __future__ import annotations

import json
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sovereign_world.observer.changes import record_changes, routes
from sovereign_world.observer.chronicle import chronicle_entries
from sovereign_world.observer.projection import project_day
from sovereign_world.observer.reader import RunReader
from sovereign_world.observer.run_export import (
    day_record,
    encode_json,
    manifest_record,
    people_bytes,
)
from sovereign_world.observer.terrain_export import TerrainBundle, terrain_bundle
from sovereign_world.state import build_initial_state

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


class RunService:
    def __init__(
        self,
        root: Path,
        *,
        poll_seconds: float = POLL_SECONDS,
        cache_days: int = CACHE_DAYS,
        chunk_tiles: int = 8,
    ) -> None:
        self.root = root
        self.poll_seconds = poll_seconds
        self.cache_days = cache_days
        self.chunk_tiles = chunk_tiles
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
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
        self._terrain: TerrainBundle | None = None

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

    def step(self) -> bool:
        """Look for new records, then walk one day; whether a day was walked."""
        with self._lock:
            self.poll()
            pending = self._pending()
            if not pending:
                return False
            self._walk(pending[0])
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
        return encode_json(day_record(view)), people_bytes(view, numbers)

    def _keep(self, cache: OrderedDict[int, bytes], day: int, body: bytes) -> None:
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
            }
            return Served(self.epoch, encode_json(body))

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
