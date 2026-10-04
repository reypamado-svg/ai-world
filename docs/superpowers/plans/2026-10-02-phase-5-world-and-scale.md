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

## S2 as built: houses, ranks, rank buildings, civil research (rules version 2)

Planned in detail with Fable 5.1. The rank buildings were added at the user's request, after research into Age of Empires and Manor Lords.

**Rules versions.**
- The manifest and the world record `rules_version`.
- `init` makes rules 2. Older manifests and saves read as rules 1 and leave the field out, so they hash and replay as before. A fork keeps its parent's rules.
- `rules.py` turns the version into switches: houses, decrees that expire, ranks, civil research.
- The shared scenario helpers stay on rules 1, so tests of other mechanics keep testing those mechanics.
- All 14 reference runs keep every daily hash.

**Houses** (`housing.py`). Every five people need a house (a household of about five is the figure most used for early towns).

| House | Cost | Needs |
|---|---|---|
| Hut | 10 timber, 5 person-days | — |
| House | 20 timber, 10 stone, 10 person-days | timbercraft |
| Stone house | 10 timber, 30 stone, 15 person-days | stoneworking |

- **Starting huts:** founders raise 7 huts at the capital. Settlers raise one hut for every five as they arrive.
- **Births:** women conceive only at a settlement that has a house for everyone it feeds (captives held there count), three months of food in its own store, and the growth decree in force. Under rules 1 the old single gate stands, and every random draw is unchanged.
- **Building:** a shelter project builds 1 to 20 houses of the best kind the people know, one after another. The builders stand at one settlement and take the materials from its store. If every builder dies, the unused materials go back.
- **Housing decree:** `HOUSING_POLICY` (0–100) keeps that share of spare room. When a settlement falls short, the two lowest-numbered idle grown-ups start a house, if its store can pay.
- **Decrees expire:** every decree now ends when its `duration_days` run out, unless a council issues it again. The check runs before the councils, so renewing a decree on its last day leaves no gap.
- **Losses:** a settlement stormed, or its storehouse burned, loses a quarter of its houses, the meanest first. One left empty for a year loses a house a month.
- **Moves:** houses pass with a ceded settlement and are gone with a fallen people.

**Settlement ranks** (`ranks.py`). Each rank above village also needs an open hall.

| Rank | People | Houses | Also needs |
|---|---|---|---|
| Small town | 300 | 60 | storehouse grade; timbercraft or stoneworking |
| Town | 1,000 | 200 | walls; writing; an open institution of one more kind |
| Big town | 3,000 | 600 | 2 more kinds; surveying or organised logistics; a quarter of houses stone |
| City | 8,000 | 1,600 | 3 more kinds including an archive; writing and organised logistics; a warehouse; stone walls; a quarter of houses stone |

- **Sizes** follow the medieval town sizes in Medieval Demographics Made Easy.
- **Evaluation:** ranks are weighed at each monthly council and move one step at a time.
- **Keeping a rank:** it holds while people and houses stay at 70% or more of the threshold, and is lost with a required work, craft or building. Losing the hall drops the settlement a step.

**Realm ranks.**

| Rank | Settlements | People | Land | Also needs |
|---|---|---|---|---|
| Kingdom | 3, including a town | 2,000 | 40 tiles | writing; an open archive or diplomatic service |
| Empire | 8, including a city | 20,000 | 150 tiles | organised logistics; rule over other peoples |

- **Rule over other peoples** means one of:
  - tribute received under a treaty in force;
  - a settlement it occupies;
  - a settlement ceded to it;
  - a tenth of its free people living by another culture.

  It is needed to rise, not to stay.
- **Sources:** Service's band–tribe–chiefdom–state sequence (1962) and Carneiro's circumscription theory (1970) shaped the chiefdom-to-kingdom step. Taagepera's work on empire sizes (1978) shaped the land a great realm needs.

**What ranks unlock** (rules 2 only; refusals use `rank_required` or `party_too_large`).

| Gate | Needs |
|---|---|
| Warehouse | small town |
| Depot | town |
| Toll takings | a small town to receive them |
| Institutions besides the hall | village 1, small town 3, town 4, big town 6, city any number |
| War party size | 16 people for a chiefdom, 24 for a kingdom, 32 for an empire (uncapped under rules 1) |
| Demanding tribute in peace terms | the proposer is a kingdom or above |
| Research | scholars in a city earn 1 point a day more |

**Rank buildings** (new institution kinds; founded and kept like the others).

