"""The sealed trial's runbook is the code's (sealed trial, slice F): every command and option it
tells the user to type exists, every environment variable it sets is one the run or Ollama
reads, and its example settings make a four-provider world with every model priced and a cap."""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import typer.main

from sovereign_world.cli import _settings, app
from sovereign_world.config import ENGINE_VERSION, RunManifest, WorldConfig
from sovereign_world.observer import TOKEN_ENV
from sovereign_world.seal import HOSTED_TOKEN_ENVS, SEAL_KEY_ENV, pins_of
from sovereign_world.spend import worst_case_round
from sovereign_world.state import build_initial_state

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "sealed-trial-runbook.md"
CALIBRATION = ROOT / "src" / "sovereign_world" / "calibration" / "__main__.py"
BLOCK = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)
KNOWN_ENV = {
    *HOSTED_TOKEN_ENVS.values(),
    "GEMINI_API_KEY",
    SEAL_KEY_ENV,
    TOKEN_ENV,
    "PYTHONUTF8",
    "OLLAMA_CONTEXT_LENGTH",
}


def _blocks(language: str) -> list[str]:
    return [body for lang, body in BLOCK.findall(DOC.read_text()) if lang == language]


def _commands() -> list[list[str]]:
    lines = [line for block in _blocks("powershell") for line in block.splitlines()]
    return [shlex.split(line.replace("\\", "/")) for line in lines if line.strip()]


def test_every_command_and_option_the_runbook_names_exists() -> None:
    group = typer.main.get_command(app)
    commands = getattr(group, "commands", {})
    used: set[str] = set()
    for words in _commands():
        if "&" not in words or not any(word.endswith("sovereign-world.exe") for word in words):
            continue
        name = words[words.index(next(w for w in words if w.endswith("sovereign-world.exe"))) + 1]
        if name.startswith("--"):
            continue
        assert name in commands, f"the runbook names no such command: {name}"
        used.add(name)
        known = {opt for param in commands[name].params for opt in param.opts}
        for word in words:
            if word.startswith("--"):
                assert word in known, f"{name} has no option {word}"
    expected = {"init", "preflight", "spend", "run", "councils", "verify", "keygen", "seal"}
    assert expected | {"observe"} <= used


def test_the_calibration_commands_it_names_exist() -> None:
    source = CALIBRATION.read_text()
    named = [words for words in _commands() if "sovereign_world.calibration" in words]
    assert named
    for words in named:
        subcommand = words[words.index("sovereign_world.calibration") + 1]
        assert f'add_parser("{subcommand}"' in source
        for word in words:
            if word.startswith("--"):
                assert f'"{word}"' in source, f"the calibration has no option {word}"


def test_it_sets_only_variables_the_run_or_ollama_reads() -> None:
    text = "\n".join(_blocks("powershell"))
    names = set(re.findall(r"\$env:([A-Z_0-9]+)", text))
    names |= set(re.findall(r"Env:([A-Z_0-9]+)", text))
    names |= set(re.findall(r'SetEnvironmentVariable\("([A-Z_0-9]+)"', text))
    assert names
    assert names <= KNOWN_ENV, sorted(names - KNOWN_ENV)
    assert {"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", SEAL_KEY_ENV} <= names


def test_keys_are_only_ever_typed_in_masked() -> None:
    for line in "\n".join(_blocks("powershell")).splitlines():
        match = re.match(r"\$env:([A-Z_0-9]+)\s*=\s*(.*)", line.strip())
        if match and match.group(1) not in {"PYTHONUTF8"}:
            assert match.group(2).startswith("Read-Host -MaskInput"), line


def test_its_settings_make_a_four_provider_world_with_a_cap(tmp_path: Path) -> None:
    blocks = [block for block in _blocks("toml") if "[sovereigns." in block]
    assert len(blocks) == 1
    path = tmp_path / "trial.toml"
    path.write_text(blocks[0], encoding="utf-8")
    settings = _settings(str(path))
    base = RunManifest.new(
        WorldConfig(seed=21, width=24, height=24, council_interval_days=28), ENGINE_VERSION
    )
    manifest = RunManifest.model_validate({**base.model_dump(), **settings})
    civilizations = {str(civ) for civ in build_initial_state(manifest).civilizations}
    assert set(manifest.sovereigns) == civilizations
    pins = pins_of(manifest)
    assert len(pins) == 4
    assert len({(pin.kind, pin.host) for pin in pins.values()}) == 4
    assert {config.label for config in manifest.sovereigns.values()} >= {"gemini", "ollama"}
    gemini = next(c for c in manifest.sovereigns.values() if c.label == "gemini")
    assert gemini.token_env == "GEMINI_API_KEY"
    spend = manifest.spend
    assert spend is not None and spend.max_cost_usd
    assert {config.model for config in manifest.sovereigns.values()} <= set(spend.prices)
    assert worst_case_round(manifest).cost_usd > 0


def test_the_documents_it_points_to_exist() -> None:
    for target in re.findall(r"\]\(([^)#]+\.md)\)", DOC.read_text()):
        assert (DOC.parent / target).exists(), target
