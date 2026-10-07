"""Sealing a run (sealed trial, slice D): an Ed25519 signature over everything that must not
change once a world is launched, checked by `run` before anything is asked or written."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from sovereign_world import runner
from sovereign_world.cli import app
from sovereign_world.config import RunManifest, SovereignConfig, WorldConfig
from sovereign_world.gateway import factory
from sovereign_world.persistence import WorldStore
from sovereign_world.replay import verify_run
from sovereign_world.rulehash import engine_hash, rule_hash
from sovereign_world.seal import (
    SEAL_KEY_ENV,
    Seal,
    SealRefused,
    check_seal,
    generate_key,
    grouped,
    load_key,
    make_seal,
    normalise_fingerprint,
    pins_of,
    stored_seal,
)
from sovereign_world.state import build_initial_state

cli = CliRunner()


def _store(root: Path, **sovereigns: SovereignConfig) -> WorldStore:
    base = RunManifest.new(WorldConfig(seed=21, width=24, height=24), "0.2.0")
    first = str(sorted(build_initial_state(base).civilizations)[0])
    played = sovereigns or {
        first: SovereignConfig(
            provider="compatible", base_url="http://127.0.0.1:1/v1", model="local", label="ollama"
        )
    }
    manifest = RunManifest.model_validate(
        {**base.model_dump(), "sovereigns": {k: v.model_dump() for k, v in played.items()}}
    )
    return WorldStore.create(root, manifest, build_initial_state(manifest))


def _sealed(root: Path, days: int = 365) -> tuple[WorldStore, str, str]:
    _store(root)
    key, fingerprint = generate_key()
    result = cli.invoke(app, ["seal", str(root), "--days", str(days)], env={SEAL_KEY_ENV: key})
    assert result.exit_code == 0, result.output
    return WorldStore(root), key, fingerprint


def _seal_of(store: WorldStore) -> Seal:
    seal = stored_seal(store)
    assert seal is not None
    return seal


def _rewrite_row(store: WorldStore, table: str, column: str, document: dict[str, Any]) -> None:
    """Change a saved row and recompute its hash, as someone editing the run would."""
    text = (
        RunManifest.model_validate(document).model_dump_json()
        if table == "manifest"
        else json.dumps(document, sort_keys=True, separators=(",", ":"))
    )
    digest = (
        RunManifest.model_validate(document).content_hash()
        if table == "manifest"
        else hashlib.sha256(text.encode()).hexdigest()
    )
    hash_column = "manifest_hash" if table == "manifest" else "seal_hash"
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(f"UPDATE {table} SET {column} = ?, {hash_column} = ?", (text, digest))
        connection.commit()


def test_keygen_prints_a_key_and_its_fingerprint_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = cli.invoke(app, ["keygen"])
    assert result.exit_code == 0
    lines = result.output.splitlines()
    name, value = lines[0].split("=", 1)
    assert name == SEAL_KEY_ENV and value.startswith("swseal1:") and len(value) == 8 + 43
    key = load_key({SEAL_KEY_ENV: value})
    printed = lines[1].removeprefix("fingerprint: ")
    assert (
        normalise_fingerprint(printed)
        == hashlib.sha256(key.public_key().public_bytes_raw()).hexdigest()
    )
    assert list(tmp_path.iterdir()) == []


def test_a_seal_signs_and_verifies_and_names_its_signer(tmp_path: Path) -> None:
    store = _store(tmp_path / "run")
    key_text, fingerprint = generate_key()
    seal = make_seal(
        store.manifest(),
        store.load_checkpoint(),
        planned_days=365,
        key=load_key({SEAL_KEY_ENV: key_text}),
        code_hash=rule_hash(),
        engine_hash=engine_hash(),
    )
    seal.verify_signature()
    assert seal.fingerprint == fingerprint
    assert len(seal.charters) == 4 and len(seal.providers) == 1
    (pin,) = seal.providers.values()
    assert (pin.scheme, pin.host, pin.path) == ("http", "127.0.0.1:1", "/v1")
    _, other = generate_key()
    store.write_seal(seal.model_dump(mode="json"))
    assert check_seal(store, store.manifest(), seal, code_hash=rule_hash()) == []
    check_seal(store, store.manifest(), seal, code_hash=rule_hash(), signer=grouped(fingerprint))
    with pytest.raises(SealRefused, match="sealed by"):
        check_seal(store, store.manifest(), seal, code_hash=rule_hash(), signer=other)
    with pytest.raises(ValueError, match="whole fingerprint"):
        normalise_fingerprint(fingerprint[:16])


@pytest.mark.parametrize(
    "change",
    [
        {"manifest_hash": "0" * 64},
        {"code_hash": "0" * 64},
        {"engine_hash": "0" * 64},
        {"versions": {"engine": "9.9.9", "generator": 3, "rules": 3, "journal_format": 2}},
        {"prompt_version": "council-1"},
        {"reply_schema_hash": "0" * 64},
        {"planned_days": 366},
        {"start_rotation": 1},
        {"run_id": "00000000-0000-0000-0000-000000000000"},
    ],
)
def test_every_sealed_field_is_tamper_evident(tmp_path: Path, change: dict[str, Any]) -> None:
    store, _, _ = _sealed(tmp_path / "run")
    seal = _seal_of(store)
    seal.verify_signature()
    with pytest.raises(SealRefused, match="signature"):
        seal.model_copy(update=change).verify_signature()


def test_a_changed_pin_charter_budget_or_key_breaks_the_signature(tmp_path: Path) -> None:
    store, _, _ = _sealed(tmp_path / "run")
    seal = _seal_of(store)
    civ = next(iter(seal.providers))
    moved = seal.providers[civ].model_copy(update={"host": "evil.example"})
    charters = {**seal.charters, civ: "0" * 64}
    for changed in (
        seal.model_copy(update={"providers": {civ: moved}}),
        seal.model_copy(update={"charters": charters}),
        seal.model_copy(update={"budgets": seal.budgets.model_copy(update={"timeout_seconds": 1})}),
        seal.model_copy(update={"public_key": "11" * 32}),
    ):
        with pytest.raises(SealRefused, match="signature"):
            changed.verify_signature()


def test_the_seal_is_saved_with_the_run_and_in_its_journal_and_the_key_nowhere(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "run")
    key, fingerprint = generate_key()
    result = cli.invoke(app, ["seal", str(store.root), "--days", "365"], env={SEAL_KEY_ENV: key})
    assert result.exit_code == 0, result.output
    assert f"fingerprint: {grouped(fingerprint)}" in result.output
    assert "sealed run" in result.output and "compatible local at http://127.0.0.1:1/v1" in (
        result.output
    )
    records = WorldStore(store.root).read_records()
    assert [record.type for record in records] == ["header", "seal"]
    assert Seal.model_validate(records[1].payload) == _seal_of(WorldStore(store.root))
    secret = key.removeprefix("swseal1:")
    assert secret not in result.output
    for path in store.root.rglob("*"):
        if path.is_file():
            assert secret.encode() not in path.read_bytes(), path
    assert os.environ.get(SEAL_KEY_ENV) is None


def test_only_a_fresh_run_is_sealed_and_only_once(tmp_path: Path) -> None:
    key, _ = generate_key()
    used = _store(tmp_path / "used")
    runner.run_days(used, 1)
    result = cli.invoke(app, ["seal", str(used.root), "--days", "5"], env={SEAL_KEY_ENV: key})
    assert result.exit_code == 1 and "day 1" in result.output
    store, _, _ = _sealed(tmp_path / "run")
    again = cli.invoke(app, ["seal", str(store.root), "--days", "5"], env={SEAL_KEY_ENV: key})
    assert again.exit_code == 1 and "already sealed" in again.output
    missing = _store(tmp_path / "nokey")
    result = cli.invoke(app, ["seal", str(missing.root), "--days", "5"], env={SEAL_KEY_ENV: ""})
    assert result.exit_code == 1 and "is not set" in result.output
    result = cli.invoke(
        app, ["seal", str(missing.root), "--days", "5"], env={SEAL_KEY_ENV: "hunter2"}
    )
    assert result.exit_code == 1 and "hunter2" not in result.output


def test_run_refuses_a_changed_manifest_before_writing_anything(tmp_path: Path) -> None:
    store, _, _ = _sealed(tmp_path / "run")
    document = json.loads(store.manifest().model_dump_json())
    document["budgets"]["timeout_seconds"] = 1.0
    _rewrite_row(store, "manifest", "manifest_json", document)
    before = store.journal_path.read_bytes()
    with pytest.raises(SealRefused, match=r"manifest hash|budgets"):
        runner.run_days(WorldStore(store.root), 1)
    result = cli.invoke(app, ["run", str(store.root), "--days", "1"])
    assert result.exit_code == 4 and "seal refused" in result.output
    assert store.journal_path.read_bytes() == before


def test_run_refuses_a_changed_or_replaced_seal(tmp_path: Path) -> None:
    store, _, _ = _sealed(tmp_path / "run")
    document = _seal_of(store).model_dump(mode="json")
    _rewrite_row(store, "seal", "seal_json", {**document, "planned_days": 9999})
    with pytest.raises(SealRefused, match="signature"):
        runner.run_days(WorldStore(store.root), 1)
    # A seal made anew with another key no longer matches the one in the journal.
    key, _ = generate_key()
    resealed = make_seal(
        store.manifest(),
        store.load_checkpoint(),
        planned_days=9999,
        key=load_key({SEAL_KEY_ENV: key}),
        code_hash=rule_hash(),
        engine_hash=engine_hash(),
    )
    _rewrite_row(store, "seal", "seal_json", resealed.model_dump(mode="json"))
    with pytest.raises(SealRefused, match="journal differs"):
        runner.run_days(WorldStore(store.root), 1)


def test_run_refuses_other_code_and_verify_reports_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _, fingerprint = _sealed(tmp_path / "run")
    runner.run_days(store, 1)
    monkeypatch.setattr(runner, "rule_hash", lambda: "f" * 64)
    with pytest.raises(SealRefused, match="code"):
        runner.run_days(WorldStore(store.root), 1)
    import sovereign_world.cli as cli_module

    monkeypatch.setattr(cli_module, "rule_hash", lambda: "f" * 64)
    result = cli.invoke(app, ["verify", str(store.root), "--signer", fingerprint])
    assert result.exit_code == 0, result.output
    assert "differs from this code" in result.output


def test_a_sealed_run_stops_at_its_planned_days(tmp_path: Path) -> None:
    store, _, _ = _sealed(tmp_path / "run", days=2)
    assert runner.run_days(store, 5).day == 2
    with pytest.raises(SealRefused, match="plans 2 days"):
        runner.run_days(WorldStore(store.root), 1)
    result = cli.invoke(app, ["run", str(store.root), "--days", "1"])
    assert result.exit_code == 4


def test_verify_names_and_pins_the_signer(tmp_path: Path) -> None:
    store, _, fingerprint = _sealed(tmp_path / "run")
    runner.run_days(store, 2)
    ok = cli.invoke(app, ["verify", str(store.root), "--signer", grouped(fingerprint)])
    assert ok.exit_code == 0, ok.output
    assert f"sealed by {grouped(fingerprint)} for 365 days" in ok.output
    assert "matches this code" in ok.output
    _, other = generate_key()
    wrong = cli.invoke(app, ["verify", str(store.root), "--signer", other])
    assert wrong.exit_code == 1 and "sealed by" in wrong.output
    plain = _store(tmp_path / "plain")
    unsealed = cli.invoke(app, ["verify", str(plain.root), "--signer", fingerprint])
    assert unsealed.exit_code == 1 and "not sealed" in unsealed.output


def test_verify_refuses_a_journal_header_naming_another_manifest(tmp_path: Path) -> None:
    store = _store(tmp_path / "run")
    runner.run_days(store, 1)
    document = json.loads(store.manifest().model_dump_json())
    document["budgets"]["timeout_seconds"] = 1.0
    _rewrite_row(store, "manifest", "manifest_json", document)
    with pytest.raises(RuntimeError, match="header"):
        verify_run(WorldStore(store.root))


def test_a_fork_of_a_sealed_run_is_unsealed(tmp_path: Path) -> None:
    store, _, _ = _sealed(tmp_path / "run")
    runner.run_days(store, 2)
    fork = tmp_path / "fork"
    settings = tmp_path / "baseline.toml"
    settings.write_text("")
    result = cli.invoke(
        app, ["fork", str(store.root), str(fork), "--day", "1", "--sovereigns", str(settings)]
    )
    assert result.exit_code == 0, result.output
    forked = WorldStore(fork)
    assert forked.seal_document() is None
    assert "seal" not in [record.type for record in forked.read_records()]
    assert runner.run_days(forked, 1).day == 2


def test_hosted_models_are_reached_at_their_sealed_addresses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import anthropic
    import openai

    made: list[tuple[str, dict[str, Any]]] = []

    def fake(name: str) -> Any:
        def build(**kwargs: Any) -> object:
            made.append((name, kwargs))
            return object()

        return build

    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:9/elsewhere")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:9/elsewhere")
    monkeypatch.setattr(anthropic, "Anthropic", fake("anthropic"))
    monkeypatch.setattr(openai, "OpenAI", fake("openai"))
    claude = SovereignConfig(provider="anthropic", model="claude-model")
    gpt = SovereignConfig(provider="openai", model="gpt-model")
    pins = pins_of(
        RunManifest.model_validate(
            {
                **RunManifest.new(WorldConfig(seed=1, width=24, height=24), "0.2.0").model_dump(),
                "sovereigns": {"a": claude.model_dump(), "b": gpt.model_dump()},
            }
        )
    )
    factory.provider_for("a", claude, base_url=pins["a"].base_url())  # type: ignore[arg-type]
    factory.provider_for("b", gpt, base_url=pins["b"].base_url())  # type: ignore[arg-type]
    factory.provider_for("a", claude)  # type: ignore[arg-type]
    assert made[0] == ("anthropic", {"max_retries": 2, "base_url": "https://api.anthropic.com"})
    assert made[1] == ("openai", {"max_retries": 2, "base_url": "https://api.openai.com/v1"})
    assert made[2] == ("anthropic", {"max_retries": 2})


def test_a_compatible_endpoint_is_pinned_by_scheme_host_and_path() -> None:
    base = RunManifest.new(WorldConfig(seed=1, width=24, height=24), "0.2.0").model_dump()
    configs = {
        "a": SovereignConfig(
            provider="compatible", base_url="http://127.0.0.1:11434/v1/", model="m"
        ),
        "b": SovereignConfig(
            provider="compatible", base_url="https://Example.COM:8443/openai/", model="m"
        ),
    }
    pins = pins_of(
        RunManifest.model_validate(
            {**base, "sovereigns": {key: value.model_dump() for key, value in configs.items()}}
        )
    )
    assert (pins["a"].scheme, pins["a"].host, pins["a"].path) == ("http", "127.0.0.1:11434", "/v1")
    assert pins["b"].base_url() == "https://example.com:8443/openai"
