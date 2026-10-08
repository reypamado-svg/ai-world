# The sealed trial: exit and record

The roadmap's last phase ends with: "the signed manifest is immutable, all launch-gate checks
pass, and the live world can recover from the last checkpoint without divergent replay."

This page has two parts:
1. **The exit mapping:** each part of that sentence, the tests that prove it on stand-ins here,
   and what only the real year on your PC can prove.
2. **The year's record:** blanks to fill in from the run, field for field what the
   [runbook](sealed-trial-runbook.md)'s section 9 asks you to send back. Filled in, it closes
   the phase.

The checks themselves are in the [launch gate](launch-gate.md); watching from outside is in
[observer-remote](observer-remote.md). A test (`test_sealed_trial_record.py`) keeps every test
named here real.

## 1. The exit mapping

| Exit clause | Proved on stand-ins by | Only the year on your PC proves |
|---|---|---|
| the signed manifest is immutable | `test_every_sealed_field_is_tamper_evident`, `test_a_changed_pin_charter_budget_or_key_breaks_the_signature`, `test_run_refuses_a_changed_manifest_before_writing_anything`, `test_run_refuses_a_changed_or_replaced_seal`, `test_run_refuses_other_code_and_verify_reports_it`, `test_only_a_fresh_run_is_sealed_and_only_once`, `test_the_seal_is_saved_with_the_run_and_in_its_journal_and_the_key_nowhere`, `test_verify_names_and_pins_the_signer`; end to end, `test_the_sealed_trial_world_passes_its_gate_and_recovers` | the key kept out of every file and cleared after `seal`; `verify --signer` passing at the end of the year with the code unchanged since `seal` |
| all launch-gate checks pass | `test_a_fresh_four_provider_run_passes_offline`, `test_the_launch_pass_checks_the_seal_and_the_day`, `test_a_probe_asks_each_stub_once_and_journals_nothing`, `test_preflight_writes_nothing_prints_no_secret_and_drops_the_seal_key`, `test_the_observer_token`, `test_a_free_three_provider_world_passes_its_gate_and_runs`, `test_the_gate_names_what_would_stop_a_free_trial`; the doc and the code kept equal by `test_every_check_in_the_code_is_in_the_doc_and_back` | `launch-gate.json` with no FAIL, from the real probe (`claude -p`, `codex exec` and Ollama), the real disk and the real clock |
| the live world can recover from the last checkpoint without divergent replay | `test_every_crash_point_resumes_to_the_same_journal`, `test_random_kills_resume_to_the_same_journal`, `test_a_day_stopped_after_its_councils_resumes_without_asking_again`, `test_a_checkpoint_is_saved_every_thirty_days_and_changes_no_journal_byte`, `test_reference_run_keeps_every_daily_hash`, `test_the_run_stops_before_a_day_that_could_pass_the_cap`; the kill and resume in `test_the_sealed_trial_world_passes_its_gate_and_recovers` | every real stop (Ctrl+C, reboot, crash) followed by `verify --signer` passing and `run` carrying on, with no model asked twice (`councils --json`: one council per civilization per council day) |

The stand-ins are the four stub models of the Phase 5 exit test and the stand-in `claude` and
`codex` programs of the free-trial exit test. They answer as the real ones document, but only
the real programs show how a sign-in, a usage limit or a slow local model behaves.

## 2. The year's record

Fill this in as the year goes. Commit the launch gate's JSON beside this page as
`sealed-trial-launch-gate.json`; never commit the run's folder itself.

### The world

| What | Value |
|---|---|
| Seed and start rotation | |
| Map, civilizations, councils | 32 × 32, 3, every 28 days (crisis gap 7) |
| Claude (Claude Code): model, as the probe named it | |
| ChatGPT (Codex): model and reasoning effort | |
| Local model (Ollama) | |
| `claude --version`, `codex --version` at the start | |
| … and at the end | |
| Engine hash (from the balance report) | `16b24380a381a1471e78d5338ee8513f3376d81bc83fe58cecbee6b66ce855a0` |
| Code hash at sealing (from `seal`) | |
| Seal fingerprint | |
| Sealed on (Dubai time) | |
| Planned days, pace | 365, `--pace 600` |

### The launch gate

| What | Value |
|---|---|
| `preflight --launch`: PASS / WARN / FAIL / SKIP | |
| Each WARN, word for word | |
| The last line (`ready to launch: sealed by …`) | |

### Spending (from `spend work\trial`)

| What | Value |
|---|---|
| Input tokens spent, of 6,000,000 | |
| Output tokens spent, of 1,500,000 | |
| Worst-case rounds still covered | |
| Any model without a price | |

### Councils by provider (from `councils work\trial --json`)

| Civilization and provider | Councils | Accepted | Repaired | Malformed | Timeout or late | Refused | Unavailable | Mean tokens in / out | Answered by another model |
|---|---|---|---|---|---|---|---|---|---|
| | | | | | | | | | |
| | | | | | | | | | |
| | | | | | | | | | |

`councils work\trial --errors`: the first line of each council that was not accepted (usage
limits show as `unavailable` with "usage limit").

### Stops and restarts

| Day | What stopped it (Ctrl+C, reboot, crash, cap) | `run` exit code | `verify --signer` after | A model asked twice? |
|---|---|---|---|---|
| | | | | |

### The end

| What | Value |
|---|---|
| Final `verify work\trial --signer FINGERPRINT` line | |
| Last day saved, last checkpoint day | |
| Started and ended (Dubai time) | |
| Size of the run's folder | |

### Checks only your PC can make

- [ ] the probe's line for `claude -p` and `codex exec`, each answered by its own model
- [ ] a used-up allowance shows as `unavailable` with "usage limit", and the run goes on
- [ ] Windows finds `claude.exe` and `codex.exe` (the gate's `endpoints` line)
- [ ] `DISABLE_AUTOUPDATER` kept Claude Code's version for the year
- [ ] one Ctrl+C during a `--pace` wait ends with `stopped at day N`, and `verify` passes after
- [ ] `preflight` and `run` print cleanly in the console and into `launch-gate.json`
- [ ] Ollama holds its 32,768-token context on the card (`ollama ps`) and answers in time
- [ ] the run's folder is on a local NTFS drive
- [ ] `doctor` passed before the rehearsal (every line PASS or SKIP, each WARN read)
- [ ] a second `run` in another window was refused (exit code 5)
- [ ] (if shared) a phone off the home Wi-Fi opens the viewer link, which shows **VIEWING ONLY · shared**, and your own link through the tunnel is refused

### What happened

Contacts, treaties, wars, eliminations, how each model played, and anything to carry forward:
