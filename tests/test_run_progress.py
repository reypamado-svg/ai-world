"""What `run` shows as it goes (sealed trial, slice F): a line per council and per council day,
a line per checkpoint, nothing on quiet days; a pace between days that a stop ends at once; and
`councils`, the summary of how each civilization's councils went. None of it changes the run."""

from __future__ import annotations

import io
import json
import threading
import time
from pathlib import Path
from uuid import UUID

import pytest
from typer.testing import CliRunner

from sovereign_world import runner
from sovereign_world.cli import app, utf8_or_replace
from sovereign_world.config import Price, RunManifest, SovereignConfig, SpendConfig, WorldConfig
from sovereign_world.gateway.factory import build_sovereigns
from sovereign_world.gateway.provider import (
    ProviderTimeout,
    ProviderUnavailable,
    Scripted,
    ScriptedProvider,
)
from sovereign_world.gateway.records import recorded_councils
from sovereign_world.ids import EntityId
from sovereign_world.persistence import WorldStore
from sovereign_world.runner import DayProgress, RunControl, run_days
from sovereign_world.spend import council_summary, tally
from sovereign_world.state import build_initial_state

QUIET = json.dumps({"commands": [], "rationale": "Wait and watch."})
SECRET = "sk-test-not-to-be-shown-0123456789"
PRICE = Price(input_per_million_usd=3, output_per_million_usd=15)
cli = CliRunner()


def _store(root: Path, *, played: bool = True) -> tuple[WorldStore, list[EntityId]]:
    """A 24 by 24 world with councils every 30 days; with `played`, its first and third
    civilizations are model-played (labelled vendors on one stand-in address)."""
    base = RunManifest.new(WorldConfig(seed=21, width=24, height=24), "0.2.0")
    civilizations = sorted(build_initial_state(base).civilizations)
    sovereigns = {}
    if played:
        for civ, label in ((civilizations[0], "alpha"), (civilizations[2], "beta")):
            sovereigns[str(civ)] = SovereignConfig(
                provider="compatible",
                label=label,
                base_url="http://127.0.0.1:1/v1",
                token_env="STUB_TOKEN",
                model="m",
            ).model_dump()
    manifest = RunManifest.model_validate(
        {
            **base.model_dump(),
            "run_id": UUID(int=21),
            "sovereigns": sovereigns,
            "spend": SpendConfig(prices={"m": PRICE}, max_cost_usd=50).model_dump(),
        }
    )
    return WorldStore.create(root, manifest, build_initial_state(manifest)), civilizations


@pytest.fixture
def scripted(monkeypatch: pytest.MonkeyPatch) -> None:
    """The first played civilization times out on day 0 and finds its model unavailable (with
    the key in the message) on day 30; the second answers badly then well on day 0 (repaired),
    and well on day 30."""

    def build(manifest, civilization_ids, *, history=(), pinned=None):  # type: ignore[no-untyped-def]
        ids = sorted(civilization_ids)
        scripts: dict[EntityId, list[Scripted]] = {
            ids[0]: [ProviderTimeout("slow"), ProviderUnavailable(f"refused key {SECRET}")],
            ids[2]: ["not json at all", QUIET, QUIET],
        }
        providers = {
            civ: ScriptedProvider(script, model="m", input_tokens=10_000, output_tokens=300)
            for civ, script in scripts.items()
        }
        return build_sovereigns(manifest, ids, history=history, providers=providers)

    monkeypatch.setattr(runner, "build_sovereigns", build)
    monkeypatch.setenv("STUB_TOKEN", SECRET)


def test_run_prints_each_council_and_checkpoint_and_records_nothing_of_it(
    tmp_path: Path, scripted: None
) -> None:
    store, civilizations = _store(tmp_path / "run")
    shown = cli.invoke(app, ["run", str(store.root), "--days", "31"])
    assert shown.exit_code == 0, shown.output
    lines = shown.output.splitlines()
    assert lines[0] == "run 00000000 day 0 -> 31, councils every 30 days, cap $50.00"
    first, third = civilizations[0], civilizations[2]
    day0 = [line for line in lines if line.startswith("  day 0: ")]
    assert len(day0) == 4
    assert any(line.startswith(f"  day 0: {first} alpha m timeout, 0 in, 0 out") for line in day0)
    assert any(
        line.startswith(f"  day 0: {third} beta m repaired, 20,000 in, 600 out, $0.0690")
        for line in day0
    )
    assert any(line.startswith(f"  day 30: {first} alpha m unavailable") for line in lines)
    assert any(line.startswith(f"  day 30: {third} beta m accepted") for line in lines)
    assert any(line.startswith("day 0 councils: 4 in ") for line in lines)
    assert any(line.startswith("day 30 councils: 4 in ") for line in lines)
    assert "checkpoint saved at day 30" in lines
    assert not any(line.startswith(("  day 1:", "day 1 ")) for line in lines)
    assert lines[-1].startswith("advanced to day 31 (")
    spent = tally(recorded_councils(store), store.manifest().spend)
    assert spent.input_tokens == 30_000 and spent.output_tokens == 900
    last_day = next(line for line in lines if line.startswith("day 30 councils:"))
    assert f"spent ${spent.cost_usd:,.4f} of $50.00 (30,000 in, 900 out)" in last_day
    assert SECRET not in shown.output
    assert shown.output.isascii()

    # The same days run without anyone watching give the same journal, byte for byte.
    quiet, _ = _store(tmp_path / "quiet")
    run_days(quiet, 31)
    assert quiet.journal_path.read_bytes() == store.journal_path.read_bytes()


