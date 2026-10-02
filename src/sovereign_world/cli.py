"""Read-only-after-launch command line interface for the Rules Laboratory."""

from __future__ import annotations

import tomllib
from pathlib import Path
from uuid import uuid4

import typer

from sovereign_world.config import (
    CURRENT_GENERATOR,
    BudgetConfig,
    RunManifest,
    SovereignConfig,
    WorldConfig,
)
from sovereign_world.engine import advance_day
from sovereign_world.gateway.factory import build_sovereigns
from sovereign_world.gateway.records import journal_councils, recorded_councils
from sovereign_world.persistence import WorldStore
from sovereign_world.replay import rederive_run, replay_run, verify_run
from sovereign_world.rng import StableRng
from sovereign_world.state import build_initial_state, state_hash, validate_world

app = typer.Typer(
    help="Create, run, inspect, checkpoint, replay, and verify a sovereign world.",
    no_args_is_help=True,
)


def _settings(path: str | None) -> dict[str, object]:
    """Sovereign assignments and budgets from a TOML file, checked before they are frozen."""
    if path is None:
        return {}
    data = tomllib.loads(Path(path).read_text())
    unknown = set(data) - {"sovereigns", "budgets"}
    if unknown:
        raise typer.BadParameter(f"unknown settings: {sorted(unknown)}")
    sovereigns = {
        str(civilization_id): SovereignConfig.model_validate(entry)
        for civilization_id, entry in dict(data.get("sovereigns", {})).items()
    }
    return {
        "sovereigns": sovereigns,
        "budgets": BudgetConfig.model_validate(data.get("budgets", {})),
    }


@app.command("init")
def initialize(
    directory: Path,
    seed: int = typer.Option(..., help="Deterministic world seed."),
    width: int = typer.Option(24, min=24),
    height: int = typer.Option(24, min=24),
    sovereigns: str | None = typer.Option(
        None, help="TOML file of sovereign assignments and budgets, frozen for the run."
    ),
) -> None:
    """Create a locked manifest and day-zero checkpoint."""
    config = WorldConfig(seed=seed, width=width, height=height)
    manifest = RunManifest.model_validate(
        {
            "run_id": uuid4(),
            "engine_version": "0.2.0",
            "config": config,
            "generator_version": CURRENT_GENERATOR,
            **_settings(sovereigns),
        }
    )
    state = build_initial_state(manifest)
    unknown = set(manifest.sovereigns) - {str(item) for item in state.civilizations}
    if unknown:
        raise typer.BadParameter(f"no such civilizations: {sorted(unknown)}")
    WorldStore.create(directory, manifest, state)
    typer.echo(f"initialized day 0 at {directory} ({state_hash(state)})")


@app.command()
def run(
    directory: Path,
    days: int = typer.Option(..., min=1, help="Number of daily ticks to advance."),
) -> None:
    """Advance the latest verified state under the run's sovereigns, recording every council."""
    store = WorldStore(directory)
    manifest = store.manifest()
    state = replay_run(store)
    rng = StableRng(manifest.config.seed)
    sovereigns = build_sovereigns(manifest, state.civilizations, history=recorded_councils(store))
    for _ in range(days):
        transition = advance_day(state, rng, sovereigns=sovereigns)
        state = transition.state
        store.append_transition(state, transition.events)
        journal_councils(store, sovereigns.values())
    store.save_checkpoint(state)
    typer.echo(f"advanced to day {state.day} ({state_hash(state)})")


@app.command()
def inspect(directory: Path) -> None:
    """Print a non-mutating summary of the latest verified state."""
    state = replay_run(WorldStore(directory))
    typer.echo(f"day: {state.day}")
    typer.echo(f"state hash: {state_hash(state)}")
    for civilization_id in sorted(state.civilizations):
        civilization = state.civilizations[civilization_id]
        typer.echo(
            "civilization: "
            f"{civilization_id} living={len(civilization.population.living_ids)} "
            f"dead={len(civilization.population.dead_ids)} "
            f"projects={len(civilization.projects)}"
        )


@app.command()
def checkpoint(directory: Path) -> None:
    """Save a verified snapshot of the latest journal state."""
    store = WorldStore(directory)
    state = replay_run(store)
    validate_world(state)
    store.save_checkpoint(state)
    typer.echo(f"checkpoint saved at day {state.day}")


@app.command()
def replay(
    directory: Path,
    day: int | None = typer.Option(None, min=0, help="Target day; latest by default."),
) -> None:
    """Reconstruct a historical state without changing the live run."""
    state = replay_run(WorldStore(directory), target_day=day)
    typer.echo(f"replayed day {state.day} ({state_hash(state)})")


@app.command()
def verify(directory: Path) -> None:
    """Verify manifest, journal hashes, replay, checkpoint, and invariants."""
    store = WorldStore(directory)
    store.manifest()
    result = verify_run(store)
    replayed = replay_run(store, target_day=result.verified_through_day)
    validate_world(replayed)
    if state_hash(replayed) != result.state_hash:
        raise typer.Exit(code=1)
    checkpoint_state = store.load_checkpoint()
    validate_world(checkpoint_state)
    if recorded_councils(store):
        # The recorded councils alone, with no model called, must give the same world.
        rederived = rederive_run(store)
        if rederived.state_hash != result.state_hash:
            raise typer.Exit(code=1)
    typer.echo(
        f"verified through day {result.verified_through_day} "
        f"({result.state_hash}, {result.records} records)"
    )


@app.command()
def fork(
    source: Path,
    directory: Path,
    day: int = typer.Option(..., min=0, help="The day of the source run to fork from."),
    sovereigns: str = typer.Option(..., help="TOML file of the fork's sovereigns and budgets."),
) -> None:
    """Start a new, separately identified run from a past day with different sovereigns."""
    store = WorldStore(source)
    parent = store.manifest()
    state = replay_run(store, target_day=day)
    manifest = RunManifest.model_validate(
        {
            "run_id": uuid4(),
            "engine_version": parent.engine_version,
            "config": parent.config,
            "parent_run_id": parent.run_id,
            "forked_at_day": state.day,
            "generator_version": parent.generator_version,
            **_settings(sovereigns),
        }
    )
    state = state.model_copy(
        update={"run_id": manifest.run_id, "manifest_hash": manifest.content_hash()}
    )
    WorldStore.create(directory, manifest, state)
    typer.echo(f"forked {parent.run_id} at day {state.day} into {manifest.run_id}")
