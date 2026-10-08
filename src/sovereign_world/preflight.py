"""The launch gate (sealed trial): `sovereign-world preflight`.

A plain checklist a sealed trial must pass before it is launched, mapped to spec §19 and the
roadmap's Phase 5 exit (`docs/launch-gate.md`). Every check says PASS, WARN, FAIL or SKIP (not
asked for); the gate passes when nothing FAILs. It writes nothing, asks a model only under
`--probe` (one tiny call per provider, journaled nowhere and counted against no cap), and never
prints a secret: only the names of the variables that hold them.

`offline_checks` is what `seal` itself asks before signing (a run on day 0, unsaved, with
sovereigns on this engine's prompt version and readable endpoints).
"""

from __future__ import annotations

import shutil
import time
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from sovereign_world.config import (
    CURRENT_GENERATOR,
    CURRENT_JOURNAL_FORMAT,
    CURRENT_RULES,
    ENGINE_VERSION,
    RunManifest,
    SovereignConfig,
    WorldConfig,
)
from sovereign_world.persistence import HEADER, WorldStore
from sovereign_world.state import WorldState

Status = Literal["PASS", "WARN", "FAIL", "SKIP"]

CHECKS: tuple[tuple[str, str], ...] = (
    ("store", "the run loads, replays and verifies"),
    ("day", "the world has not begun"),
    ("engine", "the engine replays its pinned self-test"),
    ("code", "the code's hashes"),
    ("players", "every civilization played by a model"),
    ("providers", "one distinct provider (kind and host) per civilization"),
    ("prompts", "every sovereign on this engine's prompt version"),
    ("spend", "budgets, the hard cap and prices"),
    ("keys", "the key variables are present (names only)"),
    ("endpoints", "the endpoint policy"),
    ("probe", "one tiny real call per provider"),
    ("disk", "disk space for a year"),
    ("clock", "the clock is plausible"),
    ("balance", "the balance report passed under this engine"),
    ("seal", "the run is sealed and matches its seal"),
    ("seal_key", "the seal key is out of the environment"),
    ("observer_token", "the observer token"),
)
"""Every check, in the order printed (`docs/launch-gate.md` lists the same)."""
TITLES = dict(CHECKS)

SELF_TEST_RUN_ID = UUID("00000000-0000-4000-8000-000000000005")
SELF_TEST_SEED, SELF_TEST_SIZE, SELF_TEST_DAYS = 21, 24, 10
SELF_TEST_HASH = "e3a3bcf1e05a2a7febaa2247aafeb8f819360fd1d5cb173768b7b7c57c953552"
"""The state hash (v2) of the self-test world after its days. It moves with any change to the
engine; `tests/test_preflight.py` says when to update it."""
GATE_WRITTEN = date(2026, 10, 7)
"""A clock showing an earlier date is wrong."""
DISK_FAIL_BYTES = 2 * 2**30
DISK_WARN_BYTES = 10 * 2**30


def regular_councils(interval: int, planned_days: int) -> int:
    """The regular councils in a run of `planned_days` days: day 0 and every interval after
    (13 in a year of monthly councils, 14 at 28 days, 53 weekly)."""
    return (planned_days - 1) // interval + 1


MIN_HISTORIES = 1_000
MIN_OBSERVER_TOKEN_CHARS = 32
MAX_CLOCK_SKEW_SECONDS = 300.0
PROBE_SYSTEM = (
    "You are checking a connection. Reply with exactly this JSON object and nothing else:"
    ' {"ok": true}'
)
PROBE_USER = "Reply now."
PROBE_MAX_OUTPUT_TOKENS = 256
PROBE_TIMEOUT_SECONDS = 60.0
LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1", "[::1]")
MODEL_REQUIRED = ("openai", "compatible", "claude-code", "codex")
"""Kinds that have no default model (Claude's API client has one)."""
SIGN_IN_HIJACKERS = {
    "claude-code": (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
    ),
    "codex": ("CODEX_API_KEY", "CODEX_ACCESS_TOKEN"),
}
"""Variables that would take a signed-in program off the user's plan (its adapter drops them
from the program's environment; the gate names them so they can be removed)."""
TOKEN_ENV = "SOVEREIGN_WORLD_OBSERVER_TOKEN"
"""The observer's token variable (`observer.TOKEN_ENV`; the engine side never imports the
observer)."""


