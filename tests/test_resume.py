"""Crash-safe recovery (sealed trial, slice C): councils are saved the moment they are held,
before their day; a run stopped between them reuses the saved councils and asks no model twice;
saved councils that cannot be the interrupted day's refuse the resume and write nothing."""

from __future__ import annotations

import io
import json
import shutil
import signal
import sqlite3
from pathlib import Path
from uuid import UUID

import pytest

from sovereign_world import runner
from sovereign_world.config import Price, RunManifest, SovereignConfig, SpendConfig, WorldConfig
from sovereign_world.gateway.factory import build_sovereigns
from sovereign_world.gateway.provider import ScriptedProvider
from sovereign_world.gateway.records import (
    COUNCIL_RECORD,
    CouncilRecord,
    ResumedCouncils,
    ResumeRefused,
    recorded_councils,
)
from sovereign_world.ids import EntityId
from sovereign_world.persistence import WorldStore
from sovereign_world.replay import rederive_run, verify_run
from sovereign_world.spend import tally
from sovereign_world.state import build_initial_state

QUIET = json.dumps({"commands": [], "rationale": "Wait and watch."})
PRICE = Price(input_per_million_usd=3, output_per_million_usd=15)


def _store(root: Path) -> tuple[WorldStore, list[EntityId]]:
    """A 24 by 24 world whose first and third civilizations are model-played."""
    base = RunManifest.new(WorldConfig(seed=21, width=24, height=24), "0.2.0")
    civilizations = sorted(build_initial_state(base).civilizations)
    played = [civilizations[0], civilizations[2]]
    model = SovereignConfig(provider="compatible", base_url="http://127.0.0.1:1/v1", model="m")
    manifest = RunManifest.model_validate(
        {
            **base.model_dump(),
            "run_id": UUID(int=21),
            "sovereigns": {str(civ): model.model_dump() for civ in played},
            "spend": SpendConfig(prices={"m": PRICE}).model_dump(),
        }
    )
    return WorldStore.create(root, manifest, build_initial_state(manifest)), civilizations


@pytest.fixture
def asked(monkeypatch: pytest.MonkeyPatch) -> list[tuple[EntityId, int]]:
    """Every model call, as (civilization, council day); each answers quietly."""
    calls: list[tuple[EntityId, int]] = []

    def build(manifest, civilization_ids, *, history=(), pinned=None):  # type: ignore[no-untyped-def]
        ids = sorted(civilization_ids)

        def provider(civ: EntityId) -> ScriptedProvider:
            def answer(request):  # type: ignore[no-untyped-def]
                day = int(request.user.split("Council of day ", 1)[1].split()[0].rstrip(".:"))
                calls.append((civ, day))
                return QUIET

            return ScriptedProvider(
                [answer] * 50, model="m", input_tokens=10_000, output_tokens=300
            )

        providers = {civ: provider(civ) for civ in ids if str(civ) in manifest.sovereigns}
        return build_sovereigns(manifest, ids, history=history, providers=providers)

    monkeypatch.setattr(runner, "build_sovereigns", build)
    return calls


def _lines(store: WorldStore) -> list[str]:
    return store.journal_path.read_text().splitlines(keepends=True)


def _types(store: WorldStore) -> list[tuple[str, int | None]]:
    out = []
    for line in _lines(store):
        record = json.loads(line)
        out.append((record["type"], record["payload"].get("day")))
    return out


def _cut(store: WorldStore, keep: int) -> None:
    """Only the journal's first `keep` lines kept (to forge one; nothing else is touched)."""
    store.journal_path.write_text("".join(_lines(store)[:keep]))


class Killed(BaseException):
    """Stands in for a kill: nothing after it runs, not even the final checkpoint."""


def _killed_after(store: WorldStore, days: int, *, councils: int | None = None) -> None:
    """Run until the given day's transition would be saved (or, with `councils`, until that many
    councils of the day are saved) and stop there, as a kill would."""
    append_transition = store.append_transition
    append_record = store.append_record
    saved = 0

    def transition(state, events, *, previous=None):  # type: ignore[no-untyped-def]
        if state.day == days and councils is None:
            raise Killed
        return append_transition(state, events, previous=previous)

    def record(kind, payload):  # type: ignore[no-untyped-def]
        nonlocal saved
        if councils is not None and kind == COUNCIL_RECORD and payload["day"] == days - 1:
            if saved == councils:
                raise Killed
            saved += 1
        return append_record(kind, payload)

    store.append_transition = transition  # type: ignore[method-assign]
    store.append_record = record  # type: ignore[method-assign]
    with pytest.raises(Killed):
        runner.run_days(store, days + 1)


