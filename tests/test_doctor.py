"""The readiness check (sealed trial, slice J): `sovereign-world doctor` looks over the computer
before the trial, line by line like the launch gate, writes nothing and shows no variable's
value. The stand-in `claude` and `codex` and a stand-in Ollama answer as the real ones do."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest
from fake_cli import install_fakes
from stub_model import StubModel
from typer.testing import CliRunner

from sovereign_world.cli import app
from sovereign_world.config import SovereignConfig
from sovereign_world.doctor import DOCTOR_CHECKS, machine_checks
from sovereign_world.preflight import SIGN_IN_HIJACKERS, Check

ROOMY = (500 * 2**30, 100 * 2**30, 400 * 2**30)
HIJACKERS = {name for names in SIGN_IN_HIJACKERS.values() for name in names}
SECRET = "sk-doctor-secret-value"


@pytest.fixture
def ollama() -> Iterator[StubModel]:
    stub = StubModel(pulled=("gemma3:12b",))
    try:
        yield stub
    finally:
        stub.close()


@pytest.fixture
def fakes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in HIJACKERS:
        monkeypatch.delenv(name, raising=False)
    return install_fakes(tmp_path, monkeypatch)


def _sovereigns(stub: StubModel) -> dict[str, SovereignConfig]:
    return {
        "civ-0001": SovereignConfig(provider="claude-code", model="sonnet", label="claude"),
        "civ-0002": SovereignConfig(provider="codex", model="gpt-5", label="chatgpt"),
        "civ-0003": SovereignConfig(
            provider="compatible", model="gemma3:12b", base_url=stub.base_url, label="ollama"
        ),
    }


def _which(name: str) -> str | None:
    return {"uv": "/usr/bin/uv", "nvidia-smi": "/usr/bin/nvidia-smi", "pwsh": "/usr/bin/pwsh"}.get(
        name
    )


def _run(outputs: dict[str, str]) -> object:
    def run(args: Sequence[str]) -> tuple[int, str]:
        key = Path(args[0]).name if args[0] != "powercfg" else f"powercfg {args[-1]}"
        return (0, outputs[key]) if key in outputs else (1, "")

    return run


LINUX_OUTPUTS = {"uv": "uv 0.8.17", "nvidia-smi": "NVIDIA GeForce RTX 4080, 16376"}
WINDOWS_OUTPUTS = {
    **LINUX_OUTPUTS,
    "pwsh": "7.4.6",
    "powercfg STANDBYIDLE": "    Current AC Power Setting Index: 0x00000000\n",
    "powercfg HIBERNATEIDLE": "    Current AC Power Setting Index: 0x00000000\n",
}


def _checks(stub: StubModel, **overrides: object) -> dict[str, Check]:
    environ = {
        "PATH": os.environ["PATH"],
        "OLLAMA_CONTEXT_LENGTH": "32768",
        "DISABLE_AUTOUPDATER": "1",
    }
    arguments: dict[str, object] = {
        "environ": environ,
        "which": _which,
        "run": _run(LINUX_OUTPUTS),
        "disk_usage": lambda path: ROOMY,
        "platform": "linux",
    }
    arguments.update(overrides)
    root = arguments.pop("root", Path("/srv/ai-world"))
    sovereigns = arguments.pop("sovereigns", _sovereigns(stub))
    checks = machine_checks(root, sovereigns, **arguments)  # type: ignore[arg-type]
    assert [check.id for check in checks] == [check_id for check_id, _ in DOCTOR_CHECKS]
    return {check.id: check for check in checks}


def test_a_ready_computer_passes(fakes: Path, ollama: StubModel) -> None:
    checks = _checks(ollama)
    statuses = {check_id: check.status for check_id, check in checks.items()}
    assert statuses == {
        "python": "PASS",
        "uv": "PASS",
        "powershell": "SKIP",
        "path": "PASS",
        "drive": "SKIP",
        "claude": "PASS",
        "codex": "PASS",
        "keys": "PASS",
        "autoupdater": "PASS",
        "ollama": "PASS",
        "ollama_context": "PASS",
        "gpu": "PASS",
        "disk": "PASS",
        "power": "SKIP",
        "clock": "PASS",
    }
    assert "signed in with claude.ai" in checks["claude"].detail
    assert "ChatGPT" in checks["codex"].detail
    assert "gemma3:12b pulled" in checks["ollama"].detail
    assert checks["gpu"].detail.startswith("NVIDIA GeForce RTX 4080")
    assert checks["python"].title == "Python 3.12 or later"


def test_each_problem_is_named(fakes: Path, ollama: StubModel) -> None:
    base = {
        "PATH": os.environ["PATH"],
        "OLLAMA_CONTEXT_LENGTH": "32768",
        "DISABLE_AUTOUPDATER": "1",
    }
    # Not pulled, Ollama down, a short context, no context, no updater setting.
    ollama.pulled = ("llama3:8b",)
    assert _checks(ollama)["ollama"].status == "FAIL"
    assert "ollama pull gemma3:12b" in _checks(ollama)["ollama"].detail
    down = _checks(ollama, ollama_url="http://127.0.0.1:9")["ollama"]
    assert down.status == "FAIL" and "start it" in down.detail
    short = _checks(ollama, environ={**base, "OLLAMA_CONTEXT_LENGTH": "8192"})
    assert short["ollama_context"].status == "FAIL"
    unset = _checks(ollama, environ={"PATH": base["PATH"]})
    assert unset["ollama_context"].status == "FAIL" and unset["autoupdater"].status == "WARN"
    # A variable that would replace a sign-in: named, its value never shown.
    hijacked = _checks(ollama, environ={**base, "ANTHROPIC_API_KEY": SECRET})["keys"]
    assert hijacked.status == "WARN" and "ANTHROPIC_API_KEY" in hijacked.detail
    assert SECRET not in hijacked.detail
    # Signed out, and a program missing.
    signed_out = _checks(ollama, environ={**base, "FAKE_CLI_MODE": "signedout"})
    assert signed_out["claude"].status == "FAIL" and signed_out["codex"].status == "FAIL"
    (fakes.parent / "bin" / "codex").unlink()
    assert _checks(ollama)["codex"].status == "FAIL"
    # No GPU, a full disk, a clock in the past, a path in OneDrive or with spaces.
    assert _checks(ollama, which=lambda name: None)["gpu"].status == "WARN"
    assert _checks(ollama, disk_usage=lambda path: (1, 1, 2**30))["disk"].status == "FAIL"
    assert _checks(ollama, now=lambda: 0.0)["clock"].status == "FAIL"
    onedrive = Path("/home/me/OneDrive/ai-world")
    assert _checks(ollama, root=onedrive)["path"].status == "FAIL"
    assert _checks(ollama, root=Path("/home/me/my projects/ai-world"))["path"].status == "WARN"
    # Programs left out of the settings are skipped.
    only_local = {"civ-0001": _sovereigns(ollama)["civ-0003"]}
    skipped = _checks(ollama, sovereigns=only_local)
    assert skipped["claude"].status == skipped["codex"].status == "SKIP"


def test_the_windows_checks(fakes: Path, ollama: StubModel) -> None:
    ready = _checks(
        ollama, platform="win32", run=_run(WINDOWS_OUTPUTS), drive=lambda path: (True, "NTFS")
    )
    assert ready["powershell"].status == "PASS" and ready["powershell"].detail == "7.4.6"
    assert ready["drive"].status == "PASS" and ready["power"].status == "PASS"
    sleepy = {
        **WINDOWS_OUTPUTS,
        "pwsh": "5.1.22621",
        "powercfg STANDBYIDLE": "    Current AC Power Setting Index: 0x00000708\n",
    }
    checks = _checks(ollama, platform="win32", run=_run(sleepy), drive=lambda path: (True, "exFAT"))
    assert checks["powershell"].status == "FAIL"
    assert checks["drive"].status == "FAIL" and "exFAT" in checks["drive"].detail
    assert checks["power"].status == "WARN" and "standby" in checks["power"].detail
    removable = _checks(
        ollama, platform="win32", run=_run(WINDOWS_OUTPUTS), drive=lambda path: (False, "NTFS")
    )
    assert removable["drive"].status == "FAIL"


def test_the_command_writes_nothing_and_shows_no_value(
    fakes: Path, ollama: StubModel, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = tmp_path / "trial.toml"
    settings.write_text(
        '[sovereigns."civ-0001"]\nprovider = "claude-code"\nmodel = "sonnet"\n'
        '[sovereigns."civ-0002"]\nprovider = "codex"\nmodel = "gpt-5"\n'
        f'[sovereigns."civ-0003"]\nprovider = "compatible"\nmodel = "gemma3:12b"\n'
        f'base_url = "{ollama.base_url}"\n',
        encoding="utf-8",
    )
    folder = tmp_path / "repo"
    folder.mkdir()
    monkeypatch.chdir(folder)
    monkeypatch.setenv("CODEX_API_KEY", SECRET)
    monkeypatch.setenv("OLLAMA_CONTEXT_LENGTH", "32768")
    result = CliRunner().invoke(app, ["doctor", "--settings", str(settings), "--json"])
    document = json.loads(result.output)
    assert [check["id"] for check in document["checks"]] == [i for i, _ in DOCTOR_CHECKS]
    keys = next(check for check in document["checks"] if check["id"] == "keys")
    assert keys["status"] == "WARN" and "CODEX_API_KEY" in keys["detail"]
    assert SECRET not in result.output
    assert list(folder.iterdir()) == []
    plain = CliRunner().invoke(app, ["doctor", "--settings", str(settings)])
    assert "WARN  keys" in plain.output and SECRET not in plain.output
    assert plain.exit_code == (1 if not document["passed"] else 0)