@dataclass(frozen=True, slots=True)
class Check:
    id: str
    status: Status
    detail: str

    @property
    def title(self) -> str:
        return TITLES[self.id]


@dataclass(frozen=True)
class GateOptions:
    launch: bool = False
    probe: bool = False
    signer: str | None = None
    calibration: Path | None = None
    accept_balance_failure: str | None = None
    planned_days: int = 365


@dataclass(frozen=True, slots=True)
class ProbeResult:
    civilization: str
    configured_model: str
    answering_model: str | None = None
    latency_ms: int | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None
    """The error's type and first line, with every known secret removed."""
    note: str | None = None
    """For a signed-in program: its version and sign-in."""


Prober = Callable[[str, SovereignConfig, Any, float], ProbeResult]
"""Asks one civilization's model one tiny question: (civilization, settings, pin, timeout)."""


@dataclass
class _Run:
    manifest: RunManifest
    state: WorldState
    pins: dict[str, Any] = field(default_factory=dict)


def offline_checks(store: WorldStore, manifest: RunManifest, state: WorldState) -> tuple[str, ...]:
    """What stops this run from being sealed, in plain words; empty when nothing does."""
    from sovereign_world.gateway.prompt import PROMPT_VERSION
    from sovereign_world.seal import pins_of

    problems: list[str] = []
    if state.day != 0:
        problems.append(f"the run stands at day {state.day}; only a run on day 0 can be sealed")
    if manifest.journal_format < 2:
        problems.append("the journal is format 1; only a format-2 run can be sealed")
    elif [record.type for record in store.read_records()] != [HEADER]:
        problems.append("the journal holds more than its header; a sealed run starts unsaved")
    if store.seal_document() is not None:
        problems.append("the run is already sealed")
    known = {str(civilization_id) for civilization_id in state.civilizations}
    for civilization_id, config in sorted(manifest.sovereigns.items()):
        if civilization_id not in known:
            problems.append(f"{civilization_id} is named in the settings but not in the world")
        if config.provider == "baseline":
            continue
        if config.prompt_version != PROMPT_VERSION:
            problems.append(
                f"{civilization_id} is set to prompt {config.prompt_version}, not this engine's"
                f" {PROMPT_VERSION}"
            )
        if config.provider in MODEL_REQUIRED and not config.model:
            problems.append(f"{civilization_id} names no model")
    try:
        pins_of(manifest)
    except ValueError as error:
        problems.append(str(error))
    return tuple(problems)


def self_test_hash() -> str:
    """The pinned self-test: a fixed 24 by 24 world under this engine, run with scripted
    councils, hashed."""
    from sovereign_world.engine import advance_day
    from sovereign_world.rng import StableRng
    from sovereign_world.scripted import BaselineSovereign
    from sovereign_world.state import build_initial_state, state_hash_v2

    manifest = RunManifest(
        run_id=SELF_TEST_RUN_ID,
        engine_version=ENGINE_VERSION,
        config=WorldConfig(seed=SELF_TEST_SEED, width=SELF_TEST_SIZE, height=SELF_TEST_SIZE),
        generator_version=CURRENT_GENERATOR,
        rules_version=CURRENT_RULES,
        journal_format=CURRENT_JOURNAL_FORMAT,
    )
    state = build_initial_state(manifest)
    sovereigns = {civilization: BaselineSovereign() for civilization in state.civilizations}
    rng = StableRng(manifest.config.seed)
    for _ in range(SELF_TEST_DAYS):
        state = advance_day(state, rng, sovereigns=sovereigns).state
    return state_hash_v2(state, fresh=True)


def distinct_providers(pins: Mapping[str, Any]) -> set[tuple[str, str]]:
    """The distinct (kind, host) pairs the run's models are reached at."""
    return {(pin.kind, pin.host) for pin in pins.values()}


def _loopback(host: str) -> bool:
    name = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    return name in LOOPBACK_HOSTS or name.startswith("127.")


def scrub(text: str, secrets: Iterable[str]) -> str:
    """`text` with every secret value replaced, and only its first line."""
    line = text.splitlines()[0] if text else ""
    for secret in secrets:
        if secret:
            line = line.replace(secret, "…")
    return line


def token_values(environ: Mapping[str, str], pins: Mapping[str, Any]) -> list[str]:
    names = {pin.token_env for pin in pins.values() if pin.token_env}
    return [environ[name] for name in sorted(names) if environ.get(name)]


