"""Journal format 2: snapshots, and the changes from one day to the next (Phase 5 S5).

A format-2 journal saves the whole world on its first day, every 30 days, and whenever the
day before is not at hand; on the days between it saves only what changed:

- outside the people, each top-level field of the world and of each civilization that
  changed (`set`) or went back to empty (`unset`);
- for each civilization's people, the number columns as a packed mask of the rows that
  changed and their differences, the ids no longer there, the full row of anyone whose
  other fields changed, and the people added, in order;
- the day's events, as before.

`StateCursor` rebuilds any day from the snapshot before it and the changes since, byte for
byte the same world. Everything is plain JSON, gzip-compressed without a timestamp, so the
same day always saves the same bytes.
"""

from __future__ import annotations

import gzip
import json
from base64 import b64decode, b64encode
from typing import Any

import numpy as np

from sovereign_world.people_store import (
    DICTS,
    FIELDS,
    HASH_COLUMNS,
    NUMBERS,
    TEXT_FIELDS,
    PeopleTable,
    PeopleView,
    PersonRecord,
)
from sovereign_world.state import OutsidePeople, WorldState, outside_people

SNAPSHOT_INTERVAL = 30
"""Days between whole-world snapshots."""
Parts = OutsidePeople
"""A world as JSON without its map and people: the world's fields, and each
civilization's."""
split_parts = outside_people


def _pack(raw: bytes) -> str:
    return b64encode(raw).decode()


def _unpack(text: str) -> bytes:
    return b64decode(text)


def compress(payload: object) -> str:
    # Keys keep their order: a saved world's maps keep the order they were filled in.
    encoded = json.dumps(payload, separators=(",", ":")).encode()
    return b64encode(gzip.compress(encoded, mtime=0)).decode()


def decompress(text: str) -> Any:
    return json.loads(gzip.decompress(b64decode(text)))


def _same(first: Any, second: Any) -> bool:
    """Equal and, for maps, in the same order: `==` alone ignores the order of keys, which
    a saved world keeps."""
    if first != second:
        return False
    if isinstance(first, dict | list) and first:
        return json.dumps(first, separators=(",", ":")) == json.dumps(second, separators=(",", ":"))
    return True


def _keys_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    changed = {
        key: value
        for key, value in after.items()
        if key not in before or not _same(before[key], value)
    }
    gone = sorted(key for key in before if key not in after)
    delta: dict[str, Any] = {}
    if changed:
        delta["set"] = changed
    if gone:
        delta["unset"] = gone
    return delta


def _apply_keys(target: dict[str, Any], delta: dict[str, Any]) -> None:
    for key in delta.get("unset", ()):
        target.pop(key, None)
    target.update(delta.get("set", {}))


def _numbers(table: PeopleTable, rows: np.ndarray) -> dict[str, np.ndarray]:
    return {
        "alive": table.alive[rows].astype(np.int64),
        **{name: table.nums[name][rows] for name in NUMBERS},
    }


def people_delta(before: PeopleTable, after: PeopleTable) -> dict[str, Any] | None:
    """What changed in a civilization's people, or None when their order cannot be kept
    (then a snapshot is saved instead)."""
    if (
        after.base is not None
        and before.base == after.base
        and before.version == 0
        and after.ids[: before.size] == before.ids
    ):
        return _related_delta(before, after)
    return _people_delta(before, after)


