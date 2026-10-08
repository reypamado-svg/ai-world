# The Sealed Trial: runbook for a Windows PC

This is the step-by-step for running the sealed trial world on your own Windows PC: one world,
three civilizations, each played by a different AI, for one year (365 days), sealed with your
key, at **no cost beyond the subscriptions you already have**:

| Civilization | Played by | Reached through | Paid by |
|---|---|---|---|
| 1 | Claude | Claude Code (`claude`), signed in with your Claude plan | your Claude Pro plan |
| 2 | ChatGPT | Codex (`codex`), signed in with your ChatGPT plan | your ChatGPT Plus plan |
| 3 | a local model | Ollama on this PC | nothing |

No API key is used anywhere. The run keeps a hard cap on the tokens its councils use, so a
runaway cannot eat your plans' allowances.

Every command below is for **PowerShell 7** (`pwsh`), run from the project folder. What each
launch-gate line means, and what to do about a `FAIL`, is in [the launch gate](launch-gate.md).
What the commands do in general is in [the operator guide](rules-laboratory.md).

**The rules that never bend:**
- No API key is set in the window that runs the world: Claude Code and Codex would use a key
  instead of your sign-in (and bill it). The run leaves such variables out of the programs'
  environment anyway, and the launch gate warns when one is set.
- The only secret you type is the seal key, with `Read-Host -MaskInput`, never into a file,
  never echoed, and only for the seal step.
- The world can be watched (`observe`) but not edited. Nothing here changes a run except `run`.
- Once the run is sealed, do not update the project's code on this PC (`git pull`) until the
  year is over: a sealed run refuses other code (exit code 4).

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

**Claude Code.** Install it, sign in once with your Claude account (the subscription, not an
API key), and keep it from updating itself during the year:

```powershell
winget install Anthropic.ClaudeCode
claude
[Environment]::SetEnvironmentVariable("DISABLE_AUTOUPDATER", "1", "User")
claude auth status
```

In `claude`, type `/login`, choose your Claude account and finish in the browser, then `/exit`.
`claude auth status` must show `"authMethod": "claude.ai"`. (If it shows an API key, remove
that variable; see "No API keys" below.)

**Codex.** Install it with OpenAI's own installer (the native `codex.exe`) and sign in with
ChatGPT:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://chatgpt.com/codex/install.ps1 | iex"
codex login
codex login status
```

`codex login` opens the browser; choose "Sign in with ChatGPT". `codex login status` must say
you are logged in using ChatGPT. Open a new PowerShell window afterwards so both programs are on
PATH.

**No API keys.** Make sure none is set, for this window and for your user:

```powershell
Remove-Item Env:ANTHROPIC_API_KEY, Env:ANTHROPIC_AUTH_TOKEN, Env:ANTHROPIC_BASE_URL, Env:CLAUDE_CODE_OAUTH_TOKEN, Env:CODEX_API_KEY, Env:CODEX_ACCESS_TOKEN, Env:OPENAI_API_KEY -ErrorAction SilentlyContinue
[Environment]::GetEnvironmentVariable("ANTHROPIC_API_KEY", "User")
[Environment]::GetEnvironmentVariable("CODEX_API_KEY", "User")
```

The last two lines must print nothing.

**Ollama.** Install it from ollama.com, then give it a context large enough for a council. A
council's papers run to 25,000-30,000 tokens; Ollama's default context is far smaller and it
cuts a longer prompt **silently**, which shows up as `malformed` councils. Set it for your user,
restart Ollama (quit it from the tray and start it again), and fetch the model:

```powershell
[Environment]::SetEnvironmentVariable("OLLAMA_CONTEXT_LENGTH", "32768", "User")
ollama pull gemma3:12b
ollama ps
```

`gemma3:12b` fits a 16 GB card with a 32,768-token context and answers in JSON well. An
alternative is `qwen2.5:14b-instruct`; with it, also set `OLLAMA_KV_CACHE_TYPE` to `q8_0` the
same way, so its context fits on the card. Avoid "thinking" models (Qwen3, DeepSeek-R1): they
spend the answer's budget on thought and run past the time limit. `ollama ps` (while the model
is loaded, for example after the probe in step 2) must show `100% GPU` and the context size.
The rehearsal (step 3) shows whether it copes.

## 2. The settings file

Write `work\trial.toml`. It holds no secrets.

```toml
[sovereigns."civilization:0000000001"]
provider = "claude-code"
label = "claude"
model = "claude-sonnet-5-5"