def _reference(tmp_path: Path, days: int) -> bytes:
    store, _ = _store(tmp_path / "reference")
    runner.run_days(store, days)
    return store.journal_path.read_bytes()


def test_councils_are_saved_before_their_day(
    tmp_path: Path, asked: list[tuple[EntityId, int]]
) -> None:
    store, civilizations = _store(tmp_path / "run")
    runner.run_days(store, 2)
    types = _types(store)
    first_day = types.index(("transition", 1))
    councils = [entry for entry in types if entry[0] == COUNCIL_RECORD]
    assert councils == [(COUNCIL_RECORD, 0)] * 4
    assert all(types.index(entry) < first_day for entry in councils)
    assert [record.civilization_id for record in recorded_councils(store)] == civilizations
    assert asked == [(civilizations[0], 0), (civilizations[2], 0)]
    assert verify_run(store).verified_through_day == 2
    rederive_run(store)


def test_a_day_stopped_after_its_councils_resumes_without_asking_again(
    tmp_path: Path, asked: list[tuple[EntityId, int]]
) -> None:
    reference = _reference(tmp_path, 3)
    asked.clear()
    store, _ = _store(tmp_path / "run")
    _killed_after(store, 1)
    assert json.loads(_lines(store)[-1])["type"] == COUNCIL_RECORD
    calls = len(asked)
    state = runner.run_days(WorldStore(store.root), 3)
    assert state.day == 3
    assert len(asked) == calls
    assert store.journal_path.read_bytes() == reference
    rederive_run(store)


def test_a_half_held_council_day_asks_only_the_councils_not_saved(
    tmp_path: Path, asked: list[tuple[EntityId, int]]
) -> None:
    reference = _reference(tmp_path, 3)
    asked.clear()
    store, civilizations = _store(tmp_path / "run")
    # Two councils saved (the first model-played and the first baseline one), then a kill.
    _killed_after(store, 1, councils=2)
    assert [r.civilization_id for r in recorded_councils(store)] == civilizations[:2]
    asked.clear()
    runner.run_days(WorldStore(store.root), 3)
    assert asked == [(civilizations[2], 0)]
    assert store.journal_path.read_bytes() == reference


def test_spending_saved_before_a_stop_is_counted_once(
    tmp_path: Path, asked: list[tuple[EntityId, int]]
) -> None:
    whole, _ = _store(tmp_path / "whole")
    runner.run_days(whole, 2)
    store, _ = _store(tmp_path / "run")
    _killed_after(store, 1)
    runner.run_days(WorldStore(store.root), 2)
    spend = store.manifest().spend
    assert tally(recorded_councils(store), spend).cost_usd == pytest.approx(
        tally(recorded_councils(whole), spend).cost_usd
    )


def _refused(store: WorldStore, match: str) -> None:
    before = store.journal_path.read_bytes()
    with pytest.raises(ResumeRefused, match=match):
        runner.run_days(WorldStore(store.root), 2)
    assert store.journal_path.read_bytes() == before


def _saved(store: WorldStore, civ: EntityId, day: int, **changes: object) -> None:
    first = next(r for r in recorded_councils(store) if r.civilization_id == civ)
    record = first.model_copy(update={"day": day, "report_id": f"report:{day}:{civ}", **changes})
    store.append_record(COUNCIL_RECORD, record.model_dump(mode="json"))


def test_a_council_saved_for_a_later_day_is_refused(
    tmp_path: Path, asked: list[tuple[EntityId, int]]
) -> None:
    store, civilizations = _store(tmp_path / "run")
    runner.run_days(store, 1)
    _saved(store, civilizations[0], 5)
    _refused(store, "day 5, but its last saved day is 1")


def test_a_council_saved_for_a_day_its_civilization_does_not_sit_is_refused(
    tmp_path: Path, asked: list[tuple[EntityId, int]]
) -> None:
    store, civilizations = _store(tmp_path / "run")
    runner.run_days(store, 2)
    _saved(store, civilizations[1], 2)
    calls = len(asked)
    _refused(store, "holds no council that day")
    assert len(asked) == calls


def test_a_saved_council_asked_another_question_is_refused(
    tmp_path: Path, asked: list[tuple[EntityId, int]]
) -> None:
    store, _ = _store(tmp_path / "run")
    _killed_after(store, 1, councils=1)
    first = recorded_councils(store)[0]
    # The same council, saved with a different prompt, as another history would hold it.
    _cut(store, len(_lines(store)) - 1)
    store = WorldStore(store.root)
    store.append_record(
        COUNCIL_RECORD, first.model_copy(update={"prompt_hash": "0" * 64}).model_dump(mode="json")
    )
    asked.clear()
    _refused(store, "asked a different question")
    assert asked == []


