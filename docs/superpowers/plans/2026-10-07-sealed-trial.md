# The Sealed Trial World (roadmap Phase 5)

Planned with Fable 5.1 at 3cb7cd0. The roadmap's last phase: calibrate the starts' balance
with rotated starting packages and thousands of scripted histories, freeze and sign the run's
settings, and launch one real world with four model-played sovereigns, watching only its
integrity and infrastructure.

**Exit:** the signed manifest is immutable, every launch-gate check passes, and the live world
recovers from its last checkpoint without a divergent replay.

## The user's decisions

| Question | Decision |
|---|---|
| Players | Planned as four providers (Anthropic, OpenAI, Gemini, Ollama); changed on 2026-10-08 to **three civilizations at no cost**: Claude through Claude Code (Claude Pro sign-in), ChatGPT through Codex (ChatGPT Plus sign-in), and a local Ollama model |
| Where it runs | The user's Windows PC first; reachable from outside afterwards, as its own slice |
| Spending | A hard cap per run; for the free trial, in tokens (6,000,000 in, 1,500,000 out), every price zero |
| Length | One year (365 days) |
| Sealing | An Ed25519 public-key signature |

Chosen on 2026-10-08: a 32 by 32 map, councils every 28 days, `--pace 600` (about 2.5 days for the year), the calibration made in the cloud session. The exact model names (as the probe reports them) and the seed are chosen on launch day.

## Slices

| Slice | What | Status |
|---|---|---|
| A | Balance calibration with rotated starts | Done: the 2,000-history report passed (`docs/calibration/2026-10-year-one/`) |
| B | Usage recording and the spending cap | Done (36dcb6c) |
| C | Crash-safe recovery | Done (bcc35ad) |
| D | Sealing | Done (6bbdff1) |
| E | The launch gate | Done (1b38673) |
| F | The live run on the user's PC: progress, pace, `councils`, the runbook | Built; the run itself is the user's |
| G | The free three-civilization trial: Claude Code and Codex adapters, 2-4 civilizations in the calibration and the gate, token caps, the runbook | Built (G1-G4); the balance report in G5 |
| H | Reachable from outside | Planned |
| I | Docs and close | Planned |

## A as built

- **One engine version.** `config.ENGINE_VERSION = "0.2.0"`, used by `init`.
- **Start rotation.** `RunManifest.start_rotation` (left out of the hash at 0) gives civilization
  *i* the start, regional skill and founders of package `(i + r) % n`. The founders' random
  stream follows the package, so a package moves whole. `init --start-rotation`; a fork keeps
  its parent's rotation.
- **`SovereignConfig.label`** names a compatible vendor ("gemini", "ollama"); left out of the
  hash while empty.
- **A fresh run verifies on day 0.** Founding observations need only be unique on day 0 (sorting
  them would have changed 16 reference hashes); from day 1 they must be sorted, as before.
- **Explorers in any order.** The engine sorts an expedition's explorers, and an explorer named
  twice is refused (`invalid_expedition`). The calibration policies found the crash.
- **`sovereign_world.calibration`:**
  - `policies.py`: builder (the baseline), expander, trader and raider, each driven by the
    council report alone;
  - `histories.py`: one history in memory, a row per civilization (population, peak, tiles,
    settlements, win share, wars, battles, refused orders, elimination day);
  - `batch.py`: `python -m sovereign_world.calibration run --seeds --size --days --rotations
    --assignments --workers`, appending to `histories.csv` and resumable; `--quick` for tests;
  - `report.py`: `report DIR` writes `report.md`, `summary.csv` and `report.json`.
- **Thresholds.** Builders (four identical policies): mean population per start position within
  ±15% of the overall mean, survival at least 95%, win share 15–35%. Mixed (one of each policy):
  each policy within ±20% across positions.
- **`rulehash.rule_hash()`:** the sha256 of the package's `.py` sources, `observer/` left out,
  line endings normalised. The report records it.

### The matrix (2,000 one-year histories)

