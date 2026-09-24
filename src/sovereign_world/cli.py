"""Read-only-after-launch command line interface for the Rules Laboratory."""

from __future__ import annotations

from pathlib import Path

import typer

from sovereign_world.config import RunManifest, WorldConfig
from sovereign_world.engine import advance_day
from sovereign_world.persistence import WorldStore
from sovereign_world.replay import replay_run, verify_run
from sovereign_world.rng import StableRng
from sovereign_world.scripted import BaselineSovereign
from sovereign_world.state import build_initial_state, state_hash, validate_world

app = typer.Typer(
    help="Create, run, inspect, checkpoint, replay, and verify a sovereign world.",
    no_args_is_help=True,
)


@app.command("init")
def initialize(
    directory: Path,
    seed: int = typer.Option(..., help="Deterministic world seed."),
    width: int = typer.Option(24, min=24),
    height: int = typer.Option(24, min=24),
) -> None:
    """Create a locked manifest and day-zero checkpoint."""
    config = WorldConfig(seed=seed, width=width, height=height)
    manifest = RunManifest.new(config=config, engine_version="0.1.0")
    state = build_initial_state(manifest)
    WorldStore.create(directory, manifest, state)
    typer.echo(f"initialized day 0 at {directory} ({state_hash(state)})")


@app.command()
def run(
    directory: Path,
    days: int = typer.Option(..., min=1, help="Number of daily ticks to advance."),
) -> None:
    """Advance the latest verified state under scripted sovereigns."""
    store = WorldStore(directory)
    manifest = store.manifest()
    state = replay_run(store)
    rng = StableRng(manifest.config.seed)
    sovereigns = {
        civilization_id: BaselineSovereign() for civilization_id in state.civilizations
    }
    for _ in range(days):
        transition = advance_day(state, rng, sovereigns=sovereigns)
        state = transition.state
        store.append_transition(state, transition.events)
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
    typer.echo(
        f"verified through day {result.verified_through_day} "
        f"({result.state_hash}, {result.records} records)"
    )