def probe_provider(
    civilization: str, config: SovereignConfig, pin: Any, timeout: float
) -> ProbeResult:
    """One tiny call through the same provider the run would use (at the pinned address for a
    hosted model). Nothing is journaled."""
    from sovereign_world.gateway.factory import provider_for
    from sovereign_world.gateway.provider import ModelRequest
    from sovereign_world.ids import EntityId
    from sovereign_world.seal import HOSTED_BASE_URLS

    started = time.perf_counter()
    try:
        provider = provider_for(
            EntityId(civilization),
            config,
            base_url=pin.base_url() if pin.kind in HOSTED_BASE_URLS else None,
        )
        reply = provider.complete(
            ModelRequest(
                system=PROBE_SYSTEM,
                user=PROBE_USER,
                max_output_tokens=PROBE_MAX_OUTPUT_TOKENS,
                timeout_seconds=timeout,
                purpose="probe",
            )
        )
    except Exception as error:
        return ProbeResult(
            civilization, pin.model, error=f"{type(error).__name__}: {str(error) or 'no detail'}"
        )
    status = getattr(provider, "status", None)
    note = status() if callable(status) else None
    return ProbeResult(
        civilization,
        pin.model,
        note=note if isinstance(note, str) else None,
        answering_model=reply.model or pin.model,
        latency_ms=round((time.perf_counter() - started) * 1000),
        input_tokens=reply.input_tokens,
        output_tokens=reply.output_tokens,
    )


def host_dates(hosts: Iterable[tuple[str, str]], timeout: float = 5.0) -> dict[str, float | None]:
    """Each host's clock, from the `Date` header of a plain request to its root (no token is
    sent); None where no date came back."""
    dates: dict[str, float | None] = {}
    for scheme, host in sorted(set(hosts)):
        url = f"{scheme}://{host}/"
        stamp: float | None = None
        try:
            request = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(request, timeout=timeout) as response:
                header = response.headers.get("Date")
        except urllib.error.HTTPError as error:
            header = error.headers.get("Date") if error.headers else None
        except Exception:
            header = None
        if header:
            try:
                stamp = parsedate_to_datetime(header).timestamp()
            except (TypeError, ValueError):
                stamp = None
        dates[host] = stamp
    return dates


def launch_gate(
    store: WorldStore,
    options: GateOptions,
    *,
    environ: Mapping[str, str],
    now: Callable[[], float] = time.time,
    disk_usage: Callable[[Path], Any] = shutil.disk_usage,
    prober: Prober | None = probe_provider,
    dates: Callable[[Iterable[tuple[str, str]]], dict[str, float | None]] = host_dates,
    which: Callable[[str], str | None] = shutil.which,
) -> list[Check]:
    """Every check of the launch gate, in order."""
    from sovereign_world.seal import pins_of

    checks: list[Check] = []
    run: _Run | None = None
    try:
        from sovereign_world.replay import verify_whole

        verified = verify_whole(store)
        run = _Run(store.manifest(), store.load_checkpoint(at_or_before=0))
        checks.append(
            Check(
                "store",
                "PASS",
                f"verified through day {verified.verified_through_day}, {verified.records} records",
            )
        )
        latest_day = verified.verified_through_day
    except Exception as error:
        checks.append(Check("store", "FAIL", scrub(f"{type(error).__name__}: {error}", ())))
        latest_day = -1
    if run is not None:
        try:
            run.pins = pins_of(run.manifest)
        except ValueError:
            run.pins = {}
    if run is None:
        checks.append(Check("day", "SKIP", "the run did not load"))
    else:
        checks.append(_check_day(latest_day, options))
    checks.append(_check_engine())
    checks.append(_check_code())
    if run is None:
        for check_id in ("players", "providers", "prompts", "spend", "keys", "endpoints", "probe"):
            checks.append(Check(check_id, "SKIP", "the run did not load"))
    else:
        checks.append(_check_players(run))
        checks.append(_check_providers(run))
        checks.append(_check_prompts(run))
        checks.append(_check_spend(store, run, options))
        checks.append(_check_keys(run, environ))
        checks.append(_check_endpoints(run, which))
        checks.append(_check_probe(run, options, environ, prober))
    checks.append(_check_disk(store, disk_usage))
    checks.append(_check_clock(store, run, options, now, dates))
    checks.append(_check_balance(options, run))
    checks.append(_check_seal(store, run, options))
    checks.append(_check_seal_key(options, environ))
    checks.append(_check_observer_token(environ))
    return checks


