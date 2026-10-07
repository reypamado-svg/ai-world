"""Read-only-after-launch command line interface for the Rules Laboratory."""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path
from uuid import uuid4

import typer

from sovereign_world.config import (
    CURRENT_GENERATOR,
    CURRENT_JOURNAL_FORMAT,
    CURRENT_RULES,
    ENGINE_VERSION,
    BudgetConfig,
    RunManifest,
    SovereignConfig,
    SpendConfig,
    WorldConfig,
)
from sovereign_world.gateway.records import ResumeRefused, recorded_councils
from sovereign_world.persistence import WorldStore
from sovereign_world.preflight import GateOptions, launch_gate, offline_checks, render
from sovereign_world.preflight import passed as gate_passed
from sovereign_world.replay import replay_run, verify_whole
from sovereign_world.rulehash import engine_hash, rule_hash
from sovereign_world.runner import (
    RunControl,
    SpendCapReached,
    controlled,
    interruptible,
    run_days,
)
from sovereign_world.seal import (
    SEAL_KEY_ENV,
    SealKeyInvalid,
    SealKeyMissing,
    SealRefused,
    check_seal,
    generate_key,
    grouped,
    load_key,
    make_seal,
    stored_seal,
)
from sovereign_world.spend import Tally, prompt_round, stop_reason, tally, worst_case_round
from sovereign_world.state import build_initial_state, validate_world

app = typer.Typer(
    help="Create, run, inspect, checkpoint, replay, and verify a sovereign world.",
    no_args_is_help=True,
)


def _settings(path: str | None) -> dict[str, object]:
    """Sovereign assignments and budgets from a TOML file, checked before they are frozen."""
    if path is None:
        return {}
    data = tomllib.loads(Path(path).read_text())
    unknown = set(data) - {"sovereigns", "budgets", "spend"}
    if unknown:
        raise typer.BadParameter(f"unknown settings: {sorted(unknown)}")
    sovereigns = {
        str(civilization_id): SovereignConfig.model_validate(entry)
        for civilization_id, entry in dict(data.get("sovereigns", {})).items()
    }
    spend = data.get("spend")
    return {
        "sovereigns": sovereigns,
        **({"spend": SpendConfig.model_validate(spend)} if spend is not None else {}),
        "budgets": BudgetConfig.model_validate(data.get("budgets", {})),
    }


@app.command("init")
def initialize(
    directory: Path,
    seed: int = typer.Option(..., help="Deterministic world seed."),
    width: int = typer.Option(100, min=24, help="Tiles across; each is 25 km."),
    height: int = typer.Option(100, min=24, help="Tiles down; each is 25 km."),
    civilizations: int = typer.Option(4, min=2, max=4, help="Civilizations in the world, 2 to 4."),
    sovereigns: str | None = typer.Option(
        None, help="TOML file of sovereign assignments and budgets, frozen for the run."
    ),
    start_rotation: int = typer.Option(
        0,
        min=0,
        help=(
            "Rotate the starts: civilization i takes the generator's (i + N)-th start, with its"
            " whole starting package. 0 is the world as generated."
        ),
    ),
) -> None:
    """Create a locked manifest and day-zero checkpoint."""
    config = WorldConfig(seed=seed, width=width, height=height, civilizations=civilizations)
    if start_rotation >= civilizations:
        raise typer.BadParameter("--start-rotation must be below the number of civilizations")
    manifest = RunManifest.model_validate(
        {
            "run_id": uuid4(),
            "engine_version": ENGINE_VERSION,
            "config": config,
            "generator_version": CURRENT_GENERATOR,
            "rules_version": CURRENT_RULES,
            "journal_format": CURRENT_JOURNAL_FORMAT,
            "start_rotation": start_rotation,
            **_settings(sovereigns),
        }
    )
    state = build_initial_state(manifest)
    unknown = set(manifest.sovereigns) - {str(item) for item in state.civilizations}
    if unknown:
        raise typer.BadParameter(f"no such civilizations: {sorted(unknown)}")
    WorldStore.create(directory, manifest, state)
    typer.echo(f"initialized day 0 at {directory} ({WorldStore(directory).state_hash(state)})")