[sovereigns."civilization:0000000002"]
provider = "codex"
label = "chatgpt"
model = "gpt-6.1-sol"
effort = "medium"

[sovereigns."civilization:0000000003"]
provider = "compatible"
label = "ollama"
base_url = "http://127.0.0.1:11434/v1"
model = "gemma3:12b"

[budgets]
timeout_seconds = 600
max_output_tokens = 8000

[spend]
max_input_tokens = 6000000
max_output_tokens = 1500000
reserve_councils = 1

[spend.prices."claude-sonnet-5-5"]
input_per_million_usd = 0
output_per_million_usd = 0

[spend.prices."gpt-6.1-sol"]
input_per_million_usd = 0
output_per_million_usd = 0

[spend.prices."gemma3:12b"]
input_per_million_usd = 0
output_per_million_usd = 0
```

- **The model names.** Claude Pro is best spent on Sonnet; the ChatGPT models offered to Codex
  depend on your account. The probe in step 2 prints the name each model answers with: if it
  differs from what the file says, put the probe's name in both places (the sovereign's `model`
  and its `[spend.prices."..."]` row) and make the world again.
- **Prices are zero**, because nothing is billed per token. Every model still needs its row.
- **The cap is in tokens:** 6,000,000 in and 1,500,000 out for the year. That covers 31
  worst-case council rounds by input and 15 by output: Codex cannot be given an output limit,
  so each of its calls is reserved at 32,000 output tokens. Both cover the 14 regular councils.
  Real councils use far less; the run stops cleanly (exit code 3) only if something runs away. A
  single Codex call beyond 32,000 output tokens could pass the cap by its excess on that day;
  the run then stops before the next.
- **`effort`** is how hard ChatGPT thinks (`low`, `medium`, `high`); medium keeps each council
  within its time and your plan's allowance.
- **`timeout_seconds`** is how long one model may take; 600 leaves room for the programs to
  start and the local model to write. A reply after that is thrown away (`late`).
- The output budget (`max_output_tokens`) is passed to the local model and to Claude Code (as
  `CLAUDE_CODE_MAX_OUTPUT_TOKENS`, thinking included). Codex takes no such setting; `effort` is
  its lever, and the time limit and the reply's size limit bound it.

## 3. The balance report

The launch gate wants a balance calibration made under this code, at the trial's council
interval, map size and number of civilizations. It is already made: 1,500 scripted histories of
three civilizations on 32 by 32 maps, councils every 28 days, in
`docs\calibration\2026-10-trial-3civ`. Make it again only if the gate's `balance` line says
"another engine" (the engine changed since):

```powershell
& .venv\Scripts\python.exe -m sovereign_world.calibration run --out docs\calibration\2026-10-trial-3civ-again --civilizations 3 --council-interval 28 --size 32 --seeds 0-249 --workers auto
& .venv\Scripts\python.exe -m sovereign_world.calibration report docs\calibration\2026-10-trial-3civ-again
```

It takes a few hours, and can be stopped and run again: it carries on (a history cut off
part-way is played again). A folder made under another engine is refused: use a new folder.

## 4. Keys

There are none to type for the models: Claude Code and Codex use your sign-in, and Ollama needs
none. The only secret is the seal key, in step 4. To keep a record of the window, start a
transcript:

```powershell
Start-Transcript -Path ..\trial-console-1.txt
```

## 5. The steps

### Step 1: make the world

```powershell
& .venv\Scripts\sovereign-world.exe init work\trial --seed SEED --width 32 --height 32 --civilizations 3 --council-interval 28 --sovereigns work\trial.toml
```

Choose the seed. `--start-rotation N` rotates the starts if you want another assignment of
start sites (0 is the world as generated). `--crisis-gap` is 7 by default.

### Step 2: the first gate, and the token cap

```powershell
& .venv\Scripts\sovereign-world.exe preflight work\trial --calibration docs\calibration\2026-10-trial-3civ --probe
& .venv\Scripts\sovereign-world.exe spend work\trial --dry-run
```

`--probe` asks each model one tiny question (nothing is recorded); for Claude Code and Codex it
also shows the program's version and how it is signed in. Fix every `FAIL`
([the launch gate](launch-gate.md) says what each means) and read every `WARN`: a `keys` warning
names an API-key variable to remove. `spend --dry-run` shows the worst-case council round and
how many the token cap covers: a year at 28 days holds 14 regular councils, plus crisis
councils.

### Step 3: the rehearsal

A second world with the same settings, run for 29 days with the real models, so each
civilization holds two councils (days 0 and 28) with real prompts and real time limits. It is
not sealed and is thrown away after.

```powershell
& .venv\Scripts\sovereign-world.exe init work\rehearsal --seed SEED --width 32 --height 32 --civilizations 3 --council-interval 28 --sovereigns work\trial.toml
& .venv\Scripts\sovereign-world.exe preflight work\rehearsal --probe
& .venv\Scripts\sovereign-world.exe run work\rehearsal --days 29
& .venv\Scripts\sovereign-world.exe spend work\rehearsal
& .venv\Scripts\sovereign-world.exe councils work\rehearsal --errors
& .venv\Scripts\sovereign-world.exe verify work\rehearsal
```

(The rehearsal's `preflight` has no balance report, so its `balance` line is a `WARN`.)

While it runs, `run` prints a line as each council is saved:

```
run 3f2a9c1e day 0 -> 29, councils every 28 days, cap 6,000,000 input tokens, 1,500,000 output tokens
  day 0: civilization:0000000001 claude claude-sonnet-5-5 accepted, 24,512 in, 1,830 out, $0.0000, 41.2 s