def _related_delta(before: PeopleTable, after: PeopleTable) -> dict[str, Any]:
    """`people_delta` for two copies of one table, the earlier untouched since: a person
    keeps their row, so rows are compared in place, and only rows marked as changed have
    their text and dicts compared. Gives exactly what `_people_delta` gives."""
    size = before.size
    was = before.present[:size]
    still = after.present[:size]
    before_rows = np.flatnonzero(was)
    staying = np.flatnonzero(was & still)
    gone = np.flatnonzero(was & ~still)
    added = size + np.flatnonzero(after.present[size : after.size])
    staying_mask = np.isin(before_rows, staying) if len(gone) else None
    numbers: dict[str, Any] = {}
    old = _numbers(before, staying)
    new = _numbers(after, staying)
    for name in HASH_COLUMNS:
        difference = new[name] - old[name]
        changed = difference != 0
        if not changed.any():
            continue
        if staying_mask is not None:
            full = np.zeros(len(before_rows), dtype=bool)
            full[staying_mask] = changed
            changed_mask = full
        else:
            changed_mask = changed
        numbers[name] = {
            "mask": _pack(np.packbits(changed_mask).tobytes()),
            "diff": _pack(difference[changed].astype("<i8").tobytes()),
        }
    rows: dict[str, Any] = {}
    before_objs, after_objs = before.objs, after.objs
    for row in staying[after.objs_dirty[staying]].tolist():
        for name in TEXT_FIELDS:
            first, second = before_objs[name][row], after_objs[name][row]
            if first is not second and (
                first != second or (isinstance(first, dict) and list(first) != list(second))
            ):
                rows[after.ids[row]] = row_json(after, row)
                break
    delta: dict[str, Any] = {}
    if numbers:
        delta["numbers"] = numbers
    if len(gone):
        delta["removed"] = [before.ids[row] for row in gone.tolist()]
    if rows:
        delta["rows"] = rows
    if len(added):
        delta["added"] = [row_json(after, row) for row in added.tolist()]
    return delta


def _people_delta(before: PeopleTable, after: PeopleTable) -> dict[str, Any] | None:
    """`people_delta` for any two tables."""
    before_rows = before.rows()
    before_ids = [before.ids[row] for row in before_rows.tolist()]
    after_rows = after.rows()
    after_ids = [after.ids[row] for row in after_rows.tolist()]
    staying = [person_id for person_id in before_ids if person_id in after.index]
    removed = [person_id for person_id in before_ids if person_id not in after.index]
    added = after_ids[len(staying) :]
    # The rebuilt order is: those staying, in their old order, then the newcomers.
    if after_ids[: len(staying)] != staying:
        return None
    old_rows = np.array([before.index[person_id] for person_id in staying], dtype=np.int64)
    new_rows = after_rows[: len(staying)]
    # Numbers are diffed over everyone there the day before; those leaving count as unchanged.
    staying_mask = np.isin(before_rows, old_rows) if removed else None
    numbers: dict[str, Any] = {}
    old = _numbers(before, old_rows)
    new = _numbers(after, new_rows)
    for name in HASH_COLUMNS:
        difference = new[name] - old[name]
        changed = difference != 0
        if not changed.any():
            continue
        if staying_mask is not None:
            full = np.zeros(len(before_rows), dtype=bool)
            full[staying_mask] = changed
            changed_mask = full
        else:
            changed_mask = changed
        numbers[name] = {
            "mask": _pack(np.packbits(changed_mask).tobytes()),
            "diff": _pack(difference[changed].astype("<i8").tobytes()),
        }
    rows: dict[str, Any] = {}
    before_objs, after_objs = before.objs, after.objs
    for person_id, old_row, new_row in zip(
        staying, old_rows.tolist(), new_rows.tolist(), strict=True
    ):
        for name in TEXT_FIELDS:
            first, second = before_objs[name][old_row], after_objs[name][new_row]
            if first is not second and (
                first != second or (isinstance(first, dict) and list(first) != list(second))
            ):
                rows[person_id] = row_json(after, new_row)
                break
    delta: dict[str, Any] = {}
    if numbers:
        delta["numbers"] = numbers
    if removed:
        delta["removed"] = removed
    if rows:
        delta["rows"] = rows
    if added:
        delta["added"] = [row_json(after, after.index[person_id]) for person_id in added]
    return delta


def row_json(table: PeopleTable, row: int) -> dict[str, Any]:
    """One person exactly as a saved world holds them."""
    copy = PeopleTable(1)
    copy.append(table.values(row))
    return next(iter(copy.dump("json").values()))


