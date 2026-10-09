"""Read-only-after-launch command line interface for the Rules Laboratory."""

from __future__ import annotations

import io
import json
import os
import sys
import tomllib
from pathlib import Path
from uuid import uuid4

import typer

from sovereign_world.config import (
    COUNCIL_INTERVALS,
    CURRENT_GENERATOR,
    CURRENT_JOURNAL_FORMAT,
    CURRENT_RULES,
    DEFAULT_CRISIS_GAP,
    ENGINE_VERSION,
    BudgetConfig,
    RunManifest,
    SovereignConfig,
    SpendConfig,
    WorldConfig,
)
from sovereign_world.gateway.records import CouncilRecord, ResumeRefused, recorded_councils
from sovereign_world.persistence import RunLocked, WorldStore
from sovereign_world.preflight import (
    GateOptions,
    launch_gate,
    offline_checks,
    render,
    scrub,
    token_values,
)
from sovereign_world.preflight import passed as gate_passed
from sovereign_world.replay import replay_run, verify_whole
from sovereign_world.rulehash import engine_hash, rule_hash
from sovereign_world.runner import (
    DayProgress,
    RunControl,
    SpendCapReached,
    _with_limit,
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
    pins_of,
    stored_seal,
)
from sovereign_world.spend import (
    Tally,
    caps_of,
    cost_of,
    council_summary,
    outcome_word,
    prompt_round,
    rounds_covered,
    shown_cap,
    stop_reason,
    tally,
    worst_case_round,
)
from sovereign_world.state import WorldState, build_initial_state, validate_world

app = typer.Typer(
    help="Create, run, inspect, checkpoint, replay, and verify a sovereign world.",
    no_args_is_help=True,
)


def utf8_or_replace(stream: object) -> None:
    """Let a console that cannot show every character print a stand-in instead of failing."""
    encoding = str(getattr(stream, "encoding", "") or "").lower().replace("-", "")
    if encoding != "utf8" and isinstance(stream, io.TextIOWrapper):
        stream.reconfigure(errors="replace")


@app.callback()
def _console() -> None:
    """Create, run, inspect, checkpoint, replay, and verify a sovereign world."""
    utf8_or_replace(sys.stdout)
    utf8_or_replace(sys.stderr)


def _settings(path: str | None) -> dict[str, object]:
    """Sovereign assignments and budgets from a TOML file, checked before they are frozen."""
    if path is None:
        return {}
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
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
    council_interval: int = typer.Option(
        28,
        "--council-interval",
        help="Days between regular councils: 7, 14, 21 or 28 (one to four weeks).",
    ),
    crisis_gap: int = typer.Option(
        DEFAULT_CRISIS_GAP,
        "--crisis-gap",
        min=0,
        help="The fewest days between one civilization's crisis councils; 0 holds none.",
    ),
) -> None:
    """Create a locked manifest and day-zero checkpoint."""
    if council_interval not in COUNCIL_INTERVALS:
        raise typer.BadParameter("--council-interval must be 7, 14, 21 or 28 days")
    config = WorldConfig(
        seed=seed,
        width=width,
        height=height,
        civilizations=civilizations,
        council_interval_days=council_interval,
        crisis_gap_days=crisis_gap,
    )
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
    pace: float = typer.Option(
        0.0,
        "--pace",
        min=0,
        help=(
            "Seconds to wait between days, so a year runs over days or weeks instead of hours."
            " The world records nothing about it."
        ),
    ),
) -> None:
    """Advance the latest verified state under the run's sovereigns, recording every council.

    A run with a spending cap stops cleanly, with a checkpoint and exit code 3, before any day
    whose councils could take it past the cap. Ctrl+C stops it after the day under way. After a
    crash or a kill, running it again carries on from what was saved, asking no model again
    about a council already saved. A sealed run is refused (exit code 4) if it no longer
    matches its seal, and stops at its planned days. A run already being written by another
    process (another `run`, or `observe --run-days`) is refused with exit code 5.

    It prints a line for each council as it is saved (who held it, which model, the outcome,
    tokens, cost and seconds into the day), a line for each council day and each checkpoint,
    and nothing on quiet days. No hash, setting or key is printed but the final state hash."""
    # Only `seal` uses the seal key; nothing that runs a world keeps it.
    os.environ.pop(SEAL_KEY_ENV, None)
    store = WorldStore(directory)
    control = RunControl(paused=controlled_run)
    try:
        with interruptible(control):
            if controlled_run:
                controlled(
                    store, days, spend_limit_usd=spend_limit, control=control, pace_seconds=pace
                )
                return
            shown = _Progress(_with_limit(store.manifest(), spend_limit))
            state = run_days(
                store,
                days,
                control=control,
                spend_limit_usd=spend_limit,
                on_start=shown.start,
                on_council=shown.council,
                on_progress=shown.day,
                pace_seconds=pace,
            )
    except SpendCapReached as stop:
        typer.echo(f"stopped before day {stop.state.day + 1}: {stop.reason}", err=True)
        raise typer.Exit(3) from None
    except ResumeRefused as refused:
        typer.echo(f"cannot resume: {refused}", err=True)
        raise typer.Exit(1) from None
    except SealRefused as refused:
        typer.echo(f"seal refused: {refused}", err=True)
        raise typer.Exit(4) from None
    except RunLocked as locked:
        typer.echo(str(locked), err=True)
        raise typer.Exit(5) from None
    verb = "stopped at" if control.stopped else "advanced to"
    typer.echo(f"{verb} day {state.day} ({store.state_hash(state)})")