| Building | Unlock | Cost | Effect while open |
|---|---|---|---|
| Hall | any settlement | 40 timber, 20 stone, 20 person-days | the seat every rank above village needs; its settlement's hold on the land is 10 stronger (about one more tile); takes no slot |
| Armoury | small town | 30 timber, 30 stone, 20 person-days | equipment made there 50% faster; bronze arms and catapults are made only at an open armoury |
| Training grounds | small town | 40 timber, 10 stone, 15 person-days | drill there raises arms 10 further (30, or 40 with drill doctrine) |
| Ranch, stables | reserved: village, small town | — | built once wild animals arrive and can be tamed |

The idea follows both games:
- **Age of Empires:** the Town Center is the anchor every age-up builds on.
- **Manor Lords:** the settlement level comes from housing, and the manor is the lord's seat.

**Civil research** (rules 2).

| Topic | Points | Needs |
|---|---|---|
| Writing | 250 | a small town |
| Irrigation | 300 | cultivation, and a field with a river or 10% wetland |
| Herbal care | 200 | — |
| Fishing | 150 | water at or beside a settlement |
| Surveying | 300 | writing |
| Organised logistics | 400 | writing and surveying |

- **Half pace:** from kingdom rank, civil research runs at half pace without an open school or archive.
- **Now reachable:** writing opens the archive, school and diplomatic service; herbal care opens the healers' house. These were unreachable before.
- **Deferred:** the food from irrigation and fishing comes in S3, with the land-cover mechanics that rewrite farm capacity. Navigation is not offered yet.

**Council-4.**
- **Prompt version:** `council-4`.
- **Rules text:** for rules-2 worlds the charter adds a housing rule, a ranks rule and a buildings-and-research rule, each written from the engine's own tables.
- **Reply schema:** it gains `house_count` and the new decree and institution kinds.
- **Older runs:** council-3 runs fork onto council-4, as before.
- **Report:** under rules 2 it carries `rules_version`, `housing`, `house_jobs`, `ranks` and `realm_rank`. These are left out under rules 1, so older reports and prompts read exactly as before.
- **Baseline sovereign under rules 2:**
  - adds a housing decree of 10%;
  - sizes a shelter order to restore that spare room, within what the store can afford;
  - founds a hall at the capital on day 0.

  Under rules 1 its orders are unchanged.

**A two-year sample run** (seed 21, 48×48, the baseline sovereign, rules 2):

| Measure | Result |
|---|---|
| Hall | built and open at every capital by day 20 |
| Houses | 9 to 10 per capital after two years, from 7 at the start; 9 houses built in all, always a little ahead of the people |
| People | 39, 40, 41 and 40 per civilization: the same as the same world under rules 1, so houses kept pace and did not slow growth |
| Ranks | none: a small town needs 300 people |
| Speed | 25 s for 730 days |

**Known limit.** Timber is not gathered yet; it comes in S3. A rules-2 people builds with its starting 500 timber, enough for about 25 houses or 50 huts (room for 125 to 250 more people), and then stops growing.

**For the observer (O2).** O2 will draw:
- each settlement's houses from `housing` (counts per grade; hut, house or stone-house sprite) around its storehouses;
- crowding from slots against residents;
- building sites from `house_jobs`;
- the hall, armoury and training grounds as institution sprites.

The current sample village stays until then.

## S3 as built: the land in the mechanics, gathering, worked sites, finds

Planned with Fable 5.1. Built in five commits.

**Rules version.**
- S3 completes rules 2 rather than starting a rules 3. You confirmed that no rules-2 world needs to be kept, and S2 had noted rules 2 as unfinished (timber, and the food from irrigation and fishing).
- `rules.py` gains `cover_mechanics` and `sites`, both on in rules 2. Council-4 stays.
- **Escape hatch:** if a rules-2 world ever has to be frozen, set `CURRENT_RULES = 3` and switch these two flags on at 3.
- Rules 1 keeps the old code path unchanged, and all 14 reference runs keep every daily hash.

**Food, water and forage from land cover** (`land.py`, `cover.py`). Each settlement's food capacity per day is the sum of:

| Part | Yield |
|---|---|
| Fields | open ground and half the scrub, as fertile as the soil (a grassland tile yields what it did before) |
| Water | +2 per watered tile (a lake, a river, or a tenth wetland) |
| Wild food | woods and wetland: a tile all wood or wetland gives about 2 |
| Irrigation | watered fields yield half again |
| Fishing | +1 per tile of open water or river |

