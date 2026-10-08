# The Sealed Trial: runbook for a Windows PC

This is the step-by-step for running the sealed trial world on your own Windows PC: one world,
four civilizations, each played by a different AI model (Anthropic, OpenAI, Google Gemini and a
local Ollama model), for one year (365 days), with a hard spending cap, sealed with your key.

Every command below is for **PowerShell 7** (`pwsh`), run from the project folder. What each
launch-gate line means, and what to do about a `FAIL`, is in [the launch gate](launch-gate.md).
What the commands do in general is in [the operator guide](rules-laboratory.md).

**The rules that never bend:**
- Keys live only in the PowerShell window's environment. They are typed in with
  `Read-Host -MaskInput`, never written to a file, never pasted into a command line, never
  echoed. They die with the window.
- The world can be watched (`observe`) but not edited. Nothing here changes a run except `run`.
- Once the run is sealed, do not update the code on this PC (`git pull`) until the year is over:
  a sealed run refuses other code (exit code 4).

## 1. Before the day

**PowerShell 7.** `Read-Host -MaskInput` needs PowerShell 7.1 or later; the Windows PowerShell
5.1 that ships with Windows does not have it. Check, and install if needed:

```powershell
$PSVersionTable.PSVersion
winget install Microsoft.PowerShell
```

Open "PowerShell 7" (`pwsh`) from then on.

**Python and uv.** Python 3.12 or later, and uv to install the project:

```powershell
py -3.12 --version
winget install astral-sh.uv
```

