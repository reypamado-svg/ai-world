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

## S1 as built: a fairer, richer world (generator version 3)

Planned in detail with Fable 5.1; built in six commits.

**Two to four civilizations.** `init --civilizations N` (2, 3 or 4; four by default, so existing manifests hash as before). The spec and laboratory guide say so.

**Starts as far apart as the land allows.**
- Version 3 keeps version 2's terrain and rivers exactly; only the starts and what follows differ.
- It aims for a spacing of `isqrt(land / (3 × civilizations))`, at least 12 tiles and at most half the map. It steps down two tiles at a time until a set fits.
- At each spacing it draws first from sites scoring within 80% of the best, then 60%, then any viable site (spacing before quality). Each pick is the site farthest from those already chosen.

| Map | 4 civilizations | 3 | 2 |
|---|---|---|---|
| 100×100 (seeds 1, 2, 21) | 26–27 | 31–32 | 38–39 |
| 48×48 | 13 | 15 | 19 |
| 24×24 | 12 (the floor) | 12 | 12 |

Seed 21's four capitals moved from 13 tiles apart to 26: (81,27), (95,95), (4,25) and (9,90).

**Land cover** (`cover.py`).
- Every land tile records the share of open ground, wood, scrub, wetland, rock, sand and snowfield, in basis points summing to 10,000. Water has none.
- Shares start from a typical mix per terrain; hills were tuned to pasture with wooded slopes. They then follow the tile's moisture, height and temperature, its river and its neighbours. They are integer only and come from their own random stream.
- **Rules:**
  - deserts have open ground or wetland only at an oasis;
  - grassland is at most 35% wood;
  - forests keep at least 8% clearings;
  - snowfields show only rock;
  - wetland never covers most of a tile.
- Averages on seed 21 at 100×100:

  | Terrain | Mix |
  |---|---|
  | Grassland | 68% open, 16% wood, 10% scrub, 4% wetland |
  | Forest | 74% wood, 12% open |
  | Hills | 30% open, 33% rock, 17% each wood and scrub |
  | Mountain | 67% rock, 10% snowfield |
  | Desert | 68% sand, 22% rock |

  Only 44 of 810 desert tiles have wetland: those beside a river.
- Generation takes 1.9 s at 100×100.

**Sites** (`sites.py`).
- **Kit:** each civilization gets the same kit:

  | Site | Distance from its own start (tiles) |
  |---|---|
  | Ore deposit | 3–5 |
  | Ore deposit | 6–9 |
  | Quarry | 2–4 |
  | Quarry | 5–8 |
  | Ancient ruin | 5–9 |
  | Trove | 8–12 |

- **Placement:**
  - on walkable land;
  - more than two tiles from any start;
  - strictly nearer its own start than any other;
  - at least three tiles apart.

  Within its band a site goes where the ore, stone, soil (ruins) or timber (troves) is highest. A band may widen by one tile; otherwise the attempt is redrawn.
- **Data only for now.** The world holds the sites; the rules that work them come in S3.
- **What rulers see.** A council report lists the sites on tiles the civilization knows, as of when it last saw them. It leaves the field out when there are none, so older worlds' reports and prompts are unchanged.
- **Tests:** identical kits at similar distances for two to four civilizations, and no leak of unseen sites.

**Old runs.** Generators 1 and 2 pick starts exactly as before. Saves without cover or sites are byte-identical, and all 14 reference runs keep every daily hash.

**Export version 4 and the observer.**
- **Export:** tiles carry their cover; `sites.json` lists the sites; the manifest records the cover classes and the spacing. Seed 21 was re-exported: terrain, rivers and lakes are byte-identical, and the first capital (the SAMPLE village) is unchanged.
- **Ground painting:**
  - The observer paints a tile's dominant cover as its ground and the other classes as patches whose area follows their share (measured within a few points). The patches are groves and clearings, marsh with ponds, scrub, outcrops, dunes and snow patches.
  - Far views average the patches instead of aliasing.
  - Trees grow only in wood patches, rocks on rock and bushes on scrub and open ground, so their numbers follow the shares. Nothing grows in wetland.
  - Sampling costs about 3.4 µs per point.
- **Sites on the map:** sites are marked with a glyph per kind, labelled "engine day 0" from about 600 px per tile, and shown as dots on the minimap.
- **Speeds:** the buttons are now 1×, 10×, 25×, 50× and 100×.
