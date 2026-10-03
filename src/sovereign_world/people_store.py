"""People kept in columns (Phase 5 S4): every person real, a few dozen bytes each.

A civilization's people live in one `PeopleTable`: a column per field, numbers in numpy
arrays and everything else (ids, places, family, skills) in plain lists, with a row per
person. `Person` is a row of it: two slots (table, row) and a typed attribute per field,
so code reads and writes `people[person_id].health_bp` exactly as before, and an
assignment is checked as the old model checked it. `PeopleView` is the mapping from id to
person that a `Population` holds.

Copying a table copies its columns (milliseconds for 100,000 people), not 100,000
objects. Skills, languages and held skills are dicts shared between a table and its copy
until a row's are first read, when that row gets its own. Writes mark their block of
1,024 rows dirty, for saving only what changed (S5).

Saves are exactly as before: a people mapping dumps to the same JSON, and loads from it.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, MutableMapping
from enum import StrEnum
from typing import TYPE_CHECKING, Any, overload

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, GetCoreSchemaHandler, TypeAdapter
from pydantic_core import core_schema

from sovereign_world.hexmap import HexCoord
from sovereign_world.ids import EntityId

if TYPE_CHECKING:
    from typing import Self

BLOCK = 1_024
"""Rows per dirty block."""


class AllegianceChange(BaseModel):
    """The day a person became a member of another civilization, and why."""

    model_config = ConfigDict(frozen=True)

    day: int = Field(ge=0)
    from_civilization_id: EntityId
    to_civilization_id: EntityId
    reason: str


class Sex(StrEnum):
    FEMALE = "female"
    MALE = "male"


class PersonRecord(BaseModel):
    """One person as plain data: what saves hold, and how a new person is checked."""

    model_config = ConfigDict(validate_assignment=True)

    person_id: EntityId
    civilization_id: EntityId
    sex: Sex
    birth_day: int
    age_days: int = Field(ge=0)
    location: HexCoord
    parent_ids: tuple[EntityId, ...] = ()
    health_bp: int = Field(default=10_000, ge=0, le=10_000)
    nutrition_debt: int = Field(default=0, ge=0)
    disease_load: int = Field(default=0, ge=0, le=10_000)
    skills: dict[str, int] = Field(default_factory=dict)
    alive: bool = True
    death_day: int | None = None
    captive_of: EntityId | None = None
    """The civilization holding this person prisoner; they keep their own allegiance."""
    held_at: EntityId | None = None
    """The captor's settlement holding them; none while they march with a war party."""
    allegiances: tuple[AllegianceChange, ...] = ()
    """Every change of civilization in this person's life, oldest first."""
    native_language: EntityId | None = None
    """The language this person grew up with; none means that of their civilization."""
    languages: dict[EntityId, int] = Field(default_factory=dict)
    """Fluency, 0 to 100, in each language learned besides their native one."""
    culture: EntityId | None = None
    """The culture this person lives by; none means that of their civilization."""
    assimilation: int = Field(default=0, ge=0, le=100)
    """How far, out of 100, a newcomer has become one of their civilization's people."""
    ancestry: tuple[EntityId, ...] = ()
    """The cultures of this person's forebears; none means their native language's alone."""
    held_skills: dict[str, int] = Field(default_factory=dict)
    """Skill held back while a newcomer settles in; restored on `settled_day`."""
    settled_day: int | None = None


FIELDS: tuple[str, ...] = tuple(PersonRecord.model_fields)
NUMBERS: dict[str, tuple[int | None, int | None]] = {
    "birth_day": (None, None),
    "age_days": (0, None),
    "health_bp": (0, 10_000),
    "nutrition_debt": (0, None),
    "disease_load": (0, 10_000),
    "assimilation": (0, 100),
}
"""Integer columns and their bounds."""
DICTS = ("skills", "languages", "held_skills")
OBJECTS = tuple(
    name for name in FIELDS if name not in NUMBERS and name not in DICTS and name != "alive"
)
TUPLES = frozenset({"parent_ids", "allegiances", "ancestry"})
OPTIONAL_INTS = frozenset({"death_day", "settled_day"})
OPTIONAL_IDS = frozenset({"captive_of", "held_at", "native_language", "culture"})