def passed(checks: Iterable[Check]) -> bool:
    return all(check.status != "FAIL" for check in checks)


def render(checks: Iterable[Check]) -> list[str]:
    """One line per check, then the counts."""
    listed = list(checks)
    width = max(len(check_id) for check_id, _ in CHECKS)
    lines = [
        f"{check.status:<4}  {check.id:<{width}}  {check.title}"
        + (f" — {check.detail}" if check.detail else "")
        for check in listed
    ]
    counts = {status: sum(check.status == status for check in listed) for status in STATUSES}
    lines.append(
        f"{counts['PASS']} passed, {counts['WARN']} warnings, {counts['FAIL']} failed,"
        f" {counts['SKIP']} skipped"
    )
    return lines


STATUSES: tuple[Status, ...] = ("PASS", "WARN", "FAIL", "SKIP")


def _check_day(day: int, options: GateOptions) -> Check:
    if day == 0:
        return Check("day", "PASS", "day 0")
    return Check("day", "FAIL" if options.launch else "WARN", f"the run stands at day {day}")


def _check_engine() -> Check:
    found = self_test_hash()
    if found != SELF_TEST_HASH:
        return Check(
            "engine",
            "FAIL",
            f"the self-test world hashes {found[:12]}…, not the pinned {SELF_TEST_HASH[:12]}…",
        )
    return Check("engine", "PASS", f"{SELF_TEST_DAYS} days, {SELF_TEST_HASH[:12]}…")


def _check_code() -> Check:
    from sovereign_world.rulehash import engine_hash, rule_hash

    return Check("code", "PASS", f"rule {rule_hash()[:12]}…, engine {engine_hash()[:12]}…")


def _check_players(run: _Run) -> Check:
    civilizations = sorted(str(civ) for civ in run.state.civilizations)
    problems = []
    if run.manifest.config.civilizations != len(civilizations):
        problems.append(
            f"the world has {len(civilizations)} civilizations, its settings"
            f" {run.manifest.config.civilizations}"
        )
    for civilization in civilizations:
        config = run.manifest.sovereigns.get(civilization)
        if config is None or config.provider == "baseline":
            problems.append(f"{civilization} is played by the scripted baseline")
        elif not config.model and config.provider != "anthropic":
            problems.append(f"{civilization} names no model")
    if problems:
        return Check("players", "FAIL", "; ".join(problems))
    return Check(
        "players",
        "PASS",
        f"{len(civilizations)} civilizations: "
        + "; ".join(
            f"{civ}: {run.manifest.sovereigns[civ].provider} {run.manifest.sovereigns[civ].model}"
            for civ in civilizations
        ),
    )


def _check_providers(run: _Run) -> Check:
    distinct = distinct_providers(run.pins)
    needed = len(run.state.civilizations)
    detail = ", ".join(f"{kind} at {host}" for kind, host in sorted(distinct))
    if len(distinct) < needed:
        return Check(
            "providers", "FAIL", f"{len(distinct)} distinct of {needed}: {detail or 'none'}"
        )
    return Check("providers", "PASS", detail)


def _check_prompts(run: _Run) -> Check:
    from sovereign_world.gateway.prompt import PROMPT_VERSION
    from sovereign_world.seal import reply_schema_hash

    stale = sorted(
        civ
        for civ, config in run.manifest.sovereigns.items()
        if config.provider != "baseline" and config.prompt_version != PROMPT_VERSION
    )
    if stale:
        return Check(
            "prompts", "FAIL", f"not on {PROMPT_VERSION}: {', '.join(stale)}; fork the run"
        )
    return Check("prompts", "PASS", f"{PROMPT_VERSION}, reply schema {reply_schema_hash()[:12]}…")


