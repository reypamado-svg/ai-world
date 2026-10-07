"""The launch gate (sealed trial, slice E): `sovereign-world preflight`, a plain checklist that
writes nothing and never prints a secret."""

from __future__ import annotations

import json
import os
import threading
from collections import namedtuple
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from stub_model import StubModel
from typer.testing import CliRunner

from sovereign_world import preflight
from sovereign_world.cli import app
from sovereign_world.config import (
    ENGINE_VERSION,
    Price,
    RunManifest,
    SovereignConfig,
    SpendConfig,
    WorldConfig,
)
from sovereign_world.gateway.sovereign import prompt_hash
from sovereign_world.observer import TOKEN_ENV
from sovereign_world.persistence import WorldStore
from sovereign_world.preflight import (
    CHECKS,
    PROBE_SYSTEM,
    PROBE_USER,
    SELF_TEST_HASH,
    Check,
    GateOptions,
    ProbeResult,
    host_dates,
    launch_gate,
    passed,
    scrub,
    self_test_hash,
)
from sovereign_world.rulehash import engine_hash, rule_hash
from sovereign_world.runner import run_days
from sovereign_world.seal import SEAL_KEY_ENV, generate_key
from sovereign_world.state import build_initial_state

cli = CliRunner()
Usage = namedtuple("Usage", "total used free")
ROOMY = Usage(500 * 2**30, 0, 100 * 2**30)
CIVS = tuple(f"civilization:000000000{n}" for n in range(1, 5))


def _config(n: int, **changes: Any) -> SovereignConfig:
    values: dict[str, Any] = {
        "provider": "compatible",
        "base_url": f"http://127.0.0.1:{9100 + n}/v1",
        "model": "m",
        "label": f"stub-{n}",
    }
    values.update(changes)
    return SovereignConfig(**values)


def _store(
    root: Path,
    sovereigns: dict[str, SovereignConfig] | None = None,
    spend: SpendConfig | None = None,
) -> WorldStore:
    base = RunManifest.new(WorldConfig(seed=21, width=24, height=24), "0.2.0")
    played = (
        sovereigns if sovereigns is not None else {civ: _config(n) for n, civ in enumerate(CIVS, 1)}
    )
    cap = (
        spend
        if spend is not None
        else SpendConfig(
            max_cost_usd=100, prices={"m": Price(input_per_million_usd=1, output_per_million_usd=1)}
        )
    )
    manifest = RunManifest.model_validate(
        {
            **base.model_dump(),
            "sovereigns": {civ: config.model_dump() for civ, config in played.items()},
            "spend": cap.model_dump(),
        }
    )
    return WorldStore.create(root, manifest, build_initial_state(manifest))


def _gate(store: WorldStore, options: GateOptions | None = None, **kwargs: Any) -> dict[str, Check]:
    kwargs.setdefault("environ", {})
    kwargs.setdefault("disk_usage", lambda path: ROOMY)
    checks = launch_gate(store, options or GateOptions(), **kwargs)
    assert [check.id for check in checks] == [check_id for check_id, _ in CHECKS]
    return {check.id: check for check in checks}


def _report(root: Path, *, ok: bool = True, **changes: Any) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    report = {
        "passed": ok,
        "engine_hash": engine_hash(),
        "rule_hash": rule_hash(),
        "engine_version": ENGINE_VERSION,
        "histories": 2000,
        "rows": 8000,
        "checks": [{"check": "builders: position 0 win share in 15% to 35%", "passed": ok}],
    }
    report.update(changes)
    (root / "report.json").write_text(json.dumps(report))
    return root


def test_the_engine_self_test_constant_is_fresh() -> None:
    found = self_test_hash()
    assert found == SELF_TEST_HASH, (
        f"the engine changed: set preflight.SELF_TEST_HASH = {found!r} once the change is meant"
    )