class PeopleTable:
    """The columns of one civilization's people."""

    __slots__ = (
        "alive",
        "capacity",
        "dirty",
        "ids",
        "index",
        "nums",
        "objs",
        "present",
        "shared",
        "size",
    )

    def __init__(self, capacity: int = 16) -> None:
        capacity = max(capacity, 16)
        self.size = 0
        self.capacity = capacity
        self.ids: list[EntityId] = []
        self.index: dict[EntityId, int] = {}
        self.present = np.zeros(capacity, dtype=bool)
        self.alive = np.zeros(capacity, dtype=bool)
        self.shared = np.zeros(capacity, dtype=bool)
        self.dirty = np.zeros(capacity // BLOCK + 1, dtype=bool)
        self.nums: dict[str, np.ndarray] = {
            name: np.zeros(capacity, dtype=np.int64) for name in NUMBERS
        }
        self.objs: dict[str, list[Any]] = {name: [] for name in (*OBJECTS, *DICTS)}

    # Growth and bookkeeping.

    def _grow(self) -> None:
        capacity = self.capacity * 2
        for name, column in self.nums.items():
            grown = np.zeros(capacity, dtype=np.int64)
            grown[: self.capacity] = column
            self.nums[name] = grown
        for attribute in ("present", "alive", "shared"):
            old = getattr(self, attribute)
            grown_flags = np.zeros(capacity, dtype=bool)
            grown_flags[: self.capacity] = old
            setattr(self, attribute, grown_flags)
        dirty = np.zeros(capacity // BLOCK + 1, dtype=bool)
        dirty[: len(self.dirty)] = self.dirty
        self.dirty = dirty
        self.capacity = capacity

    def mark(self, row: int) -> None:
        self.dirty[row // BLOCK] = True

    def unshare(self, row: int) -> None:
        """Give a row its own skill, language and held-skill dicts."""
        if self.shared[row]:
            for name in DICTS:
                column = self.objs[name]
                column[row] = dict(column[row])
            self.shared[row] = False
            self.mark(row)

    def dirty_blocks(self) -> tuple[int, ...]:
        return tuple(int(block) for block in np.flatnonzero(self.dirty))

    def clear_dirty(self) -> None:
        self.dirty[:] = False

    # Rows.

    def append(self, values: Mapping[str, Any]) -> int:
        """Add a person at the end, from already-checked values; return its row."""
        if self.size == self.capacity:
            self._grow()
        row = self.size
        person_id = values["person_id"]
        self.ids.append(person_id)
        self.index[person_id] = row
        self.present[row] = True
        self.alive[row] = bool(values["alive"])
        self.shared[row] = False
        for name in NUMBERS:
            self.nums[name][row] = values[name]
        for name in OBJECTS:
            self.objs[name].append(values[name])
        for name in DICTS:
            self.objs[name].append(dict(values[name]))
        self.size += 1
        self.mark(row)
        return row

    def overwrite(self, row: int, values: Mapping[str, Any]) -> None:
        self.alive[row] = bool(values["alive"])
        self.shared[row] = False
        for name in NUMBERS:
            self.nums[name][row] = values[name]
        for name in OBJECTS:
            self.objs[name][row] = values[name]
        for name in DICTS:
            self.objs[name][row] = dict(values[name])
        self.mark(row)

    def values(self, row: int) -> dict[str, Any]:
        """A row's field values in field order, as Python values (dicts not copied)."""
        out: dict[str, Any] = {}
        for name in FIELDS:
            if name == "alive":
                out[name] = bool(self.alive[row])
            elif name in NUMBERS:
                out[name] = int(self.nums[name][row])
            else:
                out[name] = self.objs[name][row]
        return out

    def remove(self, person_id: EntityId) -> int:
        """Take a person out; its row stays, unused, until the table is saved and loaded."""
        row = self.index.pop(person_id)
        self.present[row] = False
        self.mark(row)
        return row

    def rows(self) -> np.ndarray:
        return np.flatnonzero(self.present[: self.size])

    def copy(self) -> PeopleTable:
        """An independent copy. Number columns are copied; the rest are shared until
        written, and dicts until read."""
        other = PeopleTable.__new__(PeopleTable)
        other.size = self.size
        other.capacity = self.capacity
        other.ids = list(self.ids)
        other.index = dict(self.index)
        other.present = self.present.copy()
        other.alive = self.alive.copy()
        self.shared[: self.size] = True
        other.shared = self.shared.copy()
        other.dirty = np.zeros_like(self.dirty)
        other.nums = {name: column.copy() for name, column in self.nums.items()}
        other.objs = {name: list(column) for name, column in self.objs.items()}
        return other

    # Saving.

    def dump(self, mode: str) -> dict[str, dict[str, Any]]:
        """Every person, by id in row order, as the old model dumped them."""
        json = mode == "json"
        nums = {name: column[: self.size].tolist() for name, column in self.nums.items()}
        alive = self.alive[: self.size].tolist()
        objs = self.objs
        out: dict[str, dict[str, Any]] = {}
        for row in self.rows().tolist():
            person: dict[str, Any] = {}
            for name in FIELDS:
                if name == "alive":
                    person[name] = alive[row]
                elif name in nums:
                    person[name] = nums[name][row]
                else:
                    value = objs[name][row]
                    if name == "location":
                        value = {"q": value.q, "r": value.r}
                    elif name == "sex":
                        value = value.value if json else value
                    elif name == "allegiances":
                        value = [item.model_dump(mode=mode) for item in value]
                        value = value if json else tuple(value)
                    elif name in TUPLES:
                        value = list(value) if json else value
                    elif name in DICTS:
                        value = dict(value)
                    person[name] = value
            out[self.ids[row]] = person
        return out


_RECORDS: TypeAdapter[dict[EntityId, PersonRecord]] = TypeAdapter(dict[EntityId, PersonRecord])


def _checked(record: PersonRecord) -> dict[str, Any]:
    return {name: getattr(record, name) for name in FIELDS}


class _Column[T]:
    __slots__ = ("name",)

    def __set_name__(self, owner: type, name: str) -> None:
        self.name = name

    @overload
    def __get__(self, obj: None, owner: type | None = None) -> Self: ...
    @overload
    def __get__(self, obj: Person, owner: type | None = None) -> T: ...
    def __get__(self, obj: Person | None, owner: type | None = None) -> T | Self:
        if obj is None:
            return self
        return self.read(obj._table, obj._row)

    def __set__(self, obj: Person, value: T) -> None:
        table = obj._table
        self.write(table, obj._row, value)
        table.mark(obj._row)

    def read(self, table: PeopleTable, row: int) -> T:
        raise NotImplementedError

    def write(self, table: PeopleTable, row: int, value: T) -> None:
        raise NotImplementedError


class _Int(_Column[int]):
    __slots__ = ()

    def read(self, table: PeopleTable, row: int) -> int:
        return int(table.nums[self.name][row])

    def write(self, table: PeopleTable, row: int, value: int) -> None:
        if isinstance(value, bool) or not isinstance(value, int | np.integer):
            raise ValueError(f"{self.name} must be a whole number, not {value!r}")
        low, high = NUMBERS[self.name]
        if (low is not None and value < low) or (high is not None and value > high):
            raise ValueError(f"{self.name} must lie within {low} and {high}, not {value}")
        table.nums[self.name][row] = value


class _Alive(_Column[bool]):
    __slots__ = ()

    def read(self, table: PeopleTable, row: int) -> bool:
        return bool(table.alive[row])

    def write(self, table: PeopleTable, row: int, value: bool) -> None:
        if not isinstance(value, bool):
            raise ValueError(f"alive must be true or false, not {value!r}")
        table.alive[row] = value


class _Object[T](_Column[T]):
    __slots__ = ()

    def read(self, table: PeopleTable, row: int) -> T:
        value: T = table.objs[self.name][row]
        return value

    def write(self, table: PeopleTable, row: int, value: T) -> None:
        table.objs[self.name][row] = _coerce(self.name, value)


class _Dict[T](_Column[T]):
    __slots__ = ()

    def read(self, table: PeopleTable, row: int) -> T:
        if table.shared[row]:
            table.unshare(row)
        value: T = table.objs[self.name][row]
        return value

    def write(self, table: PeopleTable, row: int, value: T) -> None:
        if table.shared[row]:
            table.unshare(row)
        if not isinstance(value, Mapping):
            raise ValueError(f"{self.name} must be a mapping, not {value!r}")
        table.objs[self.name][row] = {key: int(amount) for key, amount in value.items()}


def _coerce(name: str, value: Any) -> Any:
    """Check and convert a value written to a person, as the old model did."""
    if name == "sex":
        return Sex(value)
    if name == "location":
        if not isinstance(value, HexCoord):
            raise ValueError(f"location must be a HexCoord, not {value!r}")
        return value
    if name in TUPLES:
        if name == "allegiances":
            return tuple(
                item
                if isinstance(item, AllegianceChange)
                else AllegianceChange.model_validate(item)
                for item in value
            )
        return tuple(value)
    if name in OPTIONAL_INTS:
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
            raise ValueError(f"{name} must be a whole number or none, not {value!r}")
        return value
    if value is None and name in OPTIONAL_IDS:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an id, not {value!r}")
    return value


class Person:
    """One person: a row of their civilization's table, read and written like a model."""

    __slots__ = ("_row", "_table")
    __hash__ = None  # type: ignore[assignment]

    person_id: _Object[EntityId] = _Object()
    civilization_id: _Object[EntityId] = _Object()
    sex: _Object[Sex] = _Object()
    birth_day = _Int()
    age_days = _Int()
    location: _Object[HexCoord] = _Object()
    parent_ids: _Object[tuple[EntityId, ...]] = _Object()
    health_bp = _Int()
    nutrition_debt = _Int()
    disease_load = _Int()
    skills: _Dict[dict[str, int]] = _Dict()
    alive = _Alive()
    death_day: _Object[int | None] = _Object()
    captive_of: _Object[EntityId | None] = _Object()
    held_at: _Object[EntityId | None] = _Object()
    allegiances: _Object[tuple[AllegianceChange, ...]] = _Object()
    native_language: _Object[EntityId | None] = _Object()
    languages: _Dict[dict[EntityId, int]] = _Dict()
    culture: _Object[EntityId | None] = _Object()
    assimilation = _Int()
    ancestry: _Object[tuple[EntityId, ...]] = _Object()
    held_skills: _Dict[dict[str, int]] = _Dict()
    settled_day: _Object[int | None] = _Object()

    def __init__(self, **values: Any) -> None:
        """A new person on their own, checked like the old model."""
        table = PeopleTable(1)
        row = table.append(_checked(PersonRecord(**values)))
        self._table = table
        self._row = row

    @classmethod
    def _at(cls, table: PeopleTable, row: int) -> Person:
        person = cls.__new__(cls)
        person._table = table
        person._row = row
        return person

    def _values(self) -> dict[str, Any]:
        if self._table.shared[self._row]:
            self._table.unshare(self._row)
        return self._table.values(self._row)

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Person:
        """A detached copy, with some fields replaced (unchecked, like the old model)."""
        values = self._values()
        if update:
            values.update(update)
        table = PeopleTable(1)
        row = table.append(values)
        return Person._at(table, row)

    def model_dump(self, *, mode: str = "python") -> dict[str, Any]:
        table = PeopleTable(1)
        table.append(self._values())
        return next(iter(table.dump(mode).values()))

    def to_record(self) -> PersonRecord:
        return PersonRecord.model_validate(self.model_dump(mode="json"))

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Person):
            return self._values() == other._values()
        if isinstance(other, PersonRecord):
            return self._values() == _checked(other)
        return NotImplemented

    def __repr__(self) -> str:
        fields = ", ".join(f"{name}={value!r}" for name, value in self._values().items())
        return f"Person({fields})"


class PeopleView(MutableMapping[EntityId, Person]):
    """A civilization's people by id, kept in a table: a mapping like the old dict."""

    __slots__ = ("_table",)

    def __init__(self, table: PeopleTable | None = None) -> None:
        self._table = table if table is not None else PeopleTable()

    @classmethod
    def of(cls, people: Mapping[EntityId, Person | PersonRecord] | Iterable[Person]) -> PeopleView:
        """A new table holding copies of these people."""
        items: Iterable[tuple[EntityId, Person | PersonRecord]]
        if isinstance(people, Mapping):
            items = people.items()
        else:
            items = ((person.person_id, person) for person in people)
        view = cls()
        for person_id, person in items:
            view._put(person_id, person)
        return view

    @property
    def table(self) -> PeopleTable:
        return self._table

    def __getitem__(self, person_id: EntityId) -> Person:
        return Person._at(self._table, self._table.index[person_id])

    def get(self, person_id: EntityId, default: Person | None = None) -> Person | None:  # type: ignore[override]
        row = self._table.index.get(person_id)
        return default if row is None else Person._at(self._table, row)

    def __contains__(self, person_id: object) -> bool:
        return person_id in self._table.index

    def __len__(self) -> int:
        return len(self._table.index)

    def __iter__(self) -> Iterator[EntityId]:
        ids = self._table.ids
        return iter([ids[row] for row in self._table.rows().tolist()])

    def __setitem__(self, person_id: EntityId, person: Person) -> None:
        self._put(person_id, person)

    def _put(self, person_id: EntityId, person: Person | PersonRecord) -> None:
        table = self._table
        if isinstance(person, Person):
            if person._table is table and table.index.get(person_id) == person._row:
                return
            values = person._values()
        elif isinstance(person, PersonRecord):
            values = _checked(person)
        else:
            raise TypeError(f"a person, not {type(person).__name__}")
        if values["person_id"] != person_id:
            values = {**values, "person_id": person_id}
        row = table.index.get(person_id)
        if row is None:
            table.append(values)
        else:
            table.overwrite(row, values)

    def __delitem__(self, person_id: EntityId) -> None:
        self._table.remove(person_id)

    def pop(self, person_id: EntityId, *default: Any) -> Any:
        if person_id not in self._table.index:
            if default:
                return default[0]
            raise KeyError(person_id)
        person = self[person_id].model_copy()
        self._table.remove(person_id)
        return person

    def items(self) -> Iterator[tuple[EntityId, Person]]:  # type: ignore[override]
        table = self._table
        ids = table.ids
        return iter([(ids[row], Person._at(table, row)) for row in table.rows().tolist()])

    def values(self) -> Iterator[Person]:  # type: ignore[override]
        table = self._table
        return iter([Person._at(table, row) for row in table.rows().tolist()])

    def living_ids(self) -> tuple[EntityId, ...]:
        table = self._table
        rows = np.flatnonzero(table.present[: table.size] & table.alive[: table.size])
        ids = table.ids
        return tuple(sorted(ids[row] for row in rows.tolist()))

    def dead_ids(self) -> tuple[EntityId, ...]:
        table = self._table
        rows = np.flatnonzero(table.present[: table.size] & ~table.alive[: table.size])
        ids = table.ids
        return tuple(sorted(ids[row] for row in rows.tolist()))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Mapping):
            return NotImplemented
        if list(self) != list(other):
            return False
        return all(self[key] == other[key] for key in self)

    def __repr__(self) -> str:
        return f"PeopleView({len(self)} people)"

    def __copy__(self) -> PeopleView:
        return PeopleView(self._table.copy())

    def __deepcopy__(self, memo: dict[int, object]) -> PeopleView:
        return PeopleView(self._table.copy())

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source: type[Any], handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        def validate(value: Any) -> PeopleView:
            if isinstance(value, PeopleView):
                return value
            if isinstance(value, Mapping) and all(
                isinstance(person, Person) for person in value.values()
            ):
                return cls.of(value)
            records = _RECORDS.validate_python(value)
            view = cls()
            for person_id, record in records.items():
                view._table.append({**_checked(record), "person_id": person_id})
            view._table.clear_dirty()
            return view

        def dump(value: PeopleView, info: core_schema.SerializationInfo) -> Any:
            return value._table.dump(info.mode)

        return core_schema.no_info_plain_validator_function(
            validate,
            serialization=core_schema.plain_serializer_function_ser_schema(dump, info_arg=True),
        )