def apply_people_delta(table: PeopleTable, delta: dict[str, Any]) -> None:
    rows = table.rows()
    for name, change in delta.get("numbers", {}).items():
        mask = np.unpackbits(np.frombuffer(_unpack(change["mask"]), dtype=np.uint8))
        changed = rows[mask[: len(rows)].astype(bool)]
        difference = np.frombuffer(_unpack(change["diff"]), dtype="<i8")
        if name == "alive":
            table.alive[changed] = (table.alive[changed].astype(np.int64) + difference) != 0
            table.living_changed()
        else:
            table.nums[name][changed] += difference
    for person_id in delta.get("removed", ()):
        table.remove(person_id)
    for person_id, values in delta.get("rows", {}).items():
        table.overwrite(table.index[person_id], _values(values))
    for values in delta.get("added", ()):
        table.append(_values(values))


def _values(row: dict[str, Any]) -> dict[str, Any]:
    record = PersonRecord.model_validate(row)
    values = {name: getattr(record, name) for name in FIELDS}
    for name in DICTS:
        values[name] = dict(values[name])
    return values


class Saved:
    """A day as last saved: its JSON outside the people, and copies of its people's tables,
    kept apart from the live world so later changes to it cannot touch them."""

    __slots__ = ("day", "parts", "tables")

    def __init__(self, state: WorldState, parts: Parts | None = None) -> None:
        self.day = state.day
        self.parts = parts if parts is not None else split_parts(state)
        self.tables = {
            civilization_id: civilization.population.people.table.copy()
            for civilization_id, civilization in state.civilizations.items()
        }


def encode_delta(before: Saved, after: WorldState, after_parts: Parts) -> dict[str, Any] | None:
    """Everything that changed from one day to the next, or None if a snapshot is needed."""
    if set(before.tables) != set(after.civilizations):
        return None
    world_before, civilizations_before = before.parts
    world_after, civilizations_after = after_parts
    people: dict[str, Any] = {}
    for civilization_id in sorted(after.civilizations):
        change = people_delta(
            before.tables[civilization_id],
            after.civilizations[civilization_id].population.people.table,
        )
        if change is None:
            return None
        if change:
            people[civilization_id] = change
    civilizations = {
        civilization_id: change
        for civilization_id in sorted(civilizations_after)
        if (
            change := _keys_delta(
                civilizations_before[civilization_id], civilizations_after[civilization_id]
            )
        )
    }
    return {
        "from_day": before.day,
        "world": _keys_delta(world_before, world_after),
        "civilizations": civilizations,
        "people": people,
    }


class StateCursor:
    """Rebuilds the days of a format-2 journal, one record at a time."""

    def __init__(self) -> None:
        self.state: WorldState | None = None
        self._world: dict[str, Any] = {}
        self._civilizations: dict[str, dict[str, Any]] = {}
        self._tables: dict[str, PeopleTable] = {}

    def load_snapshot(self, state: WorldState) -> WorldState:
        self.state = state
        self._world, self._civilizations = split_parts(state)
        self._tables = {
            civilization_id: civilization.population.people.table.copy()
            for civilization_id, civilization in state.civilizations.items()
        }
        return state

    def apply_delta(self, delta: dict[str, Any]) -> WorldState:
        if self.state is None or self.state.day != delta["from_day"]:
            raise RuntimeError("a day's changes need the day before them")
        _apply_keys(self._world, delta["world"])
        for civilization_id, change in delta["civilizations"].items():
            _apply_keys(self._civilizations[civilization_id], change)
        for civilization_id, change in delta["people"].items():
            apply_people_delta(self._tables[civilization_id], change)
        self.state = self._rebuild()
        return self.state

    def _rebuild(self) -> WorldState:
        assert self.state is not None
        civilizations = {
            civilization_id: {
                **fields,
                "population": {
                    **fields["population"],
                    "people": PeopleView(self._tables[civilization_id].copy()),
                },
            }
            for civilization_id, fields in self._civilizations.items()
        }
        return WorldState.model_validate(
            {**self._world, "world_map": self.state.world_map, "civilizations": civilizations}
        )