250 seeds × 4 start rotations × 2 assignments (four builders; one builder, expander, trader and
raider), 32×32 worlds, 365 days, rules 3, the scripted policies only. It took about 2.4 hours on
3–4 cores; the report, its tables, the run record and every history's rows are in
`docs/calibration/2026-10-year-one/`, under engine hash `1200ba13e4fc4922…`.

**Every threshold passed.** Builders: each start position's mean population within 0.03% of the
mean, survival 100%, win shares 24.7–25.4%. Mixed: every policy within 0.6% across positions.

**What the numbers also say.** The thresholds pass partly because a scripted year is quiet:

- **Little happens.** 32 founders become 33.6 on average (39 at most); a builder never founds a
  second settlement; expanders reach 2.7 settlements.
- **Contact is rare.** A raider declares war in about 1 history in 10 and fights 0.16 battles a
  year; nobody else fights at all. Explorers on a 32×32 map met another people in about 1 of 16
  civilizations in the probes. On the planned 64×64 trial map, four model-played civilizations
  would very likely never meet in a year.
- **Start position 0 holds less land.** Builders at position 0 hold 46 tiles at year end against
  51–54 at the others (14% below position 3). Territory is not one of the thresholds, and in a
  year it does not turn into people, but over a longer run it could.
- **Acting first is not an advantage here.** By civilization id, builders' win shares are 23%,
  25%, 25% and 27%: the first to act wins least.

These are for the user to weigh before launch: a smaller map, a longer run, or starts that know
their neighbours would make contact likely; position 0's land could be evened in the generator.

**Found on the way:** an engine crash (a war party losing captives; fixed in 6bbdff1) and the
batch's silent stall after a failing history (fixed in the same commit).

## B as built

- **Usage on every council.** `CouncilRecord.usage` holds one `ModelUsage(purpose, model,
  input_tokens, output_tokens)` per call, with the model the provider says answered (a fallback
  shows). It is left out of the record while empty, so councils without a model are saved as
  before, and old records load. It never reaches a prompt, a report or the world: the same
  council with different token counts has the same prompt hash and envelope. Elapsed time is not
  recorded, because it would make two runs' journals differ.
- **The cap.** `RunManifest.spend` (left out of the hash while unset) is a `SpendConfig`:
  `max_cost_usd`, optional `max_input_tokens` and `max_output_tokens`, a price table by model,
  `unknown_model` ("highest" prices an unlisted model at the table's dearest rates; "refuse"
  stops the run) and `reserve_councils` (default 1). Settings files take a `[spend]` table.
- **The worst-case council round** (`spend.worst_case_round`): every model-played civilization
  asks twice (the turn and a repair), each time with the charter allowance (32,000 characters),
  every budgeted section full and 2,000 characters of slack, at 3 characters a token, and writes
  the most output allowed; priced at its model's rate.
- **Before every day** `run_days` asks `spend.stop_reason`. While spent plus the reserve of
  worst-case rounds would pass any cap, it saves a checkpoint (if the session advanced a day)
  and raises `SpendCapReached`; no transition is written. What was spent is counted from the
  journal at start, so a resumed run counts what it spent before.
- **Command line.**
  - `run --spend-limit USD` lowers the cap for the session; it never raises it.
  - A run stopped by the cap prints `stopped before day N: …` and exits with code 3.
  - `run --controlled` reports `stopped N spend_cap`; the observer's runner chip shows "stopped
    at day N: the next day could pass the run's spending cap".
  - `sovereign-world spend RUN_DIR [--dry-run]`: spent by civilization and by model, unpriced
    models, the worst-case round, what remains and how many worst-case rounds it covers, and
    whether the next day may run. `--dry-run` also sizes one call per model-played civilization
    from the latest day's real prompts, asking no model.
- **Tests:** `tests/test_spend.py` (recording, prices, the worst case, every cap, refusal of
  unpriced models, the session limit, the manifest hash) and
  `tests/integration/test_spend_cap.py` (a run stops before the day that could pass the cap,
  keeps its checkpoint, asks no model again on resume, still verifies; the command line exits
  with 3 and reports the spend); a runner-link test for the new report.

