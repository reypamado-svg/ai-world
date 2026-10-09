"""Start rotation (sealed trial, balance calibration): civilization i can take the generator's
(i + r)-th start, and the whole starting package moves with it, so a start's quality can be told
apart from who holds it. Rotation 0 is the world as generated, with the same hashes."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from sovereign_world.cli import app as cli
from sovereign_world.config import RunManifest, SovereignConfig, WorldConfig
from sovereign_world.persistence import WorldStore
from sovereign_world.state import WorldState, build_initial_state

CONFIG = WorldConfig(seed=21, width=24, height=24)


def _manifest(rotation: int = 0) -> RunManifest:
    base = RunManifest.new(CONFIG, "0.2.0")
    return RunManifest.model_validate({**base.model_dump(), "start_rotation": rotation})


def _package(state: WorldState, index: int) -> tuple[Any, ...]:
    """What civilization number ``index`` starts with, without its ids."""
    civilization = state.civilizations[sorted(state.civilizations)[index]]
    founders = sorted(
        (person.sex, person.age_days, person.health_bp, tuple(sorted(person.skills.items())))
        for person in civilization.population.people.values()
    )
    return (
        civilization.start_center,
        tuple(record.capability for record in civilization.capabilities),
        tuple(founders),
        civilization.known_tiles,
    )


def test_rotation_zero_hashes_as_before() -> None:
    plain = RunManifest.new(CONFIG, "0.2.0")
    zero = RunManifest.model_validate({**plain.model_dump(), "start_rotation": 0})
    assert zero.content_hash() == plain.content_hash()
    assert build_initial_state(zero).model_dump() == build_initial_state(plain).model_dump()


@pytest.mark.parametrize("rotation", [1, 2, 3])
def test_rotating_moves_each_package_whole(rotation: int) -> None:
    plain = build_initial_state(_manifest(0))
    rotated = build_initial_state(_manifest(rotation))
    n = len(plain.civilizations)
    for index in range(n):
        assert _package(rotated, index) == _package(plain, (index + rotation) % n), index
    # The world itself, its sites included, is the same.
    assert rotated.world_map == plain.world_map
    assert rotated.sites == plain.sites


def test_rotation_enters_the_manifest_hash_only_when_set() -> None:
    hashes = {_manifest(rotation).content_hash() for rotation in range(4)}
    assert len(hashes) == 4
    with pytest.raises(ValidationError):
        _manifest(4)


def test_a_sovereign_label_enters_the_hash_only_when_set() -> None:
    base = RunManifest.new(CONFIG, "0.2.0")

    def with_label(label: str) -> RunManifest:
        sovereign = SovereignConfig(
            provider="compatible", model="m", base_url="https://x/v1", label=label
        )
        return RunManifest.model_validate(
            {**base.model_dump(), "sovereigns": {"civilization:0000000001": sovereign}}
        )

    unlabelled = RunManifest.model_validate(
        {
            **base.model_dump(),
            "sovereigns": {
                "civilization:0000000001": SovereignConfig(
                    provider="compatible", model="m", base_url="https://x/v1"
                )
            },
        }
    )
    assert with_label("").content_hash() == unlabelled.content_hash()
    assert with_label("gemini").content_hash() != unlabelled.content_hash()


def test_init_takes_a_start_rotation(tmp_path: Any) -> None:
    runner = CliRunner()
    size = ["--seed", "21", "--width", "24", "--height", "24"]
    plain = runner.invoke(cli, ["init", str(tmp_path / "a"), *size])
    turned = runner.invoke(cli, ["init", str(tmp_path / "b"), *size, "--start-rotation", "1"])
    assert plain.exit_code == 0 and turned.exit_code == 0, (plain.output, turned.output)
    a = WorldStore(tmp_path / "a")
    b = WorldStore(tmp_path / "b")
    assert b.manifest().start_rotation == 1 and a.manifest().start_rotation == 0
    refused = runner.invoke(cli, ["init", str(tmp_path / "c"), *size, "--start-rotation", "4"])
    assert refused.exit_code != 0


def test_a_fork_keeps_its_parents_starts(tmp_path: Any) -> None:
    runner = CliRunner()
    size = ["--seed", "21", "--width", "24", "--height", "24"]
    made = runner.invoke(cli, ["init", str(tmp_path / "a"), *size, "--start-rotation", "2"])
    assert made.exit_code == 0, made.output
    settings = tmp_path / "fork.toml"
    settings.write_text("")
    forked = runner.invoke(
        cli,
        [
            "fork",
            str(tmp_path / "a"),
            str(tmp_path / "b"),
            "--day",
            "0",
            "--sovereigns",
            str(settings),
        ],
    )
    assert forked.exit_code == 0, forked.output
    assert WorldStore(tmp_path / "b").manifest().start_rotation == 2
    verified = runner.invoke(cli, ["verify", str(tmp_path / "b")])
    assert verified.exit_code == 0, verified.output


def test_a_fresh_run_verifies_on_day_zero(tmp_path: Any) -> None:
    """A run checked before its first day (as the launch gate does) passes: its founding
    sightings are in map order, which the checks accept on day 0 only."""
    runner = CliRunner()
    made = runner.invoke(
        cli, ["init", str(tmp_path / "a"), "--seed", "21", "--width", "24", "--height", "24"]
    )
    assert made.exit_code == 0, made.output
    verified = runner.invoke(cli, ["verify", str(tmp_path / "a")])
    assert verified.exit_code == 0, verified.output
