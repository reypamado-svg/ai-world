"""The free sealed trial: three civilizations played by Claude through Claude Code, ChatGPT
through Codex (each signed in with the user's plan) and a local model, with token caps and no
money. Stand-ins answer for the two programs and the local model. The world passes its launch
gate, is sealed, runs to its second council day, and verifies; and the gate names what would
stop it: a program not on PATH, a variable that would replace a sign-in, two civilizations on
one provider, no cap at all."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fake_cli import calls, install_fakes
from stub_model import StubModel
from typer.testing import CliRunner

from sovereign_world.cli import app
from sovereign_world.config import ENGINE_VERSION
from sovereign_world.gateway.records import recorded_councils
from sovereign_world.observer import TOKEN_ENV
from sovereign_world.persistence import WorldStore
from sovereign_world.preflight import GateOptions, launch_gate
from sovereign_world.rulehash import engine_hash, rule_hash
from sovereign_world.seal import SEAL_KEY_ENV, pins_of

PLAYED = tuple(f"civilization:000000000{n}" for n in range(1, 4))
INTERVAL = 28
HIJACKERS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CODEX_API_KEY",
    "CODEX_ACCESS_TOKEN",
)
cli = CliRunner()


@pytest.fixture
def ollama() -> Iterator[StubModel]:
    model = StubModel()
    yield model
    model.close()


def _settings(path: Path, ollama: StubModel, *, second: str = "codex", caps: bool = True) -> Path:
    path.write_text(
        f'[sovereigns."{PLAYED[0]}"]\nprovider = "claude-code"\nlabel = "claude"\n'
        'model = "claude-sonnet-fake"\n\n'
        f'[sovereigns."{PLAYED[1]}"]\nprovider = "{second}"\nlabel = "chatgpt"\n'
        'model = "gpt-plus"\neffort = "medium"\n\n'
        f'[sovereigns."{PLAYED[2]}"]\nprovider = "compatible"\nlabel = "ollama"\n'
        f'base_url = "{ollama.base_url}"\nmodel = "stub-model"\n\n'
        "[budgets]\ntimeout_seconds = 60\n\n"
        "[spend]\n"
        + ("max_input_tokens = 6000000\nmax_output_tokens = 1500000\n" if caps else "")
        + "".join(
            f'[spend.prices."{model}"]\ninput_per_million_usd = 0\noutput_per_million_usd = 0\n'
            for model in ("claude-sonnet-fake", "gpt-plus", "stub-model")
        ),
        encoding="utf-8",
    )
    return path


def _report(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "report.json").write_text(
        json.dumps(
            {
                "passed": True,
                "engine_hash": engine_hash(),
                "rule_hash": rule_hash(),
                "engine_version": ENGINE_VERSION,
                "histories": 1500,
                "rows": 4500,
                "checks": [],
                "council_interval_days": INTERVAL,
                "size": 24,
                "days": 365,
                "civilizations": 3,
            }
        )
    )
    return root


def _init(root: Path, settings: Path) -> None:
    made = cli.invoke(
        app,
        [
            "init",
            str(root),
            "--seed",
            "21",
            "--width",
            "24",
            "--height",
            "24",
            "--civilizations",
            "3",
            "--sovereigns",
            str(settings),
        ],
    )
    assert made.exit_code == 0, made.output


@pytest.fixture
def clean(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The fakes first on PATH, no variable that would replace a sign-in, an observer token."""
    log = install_fakes(tmp_path, monkeypatch)
    for name in (*HIJACKERS, SEAL_KEY_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(TOKEN_ENV, "o" * 43)
    return log


def test_a_free_three_provider_world_passes_its_gate_and_runs(
    tmp_path: Path, clean: Path, ollama: StubModel
) -> None:
    run = tmp_path / "trial"
    _init(run, _settings(tmp_path / "trial.toml", ollama))
    report = _report(tmp_path / "calibration")

    first = cli.invoke(app, ["preflight", str(run), "--calibration", str(report), "--probe"])
    assert first.exit_code == 0, first.output
    for line in (
        "PASS  players",
        "PASS  providers",
        "PASS  spend",
        "PASS  keys",
        "PASS  endpoints",
        "PASS  probe",
        "PASS  balance",
    ):
        assert line in first.output, first.output
    assert "no token (signed-in program)" in first.output
    assert "6,000,000 input tokens" in first.output
    assert "signed in with claude.ai" in first.output
    assert [call["program"] for call in calls(clean)] == ["claude", "codex"]
    assert len(ollama.asked()) == 1
    assert "reply schema sent as json_schema" in first.output
    keys = cli.invoke(app, ["keygen"])
    key = keys.output.splitlines()[0].split("=", 1)[1]
    fingerprint = keys.output.splitlines()[1].removeprefix("fingerprint: ")
    sealed = cli.invoke(app, ["seal", str(run), "--days", "60"], env={SEAL_KEY_ENV: key})
    assert sealed.exit_code == 0, sealed.output
    assert f"{PLAYED[0]}: claude-code claude-sonnet-fake at cli://claude" in sealed.output
    launch = ["preflight", str(run), "--launch", "--signer", fingerprint, "--days", "60"]
    ready = cli.invoke(app, [*launch, "--calibration", str(report)])
    assert ready.exit_code == 0, ready.output
    assert ready.output.splitlines()[-1].startswith("ready to launch: sealed by")

    ran = cli.invoke(app, ["run", str(run), "--days", str(INTERVAL + 1)])
    assert ran.exit_code == 0, ran.output
    assert "cap 6,000,000 input tokens, 1,500,000 output tokens" in ran.output
    assert f"  day {INTERVAL}: {PLAYED[0]} claude claude-sonnet-fake accepted" in ran.output
    assert f"  day {INTERVAL}: {PLAYED[1]} chatgpt gpt-plus accepted" in ran.output
    councils = recorded_councils(WorldStore(run))
    held = {(str(record.civilization_id), record.day) for record in councils if record.usage}
    assert held == {(civ, day) for civ in PLAYED for day in (0, INTERVAL)}
    assert {record.provider.split(":", 1)[0] for record in councils} == {
        "claude-code",
        "codex",
        "compatible",
    }

    summary = cli.invoke(app, ["councils", str(run), "--json"])
    assert summary.exit_code == 0, summary.output
    rows = {row["civilization"]: row for row in json.loads(summary.output)["civilizations"]}
    assert [rows[civ]["who"] for civ in PLAYED] == ["claude", "chatgpt", "ollama"]
    assert all(rows[civ]["outcomes"] == {"accepted": 2} for civ in PLAYED)
    # The local model was held to the reply schema at the probe and at both councils.
    assert ollama.formats == ["json_schema"] * 3
    assert all(rows[civ]["cost_usd"] == 0 for civ in PLAYED)

    spent = cli.invoke(app, ["spend", str(run)])
    assert spent.exit_code == 0, spent.output
    assert "cost cap: none" in spent.output
    assert "input tokens cap: 6,000,000; remaining" in spent.output
    assert "next day: may run" in spent.output
    verified = cli.invoke(app, ["verify", str(run), "--signer", fingerprint])
    assert verified.exit_code == 0, verified.output


def test_the_gate_names_what_would_stop_a_free_trial(
    tmp_path: Path, clean: Path, ollama: StubModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = tmp_path / "trial"
    _init(run, _settings(tmp_path / "trial.toml", ollama))
    store = WorldStore(run)
    pins = pins_of(store.manifest())
    assert (pins[PLAYED[0]].scheme, pins[PLAYED[0]].host, pins[PLAYED[0]].path) == (
        "cli",
        "claude",
        "",
    )
    assert pins[PLAYED[1]].base_url() == "cli://codex" and pins[PLAYED[1]].token_env is None

    def gate(
        environ: dict[str, str] | None = None, **options: object
    ) -> dict[str, tuple[str, str]]:
        checks = launch_gate(store, GateOptions(), environ=environ or {}, **options)  # type: ignore[arg-type]
        return {check.id: (check.status, check.detail) for check in checks}

    asked: list[set[tuple[str, str]]] = []

    def dates(hosts):  # type: ignore[no-untyped-def]
        asked.append(set(hosts))
        return {}

    offline = gate(prober=None, dates=dates)
    assert offline["players"][0] == "PASS" and offline["providers"][0] == "PASS"
    missing = gate(prober=None, which=lambda name: None if name == "codex" else f"/bin/{name}")
    assert missing["endpoints"] == ("FAIL", f"{PLAYED[1]}: the codex program is not on PATH")
    hijacked = gate(prober=None, environ={"ANTHROPIC_API_KEY": "x", "CODEX_API_KEY": "y"})
    assert hijacked["keys"][0] == "WARN"
    assert "ANTHROPIC_API_KEY, CODEX_API_KEY present" in hijacked["keys"][1]

    clock = launch_gate(store, GateOptions(probe=True), environ={}, prober=None, dates=dates)
    assert asked and all(scheme in ("http", "https") for scheme, _ in asked[-1])
    assert not any(host in ("claude", "codex") for _, host in asked[-1])
    assert {check.id for check in clock} >= {"clock"}

    twice = tmp_path / "twice"
    _init(twice, _settings(tmp_path / "twice.toml", ollama, second="claude-code"))
    checks = launch_gate(WorldStore(twice), GateOptions(), environ={}, prober=None)
    providers = next(check for check in checks if check.id == "providers")
    assert providers.status == "FAIL" and providers.detail.startswith("2 distinct of 3")

    uncapped = tmp_path / "uncapped"
    _init(uncapped, _settings(tmp_path / "uncapped.toml", ollama, caps=False))
    checks = launch_gate(WorldStore(uncapped), GateOptions(), environ={}, prober=None)
    spend = next(check for check in checks if check.id == "spend")
    assert spend.status == "FAIL" and "no cap is set" in spend.detail