def test_the_progress_hooks_hear_every_saved_council_and_day(
    tmp_path: Path, scripted: None
) -> None:
    store, _ = _store(tmp_path / "run")
    heard: list[tuple[int, str, float]] = []
    days: list[DayProgress] = []
    run_days(
        store,
        31,
        on_council=lambda record, seconds: heard.append(
            (record.day, str(record.civilization_id), seconds)
        ),
        on_progress=days.append,
    )
    assert [day.day for day in days] == list(range(1, 32))
    assert [len(day.councils) for day in days if day.councils] == [4, 4]
    assert [day.day for day in days if day.checkpoint] == [30]
    assert len(heard) == 8 and all(seconds >= 0 for _, _, seconds in heard)
    spent = tally(recorded_councils(store), store.manifest().spend)
    assert days[-1].spent.cost_usd == spent.cost_usd
    # Each day's tally is its own copy, as it stood after that day (councils that used a model).
    assert days[0].spent.councils == 1 and days[-1].spent.councils == 2


def test_the_controlled_runner_says_only_its_protocol(tmp_path: Path) -> None:
    store, _ = _store(tmp_path / "run", played=False)
    out = io.StringIO()
    control = RunControl()
    commands = io.StringIO("resume\n")
    runner.controlled(store, 31, commands=commands, out=out, control=control)
    words = {line.split()[0] for line in out.getvalue().splitlines()}
    assert words <= {"start", "paused", "running", "day", "done"}
    assert not any(line.startswith(("  day", "checkpoint")) for line in out.getvalue().splitlines())


def test_a_console_that_cannot_show_a_character_prints_a_stand_in() -> None:
    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp437")
    utf8_or_replace(stream)
    stream.write("ready … — ok")
    stream.flush()
    utf8 = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    utf8_or_replace(utf8)
    assert utf8.errors == "strict"


def test_a_paced_run_waits_between_days_and_records_the_same(tmp_path: Path) -> None:
    paced, _ = _store(tmp_path / "paced", played=False)
    started = time.monotonic()
    run_days(paced, 3, pace_seconds=0.3)
    assert time.monotonic() - started >= 0.6
    plain, _ = _store(tmp_path / "plain", played=False)
    run_days(plain, 3)
    assert paced.journal_path.read_bytes() == plain.journal_path.read_bytes()
    assert paced.load_checkpoint().day == plain.load_checkpoint().day == 3


def test_a_stop_ends_the_pace_at_once() -> None:
    control = RunControl()
    threading.Timer(0.2, control.stop).start()
    started = time.monotonic()
    assert control.wait_pace(30) is False
    assert time.monotonic() - started < 1.5
    assert RunControl().wait_pace(0.05) is True


def test_a_run_stopped_during_its_pace_stops_at_that_day(tmp_path: Path) -> None:
    store, _ = _store(tmp_path / "run", played=False)
    control = RunControl()

    def stop_after_first(progress: DayProgress) -> None:
        if progress.day == 1:
            control.stop()

    started = time.monotonic()
    state = run_days(store, 5, control=control, on_progress=stop_after_first, pace_seconds=5)
    assert state.day == 1 and time.monotonic() - started < 4
    assert store.load_checkpoint().day == 1


def test_the_pace_option_runs_the_same_days(tmp_path: Path) -> None:
    paced, _ = _store(tmp_path / "paced", played=False)
    shown = cli.invoke(app, ["run", str(paced.root), "--days", "3", "--pace", "0.1"])
    assert shown.exit_code == 0, shown.output
    assert shown.output.splitlines()[-1].startswith("advanced to day 3 (")
    plain, _ = _store(tmp_path / "plain", played=False)
    run_days(plain, 3)
    assert paced.journal_path.read_bytes() == plain.journal_path.read_bytes()


def test_councils_summarises_each_civilization_and_hides_key_values(
    tmp_path: Path, scripted: None
) -> None:
    store, civilizations = _store(tmp_path / "run")
    run_days(store, 31)
    rows = {
        row.civilization: row for row in council_summary(recorded_councils(store), store.manifest())
    }
    first, third = str(civilizations[0]), str(civilizations[2])
    assert rows[first].outcomes == {"timeout": 1, "unavailable": 1} and rows[first].asked == 0
    assert rows[third].outcomes == {"repaired": 1, "accepted": 1} and rows[third].asked == 2
    assert rows[third].input_tokens == 30_000 and rows[third].cost_usd == pytest.approx(0.1035)
    assert rows[str(civilizations[1])].who == "baseline"
    assert rows[str(civilizations[1])].outcomes == {"accepted": 2}

    shown = cli.invoke(app, ["councils", str(store.root), "--errors"])
    assert shown.exit_code == 0, shown.output
    lines = shown.output.splitlines()
    assert f"{first} alpha m: 2 councils (timeout 1, unavailable 1)" in lines
    assert (
        f"{third} beta m: 2 councils (accepted 1, repaired 1); mean 15,000 in, 450 out; $0.1035"
        in lines
    )
    assert "total: 8 councils (accepted 5, repaired 1, timeout 1, unavailable 1); $0.1035" in lines
    assert "councils with errors: 3" in lines
    assert any(line.startswith(f"  day 30: {first} unavailable: ") for line in lines)
    assert SECRET not in shown.output

    as_json = cli.invoke(app, ["councils", str(store.root), "--json", "--errors"])
    assert as_json.exit_code == 0, as_json.output
    document = json.loads(as_json.output)
    by_civ = {row["civilization"]: row for row in document["civilizations"]}
    assert by_civ[third]["outcomes"] == {"accepted": 1, "repaired": 1}
    assert by_civ[third]["cost_usd"] == pytest.approx(0.1035)
    assert len(document["errors"]) == 3
    assert SECRET not in as_json.output
