"""What the observer shows of one recorded day (O2).

`project_day` turns a `WorldState` into the plain data the observer draws: each settlement
with its houses, house work, institutions and residents; every living person as a row of
columns (where they live, who they are, what duty they hold); and who owns which tile. It
reads the state and changes nothing.

Where a person lives is the engine's own rule (`housing.residents_by_settlement`): their
people at home and on its fields, and the captives held there. Anyone counted nowhere (on
the road, or a captive on the march) is "away" and is drawn at their tile.

Duties are what the council's orders and buildings ask of each person
(`commands._duties`), sorted into the observer's nine kinds of day; everyone else is a
child, an elder or, when grown, a farmer. These are what the engine records; how and where a
duty is done in the streets is presentation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from sovereign_world.commands import _duties
from sovereign_world.housing import GRADE_ORDER, HOUSEHOLD, residents_by_settlement
from sovereign_world.ids import EntityId
from sovereign_world.institutions import InstitutionKind
from sovereign_world.people_store import Sex
from sovereign_world.ranks import settlement_rank
from sovereign_world.state import WorldState
from sovereign_world.work import WorkKind

DUTIES: tuple[str, ...] = (
    "child",
    "elder",
    "farmer",
    "carrier",
    "builder",
    "woodcutter",
    "crafter",
    "scholar",
    "guard",
)
"""The observer's kinds of day, in the order of `observer/src/data/population.js`."""
DUTY = {name: index for index, name in enumerate(DUTIES)}
AWAY = 0xFFFF
"""The settlement index of someone counted at no settlement."""
GROWN_YEARS = 16
ELDER_YEARS = 65

_WORK_DUTY = {
    WorkKind.FARM: "farmer",
    WorkKind.HAUL: "carrier",
    WorkKind.GATHER: "woodcutter",
    WorkKind.CRAFT: "crafter",
    WorkKind.CARE: "crafter",
    WorkKind.CONSTRUCT: "builder",
}
_STAFF_DUTY = {
    InstitutionKind.ARCHIVE: "scholar",
    InstitutionKind.SCHOOL: "scholar",
    InstitutionKind.DIPLOMATIC_SERVICE: "scholar",
    InstitutionKind.HALL: "scholar",
    InstitutionKind.HEALERS_HOUSE: "crafter",
    InstitutionKind.WORKSHOP: "crafter",
    InstitutionKind.ARMOURY: "crafter",
    InstitutionKind.TRAINING_GROUNDS: "guard",
}
_LABEL_DUTY = {
    "scholar": "scholar",
    "teacher": "scholar",
    "apprentice": "scholar",
    "drill": "guard",
    "garrison": "guard",
    "craft": "crafter",
    "builder": "builder",
}


@dataclass(frozen=True)
class SettlementRow:
    settlement_id: str
    civilization: int
    q: int
    r: int
    capital: bool
    founded_day: int
    rank: str
    houses: dict[str, int]
    """Houses by grade (hut, house, stone_house); empty under rules 1."""
    slots: int
    residents: int
    house_jobs: tuple[dict[str, int | str], ...]
    institutions: tuple[dict[str, int | str], ...]


@dataclass
class PeopleColumns:
    """Every living person, one row each, grouped by civilization in table order."""

    ids: list[str] = field(default_factory=list)
    civilization: np.ndarray = field(default_factory=lambda: np.zeros(0, np.uint8))
    settlement: np.ndarray = field(default_factory=lambda: np.zeros(0, np.uint16))
    sex: np.ndarray = field(default_factory=lambda: np.zeros(0, np.uint8))
    """1 for a woman."""
    age: np.ndarray = field(default_factory=lambda: np.zeros(0, np.uint8))
    """Years, at most 255."""
    health: np.ndarray = field(default_factory=lambda: np.zeros(0, np.uint8))
    """Percent."""
    duty: np.ndarray = field(default_factory=lambda: np.zeros(0, np.uint8))
    q: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int16))
    r: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int16))

    def __len__(self) -> int:
        return len(self.ids)


@dataclass(frozen=True)
class DayProjection:
    day: int
    civilizations: tuple[str, ...]
    settlements: tuple[SettlementRow, ...]
    people: PeopleColumns
    travellers: tuple[tuple[int, int, int, int], ...]
    """(q, r, civilization, count): the people counted at no settlement, by tile."""
    owners: tuple[tuple[int, int, int], ...]
    """(q, r, civilization) for every owned tile."""

    def counts(self) -> dict[str, int]:
        away = int(np.count_nonzero(self.people.settlement == AWAY))
        return {"living": len(self.people), "away": away, "at_home": len(self.people) - away}


def _duty_of(label: str | None, kinds: dict[str, WorkKind], staff: dict[str, str]) -> str | None:
    if label is None:
        return None
    kind, _, key = label.partition(":")
    if kind == "work":
        work = kinds.get(key)
        return _WORK_DUTY.get(work) if work is not None else None
    if kind == "staff":
        return staff.get(key)
    return _LABEL_DUTY.get(kind)