def test_a_fresh_four_provider_run_passes_offline(tmp_path: Path) -> None:
    checks = _gate(_store(tmp_path / "run"))
    statuses = {check_id: check.status for check_id, check in checks.items()}
    assert statuses == {
        "store": "PASS",
        "day": "PASS",
        "engine": "PASS",
        "code": "PASS",
        "players": "PASS",
        "providers": "PASS",
        "prompts": "PASS",
        "spend": "PASS",
        "keys": "PASS",
        "endpoints": "PASS",
        "probe": "SKIP",
        "disk": "PASS",
        "clock": "PASS",
        "balance": "WARN",
        "seal": "SKIP",
        "seal_key": "PASS",
        "observer_token": "WARN",
    }
    assert passed(checks.values())
    assert "no token (loopback)" in checks["keys"].detail


def test_a_scripted_civilization_or_shared_host_fails(tmp_path: Path) -> None:
    configs = {civ: _config(n) for n, civ in enumerate(CIVS, 1)}
    scripted = _gate(_store(tmp_path / "a", {**configs, CIVS[3]: SovereignConfig()}))
    assert scripted["players"].status == "FAIL" and "baseline" in scripted["players"].detail
    shared = {**configs, CIVS[3]: _config(1)}
    checks = _gate(_store(tmp_path / "b", shared))
    assert checks["providers"].status == "FAIL" and "3 distinct" in checks["providers"].detail


def test_an_old_prompt_version_fails(tmp_path: Path) -> None:
    configs = {civ: _config(n) for n, civ in enumerate(CIVS, 1)}
    configs[CIVS[0]] = _config(1, prompt_version="council-1")
    checks = _gate(_store(tmp_path / "run", configs))
    assert checks["prompts"].status == "FAIL" and CIVS[0] in checks["prompts"].detail


def test_the_cap_and_prices_are_required_and_a_thin_cap_warned(tmp_path: Path) -> None:
    price = {"m": Price(input_per_million_usd=1, output_per_million_usd=1)}
    no_cap = _gate(_store(tmp_path / "a", spend=SpendConfig(prices=price)))
    assert no_cap["spend"].status == "FAIL" and "no cost cap" in no_cap["spend"].detail
    unpriced = _gate(_store(tmp_path / "b", spend=SpendConfig(max_cost_usd=100)))
    assert unpriced["spend"].status == "FAIL" and "no price for m" in unpriced["spend"].detail
    thin = _gate(_store(tmp_path / "c", spend=SpendConfig(max_cost_usd=0.5, prices=price)))
    assert thin["spend"].status == "WARN" and "fewer than a year's 13" in thin["spend"].detail


def test_keys_are_named_never_shown(tmp_path: Path) -> None:
    configs = {civ: _config(n) for n, civ in enumerate(CIVS, 1)}
    configs[CIVS[0]] = _config(
        1, base_url="https://models.example.com/v1", token_env="REMOTE_TOKEN"
    )
    store = _store(tmp_path / "run", configs)
    missing = _gate(store)
    assert missing["keys"].status == "FAIL" and "REMOTE_TOKEN" in missing["keys"].detail
    present = _gate(store, environ={"REMOTE_TOKEN": "sk-very-secret"})
    assert present["keys"].status == "PASS"
    assert "sk-very-secret" not in json.dumps([c.detail for c in present.values()])
    # A redirect matters only for a hosted model of that kind in the run.
    unused = _gate(store, environ={"REMOTE_TOKEN": "x", "OPENAI_BASE_URL": "http://127.0.0.1:1"})
    assert unused["keys"].status == "PASS"
    hosted = {**configs, CIVS[1]: SovereignConfig(provider="openai", model="gpt-model")}
    gpt_store = _store(
        tmp_path / "hosted",
        hosted,
        SpendConfig(
            max_cost_usd=100,
            prices={
                "m": Price(input_per_million_usd=1, output_per_million_usd=1),
                "gpt-model": Price(input_per_million_usd=1, output_per_million_usd=1),
            },
        ),
    )
    redirected = _gate(
        gpt_store,
        environ={"REMOTE_TOKEN": "x", "OPENAI_API_KEY": "k", "OPENAI_BASE_URL": "http://x"},
    )
    assert redirected["keys"].status == "WARN" and "OPENAI_BASE_URL" in redirected["keys"].detail


