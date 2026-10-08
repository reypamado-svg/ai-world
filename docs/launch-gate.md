# The launch gate

Real AI sovereigns may enter a sealed world only after it passes this gate (spec §19, and the
roadmap's Phase 5 exit: "the signed manifest is immutable, every launch-gate check passes, and
the live world recovers from its last checkpoint without divergent replay").

`sovereign-world preflight RUN_DIR` prints one line per check, `PASS`, `WARN`, `FAIL` or `SKIP`
(not asked for), then the counts. It exits 0 only when nothing FAILs. It writes nothing, asks a
model only with `--probe`, and prints only the names of the variables that hold keys, never
their values. `--json` prints the same as JSON, to keep outside the run's folder as the record.

## The two passes

1. **Before sealing:** `preflight RUN --calibration REPORT --probe`. Fix every FAIL.
2. `keygen` (once; keep the key safe and the fingerprint written down), then
   `seal RUN --days 365` with the key in `SOVEREIGN_WORLD_SEAL_KEY`, then remove the key from
   the shell.
3. **Before launch:** `preflight RUN --launch --signer FINGERPRINT --calibration REPORT --probe`.
   It ends with `ready to launch: sealed by … for 365 days` when it passes.

## The checks

| Check | Title | PASS | WARN | FAIL | Maps to |
|---|---|---|---|---|---|
| `store` | the run loads, replays and verifies | the journal's chain, header and every day's hash, the replayed day, the checkpoint, and the world rederived from its councils | | any difference | §19 deterministic replay and recovery |
| `day` | the world has not begun | day 0 | later, before `--launch` | later, under `--launch` | §19 |
| `engine` | the engine replays its pinned self-test | a fixed 24 by 24 world, 10 scripted days, hashes as pinned | | another hash | §19 deterministic replay |
| `code` | the code's hashes | always: the rule and engine hashes, for the record | | | §19 rules frozen and reviewable |
| `players` | four civilizations, each played by a model | four, each with a model provider and model | | fewer, or one scripted | roadmap: a four-sovereign world |
| `providers` | four distinct providers (kind and host) | four distinct (kind, host) pairs | | fewer | the user's decision: four providers |
| `prompts` | every sovereign on this engine's prompt version | all on it; the reply schema's hash shown | | any other (fork the run) | §19 prompts frozen |
| `spend` | budgets, the hard cap and prices | a cost cap, every model priced, room for a worst-case round at each regular council of the planned days (14 in a year of 28-day councils) | room for fewer | no cap, or a model without a price | the user's decision: a hard cap |
| `keys` | the key variables are present (names only) | every token variable present; loopback endpoints need none | `ANTHROPIC_BASE_URL` or `OPENAI_BASE_URL` present (ignored by a sealed run) | a token variable missing | credentials only from the environment |
| `endpoints` | the endpoint policy | hosted models at their own addresses; others `https` with a token, or on this computer | plain http on a private network; a compatible endpoint without a label | plain http off this computer; a remote endpoint with no token variable | §19 remote endpoint policy frozen and reviewable |
| `probe` | one tiny real call per provider | each answered by its own model, priced | a fallback answered, or an unpriced model | an error | provider resilience; infrastructure |
| `disk` | disk space for a year | 10 GiB free or more | 2 to 10 GiB | under 2 GiB | infrastructure |
| `clock` | the clock is plausible | after the run was last saved; with `--probe`, within 300 s of every remote host | a host is further off, or sent no date | before the run was last saved, or before this gate was written | infrastructure |
| `balance` | the balance report passed under this engine | passed, under this engine hash, for this run's council interval, map size and number of civilizations | none given (before `--launch`); another engine version; under 1,000 histories; failed but accepted | none given under `--launch`; unreadable; failed; another engine hash; another council interval, map size or number of civilizations | §19 every start is viable |
| `seal` | the run is sealed and matches its seal | the seal checks pass, by the named signer, for the planned days | no signer named under `--launch` | any difference; not sealed under `--launch` | roadmap: the signed manifest is immutable |
| `seal_key` | the seal key is out of the environment | absent | present, before `--launch` | present under `--launch` | only `seal` reads the key |
| `observer_token` | the observer token | 32 characters or more | not present (`observe` makes one each time it starts) | shorter | the observer's access boundary |

A `SKIP` means a check was not asked for: `probe` without `--probe`, `seal` before the run is
sealed (without `--launch`).

**The balance report.** `--calibration` takes the calibration's `report.json` or its folder,
such as `docs/calibration/2026-10-year-one`. If the report failed and you decide to launch
anyway, `--accept-balance-failure "REASON"` turns the FAIL into a WARN that prints the reason.
The reason is kept nowhere in the run; keep the `--json` output as the record.

**The probe** asks each model `Reply with {"ok": true}` once, with at most 256 output tokens, at
the address the run will use. It costs pennies, is journaled nowhere and is counted against no
cap.

## Spec §19 lines proved by the test suite

The gate checks what can change on the user's computer. The rest of §19 is proved by tests that
run with every change:

| §19 line | Tests |
|---|---|
| deterministic replay and recovery | `test_reference_run_keeps_every_daily_hash`, `test_every_crash_point_resumes_to_the_same_journal`, `test_random_kills_resume_to_the_same_journal`, `test_a_day_stopped_after_its_councils_resumes_without_asking_again` |
| state and information-boundary invariants | `test_engine_preserves_world_invariants`, `test_population_invariants_survive_daily_transitions`, `test_each_hidden_fact_leaves_the_perspective_alone`, `test_contact_is_a_private_civilization_record` |
| every start is viable | `test_generated_starts_are_viable_and_separated`, `test_the_quick_preset_writes_every_row_and_carries_on_where_it_stopped` |
| first contact to treaty, and to war | `test_first_contact`, `test_traders_agree_a_treaty_and_send_caravans`, `test_treaty_made_and_broken` |
| provider isolation and failure injection | `test_failures_become_provider_errors`, `test_claude_failures_become_provider_errors`, `test_openai_failures_become_provider_errors`, `test_day_zero_council_applies_valid_decree_and_isolates_failure` |
| no live state-editing capability | `test_cli_exposes_no_state_editing_command` |
| manifest, rules, prompts, assignments and endpoints frozen | `test_every_sealed_field_is_tamper_evident`, `test_run_refuses_a_changed_manifest_before_writing_anything`, `test_run_refuses_other_code_and_verify_reports_it`, `test_hosted_models_are_reached_at_their_sealed_addresses` |
| the spending cap | `test_the_run_stops_before_a_day_that_could_pass_the_cap` |
| the gate itself | `test_a_fresh_four_provider_run_passes_offline`, `test_the_launch_pass_checks_the_seal_and_the_day`, `test_preflight_writes_nothing_prints_no_secret_and_drops_the_seal_key`, `test_a_probe_asks_each_stub_once_and_journals_nothing`, `test_the_sealed_trial_world_passes_its_gate_and_recovers` |

## The four providers

As the user decided: Anthropic, OpenAI, Google Gemini through its OpenAI-compatible endpoint,
and a local Ollama model. A `sovereigns.toml` holds no secrets, only the names of variables:

```toml
[sovereigns."civilization:0000000001"]
provider = "anthropic"
model = "<chosen on launch day>"

[sovereigns."civilization:0000000002"]
provider = "openai"
model = "<chosen on launch day>"

[sovereigns."civilization:0000000003"]
provider = "compatible"
label = "gemini"
base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
token_env = "GEMINI_API_KEY"
model = "<chosen on launch day>"

[sovereigns."civilization:0000000004"]
provider = "compatible"
label = "ollama"
base_url = "http://127.0.0.1:11434/v1"
model = "<chosen on launch day>"

[spend]
max_cost_usd = 0  # chosen on launch day

[spend.prices."<each model, as its provider names it>"]
input_per_million_usd = 0
output_per_million_usd = 0
```

Anthropic and OpenAI read `ANTHROPIC_API_KEY` and `OPENAI_API_KEY`; Ollama on this computer
needs no token.

## On Windows

The whole procedure, in PowerShell 7, is in [the sealed trial runbook](sealed-trial-runbook.md):
set-up, keys typed in masked (`Read-Host -MaskInput` needs PowerShell 7.1 or later), both
passes of the gate, the rehearsal, sealing, the run and recovery. `preflight ... --json >
..\launch-gate.json` keeps the record outside the run's folder. The output has no colour codes,
and the disk check works on any drive.
