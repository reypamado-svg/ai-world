# Rules Laboratory Operator Guide

The Rules Laboratory is the first playable foundation for the AI civilization world. It creates two to four isolated communities (four by default, `init --civilizations`) with 32 founders each, advances them through deterministic daily ticks, asks scripted sovereigns for monthly decrees, and records a tamper-evident history that can be replayed and verified.

Phase 1 uses deterministic scripted sovereigns. It contains no OpenAI, Claude, local-model, HTTP, or multi-computer integration. Those adapters belong in later phases and will use the same validated command boundary.

## Civilization discovery

Each civilization starts with one practical regional capability and a small private starter map. A sovereign can request a teaching assignment or a route for living members of its own civilization. The simulation validates the request, moves an expedition one hex per day, and records the result. A sovereign cannot directly change a person's skills, location, supplies, or map.

Maps remain private. A civilization learns about a tile only through its own starting observations or travel, and its council report never contains a rival's observations. Sightings are saved in the authoritative state, so replays reconstruct the same discoveries and event sequence without querying any AI provider.

## Contact and ambassadors

An expedition creates a foreign contact only when it physically reaches a foreign settlement. That contact belongs only to the explorers' civilization; the other civilization receives no alert merely because it was seen.

After contact, a sovereign may send a living ambassador along a known, adjacent route to that settlement. The engine carries the immutable original message while the ambassador travels. Travel can be delayed or lost, and an arriving message can carry a clearly marked distorted representation. Only the receiving civilization's council report receives the delivered words. These outcomes are recorded and replayed exactly.

## Set up on Windows

From the project directory, create a Python 3.12 virtual environment and install the project:

```powershell
py -3.12 -m venv .venv
& .venv\Scripts\python.exe -m pip install -e ".[dev]"
```

If `py` is unavailable, use any installed Python 3.12 executable in the first command.

## Create and run a world

```powershell
& .venv\Scripts\sovereign-world.exe init work\demo-world --seed 21 --width 48 --height 48
& .venv\Scripts\sovereign-world.exe run work\demo-world --days 3650
& .venv\Scripts\sovereign-world.exe inspect work\demo-world
```

Without `--width` and `--height`, a world is 100 × 100 tiles (each 25 km across, a day's walk) and takes about two seconds to generate; the demo uses a smaller 48 × 48 world. Initialization fixes the manifest, seed, terrain, start packages, and founders. After launch, the CLI offers no command that edits people, resources, terrain, or outcomes.

`run` resumes the latest verified journal state, advances the requested number of daily ticks, and saves a new checkpoint. Repeating `run` continues the same world.

## Rules versions

Each world records the rules it runs under in its manifest.
- **Rules 3:** worlds made with `init` now. They keep everything in rules 2 and add town plans:
  - each council designs its settlements: a style (open, ringed, grid, river town, hill fort), where the keep, market, shrine and craft quarter stand, how far out the wall ring runs (1 to 5 blocks of 64 m) and where its 1 to 3 gates face; every settlement starts with a plain plan that changes nothing;
  - walls go up section by section along the planned ring (6 to 22 sections), each costing a tenth of a whole wall's grade step, so a standard ring costs what walls always cost;
  - walls defend in proportion to the share of the ring built and of the houses inside it; catapults batter the weakest section; houses beyond the ring burn first in a storm;
  - moving the ring or its gates pulls the old ring down for half its cost;
  - a keep at the centre with its hall open, a hill fort, a craft quarter by the water and a market by the store each change one number (defence, ground, the workshop's day, store room); the shrine is drawn only.
  - the scripted baseline designs its capital as a ringed town on day 30 and walls it from day 60: earthwork, or a palisade where its people know timbercraft.
- **Rules 2:** worlds made with `init` before rules 3. They add:
  - houses: every five people need one, and births need room;
  - settlement ranks (village to city) and realm ranks (chiefdom to empire), and what they unlock;
  - the hall, armoury and training grounds;
  - civil research;
  - decrees that end when their days run out;
  - food, water and forage from each tile's land cover; new settlements need water;
  - timber and stone gathered at home toward a materials target;
  - parties working ore deposits and quarries;
  - one-off finds at ancient ruins and troves;
  - recipes for metal, tools and planks;
  - a cap on how far a settlement's hold reaches (strength 300, about 25 tiles of open land), so borders stay local however large a city grows;
  - orders for work at home that count their workers at a settlement instead of naming them.
- **Rules 1:** worlds made before rules versions were recorded. They keep rules 1 and replay and verify exactly as before.
- **Forks:** a fork keeps its parent's rules.

**Model-played runs and prompt versions.** Councils played by a model read a charter of a given prompt version. The current one is `council-6`, which tells rules-3 councils how to design their towns and raise their walls by the section (its town plan rule is written from the engine's tables). Since `council-5` the council report sums up the people (counts by age, health and settlement, the idle grown-ups at each settlement, and up to 40 notable people) instead of listing every one, which kept the report readable at any population. A run recorded under an older prompt version still replays, verifies and rederives, but to carry on with a model sovereign it must be forked; the fork's sovereigns use the current version.

## Checkpoint, replay, and verify

```powershell
& .venv\Scripts\sovereign-world.exe checkpoint work\demo-world
& .venv\Scripts\sovereign-world.exe replay work\demo-world --day 3650
& .venv\Scripts\sovereign-world.exe verify work\demo-world
```

`replay` reads history without changing the live world. `verify` checks the manifest hash, the chained journal records, recorded state hashes, the latest checkpoint, and world invariants.

## Run the checks

```powershell
& .venv\Scripts\python.exe -m ruff check .
& .venv\Scripts\python.exe -m mypy src
& .venv\Scripts\python.exe -m pytest -m "not soak" -v
& .venv\Scripts\python.exe -m pytest -m soak -v
```

The ordinary suite includes a day-by-day multigenerational scenario. The soak suite samples 100 deterministic seeds across 50 annual boundaries and verifies world generation, mortality, invariants, persistence, and replay for every seed.

## World data

Each world directory contains:

- `world.sqlite3`: the immutable manifest and compressed checkpoints.
- `journal.jsonl`: append-only, checksummed transition records linked by hashes.

**Journal formats.** `inspect` and `verify` print a run's format.
- **Format 2** (new runs and every fork): the journal opens with a header record. Each day saves only what changed since the day before; the whole world is saved every 30 days, and after any gap. Hashes use version 2, worked out in parts, so a large world hashes and saves in a fraction of the time.
- **Format 1** (runs made before this): the whole world every day, hash version 1. Old runs keep their format, carry on in it, and replay and verify exactly as before. A fork of an old run is saved in format 2.
- `world.sqlite3-wal` and `world.sqlite3-shm`: temporary SQLite files that may appear while a command is running.

Copy the entire directory when backing up or moving a world. Do not edit either authoritative file; verification will report corruption rather than silently accepting a changed history.