class _Progress:
    """What `run` prints as it goes: plain ASCII lines, councils and checkpoints only."""

    def __init__(self, manifest: RunManifest) -> None:
        self.manifest = manifest
        self.spend = manifest.spend or SpendConfig()

    def start(self, state: WorldState, planned: int) -> None:
        caps = caps_of(self.manifest.spend)
        typer.echo(
            f"run {str(self.manifest.run_id)[:8]} day {state.day} -> {state.day + planned},"
            f" councils every {state.config.council_interval_days} days,"
            + (
                " cap " + ", ".join(shown_cap(name, cap) for name, cap in caps)
                if caps
                else " no cost cap"
            )
        )

    def council(self, record: CouncilRecord, seconds: float) -> None:
        config = self.manifest.sovereigns.get(str(record.civilization_id))
        who = (config.label or config.provider) if config is not None else record.provider
        tokens_in = sum(usage.input_tokens for usage in record.usage)
        tokens_out = sum(usage.output_tokens for usage in record.usage)
        cost = sum(
            cost_of(usage.model, usage.input_tokens, usage.output_tokens, self.spend)[0]
            for usage in record.usage
        )
        outcome = outcome_word(record)
        typer.echo(
            f"  day {record.day}: {record.civilization_id} {who} {record.model} {outcome},"
            f" {tokens_in:,} in, {tokens_out:,} out, ${cost:,.4f}, {seconds:.1f} s"
        )

    def day(self, progress: DayProgress) -> None:
        if progress.councils:
            cap = self.spend.max_cost_usd
            spent = progress.spent
            tokens_in = f"{spent.input_tokens:,} in" + (
                f" of {self.spend.max_input_tokens:,}" if self.spend.max_input_tokens else ""
            )
            tokens_out = f"{spent.output_tokens:,} out" + (
                f" of {self.spend.max_output_tokens:,}" if self.spend.max_output_tokens else ""
            )
            typer.echo(
                f"day {progress.councils[0].day} councils: {len(progress.councils)} in"
                f" {progress.elapsed_seconds:.1f} s; spent ${spent.cost_usd:,.4f}"
                + ("" if cap is None else f" of ${cap:,.2f}")
                + f" ({tokens_in}, {tokens_out})"
            )
        if progress.checkpoint:
            typer.echo(f"checkpoint saved at day {progress.day}")


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
    covered = rounds_covered(manifest, spent)
    used = {"input tokens": spent.input_tokens, "output tokens": spent.output_tokens}
    for name, cap in caps_of(manifest.spend):
        if name in used:
            typer.echo(
                f"{name} cap: {int(cap):,}; remaining {max(0, int(cap) - used[name]):,},"
                f" about {covered[name]} worst-case rounds"
            )
    reason = stop_reason(manifest, spent)
    typer.echo(f"next day: {'stops, ' + reason if reason else 'may run'}")
    if dry_run:
        state = replay_run(store)
        sized = prompt_round(manifest, state)
        for line in _spend_lines(f"prompts of day {state.day} (one call each)", sized):
            typer.echo(line)


