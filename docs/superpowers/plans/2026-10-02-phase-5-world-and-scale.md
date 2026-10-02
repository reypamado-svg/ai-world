# Phase 5 — Land, houses, ranks, sites, 2–4 civilizations, and 100,000 real people

Planned with Fable 5.1 and checked against the code. This document records
what the user asked for, the decisions taken, the order of work, and each
slice as built.

## Why

After reviewing the observer at 25 km tiles (O1b), the user asked for:

1. **Mixed land cover inside tiles,** realistic and logical, known to the engine (groves and ponds in grassland, clearings in forest, oases only by water).
2. **Faster observer speeds:** 1×, 10×, 25×, 50× and 100×.
3. **Houses that limit population,** and development stages. Settlements: village → small town → town → big town → city. The realm: chiefdom → kingdom → empire.
4. **Wild animals,** tame or aggressive. Not now, but the design must leave room for them.
5. **Civilizations kept further apart.**
6. **2–4 civilizations** chosen when a world is made, all run by AI models.
7. **A few ruins, treasure troves, mines and quarries,** balanced between civilizations.
8. **At least 100,000 people,** every one a real, followable individual, on an ordinary PC.

## Decisions (the user's)

- **Every person stays real.** Engine speed comes from compact storage, fewer copies and journals that store only what changed. People are not merged into anonymous households.
- **Land cover lives in the engine,** not only in the picture, and changes food, forage, water and extraction. It does not change travel days, so the travel rule the rulers were given stays true.
- **Ranks:** each settlement has a rank, and so does the civilization as a whole. An empire needs size and also rule over other peoples (tribute, occupation, a ceded settlement, or at least 10% of subjects of a foreign culture).
- **New rules apply to new worlds only.** Old runs replay and verify exactly as recorded; a fork can opt in. A `rules_version` on the run records which rules apply.
- **Order:** features first, then the 100,000-person work.
- **Two defaults, open to change:**
  - Council-4 orders may name a number of workers at a settlement instead of listing people.
  - 100× is the fastest viewing speed; longer spans use "skip to day".

## Order of work

| Slice | Content |
|---|---|
| S0 | Benchmark harness, reference hashes, hash-neutral speedups |
| S1 | World generator v3: 2–4 civilizations, scaled spacing, land cover, balanced sites; export v4; observer cover and speeds |
| S2 | Rules v2: houses, settlement and realm ranks, civil research, council-4 |
| S3 | Land cover in the mechanics; extraction and depletion; ruins and troves; seams for wild animals |
| S4 | People stored in columns behind the same interface |
| S5 | Journal format 2 (snapshots and deltas), hash in parts; the reader the observer's O2 builds on |
| S6 | Hot paths as array operations, indexes, and council reports that summarize at scale; 100,000-person checkpoint |
| S7 | Observer at 100,000: crowd rendering, budgets that follow the screen size, a smaller art atlas |

Each slice stops for review. The full design (thresholds, cover tables, site kits, the journal format) is in the session plan.

## S0 as built: measure first, then the easy speed

**Reference hashes** (`tests/golden.py`, `tests/test_golden_hashes.py`, `tests/fixtures/golden_hashes.json`).
- The fixture holds every daily state hash of 14 scripted runs:
  - seed 21 at 48×48 for 365 days, on generator 1 and on generator 2;
  - seeds 0–11 at 24×24 for 120 days.
- Recorded before any engine change. Every later slice must leave them identical; this test is what makes the people-store rework safe.

**Benchmark harness** (`tests/perf/bench_people.py`, `tests/perf/synthetic.py`; not collected by pytest).
- Grows a fresh world deterministically to a chosen population and runs scripted days.
- Reports milliseconds per day for the day itself, the state hash and the journal record, plus journal size and peak memory.

**Speedups that change nothing** (all reference hashes unchanged). Most of the day was spent copying every person, several times.
- A coordinate is no longer copied when the people holding it are (`HexCoord` is immutable).
- Teaching, exploration, diplomacy and journeys now copy only the people they look up: apprentices, explorers, ambassadors, travellers (`people.CopyOnRead`). Each still leaves the people it was given untouched.
- The population day updates the engine's own working copy in place. Other callers still get a copy.
- Pairing for conception sorts the men once, not once per woman.
- Feeding finds each tile's store once, not once per person.
- Sight from people standing on the same tile is worked out once.

**Measurements** on the container; compare only within one machine.

| People | Day before | Day after | µs per person per day before → after | State hash | Journal record | Journal per day |
|---|---|---|---|---|---|---|
| 1,000 | 274 ms | 106 ms | 274 → 106 | 48 ms | 84 ms | 91 KB |
| 4,000 | 1,062 ms | 278 ms | 266 → 70 | 98 ms | 159 ms | 184 KB |
| 20,000 | — | 1,042 ms | → 52 | 477 ms | 602 ms | 648 KB |

- About 4× faster per person, within S0's target of 80 µs.
- At 20,000 people the hash and the gzipped full-state journal now take as long as the day itself. That is S5's job.
- The day's remaining big cost is the one copy of each civilization at the start of the day, which S4 removes.

**Spike answers.**
1. **Batch draws:** drawing n numbers at once from a random stream gives the same numbers, and leaves the stream in the same place, as n single draws. So births and deaths can become array operations without changing any old run.
2. **Row proxy:** a slotted row object over numpy columns reads as fast as today's person objects (100,000 reads: 11.5 ms vs 9.5 ms) and writes 4× faster (validated assignment is the cost today). A whole-column update, such as everyone aging a day, takes under 1 ms for 100,000 people.

**Deferred to S5:** a lower gzip level for new journals. It belongs with the new journal format.