**The project.** Keep it at a short path on a local drive, not in OneDrive or any synced or
network folder (the run's database must be on a local NTFS drive), for example `C:\ai-world`:

```powershell
cd C:\ai-world
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe -e ".[dev,observer]"
$env:PYTHONUTF8 = "1"
& .venv\Scripts\sovereign-world.exe --help
```

Set `$env:PYTHONUTF8 = "1"` in **every** new window: it makes Python read and write UTF-8 on the
Windows console. (Without it nothing breaks: a character the console cannot show is printed as
`?`.)

**Ollama.** Install it from ollama.com, then give it a context large enough for a council. A
council's papers run to 25,000-30,000 tokens; Ollama's default context is far smaller and it
cuts a longer prompt **silently**, which shows up as `malformed` councils. Set it for your user
and restart Ollama (quit it from the tray and start it again):

```powershell
[Environment]::SetEnvironmentVariable("OLLAMA_CONTEXT_LENGTH", "32768", "User")
ollama pull YOUR-LOCAL-MODEL
ollama ps
```

`ollama ps` (while the model is loaded, for example after the probe in step 2) shows the
context it holds. Choose a local model your PC can answer with in time: each council is up to
30,000 tokens in and up to `max_output_tokens` out, within `timeout_seconds`; a reply after
the time is up is thrown away (`late`). The rehearsal (step 3) shows whether it copes. If it
is too slow, pick a smaller model or raise `timeout_seconds` before sealing (the budgets are
sealed, and a larger `max_output_tokens` raises the worst-case cost).

## 2. The settings file

Write `work\trial.toml` with your choices. It holds no secrets: only the names of the
variables that hold the keys.

```toml
[sovereigns."civilization:0000000001"]
provider = "anthropic"
model = "ANTHROPIC-MODEL"

[sovereigns."civilization:0000000002"]
provider = "openai"
model = "OPENAI-MODEL"

[sovereigns."civilization:0000000003"]
provider = "compatible"
label = "gemini"
base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
token_env = "GEMINI_API_KEY"
model = "GEMINI-MODEL"

[sovereigns."civilization:0000000004"]
provider = "compatible"
label = "ollama"
base_url = "http://127.0.0.1:11434/v1"
model = "OLLAMA-MODEL"

[budgets]
timeout_seconds = 300
max_output_tokens = 8000

[spend]
max_cost_usd = 100
reserve_councils = 1
unknown_model = "highest"

[spend.prices."ANTHROPIC-MODEL"]
input_per_million_usd = 3
output_per_million_usd = 15

[spend.prices."OPENAI-MODEL"]
input_per_million_usd = 3
output_per_million_usd = 15

[spend.prices."GEMINI-MODEL"]
input_per_million_usd = 3
output_per_million_usd = 15

[spend.prices."OLLAMA-MODEL"]
input_per_million_usd = 0
output_per_million_usd = 0
```

Replace every `...-MODEL` with the model's name and every price with the provider's current
price per million tokens (the figures above are placeholders, not prices). `max_cost_usd` is
your cap in US dollars.

- **Price names must match what answers.** The table is looked up by the model name the
  provider reports in its reply, which can differ from the name you asked for (a dated name, or
  a fallback). The probe in step 4 prints it; a model missing from the table is charged at the
  table's dearest rate (`unknown_model = "refuse"` stops the run instead). Claude may fall back
  to another model when a request is refused; list that model too.
- **Thinking models** spend part of `max_output_tokens` on thinking. If replies come back empty
  or cut off in the rehearsal, raise `max_output_tokens`.
- `reserve_councils` is how many worst-case council rounds the cap keeps in hand; the run stops
  cleanly before a day that could break the cap (exit code 3).

## 3. The balance report

The launch gate wants a balance calibration made under this code, at the trial's council
interval, map size and number of civilizations. Thousands of scripted histories, no model:

```powershell
& .venv\Scripts\python.exe -m sovereign_world.calibration run --out docs\calibration\2026-10-trial --council-interval 28 --size 32 --quick
& .venv\Scripts\python.exe -m sovereign_world.calibration run --out docs\calibration\2026-10-trial --council-interval 28 --size 32 --seeds 0-249 --workers auto
& .venv\Scripts\python.exe -m sovereign_world.calibration report docs\calibration\2026-10-trial
```

The `--quick` run (a minute) checks everything works; delete its folder before the real one,
or give the real one another `--out`. The real run takes hours (about 2.4 hours at 32 by 32 on
three cores; more for a larger map). It can be stopped and run again: it carries on. Use the
trial's own `--size` (the map must be square) and `--council-interval`. It must be made again
if the engine changes before launch. It can also be made elsewhere and copied here.

## 4. Keys

In the window that will run the world:

```powershell
$env:ANTHROPIC_API_KEY = Read-Host -MaskInput "Anthropic key"
$env:OPENAI_API_KEY = Read-Host -MaskInput "OpenAI key"
$env:GEMINI_API_KEY = Read-Host -MaskInput "Gemini key"
Test-Path Env:ANTHROPIC_API_KEY
```

`Test-Path` says `True` without showing the key. To keep a record of the window, start a
transcript **after** typing the keys (masked input never shows in it):

```powershell
Start-Transcript -Path ..\trial-console-1.txt
```

Ollama on this computer needs no key.

## 5. The steps

### Step 1: make the world

```powershell
& .venv\Scripts\sovereign-world.exe init work\trial --seed SEED --width 32 --height 32 --council-interval 28 --sovereigns work\trial.toml
```

Choose the seed. `--start-rotation N` rotates the starts if you want another assignment of
start sites (0 is the world as generated). `--crisis-gap` is 7 by default.

### Step 2: the first gate, and the cost

```powershell
& .venv\Scripts\sovereign-world.exe preflight work\trial --calibration docs\calibration\2026-10-trial --probe
& .venv\Scripts\sovereign-world.exe spend work\trial --dry-run
```

`--probe` asks each model one tiny question (pennies; nothing is recorded). Fix every `FAIL`
([the launch gate](launch-gate.md) says what each means) and read every `WARN`. `spend
--dry-run` shows the worst-case council round and how many the cap covers: a year at 28 days
holds 14 regular councils, plus crisis councils.

### Step 3: the rehearsal

A second world with the same settings, run for 31 days with real models and a small cap, to see
every provider hold two councils with real prompts. It is not sealed and is thrown away after.

```powershell
& .venv\Scripts\sovereign-world.exe init work\rehearsal --seed SEED --width 32 --height 32 --council-interval 28 --sovereigns work\trial.toml
& .venv\Scripts\sovereign-world.exe preflight work\rehearsal --probe
& .venv\Scripts\sovereign-world.exe run work\rehearsal --days 31 --spend-limit 10
& .venv\Scripts\sovereign-world.exe spend work\rehearsal
& .venv\Scripts\sovereign-world.exe councils work\rehearsal --errors
& .venv\Scripts\sovereign-world.exe verify work\rehearsal
```

(The rehearsal's `preflight` has no balance report, so its `balance` line is a `WARN`.) The
`--spend-limit` must cover at least two worst-case rounds as `spend --dry-run` showed them, or
the rehearsal stops before its second council day (exit code 3); raise the 10 if it does not.

While it runs, `run` prints a line as each council is saved:

```
run 3f2a9c1e day 0 -> 31, councils every 28 days, cap $10.00
  day 0: civilization:0000000001 anthropic ANTHROPIC-MODEL accepted, 24,512 in, 1,830 out, $0.1010, 41.2 s
...
day 0 councils: 4 in 212.4 s; spent $0.31 of $10.00 (97,120 in, 7,004 out)
checkpoint saved at day 30
advanced to day 31 (...)
```

What to look for in `councils --errors`:
- every civilization `accepted` (or `repaired`: the first reply could not be read, the second
  could);
- no `timeout`, `late` or `malformed` from Gemini or Ollama. `malformed` from Ollama usually
  means the context is too small (section 1); `late` or `timeout` means the model is too slow
  for `timeout_seconds`;
- no `answered by` or `unpriced` lines: if there are, add those names to the price table;
- the council times on the `run` lines, against `timeout_seconds`;
- the cost of a round, times 14, against your cap.

If any of this changes `work\trial.toml`, make the trial world again (it is still on day 0 and
costs nothing to remake):

```powershell
Remove-Item -Recurse work\trial
```

and go back to step 1.

### Step 4: seal

```powershell
& .venv\Scripts\sovereign-world.exe keygen
$env:SOVEREIGN_WORLD_SEAL_KEY = Read-Host -MaskInput "Seal key"
& .venv\Scripts\sovereign-world.exe seal work\trial --days 365
Remove-Item Env:SOVEREIGN_WORLD_SEAL_KEY
```

`keygen` shows a key and its fingerprint once: keep the key safe (a password manager), and write
the fingerprint down; it is how anyone checks the seal later. From here on, do not change the
code on this PC.

### Step 5: the launch gate

```powershell
& .venv\Scripts\sovereign-world.exe preflight work\trial --launch --signer FINGERPRINT --days 365 --calibration docs\calibration\2026-10-trial --probe
& .venv\Scripts\sovereign-world.exe preflight work\trial --launch --signer FINGERPRINT --days 365 --calibration docs\calibration\2026-10-trial --json > ..\launch-gate.json
```

The first must end with `ready to launch: sealed by ...`. The second keeps the record, outside
the run's folder.

### Step 6: run the year

```powershell
& .venv\Scripts\sovereign-world.exe run work\trial --days 365 --pace 600
```

`--pace` is how many seconds to wait between days (section 8); leave it out to run as fast as
the models answer. To watch, open a **second** window (no keys needed there) and serve the run
to your browser:

```powershell
$env:PYTHONUTF8 = "1"
& .venv\Scripts\sovereign-world.exe observe work\trial
```

It prints an address with `#token=...` once: open it in the browser. The page drops the token
from the address after reading it, so a reload (F5) needs the printed address again. Do not use
`observe --run-days` for the trial: the window running `run` is the run's only writer.

### Step 7: after a stop, a crash or a reboot

Press Ctrl+C once to stop after the day under way (a second Ctrl+C stops at once). To go on,
open a new window and:

```powershell
cd C:\ai-world
$env:PYTHONUTF8 = "1"
$env:ANTHROPIC_API_KEY = Read-Host -MaskInput "Anthropic key"
$env:OPENAI_API_KEY = Read-Host -MaskInput "OpenAI key"
$env:GEMINI_API_KEY = Read-Host -MaskInput "Gemini key"
& .venv\Scripts\sovereign-world.exe verify work\trial --signer FINGERPRINT
& .venv\Scripts\sovereign-world.exe run work\trial --days 365 --pace 600
```

`run` carries on from what was saved and stops at the seal's 365 days, whatever `--days` says.
A council already answered before the stop is reused: its model is not asked again.

| `run` exit code | Meaning | What to do |
|---|---|---|
| 0 | done, or stopped by Ctrl+C | run again to go on |
| 1 | `cannot resume: ...` | the journal is not this run's or was changed; send the message back |
| 3 | stopped before a day that could break the spending cap | the cap is sealed; the trial ends there |
| 4 | `seal refused: ...` | the settings, code or addresses changed since sealing; undo the change |

## 6. Keeping the PC awake

```powershell
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
```

The screen may still turn off. Keep the PC on mains power, pause Windows Update for the span of
the run (Settings, Windows Update, Pause updates), and if a virus scanner slows the run, exclude
the run's folder (`C:\ai-world\work`). A reboot is not a disaster: step 7 picks the run up.

## 7. Backups

Only while the run is stopped (Ctrl+C, wait for `stopped at day N`):

```powershell
Copy-Item -Recurse work\trial ..\backups\trial-day-N
& .venv\Scripts\sovereign-world.exe verify ..\backups\trial-day-N --signer FINGERPRINT
```

Copying the folder while `run` is writing can copy a half-written database.

## 8. Time and pace

Dubai time is UTC+4 all year (no daylight saving); `Get-Date` shows your local time, and the
launch gate's clock line is in UTC. Quiet days take well under a second; a council day takes as
long as the four models take to answer (a few minutes). With a pace the year takes about
364 times the pace, plus the council days:

| `--pace` | A day every | The year takes about | A council every |
|---|---|---|---|
| 0 | as fast as possible | 1-2 hours (the 14 council days) | a few minutes |
| 60 | minute | 6 hours | 28 minutes |
| 600 | 10 minutes | 2.5 days | 4.7 hours |
| 3600 | hour | 15 days | 28 hours |
| 86400 | day | a year | 4 weeks |

For example, started at 09:00 Dubai time with `--pace 600`, councils fall at about 09:00, 13:40,
18:20 and 23:00 on the first day, and the year ends about two and a half days later.

## 9. What to send back

- the seal's fingerprint;
- `..\launch-gate.json`;
- `spend work\trial`;
- `councils work\trial --json` and `councils work\trial --errors`;
- `verify work\trial --signer FINGERPRINT` after every interruption and at the end;
- the console transcripts (`..\trial-console-*.txt`), with their exit codes;
- the size of the run's folder (`Get-ChildItem -Recurse work\trial | Measure-Object -Sum Length`).

## 10. Checks only your PC can make

The test suite runs on Linux; these Windows details are confirmed by the steps above:
- `Read-Host -MaskInput` works (PowerShell 7.1 or later);
- one Ctrl+C during a `--pace` wait ends with `stopped at day N`, and `verify` passes after;
- `preflight` and `run` print cleanly in the console and into `launch-gate.json`;
- Ollama and Gemini accept the council requests (`response_format` JSON, `max_tokens`), report
  their token use and a model name in the price table, and answer in time (the probe and the
  rehearsal);
- the run folder is on a local NTFS drive.