def test_the_endpoint_policy(tmp_path: Path) -> None:
    configs = {civ: _config(n) for n, civ in enumerate(CIVS, 1)}
    plain = {**configs, CIVS[0]: _config(1, base_url="http://192.168.1.5:11434/v1", token_env="T")}
    checks = _gate(_store(tmp_path / "plain", plain), environ={"T": "x"})
    assert checks["endpoints"].status == "FAIL" and "plain http off" in checks["endpoints"].detail
    tokenless = {**configs, CIVS[0]: _config(1, base_url="https://models.example.com/v1")}
    checks = _gate(_store(tmp_path / "a", tokenless))
    assert (
        checks["endpoints"].status == "FAIL" and "no token variable" in checks["endpoints"].detail
    )
    private = {
        **configs,
        CIVS[0]: _config(
            1, base_url="http://192.168.1.5:11434/v1", allow_private_http=True, token_env="T"
        ),
    }
    checks = _gate(_store(tmp_path / "b", private), environ={"T": "x"})
    assert checks["endpoints"].status == "WARN" and "private network" in checks["endpoints"].detail
    unlabelled = {**configs, CIVS[0]: _config(1, label="")}
    checks = _gate(_store(tmp_path / "c", unlabelled))
    assert checks["endpoints"].status == "WARN" and "no label" in checks["endpoints"].detail


def test_disk_and_clock(tmp_path: Path) -> None:
    store = _store(tmp_path / "run")
    low = _gate(store, disk_usage=lambda path: Usage(0, 0, 2**30))
    assert low["disk"].status == "FAIL"
    some = _gate(store, disk_usage=lambda path: Usage(0, 0, 5 * 2**30))
    assert some["disk"].status == "WARN"
    past = _gate(store, now=lambda: 1_000_000_000.0)
    assert past["clock"].status == "FAIL" and "before this gate was written" in past["clock"].detail
    saved = store.journal_path.stat().st_mtime
    behind = _gate(store, now=lambda: max(saved - 3600, 1_791_000_000.0))
    assert behind["clock"].status in ("FAIL", "PASS")
    if saved - 3600 > 1_791_000_000.0:
        assert "last saved" in behind["clock"].detail


def test_the_balance_report(tmp_path: Path) -> None:
    store = _store(tmp_path / "run")
    good = _gate(store, GateOptions(calibration=_report(tmp_path / "good")))
    assert good["balance"].status == "PASS" and "2000 histories" in good["balance"].detail
    as_file = _gate(store, GateOptions(calibration=tmp_path / "good" / "report.json"))
    assert as_file["balance"].status == "PASS"
    failed = _gate(store, GateOptions(calibration=_report(tmp_path / "bad", ok=False)))
    assert failed["balance"].status == "FAIL" and "position 0 win share" in failed["balance"].detail
    accepted = _gate(
        store,
        GateOptions(
            calibration=tmp_path / "bad", accept_balance_failure="contacts are rare in a year"
        ),
    )
    assert accepted["balance"].status == "WARN"
    assert "contacts are rare in a year" in accepted["balance"].detail
    other = _gate(store, GateOptions(calibration=_report(tmp_path / "other", engine_hash="0" * 64)))
    assert other["balance"].status == "FAIL" and "another engine" in other["balance"].detail
    small = _gate(store, GateOptions(calibration=_report(tmp_path / "small", histories=8)))
    assert small["balance"].status == "WARN"
    missing = _gate(store, GateOptions(launch=True))
    assert missing["balance"].status == "FAIL"


