# Rules Laboratory Operator Guide

The Rules Laboratory is the first playable foundation for the AI civilization world. It creates four isolated communities with 32 founders each, advances them through deterministic daily ticks, asks scripted sovereigns for monthly decrees, and records a tamper-evident history that can be replayed and verified.

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
& .venv\Scripts\sovereign-world.exe init work\demo-world --seed 21
& .venv\Scripts\sovereign-world.exe run work\demo-world --days 3650
& .venv\Scripts\sovereign-world.exe inspect work\demo-world
```

Initialization fixes the manifest, seed, terrain, start packages, and founders. After launch, the CLI offers no command that edits people, resources, terrain, or outcomes.

`run` resumes the latest verified journal state, advances the requested number of daily ticks, and saves a new checkpoint. Repeating `run` continues the same world.

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
- `world.sqlite3-wal` and `world.sqlite3-shm`: temporary SQLite files that may appear while a command is running.

Copy the entire directory when backing up or moving a world. Do not edit either authoritative file; verification will report corruption rather than silently accepting a changed history.
