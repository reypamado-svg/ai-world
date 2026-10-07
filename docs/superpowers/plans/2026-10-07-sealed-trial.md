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
| Players | Four providers: Anthropic, OpenAI, Google Gemini (its OpenAI-compatible endpoint) and a local Ollama model |
| Where it runs | The user's Windows PC first; reachable from outside afterwards, as its own slice |
| Spending | A hard cap per run |
| Length | One year (365 days) |
| Sealing | An Ed25519 public-key signature |

The models, the cap, the seed, the size, the rotation and the pace are chosen on launch day.

## Slices

| Slice | What | Status |
|---|---|---|
| A | Balance calibration with rotated starts | Code done (c70dd1f, 098acef); the 2,000-history matrix is running |
| B | Usage recording and the spending cap | Done (this commit) |
| C | Crash-safe recovery | Planned |
| D | Sealing | Planned |
| E | The launch gate | Planned |
| F | The live run on the user's PC | Planned |
| G | Reachable from outside | Planned |
| H | Docs and close | Planned |

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
