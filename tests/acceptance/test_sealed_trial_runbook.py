"""The sealed trial's runbook is the code's (sealed trial, slices F and G): every command and
option it tells the user to type exists, it sets only variables the run, Claude Code or Ollama
read, no API key is ever set, and its example settings make the free three-provider world:
Claude Code, Codex and a local model, priced at nothing, with a token cap."""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import typer.main

from sovereign_world.cli import _settings, app
from sovereign_world.config import ENGINE_VERSION, RunManifest, WorldConfig
from sovereign_world.observer import TOKEN_ENV
from sovereign_world.preflight import SIGN_IN_HIJACKERS
from sovereign_world.seal import SEAL_KEY_ENV, pins_of
from sovereign_world.spend import worst_case_round
from sovereign_world.state import build_initial_state

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "sealed-trial-runbook.md"
CALIBRATION = ROOT / "src" / "sovereign_world" / "calibration" / "__main__.py"
BLOCK = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)
REMOVED = {*(name for names in SIGN_IN_HIJACKERS.values() for name in names), "OPENAI_API_KEY"}
"""Variables the runbook tells the user to remove: they would replace a program's sign-in."""
KNOWN_ENV = {
    SEAL_KEY_ENV,
    TOKEN_ENV,
    "PYTHONUTF8",
    "OLLAMA_CONTEXT_LENGTH",
    "OLLAMA_KV_CACHE_TYPE",
    "DISABLE_AUTOUPDATER",
    *REMOVED,
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
    assert {SEAL_KEY_ENV, "DISABLE_AUTOUPDATER", "OLLAMA_CONTEXT_LENGTH"} <= names
    removed = set(re.findall(r"Remove-Item ([^\n]+)", text))
    assert any(all(f"Env:{name}" in line for name in REMOVED) for line in removed)


def test_no_api_key_is_ever_set_and_the_seal_key_only_masked() -> None:
    for line in "\n".join(_blocks("powershell")).splitlines():
        match = re.match(r"\$env:([A-Z_0-9]+)\s*=\s*(.*)", line.strip())
        if match:
            assert match.group(1) in {"PYTHONUTF8", SEAL_KEY_ENV}, line
            if match.group(1) == SEAL_KEY_ENV:
                assert match.group(2).startswith("Read-Host -MaskInput"), line
        set_for_user = re.search(r'SetEnvironmentVariable\("([A-Z_0-9]+)"', line)
        assert not (set_for_user and "KEY" in set_for_user.group(1)), line


def test_its_settings_make_the_free_three_provider_world(tmp_path: Path) -> None:
    blocks = [block for block in _blocks("toml") if "[sovereigns." in block]
    assert len(blocks) == 1
    path = tmp_path / "trial.toml"
    path.write_text(blocks[0], encoding="utf-8")
    settings = _settings(str(path))
    base = RunManifest.new(
        WorldConfig(seed=21, width=32, height=32, civilizations=3, council_interval_days=28),
        ENGINE_VERSION,
    )
    manifest = RunManifest.model_validate({**base.model_dump(), **settings})
    civilizations = {str(civ) for civ in build_initial_state(manifest).civilizations}
    assert set(manifest.sovereigns) == civilizations and len(civilizations) == 3
    assert {config.provider for config in manifest.sovereigns.values()} == {
        "claude-code",
        "codex",
        "compatible",
    }
    pins = pins_of(manifest)
    assert len({(pin.kind, pin.host) for pin in pins.values()}) == 3
    assert all(pin.token_env is None for pin in pins.values())
    spend = manifest.spend
    assert spend is not None and spend.max_cost_usd is None
    assert spend.max_input_tokens and spend.max_output_tokens
    models = {config.model for config in manifest.sovereigns.values()}
    assert models <= set(spend.prices)
    assert all(
        price.input_per_million_usd == price.output_per_million_usd == 0
        for price in spend.prices.values()
    )
    worst = worst_case_round(manifest)
    assert worst.cost_usd == 0 and worst.input_tokens > 0
    assert spend.max_input_tokens // worst.input_tokens >= 14
    assert spend.max_output_tokens // worst.output_tokens >= 14


def test_the_world_and_calibration_it_makes_have_three_civilizations() -> None:
    commands = _commands()
    inits = [words for words in commands if "init" in words and "sovereign-world.exe" in words[1]]
    assert inits and all(words[words.index("--civilizations") + 1] == "3" for words in inits)
    runs = [words for words in commands if "sovereign_world.calibration" in words]
    made = [words for words in runs if "run" in words]
    assert made and all(words[words.index("--civilizations") + 1] == "3" for words in made)


def test_the_documents_it_points_to_exist() -> None:
    for target in re.findall(r"\]\(([^)#]+\.md)\)", DOC.read_text()):
        assert (DOC.parent / target).exists(), target
