"""The people store behaves like a dict of the old person model, op for op."""

from copy import deepcopy

from hypothesis import given, settings
from hypothesis import strategies as st

from sovereign_world.hexmap import HexCoord
from sovereign_world.ids import EntityId
from sovereign_world.people_store import PeopleView, Person, PersonRecord, people_hash

IDS = [EntityId(f"person:{number:010d}") for number in range(12)]


def _record(person_id: EntityId, seed: int) -> PersonRecord:
    return PersonRecord(
        person_id=person_id,
        civilization_id=EntityId("civilization:0000000001"),
        sex="female" if seed % 2 else "male",
        birth_day=-seed * 37,
        age_days=seed * 37,
        location=HexCoord(seed % 5, seed % 7),
        parent_ids=(IDS[0],) if seed % 3 else (),
        health_bp=9_000 + seed,
        skills={"survival": seed},
        languages={EntityId("civilization:0000000002"): seed % 100} if seed % 4 else {},
    )


operation = st.one_of(
    st.tuples(st.just("insert"), st.sampled_from(IDS), st.integers(0, 200)),
    st.tuples(st.just("pop"), st.sampled_from(IDS), st.just(0)),
    st.tuples(st.just("health"), st.sampled_from(IDS), st.integers(0, 10_000)),
    st.tuples(st.just("move"), st.sampled_from(IDS), st.integers(-5, 5)),
    st.tuples(st.just("die"), st.sampled_from(IDS), st.integers(0, 400)),
    st.tuples(st.just("skill"), st.sampled_from(IDS), st.integers(0, 100)),
    st.tuples(st.just("skills"), st.sampled_from(IDS), st.integers(0, 100)),
    st.tuples(st.just("copy"), st.just(IDS[0]), st.just(0)),
    st.tuples(st.just("detach"), st.sampled_from(IDS), st.integers(0, 100)),
)


def _apply(view: PeopleView, oracle: dict[EntityId, PersonRecord], op) -> None:
    kind, person_id, value = op
    if kind == "insert":
        record = _record(person_id, value)
        view[person_id] = Person(**record.model_dump())
        oracle[person_id] = record
        return
    if person_id not in oracle:
        assert person_id not in view
        return
    person, record = view[person_id], oracle[person_id]
    if kind == "pop":
        popped = view.pop(person_id)
        assert popped == oracle.pop(person_id)
    elif kind == "health":
        person.health_bp = value
        record.health_bp = value
    elif kind == "move":
        person.location = HexCoord(value, -value)
        record.location = HexCoord(value, -value)
    elif kind == "die":
        person.alive = False
        person.death_day = value
        record.alive = False
        record.death_day = value
    elif kind == "skill":
        person.skills["craft"] = value
        record.skills["craft"] = value
    elif kind == "skills":
        person.skills = {**person.skills, "lore": value}
        record.skills = {**record.skills, "lore": value}
    elif kind == "detach":
        copied = person.model_copy(update={"nutrition_debt": value})
        view[person_id] = copied
        oracle[person_id] = record.model_copy(update={"nutrition_debt": value}, deep=True)


def _expected(oracle: dict[EntityId, PersonRecord], mode: str):
    return {key: record.model_dump(mode=mode) for key, record in oracle.items()}


@settings(max_examples=150, deadline=None)
@given(st.lists(operation, max_size=40))
def test_the_store_matches_a_dict_of_records(ops) -> None:
    view = PeopleView()
    oracle: dict[EntityId, PersonRecord] = {}
    snapshots: list[tuple[PeopleView, dict]] = []
    for op in ops:
        if op[0] == "copy":
            snapshots.append((deepcopy(view), _expected(oracle, "json")))
            view = deepcopy(view)
            continue
        _apply(view, oracle, op)
        # Hash v2 of the people: cached, from scratch, and from a saved and reloaded copy.
        cached = people_hash(view.table)
        assert cached == people_hash(view.table, fresh=True)
        reloaded = PeopleView.of(
            {key: PersonRecord(**value) for key, value in view.table.dump("python").items()}
        )
        assert cached == people_hash(reloaded.table, fresh=True)
    assert list(view) == list(oracle)
    assert view.table.dump("json") == _expected(oracle, "json")
    assert view.table.dump("python") == _expected(oracle, "python")
    assert view.living_ids() == tuple(sorted(k for k, r in oracle.items() if r.alive))
    assert view.dead_ids() == tuple(sorted(k for k, r in oracle.items() if not r.alive))
    for key, record in oracle.items():
        assert view[key] == record
    # A copy taken along the way is untouched by everything done after it.
    for snapshot, expected in snapshots:
        assert snapshot.table.dump("json") == expected


def test_values_come_back_as_plain_python() -> None:
    person = Person(**_record(IDS[1], 5).model_dump())
    for name in ("age_days", "health_bp", "birth_day", "nutrition_debt"):
        assert type(getattr(person, name)) is int
    assert type(person.alive) is bool
    assert type(person.location) is HexCoord


def test_writes_are_checked_like_the_old_model() -> None:
    person = Person(**_record(IDS[1], 5).model_dump())
    for name, value in (
        ("health_bp", 10_001),
        ("health_bp", -1),
        ("age_days", -1),
        ("assimilation", 101),
        ("alive", 1),
        ("location", (1, 2)),
        ("death_day", "soon"),
        ("health_bp", 1.5),
    ):
        try:
            setattr(person, name, value)
        except ValueError:
            continue
        raise AssertionError(f"{name} = {value!r} was accepted")


def test_a_copy_and_its_original_never_share_writes() -> None:
    view = PeopleView.of({IDS[1]: Person(**_record(IDS[1], 5).model_dump())})
    copy = deepcopy(view)
    copy[IDS[1]].skills["craft"] = 9
    copy[IDS[1]].health_bp = 1
    assert "craft" not in view[IDS[1]].skills and view[IDS[1]].health_bp != 1
    view[IDS[1]].languages["x"] = 3
    assert "x" not in copy[IDS[1]].languages


def test_writes_mark_their_block_dirty() -> None:
    view = PeopleView()
    for number in range(2_100):
        person_id = EntityId(f"person:{number:010d}")
        view[person_id] = Person(**_record(person_id, number % 50).model_dump())
    table = view.table
    table.clear_dirty()
    view[EntityId(f"person:{1_500:010d}")].health_bp = 5
    assert table.dirty_blocks() == (1,)
    copy = deepcopy(view)
    assert copy.table.dirty_blocks() == ()
    _ = copy[EntityId(f"person:{2:010d}")].skills
    assert copy.table.dirty_blocks() == (0,), "a handed-out dict may change: its block is dirty"