def _check_spend(store: WorldStore, run: _Run, options: GateOptions) -> Check:
    from sovereign_world.gateway.records import recorded_councils
    from sovereign_world.spend import caps_of, rounds_covered, shown_cap, tally

    spend = run.manifest.spend
    caps = caps_of(spend)
    if spend is None or not caps:
        return Check(
            "spend", "FAIL", "no cap is set in the run's [spend] settings (cost or tokens)"
        )
    models = sorted(
        {
            config.model
            for config in run.manifest.sovereigns.values()
            if config.provider != "baseline"
        }
    )
    unpriced = [model for model in models if model not in spend.prices]
    if unpriced:
        return Check("spend", "FAIL", f"no price for {', '.join(unpriced)}")
    covered = rounds_covered(run.manifest, tally(recorded_councils(store), spend))
    rounds = min(covered.values())
    budgets = run.manifest.budgets
    detail = (
        "cap "
        + ", ".join(shown_cap(name, cap) for name, cap in caps)
        + f"; covers {rounds} worst-case rounds; {budgets.max_output_tokens:,} output tokens,"
        f" {budgets.timeout_seconds:g} s a call"
    )
    needed = regular_councils(run.manifest.config.council_interval_days, options.planned_days)
    if rounds < needed:
        return Check(
            "spend",
            "WARN",
            f"{detail}: fewer than the {needed} regular councils of {options.planned_days} days",
        )
    return Check("spend", "PASS", detail)


def _check_keys(run: _Run, environ: Mapping[str, str]) -> Check:
    from sovereign_world.seal import CLI_PROGRAMS

    missing: list[str] = []
    present: list[str] = []
    notes: list[str] = []
    hijackers: set[str] = set()
    for civ, pin in sorted(run.pins.items()):
        if pin.kind in CLI_PROGRAMS:
            notes.append(f"{civ}: no token (signed-in program)")
            hijackers |= {name for name in SIGN_IN_HIJACKERS[pin.kind] if environ.get(name)}
        elif pin.token_env:
            (present if environ.get(pin.token_env) else missing).append(pin.token_env)
        elif _loopback(pin.host):
            notes.append(f"{civ}: no token (loopback)")
    kinds = {pin.kind for pin in run.pins.values()}
    redirects = [
        name
        for kind, name in (("anthropic", "ANTHROPIC_BASE_URL"), ("openai", "OPENAI_BASE_URL"))
        if kind in kinds and environ.get(name)
    ]
    detail = "; ".join(
        [
            *([f"present: {', '.join(sorted(set(present)))}"] if present else []),
            *notes,
        ]
    )
    if missing:
        return Check("keys", "FAIL", f"not present: {', '.join(sorted(set(missing)))}")
    warnings = []
    if redirects:
        warnings.append(f"{', '.join(redirects)} present (ignored by a sealed run)")
    if hijackers:
        warnings.append(
            f"{', '.join(sorted(hijackers))} present (left out of the signed-in programs'"
            " environment; remove it)"
        )
    if warnings:
        return Check("keys", "WARN", "; ".join([detail, *warnings]))
    return Check("keys", "PASS", detail)


def _check_endpoints(run: _Run, which: Callable[[str], str | None] = shutil.which) -> Check:
    from sovereign_world.seal import CLI_PROGRAMS, HOSTED_BASE_URLS

    problems: list[str] = []
    warnings: list[str] = []
    shown: list[str] = []
    for civ, pin in sorted(run.pins.items()):
        config = run.manifest.sovereigns[civ]
        label = config.label or pin.kind
        if pin.kind in CLI_PROGRAMS:
            found = which(pin.host)
            if found is None:
                problems.append(f"{civ}: the {pin.host} program is not on PATH")
            else:
                shown.append(f"{civ}: {label} by the signed-in program {found}")
            continue
        shown.append(f"{civ}: {label} at {pin.base_url()}")
        if pin.kind in HOSTED_BASE_URLS:
            if pin.base_url() != HOSTED_BASE_URLS[pin.kind]:
                problems.append(f"{civ} is not at {pin.kind}'s address")
            continue
        if not config.label:
            warnings.append(f"{civ} has no label naming its vendor")
        if _loopback(pin.host):
            continue
        if pin.scheme != "https":
            if config.allow_private_http:
                warnings.append(f"{civ} uses plain http on a private network")
            else:
                problems.append(f"{civ} uses plain http off this computer")
        if not pin.token_env:
            problems.append(f"{civ} is off this computer with no token variable")
    if problems:
        return Check("endpoints", "FAIL", "; ".join(problems))
    if warnings:
        return Check("endpoints", "WARN", "; ".join([*warnings, *shown]))
    return Check("endpoints", "PASS", "; ".join(shown))