## C as built (detailed by Fable 5.1 at 36dcb6c)

**The gap it closes.** A run day used to advance the world (asking the models), save the day,
then save its councils, with one checkpoint at the end. A kill between the day and its councils
lost paid replies and left a run `rederive` could never pass; a kill during the third model's
call threw away the first two replies.

- **Councils saved as they are held.** `run_days` gives every recording sovereign a sink
  (`record_to`); each council is appended to the journal the moment `decide` finishes, before
  the day's transition. Only a reply still in flight can be lost to a kill. Without a sink
  (scenarios, tests) councils queue for `journal_councils` as before.
- **Resuming.** On start the journal's councils are split against the resumed day S
  (`split_resumed`): earlier days are the sovereigns' history; councils of day S are the
  interrupted day's (`ResumedCouncils`), never put into history, never written again, and
  counted once in the spend. When day S is held again, each civilization's saved council is
  given back: the model-played one returns its saved orders after checking the report id and
  the prompt hash; a scripted one checks that it gives the same orders. Nothing is asked.
- **Refusals** (`ResumeRefused`, `run` exits 1 with `cannot resume: …`), before anything is
  asked or written:
  - a council of a day after the last saved day;
  - two councils of one civilization, or of two days;
  - a saved council of a civilization that is gone, not recorded, or does not sit in council
    that day (the monthly council, or a crisis council for sovereigns that take them);
  - a saved council asked a different question, or (scripted) giving other orders;
  - a saved council not held again.
  The engine swallows errors from `decide`, so the refusal is carried on the shared
  `ResumedCouncils` and raised by the runner after the day is advanced and before it is saved.
- **Why the journal ends the same.** With the same replies, an unbroken run and any run killed
  and resumed write the same records in the same order: a day's councils in civilization order,
  each once, then the day. Records carry no time, so their bytes and hash chain are equal.
  Only the checkpoint table may differ (a stop adds one).
- **Checkpoints every 30 days** (`CHECKPOINT_INTERVAL`), and at the end; SQLite only, no
  journal byte changes.
- **Ctrl+C** (`interruptible`, in `run` for both modes): the first stops after the day under
  way and prints how to stop at once; the second raises `KeyboardInterrupt`. `run` then says
  `stopped at day N` and exits 0. A paused controlled runner waits in half-second steps so
  Ctrl+C reaches it on Windows too. The observer's protocol is unchanged.
- **Crash hook, for tests only.** `SOVEREIGN_WORLD_CRASH_AT=point:day[:n]` ends the process at
  once (`os._exit(137)`) at `before_day`, `after_council` (the n-th council saved that day),
  `after_councils`, `after_transition` or `after_checkpoint`. Nothing sets it outside the kill
  test.
- **Tests:**
  - `tests/test_resume.py`: councils before their day; a day stopped after its councils, and one
    stopped after two of four, resume to the unbroken journal byte for byte with no model asked
    again; spend counted once; each refusal, with the journal unchanged; checkpoints at 0, 30
    and the end; Ctrl+C once and twice.
  - `tests/integration/test_kill_recovery.py`, with `tests/stub_model.py` (an OpenAI-compatible
    stand-in on loopback): two model-played civilizations, `sovereign-world run` killed at 11
    points across days 0–31, then resumed: the journal equals the unbroken run's, `verify`
    passes, every council's model was asked exactly once. Random kills (two seeds, at least two
    kills each, some inside a model call) end the same way. A real SIGINT stops a running world
    after its day (not on Windows, where a test cannot send one).
- **Limit:** a run recorded before this slice that lost its councils at a kill cannot be
  repaired; `rederive` still fails on it.

## D as built (detailed by Fable 5.1 at bcc35ad)

