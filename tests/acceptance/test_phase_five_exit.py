"""The Phase 5 exit (sealed trial): "the signed manifest is immutable, every launch-gate check
passes, and the live world recovers from its last checkpoint without divergent replay".

Four model-played civilizations, each answered by its own stand-in model server: the launch gate
passes before and after sealing, the sealed world runs, is killed in the middle of a council
day, resumes without asking any model twice, verifies by its signer, and refuses to go on once
its manifest is changed."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

import pytest
from stub_model import StubModel
from typer.testing import CliRunner

from sovereign_world.cli import app
from sovereign_world.config import ENGINE_VERSION, RunManifest
from sovereign_world.gateway.records import recorded_councils
from sovereign_world.observer import TOKEN_ENV
from sovereign_world.persistence import WorldStore
from sovereign_world.rulehash import engine_hash, rule_hash
from sovereign_world.runner import CRASH_ENV
from sovereign_world.seal import SEAL_KEY_ENV

DAYS = 95
KILL = "after_council:90:2"
PLAYED = tuple(f"civilization:000000000{n}" for n in range(1, 5))
INIT = ("init", "--seed", "21", "--width", "24", "--height", "24")
RUN = (sys.executable, "-m", "sovereign_world.cli", "run")
cli = CliRunner()


@pytest.fixture
def stubs() -> Iterator[list[StubModel]]:
    models = [StubModel() for _ in PLAYED]
    yield models
    for model in models:
        model.close()


def _report(root: Path, *, ok: bool) -> Path:
    root.mkdir(parents=True)
    checks = [{"check": "mixed: raider at position 3 within ±20%", "passed": ok}]
    (root / "report.json").write_text(
        json.dumps(
            {
                "passed": ok,
                "engine_hash": engine_hash(),
                "rule_hash": rule_hash(),
                "engine_version": ENGINE_VERSION,
                "histories": 2000,
                "rows": 8000,
                "checks": checks,
            }
        )
    )
    return root


def _run(root: Path, days: int, crash: str | None = None) -> subprocess.CompletedProcess[str]:
    env = {key: value for key, value in os.environ.items() if key != CRASH_ENV}
    if crash is not None:
        env[CRASH_ENV] = crash
    return subprocess.run(
        [*RUN, str(root), "--days", str(days)],
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


def _saved_day(root: Path) -> int:
    day = 0
    for line in (root / "journal.jsonl").read_text().splitlines():
        record = json.loads(line)
        if record["type"] == "transition":
            day = int(record["payload"]["day"])
    return day


def test_the_sealed_trial_world_passes_its_gate_and_recovers(
    tmp_path: Path, stubs: list[StubModel], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STUB_TOKEN_A", "token-a-not-shown")
    monkeypatch.setenv("STUB_TOKEN_B", "token-b-not-shown")
    monkeypatch.setenv(TOKEN_ENV, "o" * 43)
    monkeypatch.delenv(SEAL_KEY_ENV, raising=False)
    tokens = {PLAYED[0]: "STUB_TOKEN_A", PLAYED[1]: "STUB_TOKEN_B"}
    settings = tmp_path / "sovereigns.toml"
    settings.write_text(
        "".join(
            f'[sovereigns."{civ}"]\nprovider = "compatible"\nlabel = "stub-{n}"\n'
            f'base_url = "{stub.base_url}"\nmodel = "stub-model"\n'
            + (f'token_env = "{tokens[civ]}"\n' if civ in tokens else "")
            for n, (civ, stub) in enumerate(zip(PLAYED, stubs, strict=True), 1)
        )
        + "[budgets]\ntimeout_seconds = 30\n"
        + "[spend]\nmax_cost_usd = 20\n"
        + '[spend.prices."stub-model"]\ninput_per_million_usd = 1\noutput_per_million_usd = 1\n'
    )
    run = tmp_path / "trial"
    made = cli.invoke(app, [*INIT[:1], str(run), *INIT[1:], "--sovereigns", str(settings)])
    assert made.exit_code == 0, made.output
    good, bad = _report(tmp_path / "good", ok=True), _report(tmp_path / "bad", ok=False)

    # The first pass, before sealing: everything but the seal, with one call per provider.
    first = cli.invoke(app, ["preflight", str(run), "--calibration", str(good), "--probe"])
    assert first.exit_code == 0, first.output
    for line in ("PASS  providers", "PASS  keys", "PASS  probe", "SKIP  seal", "PASS  balance"):
        assert line in first.output, first.output
    assert "not-shown" not in first.output
    assert [len(stub.asked()) for stub in stubs] == [1, 1, 1, 1]

    # A failed balance report stops the gate, unless the operator accepts it with a reason.
    refused = cli.invoke(app, ["preflight", str(run), "--calibration", str(bad)])
    assert refused.exit_code == 1 and "FAIL  balance" in refused.output
    accepted = cli.invoke(
        app,
        [
            "preflight",
            str(run),
            "--calibration",
            str(bad),
            "--accept-balance-failure",
            "contacts between civilizations are rare",
        ],
    )
    assert accepted.exit_code == 0, accepted.output
    assert "contacts between civilizations are rare" in accepted.output

    # Seal it, then the second pass.
    keys = cli.invoke(app, ["keygen"])
    key = keys.output.splitlines()[0].split("=", 1)[1]
    fingerprint = keys.output.splitlines()[1].removeprefix("fingerprint: ")
    sealed = cli.invoke(app, ["seal", str(run), "--days", str(DAYS)], env={SEAL_KEY_ENV: key})
    assert sealed.exit_code == 0, sealed.output
    launch = ["preflight", str(run), "--launch", "--signer", fingerprint, "--days", str(DAYS)]
    launch += ["--calibration", str(good)]
    ready = cli.invoke(app, launch)
    assert ready.exit_code == 0, ready.output
    assert ready.output.splitlines()[-1].startswith("ready to launch: sealed by")
    held = cli.invoke(app, launch, env={SEAL_KEY_ENV: key})
    assert held.exit_code == 1 and "FAIL  seal_key" in held.output
    other = cli.invoke(app, [*launch[:4], "0" * 64, *launch[5:]])
    assert other.exit_code == 1 and "FAIL  seal" in other.output

    # The world runs, is killed in the middle of day 90's councils, and resumes.
    for stub in stubs:
        stub.calls.clear()
    killed = _run(run, DAYS, KILL)
    assert killed.returncode == 137, killed.stderr
    assert f"crash hook: {KILL}" in killed.stderr
    assert _saved_day(run) == 90
    resumed = _run(run, DAYS - _saved_day(run))
    assert resumed.returncode == 0, resumed.stderr
    assert resumed.stdout.startswith(f"advanced to day {DAYS}")
    verified = cli.invoke(app, ["verify", str(run), "--signer", fingerprint])
    assert verified.exit_code == 0, verified.output
    assert f"verified through day {DAYS}" in verified.output
    assert f"for {DAYS} days" in verified.output and "matches this code" in verified.output
    assert WorldStore(run).load_checkpoint().day == DAYS
    asked = Counter(prompt for stub in stubs for prompt in stub.asked())
    assert set(asked.values()) == {1}, "a council's model was asked twice"
    councils = recorded_councils(WorldStore(run))
    used = {(str(record.civilization_id), record.day) for record in councils if record.usage}
    assert {(civ, day) for civ in PLAYED for day in (0, 30, 60, 90)} <= used

    # Changing the manifest afterwards refuses the run, and the gate says so.
    store = WorldStore(run)
    document = json.loads(store.manifest().model_dump_json())
    document["budgets"]["timeout_seconds"] = 1.0
    changed = RunManifest.model_validate(document)
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE manifest SET manifest_json = ?, manifest_hash = ?",
            (changed.model_dump_json(), changed.content_hash()),
        )
        connection.commit()
    journal = store.journal_path.read_bytes()
    tampered = cli.invoke(app, ["run", str(run), "--days", "1"])
    assert tampered.exit_code == 4 and "seal refused" in tampered.output
    assert store.journal_path.read_bytes() == journal
    gate = cli.invoke(app, launch)
    assert gate.exit_code == 1 and "FAIL  seal" in gate.output