def _check_probe(
    run: _Run, options: GateOptions, environ: Mapping[str, str], prober: Prober | None
) -> Check:
    if not options.probe or prober is None:
        return Check("probe", "SKIP", "not asked (--probe makes one tiny call per provider)")
    spend = run.manifest.spend
    secrets = token_values(environ, run.pins)
    timeout = min(PROBE_TIMEOUT_SECONDS, run.manifest.budgets.timeout_seconds)
    failed: list[str] = []
    warned: list[str] = []
    parts: list[str] = []
    for civ, pin in sorted(run.pins.items()):
        result = prober(civ, run.manifest.sovereigns[civ], pin, timeout)
        if result.error is not None:
            failed.append(f"{civ}: {scrub(result.error, secrets)}")
            continue
        answered = result.answering_model or pin.model
        parts.append(
            f"{civ}: {answered} ({result.latency_ms} ms,"
            f" {result.input_tokens} in/{result.output_tokens} out"
            + (f"; {result.note}" if result.note else "")
            + ")"
        )
        if answered != pin.model:
            warned.append(f"{civ}: a fallback answered ({answered}, not {pin.model})")
        if spend is not None and answered not in spend.prices:
            warned.append(f"{civ}: {answered} has no price")
    tail = "not journaled, not counted against the cap"
    if failed:
        return Check("probe", "FAIL", "; ".join(failed))
    if warned:
        return Check("probe", "WARN", "; ".join([*warned, *parts, tail]))
    return Check("probe", "PASS", "; ".join([*parts, tail]))


def _check_disk(store: WorldStore, disk_usage: Callable[[Path], Any]) -> Check:
    free = int(disk_usage(store.root).free)
    detail = f"{free / 2**30:,.1f} GiB free"
    if free < DISK_FAIL_BYTES:
        return Check("disk", "FAIL", f"{detail}; at least {DISK_FAIL_BYTES // 2**30} GiB needed")
    if free < DISK_WARN_BYTES:
        return Check("disk", "WARN", f"{detail}; {DISK_WARN_BYTES // 2**30} GiB advised")
    return Check("disk", "PASS", detail)


def _check_clock(
    store: WorldStore,
    run: _Run | None,
    options: GateOptions,
    now: Callable[[], float],
    dates: Callable[[Iterable[tuple[str, str]]], dict[str, float | None]],
) -> Check:
    current = now()
    newest = max(
        (
            path.stat().st_mtime
            for path in (store.database_path, store.journal_path)
            if path.exists()
        ),
        default=0.0,
    )
    floor = datetime(GATE_WRITTEN.year, GATE_WRITTEN.month, GATE_WRITTEN.day, tzinfo=UTC)
    shown = datetime.fromtimestamp(current, UTC).strftime("%Y-%m-%d %H:%M UTC")
    if current < floor.timestamp():
        return Check("clock", "FAIL", f"the clock says {shown}, before this gate was written")
    if current + 1 < newest:
        return Check("clock", "FAIL", f"the clock says {shown}, before the run was last saved")
    if not options.probe or run is None:
        return Check("clock", "PASS", shown)
    hosts = {
        (pin.scheme, pin.host)
        for pin in run.pins.values()
        if pin.scheme in ("http", "https") and not _loopback(pin.host)
    }
    skews: list[str] = []
    for host, stamp in sorted(dates(hosts).items()):
        if stamp is None:
            skews.append(f"{host} sent no date")
        elif abs(stamp - current) > MAX_CLOCK_SKEW_SECONDS:
            skews.append(f"{host} is {stamp - current:+.0f} s from this clock")
    if skews:
        return Check("clock", "WARN", f"{shown}; " + "; ".join(skews))
    return Check("clock", "PASS", f"{shown}; within {MAX_CLOCK_SKEW_SECONDS:g} s of every host")