- **Keys.** `sovereign-world keygen` prints `SOVEREIGN_WORLD_SEAL_KEY=swseal1:<43 base64url
  characters>` (an Ed25519 private seed) and its fingerprint (the sha256 of the 32-byte public
  key, shown in groups of four hex digits), and writes nothing. Only `seal` reads the variable;
  `seal`, `run` and `observe` remove it from their own environment, and the observer never
  passes it to the runner it starts. Errors about the key never show its value.
- **The seal** (`seal.py`, a signed `Seal` document, canonical sorted-key JSON as the manifest
  hash uses): the run id; the manifest hash; the code hash (`rule_hash`, every package source
  but the observer's) and the engine hash (`engine_hash`, see below); the engine, generator,
  rules and journal versions; the prompt version and the reply schema's hash; each
  civilization's charter hash (the charter depends only on who it is and the rules version, so
  it holds for the whole run); the provider pins (kind, model, scheme, host with port, path,
  and the name of the token variable); the budgets; the spending cap; the planned days; the
  start rotation; the public key and the signature.
- **Sealing.** `sovereign-world seal RUN_DIR --days 365` on day 0 only, after offline checks
  (`preflight.offline_checks`: day 0, journal format 2, nothing saved but the header, not yet
  sealed, every sovereign in the world, model-played ones on this engine's prompt version with
  a model named, every endpoint readable). The seal is saved in a SQLite `seal` table and as
  the journal's `seal` record right after the header. It prints the fingerprint, the hashes and
  each pin, never the key.
- **Enforcement.** Before anything is replayed, asked or written, `run` (and the observer's
  runner) checks the signature, the manifest hash, run id, versions, budgets, cap, rotation and
  provider pins against the run, the journal's seal record and header, and the code hash.
  Any difference refuses the run (`SealRefused`, exit code 4, `seal refused: …`). Hosted models
  are reached only at their sealed addresses (`https://api.anthropic.com`,
  `https://api.openai.com/v1`), passed to the clients so `ANTHROPIC_BASE_URL` and
  `OPENAI_BASE_URL` have no effect. A request for more days than the plan has left is cut to
  what is left; a run at its planned end is refused.
- **`verify`** now checks that the journal's header names the stored manifest and that every
  saved day carries the manifest hash the run started under. For a sealed run it also checks
  the seal, and `verify --signer FINGERPRINT` refuses a run sealed by another key. Code that
  differs from the sealed code is reported, not refused, so an archived run still verifies.
- **A fork of a sealed run** is a new, unsealed run (it may be sealed anew).
- **The observer** answers `GET /api/run/seal` (behind the token) with `{"sealed": false}` or
  the seal's public parts, its fingerprint and whether its signature verifies; a live page
  shows `SEALED · 1a2b 3c4d 5e6f 7a8b`, in warning colours if the signature does not verify.
- **Two code hashes.** The seal freezes everything that runs the world (`rule_hash`), because
  the runner, the cap, the gateway and the seal's own checks must not move under it. The
  balance calibration measures only the engine and the scripted policies, so it records
  `engine_hash`, which leaves out the observer, the gateway, running, saving, replaying,
  spending, sealing and the calibration's own batch and report; a fix there does not make a
  finished 2,000-history calibration stale. The launch gate (slice E) checks the report's
  engine hash.
- **Limit.** Whoever holds the key can seal a changed run anew; the fingerprint the operator
  checks with `verify --signer` is what ties a run to its key.

### An engine bug the calibration found, fixed alongside D

A raiding party that lost one of its eight fighters to capture at home kept the food it had
packed for eight, which seven cannot carry, so the world stopped on its own validity check
(seed 10, mixed policies, rotation 1, day 140). Captured fighters now take their share with
them: the food packed (and, if that is not enough, gear) is cut to what the rest can bear. A
party that still fits keeps its load exactly, so every run that did not crash is unchanged.
The calibration batch also now names a history the engine fails on (`failures.txt`) and plays
the rest, instead of stopping at the first and silently playing on.

## E as built (detailed by Fable 5.1 at 6bbdff1)