def test_the_launch_pass_checks_the_seal_and_the_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path / "run")
    unsealed = _gate(store, GateOptions(launch=True))
    assert unsealed["seal"].status == "FAIL" and "not sealed" in unsealed["seal"].detail
    key, fingerprint = generate_key()
    result = cli.invoke(app, ["seal", str(store.root), "--days", "365"], env={SEAL_KEY_ENV: key})
    assert result.exit_code == 0, result.output
    assert f"--signer {fingerprint}" in result.output
    report = _report(tmp_path / "report")
    launch = GateOptions(launch=True, signer=fingerprint, calibration=report)
    checks = _gate(WorldStore(store.root), launch)
    assert checks["seal"].status == "PASS" and "for 365 days" in checks["seal"].detail
    assert passed(checks.values())
    _, other = generate_key()
    wrong = _gate(WorldStore(store.root), GateOptions(launch=True, signer=other))
    assert wrong["seal"].status == "FAIL" and "sealed by" in wrong["seal"].detail
    days = _gate(
        WorldStore(store.root),
        GateOptions(launch=True, signer=fingerprint, planned_days=366, calibration=report),
    )
    assert days["seal"].status == "FAIL" and "plans 365 days" in days["seal"].detail
    holding = _gate(WorldStore(store.root), launch, environ={SEAL_KEY_ENV: key})
    assert holding["seal_key"].status == "FAIL"
    assert _gate(store, environ={SEAL_KEY_ENV: key})["seal_key"].status == "WARN"
    monkeypatch.setattr("sovereign_world.rulehash.engine_hash", lambda root=None: "f" * 64)
    changed = _gate(WorldStore(store.root), launch)
    assert changed["balance"].status == "FAIL" and changed["seal"].status == "FAIL"
    monkeypatch.undo()
    run_days(WorldStore(store.root), 1)
    begun = _gate(WorldStore(store.root), launch)
    assert begun["day"].status == "FAIL" and "day 1" in begun["day"].detail


def test_the_gate_names_the_observers_own_token_variable() -> None:
    assert preflight.TOKEN_ENV == TOKEN_ENV


def test_the_observer_token(tmp_path: Path) -> None:
    store = _store(tmp_path / "run")
    short = _gate(store, environ={TOKEN_ENV: "x" * 10})
    assert short["observer_token"].status == "FAIL"
    good = _gate(store, environ={TOKEN_ENV: "y" * 43})
    assert good["observer_token"].status == "PASS"
    assert good["observer_token"].detail == "43 characters"


def test_preflight_writes_nothing_prints_no_secret_and_drops_the_seal_key(tmp_path: Path) -> None:
    configs = {civ: _config(n) for n, civ in enumerate(CIVS, 1)}
    configs[CIVS[0]] = _config(1, base_url="https://models.example.com/v1", token_env="REMOTE")
    store = _store(tmp_path / "run", configs)

    def stamps() -> dict[str, tuple[bytes, int]]:
        return {
            path.name: (path.read_bytes(), path.stat().st_mtime_ns)
            for path in sorted(store.root.iterdir())
            if path.is_file() and not path.name.endswith("-shm")
        }

    before = stamps()
    result = cli.invoke(
        app,
        ["preflight", str(store.root)],
        env={"REMOTE": "sk-top-secret", SEAL_KEY_ENV: "swseal1:abc", TOKEN_ENV: "t" * 40},
    )
    assert "sk-top-secret" not in result.output and "swseal1:abc" not in result.output
    assert "PASS  keys" in result.output
    assert "WARN  seal_key" in result.output
    assert stamps() == before
    assert os.environ.get(SEAL_KEY_ENV) is None
    as_json = cli.invoke(app, ["preflight", str(store.root), "--json"], env={"REMOTE": "s"})
    document = json.loads(as_json.output)
    assert [check["id"] for check in document["checks"]] == [check_id for check_id, _ in CHECKS]


def _prober(results: dict[str, ProbeResult]) -> Any:
    return lambda civ, config, pin, timeout: results[civ]


