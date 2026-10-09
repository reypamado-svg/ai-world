"""Kill a model-played run at any moment and run it again (sealed trial, slice C): the journal
ends byte for byte as an unbroken run's, `verify` passes, and no council's model is asked twice
unless the kill landed while it was answering."""

from __future__ import annotations

import json
import os
import random
import shutil
import signal
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

import pytest
from stub_model import StubModel
from typer.testing import CliRunner

from sovereign_world.cli import app
from sovereign_world.gateway.records import recorded_councils
from sovereign_world.persistence import WorldStore
from sovereign_world.runner import CRASH_ENV

DAYS = 35
INTERVAL = 28
"""Days between councils (`init`'s default): councils sit on days 0 and 28 of the run."""
PLAYED = ("civilization:0000000001", "civilization:0000000003")
cli = CliRunner()
RUN = (sys.executable, "-m", "sovereign_world.cli", "run")


INIT = ("init", "--seed", "21", "--width", "24", "--height", "24")


@pytest.fixture
def stub() -> Iterator[StubModel]:
    model = StubModel()
    yield model
    model.close()


@pytest.fixture
def worlds(tmp_path: Path, stub: StubModel) -> tuple[Path, Path, bytes]:
    """One world, initialised once and copied: `plain` run to day 35 unbroken (its journal
    returned), and `killed`, untouched, for a test to run in pieces."""
    settings = tmp_path / "sovereigns.toml"
    settings.write_text(
        "".join(
            f'[sovereigns."{civ}"]\nprovider = "compatible"\nbase_url = "{stub.base_url}"\n'
            'model = "stub-model"\n'
            for civ in PLAYED
        )
        + "[budgets]\ntimeout_seconds = 30\n"
        + '[spend.prices."stub-model"]\ninput_per_million_usd = 1\noutput_per_million_usd = 1\n'
    )
    base = tmp_path / "base"
    init = cli.invoke(
        app,
        [*INIT, str(base), "--sovereigns", str(settings)],
    )
    assert init.exit_code == 0, init.output
    plain, killed = tmp_path / "plain", tmp_path / "killed"
    shutil.copytree(base, plain)
    shutil.copytree(base, killed)
    assert _run(plain, DAYS).returncode == 0
    reference = (plain / "journal.jsonl").read_bytes()
    stub.calls.clear()
    return plain, killed, reference


def _run(root: Path, days: int, crash: str | None = None) -> subprocess.CompletedProcess[str]:
    env = {key: value for key, value in os.environ.items() if key != CRASH_ENV}
    if crash is not None:
        env[CRASH_ENV] = crash
    return subprocess.run(
        [sys.executable, "-m", "sovereign_world.cli", "run", str(root), "--days", str(days)],
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


def _saved_day(root: Path) -> int:
    """The last day the journal holds, read as a file (a torn last line is skipped)."""
    day = 0
    for line in (root / "journal.jsonl").read_text().splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if record["type"] == "transition":
            day = int(record["payload"]["day"])
    return day


def _verify(root: Path) -> None:
    result = cli.invoke(app, ["verify", str(root)])
    assert result.exit_code == 0, result.output


def _model_hashes(root: Path) -> list[str]:
    return [
        record.prompt_hash
        for record in recorded_councils(WorldStore(root))
        if str(record.civilization_id) in PLAYED
    ]


def test_every_crash_point_resumes_to_the_same_journal(
    worlds: tuple[Path, Path, bytes], stub: StubModel
) -> None:
    plain, killed, reference = worlds
    points = (
        "before_day:0",
        "after_council:0:1",
        "after_council:0:3",
        "after_councils:0",
        "after_transition:1",
        "before_day:17",
        "after_councils:17",
        f"after_council:{INTERVAL}:2",
        f"after_councils:{INTERVAL}",
        f"after_transition:{INTERVAL + 1}",
        "after_checkpoint:30",
    )
    for point in points:
        result = _run(killed, DAYS - _saved_day(killed), point)
        assert result.returncode == 137, (point, result.stdout, result.stderr)
        assert f"crash hook: {point}" in result.stderr
    final = _run(killed, DAYS - _saved_day(killed))
    assert final.returncode == 0, final.stderr
    assert (killed / "journal.jsonl").read_bytes() == reference
    _verify(killed)
    assert WorldStore(killed).load_checkpoint().day == DAYS
    hashes = _model_hashes(killed)
    assert len(hashes) == 2 * len(PLAYED)
    asked = Counter(stub.asked())
    assert set(asked) == set(hashes) == set(_model_hashes(plain))
    assert set(asked.values()) == {1}


@pytest.mark.parametrize("seed", [1, 2])
def test_random_kills_resume_to_the_same_journal(
    worlds: tuple[Path, Path, bytes], stub: StubModel, seed: int
) -> None:
    _, killed, reference = worlds
    stub.delay_seconds = 0.3
    chance = random.Random(seed)
    kills = 0
    while kills < 8 and _saved_day(killed) < DAYS:
        # A pace keeps each run alive across the random kill times (unpaced, the 35 days can end
        # before a second kill lands); it records nothing, and kills during its waits count too.
        process = subprocess.Popen(
            [*RUN, str(killed), "--days", str(DAYS - _saved_day(killed)), "--pace", "0.2"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(chance.uniform(0.3, 2.0))
        if process.poll() is None:
            process.kill()
            kills += 1
        process.wait(timeout=60)
    assert kills >= 2
    if _saved_day(killed) < DAYS:
        assert _run(killed, DAYS - _saved_day(killed)).returncode == 0
    assert (killed / "journal.jsonl").read_bytes() == reference
    _verify(killed)
    hashes = _model_hashes(killed)
    asked = stub.asked()
    assert set(asked) == set(hashes)
    assert len(asked) <= len(hashes) + kills


@pytest.mark.skipif(sys.platform == "win32", reason="sends a POSIX SIGINT")
def test_ctrl_c_stops_a_running_world_after_the_day_under_way(
    worlds: tuple[Path, Path, bytes],
) -> None:
    _, killed, _ = worlds
    process = subprocess.Popen(
        [sys.executable, "-m", "sovereign_world.cli", "run", str(killed), "--days", "3000"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 120
    while _saved_day(killed) < 2 and time.monotonic() < deadline:
        time.sleep(0.1)
    process.send_signal(signal.SIGINT)
    out, err = process.communicate(timeout=120)
    assert process.returncode == 0, err
    assert "stopping after the day under way" in err
    last = out.splitlines()[-1]
    assert last.startswith("stopped at day ")
    day = int(last.split()[3])
    assert 2 <= day < 3000
    assert WorldStore(killed).load_checkpoint().day == day
    _verify(killed)