- **`sovereign-world preflight RUN_DIR [--probe] [--launch] [--signer FP] [--calibration REPORT]
  [--accept-balance-failure REASON] [--days N] [--json]`**: 17 checks, each PASS, WARN, FAIL or
  SKIP, then the counts; exit 0 only with no FAIL. The table, with what each check maps to in
  spec §19 and the roadmap, is `docs/launch-gate.md`; a test keeps the two identical.
- **What it checks:**
  - the run itself (`verify`'s checks, now `replay.verify_whole`), day 0, a pinned engine
    self-test (a fixed 24 by 24 world, 10 scripted days, under a second) and the code hashes;
  - four civilizations played by models on four distinct (kind, host) providers, all on this
    engine's prompt version;
  - the cost cap set, every model priced (an unpriced model would be priced at the table's
    dearest rate, nothing on an empty table), and room for a year's 13 worst-case rounds;
  - every token variable present (by name), and the endpoint policy (hosted models at their
    own addresses; others `https` with a token, or on this computer);
  - with `--probe`, one tiny call per provider through the provider the run uses, reporting
    the answering model, time and tokens, journaled nowhere and counted against no cap; and
    every remote host's clock;
  - disk space (FAIL under 2 GiB, WARN under 10), and a plausible clock;
  - the balance report: passed, under this engine hash. A failed one is a FAIL unless the
    operator passes `--accept-balance-failure "REASON"`, which makes it a WARN printing the
    reason, kept nowhere in the run;
  - with `--launch`: the seal (signature, signer, every sealed field, the engine hash and the
    planned days), the seal key out of the environment, and the observer token's length.
- **It writes nothing** and prints no secret: errors from a provider are cut to one line with
  every known token value removed. It removes the seal key from its own environment, as `run`
  does. `seal` keeps its own offline checks only (the sealing shell holds the seal key and
  nothing else) and prints the `preflight --launch` command to run next.
- **`verify`** now says why it fails (`not verified: …`) instead of exiting silently.
- **The Phase 5 exit test** (`tests/acceptance/test_phase_five_exit.py`): four stand-in models
  on four loopback ports; the gate passes before sealing with a probe (each model asked once);
  a failed balance report stops it unless accepted with a reason; `keygen`, `seal --days 95`;
  the launch pass is ready, and fails with the seal key still present or another signer; the
  sealed world runs, is killed after the second council of day 84 (its third 28-day council), resumes, and verifies by its
  signer through day 95 with no model asked twice; a changed manifest is then refused by `run`
  (exit 4) and by the gate.
- **Defaults taken** (the user may change them): the override is printed, not recorded in the
  seal; disk thresholds 2 and 10 GiB; the clock floor is the day the gate was written.

## Adjustable council cadence (detailed by Fable 5.1 at 1b38673)

The user asked whether the councils' frequency can be adjusted, and chose: regular councils
every 1, 2, 3 or 4 weeks, set when a world is made, 4 weeks by default; 30 days only for worlds
made before; and the crisis councils' spacing settable too.

- **Settings.** `WorldConfig.council_interval_days` accepts 7, 14, 21, 28, or 30 for older
  worlds; `crisis_gap_days` (default 7, 0 holds no crisis councils) is left out of every hash at
  its default, so every world made before keeps its manifest, state, journal and checkpoint
  hashes. `sovereign-world init --council-interval {7,14,21,28}` (default 28) and
  `--crisis-gap N`; `inspect` shows both; a fork keeps its parent's; the seal covers both
  through the manifest.
- **Engine.** One `council_day(state)` rule decides regular council days everywhere (engine,
  resume check, scenario helper). The crisis gap is the world's own. Escapes, petition
  refusals and rank steps stay "once a council": with weekly councils they come four times as
  often as with 28-day ones, and so do model calls and their cost.
- **The charter** says the world's cadence ("Every 14 days (on day 0 and every 14th day after),
  and when a crisis strikes (at most once in 3 days), ..."). For a 30-day world with the usual
  gap it is byte-identical to before (pinned by test for rules 1, 2 and 3), so recorded prompts
  and the prompt version (council-7) are unchanged. The council report carries both settings,
  left out at 30 and 7.
- **Calibration** takes `--council-interval` (default 28) and records the interval, map size,
  days and civilizations in `run.json` and `report.json`; the scripted policies count councils
  rather than days (unchanged for monthly worlds). **The launch gate** fails a report made for
  another interval, map size or number of civilizations, and counts the cap's room in the
  planned days' regular councils.
- **The observer** reads the interval from the export (named only when it is not 30, so older
  exports are unchanged), and "at its last council" lands on the world's own council day.

**Before launch:** the balance report in `docs/calibration/2026-10-year-one/` measured 30-day
councils, and the engine hash has moved, so the trial needs a fresh calibration at its own
interval and map size (about 2.4 hours unattended).

## F as built (detailed by Fable 5.1 at 3125d40)

The code a year-long run on the user's PC needs, and the runbook to do it with. The rehearsal
and the year itself are run by the user.

- **`run` shows its progress.** A header (`run <id> day S -> T, councils every N days, cap $X`),
  a line for each council the moment it is saved (civilization, vendor, model, outcome, tokens,
  cost, seconds into the day), a line for each council day (councils, seconds, spent against
  the cap) and for each checkpoint, and nothing on quiet days; ASCII only, on standard output.
  Two hooks on `run_days` (`on_council`, `on_progress`) carry it; the times are measured there
  and recorded nowhere, and the journal is byte-identical with and without them (tested). The
  controlled runner (`observe --run-days`) passes no hooks, so its protocol is unchanged.
- **`run --pace SECONDS`** waits between days, so a year can run over days or weeks. It waits
  in half-second steps, and the first Ctrl+C ends the wait: the run stops at that day with its
  checkpoint. A paced run records exactly what an unpaced one does (tested).
- **`councils RUN_DIR [--json] [--errors]`** sums up each civilization's councils: vendor and
  model, outcomes (accepted, repaired, timeout, late, malformed, refused, unavailable), mean
  tokens, cost, models that answered in place of the configured one, and unpriced models.
  `--errors` lists every council whose first reply failed, with key values removed. It never
  asks a model and never writes.
- **The console.** A console that cannot show a character prints `?` instead of failing, and
  the settings file is read as UTF-8 whatever the PC's code page.
- **The runbook**, [`docs/sealed-trial-runbook.md`](../../sealed-trial-runbook.md), in
  PowerShell 7: set-up with uv, Ollama's context length, the settings file, the balance report,
  keys typed in masked, the seven steps (make, gate, rehearse, seal, gate again, run, recover),
  keeping the PC awake, backups, pace in Dubai time, and what to send back.
  `tests/acceptance/test_sealed_trial_runbook.py` keeps it honest: every command and option it
  names exists, it sets only known variables and types keys only masked, and its example
  settings make a four-provider world with every model priced and a cap.
- **The rehearsal** is a second world made with the same settings and run 31 days with a small
  `--spend-limit`: two council days with real models, real prompts and real time limits, read
  with `spend`, `councils --errors` and `verify`.
- **The engine hash is unchanged**: the new code lives in files outside it (`cli.py`,
  `runner.py`, `spend.py`, `preflight.py`). The rule (code) hash moves, so the code on the PC
  must be final before `seal`.
- **A flaky test made robust.** The random-kill recovery test failed about 2 runs in 5 even
  before F: its 35-day run could end before a second random kill landed. The killed runs are
  now paced (0.2 s), which records nothing and keeps each run alive across the kill window.

**Left for the user's PC** (the Linux suite cannot check them): `Read-Host -MaskInput` in
PowerShell 7.1+; Ctrl+C during a pace; the console output; Ollama and Gemini accepting the
council requests, reporting usage and a priced model name, and answering in time; the run on a
local NTFS drive.

**Defaults taken:** progress on standard output; quiet days silent; council times in seconds
since the day began; `councils` includes baseline-played civilizations (no cost); the rehearsal
uses the trial's settings for 31 days with `--spend-limit 10`.