def test_the_probe_reports_each_answer(tmp_path: Path) -> None:
    store = _store(tmp_path / "run")
    fine = {civ: ProbeResult(civ, "m", "m", 120, 30, 5) for civ in CIVS}
    checks = _gate(store, GateOptions(probe=True), prober=_prober(fine), dates=lambda hosts: {})
    assert checks["probe"].status == "PASS"
    assert "120 ms" in checks["probe"].detail and "not journaled" in checks["probe"].detail
    fallback = {**fine, CIVS[0]: ProbeResult(CIVS[0], "m", "other-model", 90, 30, 5)}
    checks = _gate(store, GateOptions(probe=True), prober=_prober(fallback), dates=lambda h: {})
    assert checks["probe"].status == "WARN" and "fallback" in checks["probe"].detail
    assert "other-model has no price" in checks["probe"].detail
    broken = {
        **fine,
        CIVS[1]: ProbeResult(
            CIVS[1], "m", error="OpenAIError: Missing credentials sk-live-abc\nmore detail"
        ),
    }
    checks = _gate(
        store,
        GateOptions(probe=True),
        prober=_prober(broken),
        dates=lambda h: {},
        environ={},
    )
    assert checks["probe"].status == "FAIL" and "more detail" not in checks["probe"].detail
    assert scrub("key sk-live-abc refused\nnext", ["sk-live-abc"]) == "key … refused"


def test_a_probe_asks_each_stub_once_and_journals_nothing(tmp_path: Path) -> None:
    stubs = [StubModel() for _ in CIVS]
    try:
        configs = {
            civ: _config(n, base_url=stub.base_url, model="stub-model")
            for n, (civ, stub) in enumerate(zip(CIVS, stubs, strict=True), 1)
        }
        price = Price(input_per_million_usd=1, output_per_million_usd=1)
        store = _store(
            tmp_path / "run",
            configs,
            SpendConfig(max_cost_usd=100, prices={"stub-model": price}),
        )
        journal = store.journal_path.read_bytes()
        result = cli.invoke(app, ["preflight", str(store.root), "--probe"])
        assert "PASS  probe" in result.output, result.output
        expected = prompt_hash(PROBE_SYSTEM, PROBE_USER)
        assert [stub.asked() for stub in stubs] == [[expected]] * 4
        assert store.journal_path.read_bytes() == journal
    finally:
        for stub in stubs:
            stub.close()


def test_hosted_models_are_probed_at_their_sealed_addresses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import anthropic
    import openai

    from sovereign_world.seal import pins_of

    made: list[tuple[str, dict[str, Any]]] = []

    def fake(name: str) -> Any:
        def build(**kwargs: Any) -> object:
            made.append((name, kwargs))
            raise RuntimeError(f"{name} would be called here")

        return build

    monkeypatch.setattr(anthropic, "Anthropic", fake("anthropic"))
    monkeypatch.setattr(openai, "OpenAI", fake("openai"))
    claude = SovereignConfig(provider="anthropic", model="claude-model")
    gpt = SovereignConfig(provider="openai", model="gpt-model")
    manifest = RunManifest.model_validate(
        {
            **RunManifest.new(WorldConfig(seed=1, width=24, height=24), "0.2.0").model_dump(),
            "sovereigns": {"a": claude.model_dump(), "b": gpt.model_dump()},
        }
    )
    pins = pins_of(manifest)
    first = preflight.probe_provider("a", claude, pins["a"], 5.0)
    second = preflight.probe_provider("b", gpt, pins["b"], 5.0)
    assert first.error and second.error
    assert made == [
        ("anthropic", {"max_retries": 2, "base_url": "https://api.anthropic.com"}),
        ("openai", {"max_retries": 2, "base_url": "https://api.openai.com/v1"}),
    ]


def test_host_dates_read_the_date_header() -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response_only(404)
            self.send_header("Date", "Wed, 07 Oct 2026 12:00:00 GMT")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        host = f"127.0.0.1:{server.server_address[1]}"
        dates = host_dates([("http", host), ("http", "127.0.0.1:1")], timeout=2.0)
    finally:
        server.shutdown()
        server.server_close()
    assert dates[host] == 1_791_374_400.0
    assert dates["127.0.0.1:1"] is None