@app.command()
def run(
    directory: Path,
    days: int = typer.Option(..., min=1, help="Number of daily ticks to advance."),
    controlled_run: bool = typer.Option(
        False,
        "--controlled",
        help=(
            "Start paused and take 'pause' and 'resume' lines on standard input, reporting each"
            " saved day on standard output (as 'observe --run-days' does); the end of the input"
            " stops the run after the day in progress."
        ),
    ),
    spend_limit: float | None = typer.Option(
        None,
        "--spend-limit",
        min=0,
        help=(
            "Stop before any day that could take the run's model spending past this many US"
            " dollars. It may lower the run's own cap for this session, never raise it."
        ),
    ),
) -> None:
    """Advance the latest verified state under the run's sovereigns, recording every council.

    A run with a spending cap stops cleanly, with a checkpoint and exit code 3, before any day
    whose councils could take it past the cap. Ctrl+C stops it after the day under way. After a
    crash or a kill, running it again carries on from what was saved, asking no model again
    about a council already saved. A sealed run is refused (exit code 4) if it no longer
    matches its seal, and stops at its planned days."""
    # Only `seal` uses the seal key; nothing that runs a world keeps it.
    os.environ.pop(SEAL_KEY_ENV, None)
    store = WorldStore(directory)
    control = RunControl(paused=controlled_run)
    try:
        with interruptible(control):
            if controlled_run:
                controlled(store, days, spend_limit_usd=spend_limit, control=control)
                return
            state = run_days(store, days, control=control, spend_limit_usd=spend_limit)
    except SpendCapReached as stop:
        typer.echo(f"stopped before day {stop.state.day + 1}: {stop.reason}", err=True)
        raise typer.Exit(3) from None
    except ResumeRefused as refused:
        typer.echo(f"cannot resume: {refused}", err=True)
        raise typer.Exit(1) from None
    except SealRefused as refused:
        typer.echo(f"seal refused: {refused}", err=True)
        raise typer.Exit(4) from None
    verb = "stopped at" if control.stopped else "advanced to"
    typer.echo(f"{verb} day {state.day} ({store.state_hash(state)})")


def _spend_lines(title: str, total: Tally) -> list[str]:
    lines = [
        f"{title}: ${total.cost_usd:,.4f}, {total.input_tokens:,} tokens in,"
        f" {total.output_tokens:,} out, {total.councils} councils"
    ]
    for label, table in (("civilization", total.by_civilization), ("model", total.by_model)):
        for key, (tokens_in, tokens_out, cost) in sorted(table.items()):
            lines.append(
                f"  {label} {key}: ${cost:,.4f}, {int(tokens_in):,} in, {int(tokens_out):,} out"
            )
    return lines