- **Maps without cover** yield as before.
- **Checked on eight start positions:** food lands within about ±25% of rules 1. It is slightly higher on ordinary land and lower only where the start is ringed by lakes.
- **Foraging:** travellers find more food in woods and wetland, on the same random draws.
- **New settlements:** a new settlement needs water on its tile or beside it.
- **Reports:** they show each settlement's daily food, timber and stone capacity.

**Gathering at home.**
- **Materials decree:** sets the timber each settlement keeps, and half as much stone.
- **Who gathers:** hands not needed in the fields gather toward the target, as fast as the woods and loose rock of the settlement's land allow. Woods grow back.
- **Tools:** a tool in store doubles one gatherer's day.
- **No gathering** under siege, without labour priority, or under rules 1.
- **House choice:** a shelter order may name the kind of house. The housing decree builds the best kind the store can pay for, and builds huts when stone runs short.
- **Baseline:**
  - keeps 300 timber and 150 stone;
  - founds its hall as soon as the store can pay;
  - orders the best affordable house.

**Mining and quarrying trips.**
- **The order:** an extract order sends workers to a deposit or quarry their people know of, for 1 to 60 work days.

  | Site | Yield per worker per day |
  |---|---|
  | Ore deposit | 2 ore |
  | Quarry | 4 stone |

- **Limits:** each day's take is capped by what the site still holds and the room in the packs; packs hold 50 each, food for the stay included.
- **Return:** the party goes home when its days are done, its packs are full or the site is spent. The goods go into the store it left from.
- **Size:** eight workers 4 tiles away bring home about 160 ore per trip. A deposit lasts about 15 such trips and a quarry about 30.
- **Hash safety:** the new journey fields are left out of saves at their defaults.

**Ruins and troves.**
- A salvage party to an ancient ruin or a trove takes everything it holds, if it arrives first. Later parties find nothing.

  | Site | Find |
  |---|---|
  | Ancient ruin | 120 stone, 8 tools, and 100 research points toward the first civil art the finders lack (they count once someone studies it) |
  | Trove | 60 metal, 6 tools, 4 bronze arms |

**Recipes** (rules 2).

| Product | Made from | Needs |
|---|---|---|
| Metal | 2 ore | metallurgy |
| Tool | 1 metal + 1 timber | — |
| Plank | 2 timber | timbercraft |

**Placeholders for wild animals** (no animals yet):
- `graze_bp` and `browse_bp` from land cover;
- a reserved `fauna` random stream;
- `FORAGE_HAZARD_CAUSE`;
- a `fauna` argument to the observer's `cellPlan`, accepted and ignored, with a test.

**Council-4** adds a land rule written from the engine's tables: fields, water, irrigation, fishing, gathering, worked sites, finds and the goods recipes.

**Sample runs** (seed 21, 48×48, baseline sovereign):

| Run | Result |
|---|---|
| Two years, rules 2 | 39, 40, 41 and 40 people per civilization, the same as rules 1, with food stores within 5% |
| Starting stock in that run | never fell below the 300-timber target, so no gathering was needed |
| 90 days starting with no timber or stone | every civilization gathered 310 timber and up to 180 stone, refilled to its targets, and built houses from day 4 |
| Daily capacities across the four starts | food 116–214, timber 14–140, stone 1–19 |

**Growth limit lifted.** The S2 timber limit is gone: woods renew, and stone comes from loose rock and quarries. Growth is now bounded by food and births, as under rules 1.

## S4 as built: every person real, kept in columns

Planned with Fable 5.1. Built in five commits.

**The store** (`people_store.py`).
- **Tables:** a civilization's people live in one `PeopleTable`:
  - numbers in numpy columns;
  - ids, places, family, allegiances and skills in plain lists;
  - one row per person.
- **`Person`:** a two-slot row of the table, made once per row. It is read and written exactly as before, and every write is checked as the old model checked it.
- **`PeopleView`:** the id-to-person mapping a population holds. It saves and loads the very same JSON as before.
- **Copying:** copying a table copies its columns. Skill, language and held-skill dicts are shared until a row's are first read.
- **Dirty blocks:** writes mark their block of 1,024 rows dirty, for S5.
- **Overlays:** `CopyOnRead` is now an overlay. It copies a person only when that person is looked up. Exploration, teaching, journeys and embassies hand back only the people they copied, instead of copying and re-merging everyone.
- **Kept as columns:** `age_days` stays its own column, because for the dead it differs from day − birth day, depending on how they died. Skills stay dicts, because a skill of 0 is saved and hashed.