...
day 0 councils: 3 in 212.4 s; spent $0.0000 (73,120 in of 6,000,000, 5,004 out of 1,500,000)
advanced to day 29 (...)
```

What to look for in `councils --errors`:
- every civilization `accepted` (or `repaired`: the first reply could not be read, the second
  could);
- `unavailable` with "usage limit reached": your plan's allowance ran out for now; the
  civilization does nothing that council and carries on at the next. If it happens in the
  rehearsal, choose a lighter model or effort;
- `unavailable` with "not signed in": sign the program in again (section 1);
- `malformed` from Ollama usually means the context is too small (section 1); `late` or
  `timeout` means the model is too slow for `timeout_seconds`;
- no `answered by` lines: if there are, the probe's model names were not used (section 2);
- the council times on the `run` lines, against `timeout_seconds`;
- the output tokens (`councils --json`): Claude's calls at or under `max_output_tokens`, Codex's
  well under 32,000; if Codex comes near it, lower its `effort`.

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
& .venv\Scripts\sovereign-world.exe preflight work\trial --launch --signer FINGERPRINT --days 365 --calibration docs\calibration\2026-10-trial-3civ --probe
& .venv\Scripts\sovereign-world.exe preflight work\trial --launch --signer FINGERPRINT --days 365 --calibration docs\calibration\2026-10-trial-3civ --json > ..\launch-gate.json
```

The first must end with `ready to launch: sealed by ...`. The second keeps the record, outside
the run's folder.

### Step 6: run the year

```powershell
& .venv\Scripts\sovereign-world.exe run work\trial --days 365 --pace 600
```

`--pace` is how many seconds to wait between days (section 8). At 600, a council falls every 4.7
hours, which spreads the councils over your plans' 5-hour allowance windows. To watch, open a
**second** window and serve the run to your browser:

```powershell
$env:PYTHONUTF8 = "1"
& .venv\Scripts\sovereign-world.exe observe work\trial
```

It prints an address with `#token=...` once: open it in the browser. The page drops the token
from the address after reading it, so a reload (F5) needs the printed address again. Do not use
`observe --run-days` for the trial: the window running `run` is the run's only writer.

### Step 6b: share it (optional)

To let others watch from anywhere, viewing only, serve the run publicly and open a tunnel to
it ([details](observer-remote.md)). Once, install the tunnel program:

```powershell
winget install --id Cloudflare.cloudflared
```