def _check_balance(options: GateOptions, run: _Run | None) -> Check:
    import json

    from sovereign_world.rulehash import engine_hash

    path = options.calibration
    if path is None:
        status: Status = "FAIL" if options.launch else "WARN"
        return Check("balance", status, "no report given (--calibration)")
    report_path = path / "report.json" if path.is_dir() else path
    try:
        report = json.loads(report_path.read_text())
    except (OSError, ValueError) as error:
        return Check("balance", "FAIL", f"cannot read {report_path}: {type(error).__name__}")
    if report.get("engine_hash") != engine_hash():
        return Check(
            "balance",
            "FAIL",
            "the report was made under another engine; run the calibration again",
        )
    if run is not None:
        # The report must measure worlds like this one (calibration maps are square).
        config = run.manifest.config
        expected: dict[str, tuple[object, str, str]] = {
            "council_interval_days": (
                config.council_interval_days,
                "councils every {} days",
                f"councils every {config.council_interval_days} days",
            ),
            "size": (
                config.width if config.width == config.height else None,
                "{0} by {0} worlds",
                f"a {config.width} by {config.height} world",
            ),
            "civilizations": (
                config.civilizations,
                "{} civilizations",
                f"{config.civilizations} civilizations",
            ),
        }
        for key, (wanted, measured, this) in expected.items():
            found = report.get(key)
            if found is None:
                return Check(
                    "balance",
                    "FAIL",
                    f"the report does not say its {key.replace('_', ' ')}; make it again",
                )
            if found != wanted:
                return Check(
                    "balance",
                    "FAIL",
                    f"the report measured {measured.format(found)}; this run has {this}",
                )
    notes: list[str] = []
    if report.get("engine_version") != ENGINE_VERSION:
        notes.append(f"made under engine {report.get('engine_version')}")
    histories = int(report.get("histories") or 0)
    if histories < MIN_HISTORIES:
        notes.append(f"only {histories} histories")
    if not report.get("passed"):
        failed = [check["check"] for check in report.get("checks", []) if not check["passed"]]
        reason = failed[0] if failed else "the report did not pass"
        if options.accept_balance_failure:
            notes.append(f"failed ({reason}), accepted: {options.accept_balance_failure}")
            return Check("balance", "WARN", "; ".join(notes))
        return Check("balance", "FAIL", f"failed: {reason}")
    detail = "; ".join([f"passed, {histories} histories", *notes])
    return Check("balance", "WARN" if notes else "PASS", detail)


def _check_seal(store: WorldStore, run: _Run | None, options: GateOptions) -> Check:
    from sovereign_world.rulehash import engine_hash, rule_hash
    from sovereign_world.seal import SealRefused, check_seal, grouped, stored_seal

    try:
        seal = stored_seal(store)
    except Exception as error:
        return Check("seal", "FAIL", f"the seal cannot be read: {type(error).__name__}")
    if seal is None:
        if options.launch:
            return Check("seal", "FAIL", "the run is not sealed")
        return Check("seal", "SKIP", "not sealed yet: seal it, then run with --launch")
    if run is None:
        return Check("seal", "FAIL", "the run did not load")
    try:
        check_seal(store, run.manifest, seal, code_hash=rule_hash(), signer=options.signer)
    except (SealRefused, ValueError) as error:
        return Check("seal", "FAIL", str(error))
    if seal.engine_hash != engine_hash():
        return Check("seal", "FAIL", "the engine hash differs from the sealed one")
    if seal.planned_days != options.planned_days:
        return Check(
            "seal",
            "FAIL",
            f"the seal plans {seal.planned_days} days, not {options.planned_days}",
        )
    signer = "" if options.signer else "; no signer named (--signer)"
    return Check(
        "seal",
        "PASS" if options.signer or not options.launch else "WARN",
        f"sealed by {grouped(seal.fingerprint)} for {seal.planned_days} days{signer}",
    )


def _check_seal_key(options: GateOptions, environ: Mapping[str, str]) -> Check:
    from sovereign_world.seal import SEAL_KEY_ENV

    if environ.get(SEAL_KEY_ENV):
        return Check(
            "seal_key",
            "FAIL" if options.launch else "WARN",
            f"{SEAL_KEY_ENV} is still in the environment; remove it once the run is sealed",
        )
    return Check("seal_key", "PASS", "absent")


def _check_observer_token(environ: Mapping[str, str]) -> Check:
    token = environ.get(TOKEN_ENV, "")
    if not token:
        return Check(
            "observer_token",
            "WARN",
            f"{TOKEN_ENV} is not present; `observe` makes a new token each time it starts",
        )
    if len(token) < MIN_OBSERVER_TOKEN_CHARS:
        return Check(
            "observer_token",
            "FAIL",
            f"{len(token)} characters; at least {MIN_OBSERVER_TOKEN_CHARS} needed",
        )
    return Check("observer_token", "PASS", f"{len(token)} characters")