@app.command()
def spend(
    directory: Path,
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Also size a council round from the latest day's real prompts; no model is asked.",
    ),
) -> None:
    """What the run's councils have spent on models, what remains of its cap, and how many
    worst-case council rounds that covers. Never asks a model, never writes."""
    store = WorldStore(directory)
    manifest = store.manifest()
    spent = tally(recorded_councils(store), manifest.spend)
    for line in _spend_lines("spent", spent):
        typer.echo(line)
    if spent.unpriced:
        unpriced = ", ".join(sorted(spent.unpriced))
        typer.echo(f"unpriced models (charged at the table's highest rate): {unpriced}")
    worst = worst_case_round(manifest)
    for line in _spend_lines("worst-case council round", worst):
        typer.echo(line)
    cap = manifest.spend.max_cost_usd if manifest.spend is not None else None
    if cap is None:
        typer.echo("cost cap: none")
    else:
        remaining = max(0.0, cap - spent.cost_usd)
        rounds = int(remaining // worst.cost_usd) if worst.cost_usd > 0 else None
        typer.echo(
            f"cost cap: ${cap:,.2f}; remaining ${remaining:,.4f}"
            + (f", about {rounds} worst-case rounds" if rounds is not None else "")
        )
    reason = stop_reason(manifest, spent)
    typer.echo(f"next day: {'stops, ' + reason if reason else 'may run'}")
    if dry_run:
        state = replay_run(store)
        sized = prompt_round(manifest, state)
        for line in _spend_lines(f"prompts of day {state.day} (one call each)", sized):
            typer.echo(line)


@app.command()
def inspect(directory: Path) -> None:
    """Print a non-mutating summary of the latest verified state."""
    store = WorldStore(directory)
    state = replay_run(store)
    typer.echo(f"day: {state.day}")
    typer.echo(f"journal format: {store.journal_format}")
    typer.echo(f"state hash: {store.state_hash(state)}")
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
    store = WorldStore(directory)
    state = replay_run(store, target_day=day)
    typer.echo(f"replayed day {state.day} ({store.state_hash(state)})")


@app.command()
def verify(
    directory: Path,
    signer: str | None = typer.Option(
        None,
        "--signer",
        help="The fingerprint the run must be sealed by (64 hex digits; spaces allowed).",
    ),
) -> None:
    """Verify manifest, journal hashes, replay, checkpoint, and invariants, and a sealed run's
    seal (by the given signer, if one is named)."""
    store = WorldStore(directory)
    manifest = store.manifest()
    sealed = stored_seal(store)
    notes: list[str] = []
    if signer is not None and sealed is None:
        typer.echo("this run is not sealed", err=True)
        raise typer.Exit(code=1)
    if sealed is not None:
        try:
            notes = check_seal(
                store, manifest, sealed, code_hash=rule_hash(), signer=signer, strict_code=False
            )
        except (SealRefused, ValueError) as refused:
            typer.echo(f"seal refused: {refused}", err=True)
            raise typer.Exit(code=1) from None
    try:
        result = verify_whole(store)
    except RuntimeError as error:
        typer.echo(f"not verified: {error}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(
        f"verified through day {result.verified_through_day} "
        f"({result.state_hash}, {result.records} records, journal format {store.journal_format})"
    )
    if sealed is not None:
        code = "differs from this code" if notes else "matches this code"
        typer.echo(
            f"sealed by {grouped(sealed.fingerprint)} for {sealed.planned_days} days;"
            f" its code hash {code}"
        )


@app.command()
def keygen() -> None:
    """Make a new seal key: printed once, saved nowhere. Keep it, and its fingerprint."""
    key, fingerprint = generate_key()
    typer.echo(f"{SEAL_KEY_ENV}={key}")
    typer.echo(f"fingerprint: {grouped(fingerprint)}")
    typer.echo(
        "This key is shown once and saved nowhere. Put it in the environment only to seal a run,"
        " and keep the fingerprint to check the run's seal later."
    )


@app.command()
def seal(
    directory: Path,
    days: int = typer.Option(..., min=1, help="The days the sealed run is planned to last."),
) -> None:
    """Seal a run on day 0 with the key in SOVEREIGN_WORLD_SEAL_KEY. From then on `run` refuses
    any change to its manifest, code, prompts or model endpoints, and stops at the planned
    days. The key is read from the environment, used once, and never written or shown."""
    store = WorldStore(directory)
    manifest = store.manifest()
    state = store.load_checkpoint()
    problems = offline_checks(store, manifest, state)
    if problems:
        for problem in problems:
            typer.echo(f"cannot seal: {problem}", err=True)
        raise typer.Exit(1)
    try:
        key = load_key(os.environ)
    except (SealKeyMissing, SealKeyInvalid) as error:
        typer.echo(f"cannot seal: {error}", err=True)
        raise typer.Exit(1) from None
    finally:
        os.environ.pop(SEAL_KEY_ENV, None)
    sealed = make_seal(
        manifest,
        state,
        planned_days=days,
        key=key,
        code_hash=rule_hash(),
        engine_hash=engine_hash(),
    )
    del key
    store.write_seal(sealed.model_dump(mode="json"))
    typer.echo(f"sealed run {sealed.run_id} for {days} days")
    typer.echo(f"fingerprint: {grouped(sealed.fingerprint)}")
    typer.echo(f"manifest hash: {sealed.manifest_hash}")
    typer.echo(f"code hash: {sealed.code_hash}")
    typer.echo(f"engine hash: {sealed.engine_hash}")
    typer.echo(f"prompt {sealed.prompt_version}, reply schema {sealed.reply_schema_hash}")
    for civilization_id, pin in sealed.providers.items():
        typer.echo(f"{civilization_id}: {pin.kind} {pin.model} at {pin.base_url()}")
    typer.echo(
        f"next: sovereign-world preflight {directory} --launch --signer"
        f" {sealed.fingerprint} --days {days} --calibration REPORT --probe"
    )


@app.command()
def preflight(
    directory: Path,
    probe: bool = typer.Option(
        False, "--probe", help="One tiny real call per provider; journaled nowhere."
    ),
    launch: bool = typer.Option(
        False,
        "--launch",
        help="The pass before launch: the run must be sealed, on day 0, with a balance report.",
    ),
    signer: str | None = typer.Option(
        None, "--signer", help="The fingerprint the run must be sealed by."
    ),
    calibration: str | None = typer.Option(
        None, "--calibration", help="The balance calibration's report.json, or its folder."
    ),
    accept_balance_failure: str | None = typer.Option(
        None,
        "--accept-balance-failure",
        metavar="REASON",
        help="Launch although the balance report failed; the reason is printed, kept nowhere.",
    ),
    days: int = typer.Option(365, "--days", min=1, help="The days the seal must plan."),
    as_json: bool = typer.Option(False, "--json", help="Print the checks as JSON."),
) -> None:
    """The launch gate: a plain checklist a sealed trial must pass before it starts. Writes
    nothing, and prints only the names of the variables that hold keys."""
    environ = dict(os.environ)
    os.environ.pop(SEAL_KEY_ENV, None)
    checks = launch_gate(
        WorldStore(directory),
        GateOptions(
            launch=launch,
            probe=probe,
            signer=signer,
            calibration=Path(calibration) if calibration else None,
            accept_balance_failure=accept_balance_failure,
            planned_days=days,
        ),
        environ=environ,
    )
    ready = gate_passed(checks)
    if as_json:
        typer.echo(
            json.dumps(
                {
                    "passed": ready,
                    "checks": [
                        {"id": c.id, "title": c.title, "status": c.status, "detail": c.detail}
                        for c in checks
                    ],
                },
                indent=2,
            )
        )
    else:
        for line in render(checks):
            typer.echo(line)
        seal_check = next(check for check in checks if check.id == "seal")
        if launch and ready:
            typer.echo(f"ready to launch: {seal_check.detail}")
    if not ready:
        raise typer.Exit(1)


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
            "rules_version": parent.rules_version,
            # A fork's journal is new, so it is saved in the current format.
            "journal_format": CURRENT_JOURNAL_FORMAT,
            # Its world was built with the parent's starts.
            "start_rotation": parent.start_rotation,
            **_settings(sovereigns),
        }
    )
    state = state.model_copy(
        update={"run_id": manifest.run_id, "manifest_hash": manifest.content_hash()}
    )
    WorldStore.create(directory, manifest, state)
    typer.echo(f"forked {parent.run_id} at day {state.day} into {manifest.run_id}")


@app.command()
def observe(
    directory: Path,
    host: str = typer.Option("127.0.0.1", help="Address to listen on."),
    port: int = typer.Option(8766, min=1, max=65535, help="Port to listen on."),
    run_days: int | None = typer.Option(
        None,
        "--run-days",
        min=1,
        help=(
            "Also run the world up to this many days further, as its only writer, while the"
            " page plays; it waits, paused, until then."
        ),
    ),
) -> None:
    """Serve a run to the observer, live and read-only, behind a token (needs the
    'observer' extra)."""
    try:
        from sovereign_world.observer.server import serve
    except ImportError as error:
        raise typer.BadParameter(
            "the observer server needs FastAPI and uvicorn: install the 'observer' extra"
        ) from error
    os.environ.pop(SEAL_KEY_ENV, None)
    serve(directory, host=host, port=port, run_days=run_days)


if __name__ == "__main__":
    app()