In the **second** window, serve the run with `--public` instead of the plain `observe` above:

```powershell
$env:PYTHONUTF8 = "1"
& .venv\Scripts\sovereign-world.exe observe work\trial --public
```

It prints your own address and a line `Share https://<your tunnel address>/?run=live#token=...`.
In a **third** window, open the tunnel:

```powershell
cloudflared tunnel --url http://127.0.0.1:8766
```

It prints an address ending in `trycloudflare.com`. Put it in place of `<your tunnel address>`
and send the whole link. Viewers see **VIEWING ONLY · shared**; nobody, you included, can pause
or steer the run from a public observer. Ctrl+C in the third window stops sharing; restarting
the second window makes a new link and cancels the old ones. The tunnel's address changes each
time it starts.

### Step 7: after a stop, a crash or a reboot

Press Ctrl+C once to stop after the day under way (a second Ctrl+C stops at once). To go on,
open a new window and:

```powershell
cd C:\ai-world
$env:PYTHONUTF8 = "1"
& .venv\Scripts\sovereign-world.exe verify work\trial --signer FINGERPRINT
& .venv\Scripts\sovereign-world.exe run work\trial --days 365 --pace 600
```

There are no keys to type again: the programs stay signed in. `run` carries on from what was
saved and stops at the seal's 365 days, whatever `--days` says. A council already answered
before the stop is reused: its model is not asked again.

| `run` exit code | Meaning | What to do |
|---|---|---|
| 0 | done, or stopped by Ctrl+C | run again to go on |
| 1 | `cannot resume: ...` | the journal is not this run's or was changed; send the message back |
| 3 | stopped before a day that could break the token cap | the cap is sealed; the trial ends there |
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

## 8. Time, pace and your plans' allowances

Dubai time is UTC+4 all year (no daylight saving); `Get-Date` shows your local time, and the
launch gate's clock line is in UTC. Quiet days take well under a second; a council day takes as
long as the three models take to answer (a few minutes). With a pace the year takes about
364 times the pace, plus the council days:

| `--pace` | A day every | The year takes about | A council every |
|---|---|---|---|
| 0 | as fast as possible | 1-2 hours (the 14 council days) | a few minutes |
| 600 | 10 minutes | 2.5 days | 4.7 hours |
| 3600 | hour | 15 days | 28 hours |

For example, started at 09:00 Dubai time with `--pace 600`, councils fall at about 09:00, 13:40,
18:20 and 23:00 on the first day, and the year ends about two and a half days later.

Claude Pro and ChatGPT Plus each give an allowance per 5-hour window and per week. One council
is one or two large messages per program, so at `--pace 600` each window holds at most one or
two councils. Using Claude or ChatGPT yourself during the run draws on the same allowance. A
used-up allowance costs a civilization that council (`unavailable`), not the run.

## 9. What to send back

Fill in [the year's record](sealed-trial-record.md) from these, and send them:

- the seal's fingerprint;
- `..\launch-gate.json`;
- `spend work\trial`;
- `councils work\trial --json` and `councils work\trial --errors`;
- `verify work\trial --signer FINGERPRINT` after every interruption and at the end;
- the console transcripts (`..\trial-console-*.txt`), with their exit codes;
- `claude --version` and `codex --version` at the start and the end;
- the size of the run's folder (`Get-ChildItem -Recurse work\trial | Measure-Object -Sum Length`).

## 10. Checks only your PC can make

The test suite runs on Linux with stand-ins for the two programs; these are confirmed by the
probe and the rehearsal:
- `claude -p` and `codex exec` answer as their documentation says (the probe's line for each);
- what a used-up allowance looks like (`unavailable` with "usage limit" in `councils --errors`);
- Windows finds `claude.exe` and `codex.exe` (the gate's `endpoints` line names the paths);
- `DISABLE_AUTOUPDATER` keeps Claude Code's version for the year (compare `claude --version`);
- one Ctrl+C during a `--pace` wait ends with `stopped at day N`, and `verify` passes after;
- `preflight` and `run` print cleanly in the console and into `launch-gate.json`;
- Ollama holds its 32,768-token context on the card (`ollama ps`) and answers in time;
- the run folder is on a local NTFS drive.