def test_a_saved_baseline_council_with_other_orders_is_refused(
    tmp_path: Path, asked: list[tuple[EntityId, int]]
) -> None:
    store, civilizations = _store(tmp_path / "run")
    _killed_after(store, 1, councils=2)
    second = recorded_councils(store)[1]
    assert second.civilization_id == civilizations[1] and second.envelope.commands
    _cut(store, len(_lines(store)) - 1)
    store = WorldStore(store.root)
    changed = second.envelope.model_copy(update={"commands": second.envelope.commands[:1]})
    store.append_record(
        COUNCIL_RECORD, second.model_copy(update={"envelope": changed}).model_dump(mode="json")
    )
    _refused(store, "gave other orders")


def test_two_councils_of_one_civilization_or_two_days_are_refused() -> None:
    record = CouncilRecord.model_validate_json(
        json.dumps(
            {
                "civilization_id": "civilization:0000000001",
                "day": 0,
                "report_id": "report:0:civilization:0000000001",
                "provider": "p",
                "model": "m",
                "prompt_version": "v",
                "prompt_hash": "",
                "outcome": "accepted",
                "envelope": {
                    "schema_version": 1,
                    "civilization_id": "civilization:0000000001",
                    "council_day": 0,
                    "report_id": "report:0:civilization:0000000001",
                    "correlation_id": "c",
                    "commands": [],
                },
            }
        )
    )
    with pytest.raises(ResumeRefused, match="two councils"):
        ResumedCouncils([record, record])
    other = record.model_copy(
        update={"civilization_id": EntityId("civilization:0000000002"), "day": 30}
    )
    with pytest.raises(ResumeRefused, match="days"):
        ResumedCouncils([record, other])


def test_a_copy_of_a_stopped_run_resumes_the_same_way(
    tmp_path: Path, asked: list[tuple[EntityId, int]]
) -> None:
    """The saved councils are in the journal only: a copied run directory resumes alike."""
    store, _ = _store(tmp_path / "run")
    _killed_after(store, 1)
    copy = tmp_path / "copy"
    shutil.copytree(store.root, copy)
    runner.run_days(WorldStore(store.root), 2)
    runner.run_days(WorldStore(copy), 2)
    assert (copy / "journal.jsonl").read_bytes() == store.journal_path.read_bytes()


def _checkpoint_days(store: WorldStore) -> list[int]:
    connection = sqlite3.connect(f"{store.database_path.as_uri()}?mode=ro", uri=True)
    try:
        rows = connection.execute("SELECT day FROM checkpoints ORDER BY day").fetchall()
    finally:
        connection.close()
    return [int(day) for (day,) in rows]


def test_a_checkpoint_is_saved_every_thirty_days_and_changes_no_journal_byte(
    tmp_path: Path, asked: list[tuple[EntityId, int]]
) -> None:
    whole, _ = _store(tmp_path / "whole")
    runner.run_days(whole, 35)
    assert _checkpoint_days(whole) == [0, 30, 35]
    split, _ = _store(tmp_path / "split")
    runner.run_days(split, 20)
    runner.run_days(WorldStore(split.root), 15)
    assert _checkpoint_days(split) == [0, 20, 30, 35]
    assert split.journal_path.read_bytes() == whole.journal_path.read_bytes()


def test_the_first_ctrl_c_stops_after_the_day_under_way_and_the_second_at_once(
    tmp_path: Path, asked: list[tuple[EntityId, int]]
) -> None:
    store, _ = _store(tmp_path / "run")
    control = runner.RunControl()
    before = signal.getsignal(signal.SIGINT)
    said = io.StringIO()

    def press(state: object) -> None:
        if getattr(state, "day", None) == 2:
            handler = signal.getsignal(signal.SIGINT)
            assert callable(handler)
            handler(signal.SIGINT, None)

    with runner.interruptible(control, out=said):
        final = runner.run_days(store, 10, control=control, on_day=press)
        assert final.day == 2 and control.stopped
        handler = signal.getsignal(signal.SIGINT)
        assert callable(handler)
        with pytest.raises(KeyboardInterrupt):
            handler(signal.SIGINT, None)
    assert signal.getsignal(signal.SIGINT) is before
    assert "stopping after the day under way" in said.getvalue()
    assert _checkpoint_days(store) == [0, 2]
    assert verify_run(store).verified_through_day == 2