**Nothing changes in the results.**
- All 14 reference runs keep every daily hash.
- Two new parity scenarios, recorded before the change (320 days each, rules 1 and rules 2), replay every day's hash, and their saved worlds load and save again byte for byte.
- A property test runs random sequences of inserts, removals, writes, in-place dict writes, detached copies and table copies against a dict of the old model. It finds the same saves, order and living/dead ids, and copies untouched by later writes.

**Measurements** (this container, which runs about 1.35× slower than S0's):

| People | Measure | Before | After |
|---|---|---|---|
| 20,000 | µs per person per day | 73.5 | **38.3** |
| 100,000 | Day | 7.26 s | **3.87 s** (38.7 µs per person) |
| 100,000 | One copy of every population | 1,980 bytes per person | 249 |
| 100,000 | The people store in all | about 2 KB per person (about 200 MB) | about 750 bytes per person (**75 MB**) |
| 100,000 | Peak process memory | 919 MB | 836 MB |

Both targets are met: at most 40 µs per person per day, and at most 150 MB for people at 100K. The peak is still dominated by the full JSON save made each day.

**Left for S5.** At 100K the hash takes 6.2 s and the saved day 4.8 s (2.9 MB). S5 hashes and saves only what changed.

## S5 as built: save and hash only what changed (journal format 2)

Planned with Fable 5.1. Built in six commits, plus these docs.

**Two formats, side by side.**
- **Format 1** is every run made before S5. Each day saves the whole world, gzipped, and hashes it with version 1. Old runs keep this format, carry on in it, and replay, verify and rederive exactly as before. A committed 5-day format-1 run (`tests/fixtures/format-one`) proves it.
- **Format 2** is every new run and every fork, whatever its rules. `RunManifest.journal_format` is 2 for them; it is 1 by default and then left out of the manifest hash, so old manifests keep theirs.

**Hash version 2** (`state_hash_v2`). The sha256 of a small JSON of parts:
- the map's own hash, worked out once (the map never changes);
- the world's fields without the map and people, from one dump;
- for each civilization, the hash of its fields and the hash of its people.

The people hash covers the present rows in order: one matrix of the number columns, and one digest per row of everything else. Row digests are kept between days and redone only for rows written to, or whose skill or language map changed. Verification always works everything out from scratch, and a property test checks that the cached, fresh and reloaded hashes agree after every operation. Version 1 is unchanged, so the 14 reference runs and both parity scenarios keep every hash.

**The format-2 journal** (`journal.py`):
- **Header:** the first record names the format and hash version; the store refuses a journal whose header disagrees with its manifest.
- **Whole-world snapshots:** the creation checkpoint, then day 30, 60, ..., and any day that does not follow the last one saved (a gap, or a store opened afresh without the day before). Gzip has no timestamp, so the same day saves the same bytes.
- **A day's changes**, against the day before:
  - world and civilization fields that changed (`set`) or became empty (`unset`);
  - for each civilization's people: the number columns as a packed mask of changed rows and their differences, the ids gone, the full row of anyone whose other fields changed, and the newcomers in order;
  - the day's events, as before.
- **Rebuilding:** `StateCursor` takes the snapshot at or before a day and applies each day's changes. Replay, verify and the observer's reader all use it.

**Proof that nothing is lost.**
- Both parity scenarios (320 days each, rules 1 and 2) and a war ending in a ceded colony rebuild every day with the recorded hashes, v1 and v2. On the saved fixture days the rebuilt world is byte-identical.
- Identical runs save identical journals.
- A flipped byte fails the record checksum; a re-chained, edited change fails verification; changes without the day before are refused; a cut-off last line is ignored and the run carries on.
- Runs with recorded councils rederive from them alone.
- A property test applies random people changes and checks that rebuilding gives exactly the next day.
- The phase exit scenarios now record in format 2.

**The observer's reader** (`observer/reader.py`): `RunReader(root)` lists the saved days, gives the world on any day (forward reading rebuilds each day from the one before), that day's events, the councils, and new days on `refresh()`. It reads both formats. It keeps the journal's records in memory; O2 decides what to page out.

**Measurements** (`tests/perf/bench_people.py`, 31 days, format 2, this container):

| People | Map | Day | Hash v2 | Saving a day | Verify, per day | Changes per day | Snapshot | Journal per year | Peak memory |
|---|---|---|---|---|---|---|---|---|---|
| 20,000 | 48×48 | 0.85 s | 67 ms | 0.25 s | 0.59 s | 43 KB | 0.9 MB | 26 MB | 292 MB |
| 100,000 | 48×48 | 4.17 s | 170 ms | 0.90 s | 2.46 s | 69 KB | 3.8 MB | 71 MB | 1,067 MB |
| 100,000 | 100×100 | 5.14 s | 266 ms | 1.14 s | 2.96 s | 148 KB | 4.1 MB | 103 MB | 1,166 MB |

Before S5, at 100,000 people, the hash took 6.2 s and saving a day 4.8 s (2.9 MB a day, over 1 GB a year).
- **Journal:** 71–103 MB a year, under the 200 MB target. 100×100 stays under 150 MB, so the territory map needs no compact delta of its own (C6b not built).
- **Hash:** 0.17 s at 48×48 meets the 0.2 s target; at 100×100 it is 0.27 s. Most of that is dumping the world's fields (the territory map grows with the map), which saving a day dumps again. S6 can share one dump between the two.
- **Memory:** the peak rose to about 1.07 GB at 100K. S6 measured where it comes from: keeping the day before costs only about 30 MB; the peak is the whole-world JSON dump on snapshot and checkpoint days (about +340 MB), and those S5 rows were taken with `--verify`. S6's compact snapshots remove that dump.
- **A small world runs faster too:** the observer throughput run (seed 21, 48×48, a year, journal included) does 13.1 days a second, against 7.0 in Phase 4.
- **Verification** works every hash from scratch: about 2.5–3 s a simulated day at 100K, so a year takes about 15–18 minutes.

**Defaults taken** (the user can change them at review):
- A fork is saved in format 2, even from a format-1 run: the format is storage, not rules.
- No extra SQLite checkpoints every 30 days; `run` still saves one at its end.

## S6 as built: 100,000 people in well under a second a day

Planned with Fable 5.1 from a profile of a 100K day. Built in ten commits plus these docs. Every reference hash and both parity scenarios hold throughout.

**Measurements** (`tests/perf/bench_people.py 100000 --days 31`, this container). Before is c653256 with the C0 harness; a 31-day run includes one snapshot day (day 30).

| Case | Day, before → after | Hash v2 | Saving a day (average) | Day, hash and save | Snapshot day | Peak memory | Journal per year |
|---|---|---|---|---|---|---|---|
| Rules 1, 48×48 | 3.90 → **0.50 s** | 0.14 → 0.10 s | 0.80 → 0.13 s | 4.85 → **0.73 s** | 6.0 → 1.2 s | 623 → 436 MB | 70 → 59 MB |
| Rules 2, 48×48 | 5.54 → **0.45 s** | 0.14 → 0.14 s | 0.84 → 0.13 s | 6.52 → **0.73 s** | 6.1 → 1.3 s | 620 → 580 MB | 71 → 57 MB |
| Rules 2, 100×100 | 6.48 → **0.44 s** | 0.23 → 0.10 s | 1.10 → 0.14 s | 7.81 → **0.68 s** | 7.0 → 1.6 s | 672 → 571 MB | 105 → 63 MB |

All targets are met:
- at most 2 s a day with the journal;
- at most 1 GB of memory;
- at most 200 MB of journal a year;
- reference hashes unchanged.

Council days also became cheap. At 100K a report takes 49 ms instead of 202 ms per civilization, and a model's prompt 13 ms instead of 2.0 s.

**What changed**
- **C0, harness:** the synthetic world keeps five granaries instead of one per 5,000 food, which had given 1,800 per civilization at 100K. New `--rules 2` and `--phases` options.
- **C1, table indexes:** each people table keeps, never saved or hashed:
  - each row's place as one integer, and a captive mask;
  - which rows' text or dicts changed since the table was copied;
  - the id order, and the sorted living ids.

  Whole-column reads make no proxies and hand out no dicts.
- **C2, population day:** aging, death chances and conception pairs are worked out as arrays. The day's random rolls are drawn as one batch, in id order, which gives the same numbers as one draw per person; a standing test checks it.
- **C3, daily scans:** these steps read whole columns instead of visiting every person through a proxy:
  - feeding, hunger and recovery, including healers' houses;
  - residents per settlement, sight and enemy checks;
  - newcomers, assimilation and escapes;
  - language learning;
  - `validate_world`.
- **C4, territory:**
  - each map's neighbour and step costs are worked out once per set of roads and bridges, giving the same field in the same order;
  - coordinates sort by their (q, r) keys;
  - unchanged holds keep yesterday's record;
  - the day's territory is built in canonical order and not re-checked as a whole when set. It is checked on load, and a test checks each day of a 40-day run.
- **C4b, reach cap (rules 2):** a settlement's strength, its hall's bonus included, is capped at 300: a town of about 1,000 people, reaching about 25 tiles of open land. Before, a 25,000-person capital held every tile of a 100×100 map, so borders were decided by headcount alone.
- **C5, saving:**
  - hash v2 and the journal record share one dump of the world outside its people;
  - when a day's table is a copy of the one last saved, its changes are found by comparing rows in place, checking field by field only the rows marked as changed;
  - the housing policy and language learning skip the people they cannot affect.
- **C6, compact snapshots:**
  - format-2 snapshots and checkpoints store people as columns (snapshot version 2);
  - format-2 files compress at gzip level 6;
  - older snapshots still load; the two are told apart by their first key.
- **C7, council reports and council-5:**
  - reports add a population summary and up to 40 notable people;
  - the people fields are read from the columns, exactly as before, and an oracle test keeps the old code;
  - the prompt's state section no longer lists every person, and is always valid JSON within its budget. It used to be cut mid-list from about 550 people per civilization;
  - rules-2 charters state the reach cap.
- **C8, worker counts (rules 2):**
  - work at home may give `worker_count` and `settlement_id`, and the lowest-numbered idle grown-ups there are taken;
  - refusals are `invalid_workers`, `unknown_settlement` and `too_few_idle_workers`;
  - envelopes from model replies are schema 2.
- **Fix after C8:** the non-interference test caught that report counts used people's true captive state. Reports now count only what the council knows. The rules-2 housing view in reports had the same leak since S2.

**Not done, measured as not worth it:**
- replacing the remaining `CopyOnRead` uses: 8 ms a day;
- a custom civilization copy and digest arrays: about 50 ms a day together;
- the columnar territory delta (C6b): the 100×100 journal is 63 MB a year.

**Defaults taken** (the user can change them at review)
- Notable people: those on a duty first, then idle grown-ups taken in turn from each settlement, capital first.
- Ailing means health below the level needed to conceive.
- Children are under 16; elders are 65 and over.
- Drill counts take only people able to fight.
- At most 100 workers to a counted order.

**Checks**
- The full suite passes: 717 passed, 2 skipped.
- The soak suite passes: 218, all of it, for the first time in this phase.
- The observer throughput run (seed 21, 48×48, a year, journal included) does 16.3 days a second, against 13.1 after S5.
- Three soak tests were failing before S6. They are fixed in their scenarios only:
  - **Territory seeds 4 and 14**, failing since S3 required water for new settlements: the test's expanding sovereign now picks sites with water or a river beside them, as the rule asks.
  - **Cession seed 2**, failing since before Phase 5: its capital stands at the end of a causeway in open water, so the scenario raises an island for the colony there. Seeds that already found dry land keep exactly their setup.

**Known**
- Under rules 1 (no reach cap), territory on large maps is still the costliest step: 195 ms a day at 100K.
- Verification still works every hash from scratch.
- A counted order is resolved against the true state, as named orders always were. A capture the council has not heard of can therefore leave it one idle worker short of what its report showed.

## S7 as built: the observer shows 100,000 people

Planned with Fable 5.1 from measurements of the observer at 821aacf. Built in five commits plus these docs. The engine did not change; the Python suite was not touched.

**What you can open**
- `index.html?people=100000` loads 100,000 synthetic people, 25,000 at each capital, every one an individual you can click, inspect and follow.
- `index.html?people=100000&measure=auto` measures your PC. It spends 10 s in each band and then shows a table; **Copy** gives the full JSON to send back.

**Measurements** (headless Chromium in this container, software GL, 1600 × 900). Frame intervals here are not those of a real GPU. The JS update time (our own per-frame work, timed over 60 steps with the clock paused) and the memory are comparable.

| | Before (5,000 SAMPLE citizens) | After: 5,000 people | After: 50,000 people | After: 100,000 people |
|---|---|---|---|---|
| JS update, settlement band | 35.7 ms | 0.8 ms | 1.7 ms | 2.6 ms |
| JS update, local band | 5.3 ms | 1.2 ms | 1.8 ms | 1.1 ms |
| Frame update with the clock running, avg / p95 (settlement) | 39.5 / 80.8 ms | 4.7 / 12.7 ms | 6.2 / 15.6 ms | 6.6 / 15.2 ms |
| JS heap | 73–122 MB | 16–22 MB | 18–33 MB | 23–35 MB |
| Art atlas and village ground | 224.8 MB | 41.9 MB | 41.9 MB | 41.9 MB |
| People's own data | about 19 KB a person | 36 bytes a person | 36 bytes a person | 36 bytes a person (3.6 MB) |

`crowd.test.mjs` measures the crowd alone at 50,000 people: 330 bytes of heap a person, all pools included.

**Targets for "not straining a normal PC"** (1920 × 1080, a 2019-class laptop). Some can be measured only on your PC:

| Target | Goal | Here | Your PC (first run)* | Your PC (second run, after S7b)** |
|---|---|---|---|---|
| Frame interval p95, every band | ≤ 20 ms | (software GL: not meaningful) | 4.3–8.3 ms (238 fps) | 4.3 ms in every band (about 240 fps) |
| JS update | ≤ 8 ms avg, ≤ 16 ms p95 | 6.6 ms avg; p95 15.2 ms (settlement), 16.9 ms (regional, while streaming) | 0.4–1.7 ms avg; 0.6–6.5 ms p95 | 0.2–1.3 ms avg; 0.4–1.8 ms p95 |
| Textures | atlas ≤ 60, patches ≤ 138 (1080p cap), terrain ≤ 128 MB | 41.9; 37.5; 52.5 MB peak | 41.9; 150.5 (this screen's cap is 224); 68.2 MB | 41.9; 97.8 (485 of 1,326 entries, no evictions); 64.2 MB |
| JS heap | ≤ 150 MB | 35 MB | 54–134 MB | 44–120 MB |
| First frame | ≤ 10 s | 27–30 s (painting the art; unchanged by S7) | not recorded (S7b adds it) | 3.8 s |

\* Edge 154 on Windows, 2552 × 1283 at 1.5 device pixel ratio (7.4 M device pixels, caps × 2.33). The camera was moved during this first tour: its stops ended at zooms 2.6, 0.061 and 0.787 instead of 1.0, 0.1 and 0.0053. So the "regional" row measured the settlement band, and the regional band was not measured. That hand-zooming across eight patch levels also filled the patch cache to its entry cap (746 of 746, 1,001 evictions); a held view needs 80 to 270 patches. S7b holds the camera during the tour.

\*\* The same PC and screen after S7b. No stop was touched, and each row measured its own band (zooms 1.0, 0.1, 0.0053 and the whole world). The page was ready, with its first frame, 3.8 s after navigation began; painting the citizen designs took 2 of those seconds. No patch or terrain evictions happened during any stop. Every target is met.

**What changed**
- **C0, harness:** `__observer.atlasStats()` and `tests/measure.mjs --citizens` with JS update times. The "before" numbers above.
- **C1, atlas diet (224.8 → 41.9 MB):**
  - pages of 2048 px, packed tallest-first once all art is added, with no mipmaps;
  - people, nature, props, crops and vehicles kept at screen size (painted at twice the size, then halved); buildings keep twice the size;
  - each citizen frame is painted once with white accents. A cropped accent mask beside it is tinted with the civilization's colour when drawn, so four civilizations cost no more frames than one;
  - village ground baked at half resolution;
  - `textureBytes()` is exact.
- **C2, population feed and settlement plans:**
  - `PeopleFrame` (`src/data/population.js`) keeps people as typed columns: civilization, tile, settlement, sex, age, health, duty, design and house, with ids kept as numbers;
  - `syntheticPopulation` fills it deterministically, five to a house, with a simple age pyramid and fixed shares of duties;
  - a settlement plan lays out wards of 64 m blocks (16 houses each) in rings around the core until everyone has a house, and moves the fields beyond the wards. 25,000 people live within 685 m;
  - everyone follows one of nine daily routines by duty, sampled into a table of phases per minute, and walks the streets between home and work. Placing 100,000 people takes 9 ms.
- **C3, crowd layer:**
  - **settlement band:** the quality's crowd budget nearest the centre are animated sprites with a tinted accent; the rest in view are still particles; ward houses are sprites;
  - **local band:** houses are static particles and people are dots per 8, 16 or 32 m cell. A click lists the cell's people in the inspector;
  - **further out:** a badge per capital;
  - wards are painted into the ground: streets between the blocks and yards inside them;
  - picking is exact for sprites and particles, and the selected person is always drawn or pinned, so anyone can be followed.
- **C4, budgets:** the patch and terrain caches scale with the screen's device pixels, up to 2.33 times:
  - patches 96 MB × f, with 192 px patches from 3.5 M device pixels; 320 × f entries at 256 px, times (256/px)² for smaller patches (S7b), so the byte cap is the one that binds;
  - terrain 128 MB × f, at most 192 MB;
  - recomputed on resize;
  - the Measurements panel shows each cache's use against its cap.
- **C5, measuring at 100K:** the `?measure=auto` tour and `tests/measure.mjs --people`.

**Defaults taken** (the user can change them at review)
- The 100,000 live equally at the four capitals, 25,000 each.
- Ward houses are static and cannot be picked.
- Buildings keep twice-size art; everything else is screen size.
- Mipmaps are off. If the local band shimmers on your PC, they can be turned on for the building page only (+6 MB).
- The terrain cap is 128 MB at 1600 × 900 and grows to 192 MB.
- `?citizens` stays, for the existing tests.
- A local-band click lists the cell's people.
- Particles use a still frame; walking frames for particles come later.

**Known**
- Still particles are drawn over ward houses. Only people beyond the crowd budget, away from the centre of the view, are particles.
- Crowd people walking through a capital's core are drawn over the SAMPLE village's buildings.
- The old `?citizens=5000` path still makes two sprites per citizen (38.5 ms of JS at 5,000). It is kept for the existing tests; the crowd replaces it for large numbers.
- Loading takes 27–30 s here, nearly all of it painting the art. S7 did not change that.
- Routines are the same for everyone with the same duty, offset by up to half an hour; walks follow the street grid in an L.

**Handoff to O2.** The O2 reader fills the same `PeopleFrame` columns from `RunReader.state_at(day)`:
- ids are the engine's person numbers;
- the tile comes from `location`;
- the settlement is the store whose tile matches;
- age and health come from the columns;
- duty comes from work orders, institution staff, scholars and drills, with grown-ups otherwise farmers.

The settlement plan should then be sized from the engine's `housing` (houses per settlement) instead of residents ÷ 5. Which house each person lives in stays presentation.

**Checks**
- The observer suite passes: 70 tests, one file at a time.
- New tests: `atlas`, `population`, `settlement-plan`, `crowd` (at 5,000 and 50,000 people) and `budgets`.

### S7b: after the first measurement on your PC

Planned with Fable 5.1 from the first `?people=100000&measure=auto` result on the user's PC. Every measured target was met with room to spare (table above). Two things were fixed.

- **Patch entries follow the patch area.** With 192 px patches a view needs (256/192)², about 1.78 times, as many entries as with 256 px. So the entry cap bound first: 746 entries at 150 of 224 MB. Entries are now 320 × f × (256/px)²: 1,326 on that screen, where the 224 MB byte cap binds at about 1,109 entries. Patch memory can now read up to 224 MB there; that cap is by design. `patchTextureBytes(px)` is the one byte rule, and a test checks on five screens that bytes govern.
- **The tour holds the camera.**
  - During each stop the camera is put back if anything moves it (wheel, drag, buttons, minimap, follow); clicks select nothing; a banner asks for hands off;
  - each row records the band it really measured (`viewBand`), its zoom, whether it was `touched`, and per-stop deltas of patch and terrain evictions and patch bakes;
  - the heap is recorded at the start and end of each stop;
  - `measurement().load` records each loading phase's time, when the page was ready and the first frame.

  `tour.test.mjs` wheels, drags and clicks during the tour and checks the camera did not move.

**To measure again:** open `index.html?people=100000&measure=auto`, keep hands off until the table appears (about 50 s after loading), press **Copy** and send the text.

## Phase 5 complete

All nine of the user's asks are built and measured:
1. Mixed land cover, in the engine (S1, S3).
2. Speeds of 1× to 100× (S1).
3. Houses, settlement ranks and realm ranks (S2).
4. Seams for wild animals (S3).
5. Civilizations spaced further apart (S1).
6. 2–4 AI civilizations (S1).
7. Balanced sites (S1, S3).
8. 100,000 real people at 0.7 s a simulated day (S4–S6), shown and followable in the observer on the user's PC at about 240 fps (S7).
9. The atlas cut from 225 MB to 42 MB, with patch caches that follow the screen (S7, S7b).

Next is O2 (the read-only reader and a recorded run in the observer), planned in the phase-4 observer plan's sequence.