def project_day(state: WorldState) -> DayProjection:
    """The observer's view of one day; reads the state and changes nothing."""
    civilizations = tuple(sorted(state.civilizations))
    civ_index = {civilization_id: index for index, civilization_id in enumerate(civilizations)}
    settlements: list[SettlementRow] = []
    home: dict[EntityId, int] = {}
    for civilization_id in civilizations:
        civilization = state.civilizations[civilization_id]
        residents = residents_by_settlement(state, civilization_id)
        for item in civilization.settlements:
            index = len(settlements)
            here = residents.get(item.settlement_id, [])
            for person_id in here:
                home[person_id] = index
            housing = civilization.housing.get(item.settlement_id)
            houses = (
                {
                    grade.value: housing.houses[grade]
                    for grade in GRADE_ORDER
                    if grade in housing.houses
                }
                if housing is not None
                else {}
            )
            settlements.append(
                SettlementRow(
                    settlement_id=str(item.settlement_id),
                    civilization=civ_index[civilization_id],
                    q=item.tile.q,
                    r=item.tile.r,
                    capital=item.capital,
                    founded_day=item.founded_day,
                    rank=settlement_rank(civilization, item.settlement_id).value,
                    houses=houses,
                    slots=sum(houses.values()) * HOUSEHOLD,
                    residents=len(here),
                    house_jobs=tuple(
                        {
                            "grade": job.grade.value,
                            "count": job.count,
                            "built": job.built(),
                            "workers": len(job.worker_ids),
                        }
                        for job in civilization.house_jobs
                        if job.settlement_id == item.settlement_id
                    ),
                    institutions=tuple(
                        {"kind": institution.kind.value, "staff": len(institution.staff_ids)}
                        for institution in civilization.institutions
                        if institution.settlement_id == item.settlement_id
                    ),
                )
            )
    people = PeopleColumns()
    parts: dict[str, list[np.ndarray]] = {
        name: []
        for name in ("civilization", "settlement", "sex", "age", "health", "duty", "q", "r")
    }
    for civilization_id in civilizations:
        civilization = state.civilizations[civilization_id]
        table = civilization.population.people.table
        rows = table.living_rows()
        ids = [table.ids[row] for row in rows.tolist()]
        n = len(ids)
        people.ids.extend(str(person_id) for person_id in ids)
        kinds = {str(order.order_id): order.kind for order in civilization.work_orders}
        staff = {
            str(institution.institution_id): _STAFF_DUTY[institution.kind]
            for institution in civilization.institutions
        }
        labels = _duties(state, civilization_id)
        years = table.nums["age_days"][rows] // 365
        duty = np.empty(n, np.uint8)
        for k, person_id in enumerate(ids):
            name = _duty_of(labels.get(person_id), kinds, staff)
            if name is None:
                age = int(years[k])
                name = "child" if age < GROWN_YEARS else "elder" if age >= ELDER_YEARS else "farmer"
            duty[k] = DUTY[name]
        places = table.objs["location"]
        sexes = table.objs["sex"]
        parts["civilization"].append(np.full(n, civ_index[civilization_id], np.uint8))
        parts["settlement"].append(np.array([home.get(p, AWAY) for p in ids], np.uint16))
        parts["sex"].append(np.array([sexes[row] == Sex.FEMALE for row in rows.tolist()], np.uint8))
        parts["age"].append(np.minimum(years, 255).astype(np.uint8))
        parts["health"].append((table.nums["health_bp"][rows] // 100).astype(np.uint8))
        parts["duty"].append(duty)
        parts["q"].append(np.array([places[row].q for row in rows.tolist()], np.int16))
        parts["r"].append(np.array([places[row].r for row in rows.tolist()], np.int16))
    for name, chunks in parts.items():
        dtype = getattr(people, name).dtype
        setattr(
            people, name, np.concatenate(chunks).astype(dtype) if chunks else getattr(people, name)
        )
    away = people.settlement == AWAY
    groups: dict[tuple[int, int, int], int] = {}
    for q, r, civ in zip(
        people.q[away].tolist(),
        people.r[away].tolist(),
        people.civilization[away].tolist(),
        strict=True,
    ):
        groups[(q, r, civ)] = groups.get((q, r, civ), 0) + 1
    travellers = tuple((q, r, civ, n) for (q, r, civ), n in sorted(groups.items()))
    owners = tuple(
        sorted(
            (owner.tile.q, owner.tile.r, civ_index[owner.civilization_id])
            for owner in state.territory.owners
            if owner.civilization_id in civ_index
        )
    )
    return DayProjection(
        day=state.day,
        civilizations=tuple(str(c) for c in civilizations),
        settlements=tuple(settlements),
        people=people,
        travellers=travellers,
        owners=owners,
    )