@app.command()
def councils(
    directory: Path,
    as_json: bool = typer.Option(False, "--json", help="Print the summary as JSON."),
    errors: bool = typer.Option(
        False, "--errors", help="Also list every council whose model's reply was not accepted."
    ),
) -> None:
    """How each civilization's councils went: which model held them, the outcomes, tokens and
    cost. Never asks a model, never writes; key values never appear in what it prints."""
    store = WorldStore(directory)
    manifest = store.manifest()
    records = recorded_councils(store)
    rows = council_summary(records, manifest)
    secrets = token_values(os.environ, pins_of(manifest))
    failed = [
        {
            "day": record.day,
            "civilization": str(record.civilization_id),
            "outcome": outcome_word(record),
            "error": scrub(record.errors[0], secrets),
        }
        for record in records
        if record.errors
    ]
    if as_json:
        document: dict[str, object] = {
            "civilizations": [
                {
                    "civilization": row.civilization,
                    "who": row.who,
                    "model": row.model,
                    "councils": row.councils,
                    "asked": row.asked,
                    "outcomes": dict(sorted(row.outcomes.items())),
                    "input_tokens": row.input_tokens,
                    "output_tokens": row.output_tokens,
                    "cost_usd": round(row.cost_usd, 6),
                    "answering_models": sorted(row.answering),
                    "unpriced_models": sorted(row.unpriced),
                }
                for row in rows
            ]
        }
        if errors:
            document["errors"] = failed
        typer.echo(json.dumps(document, indent=2))
        return
    total: dict[str, int] = {}
    for row in rows:
        for word, count in row.outcomes.items():
            total[word] = total.get(word, 0) + count
        outcomes = ", ".join(f"{word} {count}" for word, count in sorted(row.outcomes.items()))
        line = f"{row.civilization} {row.who} {row.model}: {row.councils} councils"
        line += f" ({outcomes})" if outcomes else ""
        if row.asked:
            line += (
                f"; mean {row.input_tokens // row.asked:,} in, {row.output_tokens // row.asked:,}"
                f" out; ${row.cost_usd:,.4f}"
            )
        typer.echo(line)
        if row.answering:
            typer.echo(f"  answered by: {', '.join(sorted(row.answering))}")
        if row.unpriced:
            unpriced = ", ".join(sorted(row.unpriced))
            typer.echo(f"  unpriced (charged at the dearest rate): {unpriced}")
    cost = sum(row.cost_usd for row in rows)
    outcomes = ", ".join(f"{word} {count}" for word, count in sorted(total.items()))
    typer.echo(
        f"total: {sum(row.councils for row in rows)} councils"
        + (f" ({outcomes})" if outcomes else "")
        + f"; ${cost:,.4f}"
    )
    if errors:
        typer.echo(f"councils with errors: {len(failed)}")
        for entry in failed:
            where = f"day {entry['day']}: {entry['civilization']}"
            typer.echo(f"  {where} {entry['outcome']}: {entry['error']}")


@app.command()
def inspect(directory: Path) -> None:
    """Print a non-mutating summary of the latest verified state."""
    store = WorldStore(directory)
    state = replay_run(store)
    typer.echo(f"day: {state.day}")
    typer.echo(f"journal format: {store.journal_format}")
    typer.echo(f"council interval: {state.config.council_interval_days} days")
    gap = state.config.crisis_gap_days
    typer.echo(f"crisis gap: {gap} days" if gap else "crisis councils: none")
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
    try:
        store.write_seal(sealed.model_dump(mode="json"))
    except RunLocked as locked:
        typer.echo(f"cannot seal: {locked}", err=True)
        raise typer.Exit(5) from None
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
def doctor(
    settings: str | None = typer.Option(
        None, "--settings", help="The trial's settings file: the models to look for."
    ),
    root: str = typer.Option(
        ".", "--root", help="The repository folder (its path and drive are checked)."
    ),
    ollama: str | None = typer.Option(
        None, "--ollama", help="Ollama's address, if not the one in the settings."
    ),
    as_json: bool = typer.Option(False, "--json", help="Print the checks as JSON."),
) -> None:
    """Check this computer before the trial: Python, uv, PowerShell, the repository's folder
    and drive, Claude Code and Codex signed in, the variables that would replace their
    sign-ins, Ollama and its model and context, the GPU, disk, sleep and the clock.
    It writes nothing and shows no variable's value; it exits 1 if any check fails."""
    from sovereign_world.doctor import DOCTOR_TITLES, machine_checks

    os.environ.pop(SEAL_KEY_ENV, None)
    chosen = _settings(settings).get("sovereigns")
    sovereigns = chosen if isinstance(chosen, dict) else None
    checks = machine_checks(Path(root), sovereigns, ollama_url=ollama, environ=dict(os.environ))
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
        for line in render(checks, ids=DOCTOR_TITLES):
            typer.echo(line)
    if not ready:
        raise typer.Exit(1)


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
    public: bool = typer.Option(
        False,
        "--public",
        help=(
            "Also serve viewers holding the shared link (through a tunnel): viewing only, for"
            " everyone, with request limits."
        ),
    ),
) -> None:
    """Serve a run to the observer, live and read-only, behind a token (needs the
    'observer' extra)."""
    try:
        from sovereign_world.observer.server import check_public, serve
    except ImportError as error:
        raise typer.BadParameter(
            "the observer server needs FastAPI and uvicorn: install the 'observer' extra"
        ) from error
    if public:
        try:
            check_public(host=host, run_days=run_days)
        except ValueError as error:
            raise typer.BadParameter(str(error)) from error
    os.environ.pop(SEAL_KEY_ENV, None)
    try:
        serve(directory, host=host, port=port, run_days=run_days, public=public)
    except ValueError as error:
        if not public:
            raise
        raise typer.BadParameter(str(error)) from error


if __name__ == "__main__":
    app()
