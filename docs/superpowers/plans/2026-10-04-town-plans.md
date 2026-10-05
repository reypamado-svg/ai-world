# T1 — Councils design their settlements like a kingdom (rules version 3)

Planned with Fable 5.1 at 08197f1; built on `claude/blissful-heisenberg-5q66cf`.

## What the user asked for

> Can we ask the ai model to design their settlement like a kingdom?

**Decisions**
- The AI council designs each settlement; the engine checks and records the design.
- It changes the game, so it is a new rules version, 3.
- Every settlement starts with a neutral, plain plan.
- Walls are built section by section, and a council may redesign or widen them at any time, at a cost.
- The shrine is drawn but has no effect yet.

Rules 1 and 2 runs are byte-identical: the reference hashes, the parity fixtures and every rules-1/2 path are unchanged.

## As built

### The design (`townplan.py`)

| Field | Values |
|---|---|
| `style` | open, ringed, grid, river_town (needs water), hill_fort (needs hills or mountains) |
| `keep` (the hall's place) | centre, by_store, by_gate, by_water (needs water), edge |
| `market`, `shrine`, `craft_quarter` | the same places, each optional |
| `wall_ring` | 1 to 5 blocks of 64 m from the centre |
| `gates` | 1 to 3 distinct hex directions, 0 to 5 |

- `TownPlan` adds `settlement_id` and `planned_day`; plans live in `CivilizationState.town_plans`, omitted when empty.
- The plain plan is open, keep at the edge, ring 2, one gate east. A rules-3 capital and every settlers' town start with it.
- Plans move with a ceded settlement and go with a fallen people. `validate_world` checks that a plan belongs to its own settlement and suits its land.

### The plan order

- `plan_settlement` with `settlement_id` and `town_plan`. It costs nothing.
- Refusals all use `invalid_town_plan`, with these messages:
  - this world's rules have no town plans (rules 1 and 2);
  - a plan order names its settlement and gives its design;
  - only this civilization's own settlements are planned;
  - a settlement is planned once a council;
  - a hill fort needs hills or mountains, or a river town or a place by the water needs water.
- A design that repeats a gate, or faces one outside 0–5, does not fit the reply schema, so the model is asked again.
- Event: `settlement_planned`.

### Walls by section (`rings.py`)

**The ring.** A ring of radius r is a square of side 2r+1 blocks, and a section is two block-sides.

| Ring | Sections | Houses inside |
|---|---|---|
| 1 | 6 | 128 |
| 2 | 10 | 384 |
| 3 | 14 | 768 |
| 4 | 18 | 1,280 |
| 5 | 22 | 1,920 |

Gate g is section ⌊g·n/6⌋.

**Cost.** A section costs a tenth of a whole wall's grade step, so a complete ring 2 costs exactly what walls always cost. Ring 5 costs 2.2 times as much.

| Grade | One section |
|---|---|
| Earthwork | 3 person-days |
| Palisade | 4 timber, 6 person-days |
| Drystone wall | 8 stone, 12 person-days |
| Mortared wall | 15 stone, 1 tool, 20 person-days |
| Fortress wall | 25 stone, 2 tools, 30 person-days |

**State.**
- `CivilizationState.wall_rings` holds a `WallRing` per settlement: each section's grade (or none), strength and gate flag, and the towers. It is omitted when empty.
- Ring work rides in the existing `wall_jobs` as a `WallJob` with `sections` and `section_grades`. Those two fields are omitted when empty, so rules-1/2 jobs dump as before. Every reader of wall jobs (busy people, departures, what spies count) therefore works unchanged.

**Orders.**
- `build_walls` takes an optional `wall_sections` (1–22). It raises the weakest sections first, lowest index on ties, to `wall_grade`. Without a count it raises every section below that grade. All materials are taken when the work begins.
- `repair_walls` mends every damaged section for a quarter of its share, rounded up.
- `build_towers` adds towers to a complete ring, up to the weakest grade's towers per ten sections (a standard palisade ring carries 2; a fortress ring 5 carries 13).
- One wall job per settlement at a time. Under rules 1 and 2, `wall_sections` is refused with `invalid_walls`.

**Effects.**
- **Defence:** each standing section contributes its share of its grade's bonus, after the attackers' engines. The total is scaled by the share of the settlement's houses inside the ring. Five of ten palisade sections give +1,250 bp instead of +2,500; 600 houses behind ring 2 keep 384/600 = 64% of the bonus.
- **Siege:** every catapult hit lands on the most battered standing section (lowest strength, lowest index on ties), with no new random draw, so a breach is pressed. A section whose strength runs out falls a grade at full strength; earthwork that falls leaves a gap. Towers beyond what the weakest standing grade carries fall with it. Events: `wall_section_damaged`, `wall_section_fell`.
- **Redesign:** a plan that moves the ring or its gates pulls the old ring down. Half of what its sections and towers cost comes back to the store, and wall work on it stops with its unused materials returned. Event: `walls_salvaged`.
- **Reach of "walls" elsewhere:** a complete ring counts as walls of its weakest grade for ranks, ruins and spies. A ruin's walls go back up along a resettled town's line.

### The plan's places count (rules 3)

| Place | Effect |
|---|---|
| Keep at the centre, hall open | the settlement's defence 12,500 → 13,000 bp |
| Hill fort | +500 bp ground defence at home |
| Craft quarter by the water, workshop open | the workshop's extra day every 3rd day instead of 4th |
| Market by the store | +2,000 store room (taken away again if the market moves) |
| Shrine | drawn only |

When a settlement is stormed, the houses beyond the wall line are the first to burn. The number lost is the quarter it always was; the event says how many stood outside (`outside`).

### Council-6 and the baseline

- `PROMPT_VERSION = "council-6"`. `town_plan_rule()` is written from the tables and told only to rules-3 worlds. Council-5 manifests get the usual "fork the run" refusal.
- `town_plans` and `wall_rings` are in the council report and kept in the state summary until last.
- The baseline, under rules 3, designs its capital while it still has the plain plan: ringed, keep at the centre, market by the store, the craft quarter by the water (else by the store), ring 2, gates east and west. The order goes last, so it lands at the first council with an order to spare, day 30.
- **Baseline walls (T1b).** From the council after the design, the baseline walls its capital along the ring:
  - **Crew:** two idle people not named by its other orders, timbercraft knowers first.
  - **Grade:** a palisade when one of them knows timbercraft, otherwise earthwork. The whole ring goes in one order.
  - **Reserve:** it keeps 150 timber (half its materials target) after paying.
  - **Repairs:** it mends damaged sections first, when the crew can.
  - **Upgrades:** an earthwork ring is raised to palisade once a timbercraft knower is idle.
  - Towers and gatehouses came later, with the base defence work (`2026-10-05-base-defence.md`). The wall order goes last too, so it never displaces houses, storage or the hall.

### Observer

- **Export:** run export version 2. Rules-3 settlements carry `plan` and `walls` (each section's grade and strength, the gate sections and the towers).
- **Layout:** a designed town lays its wards inside the ring first. The keep, market, shrine and craft quarter take whole blocks where the design puts them.
- **The wall line:**
  - built sections are drawn from 8 m wall modules in their grade's look;
  - each gate section has a timber or stone gatehouse at its middle, and towers stand at the corners, then at section ends;
  - unbuilt sections are a dashed line on the ground, and battered sections (below half strength) are tinted;
  - guards walk the walls, and the fields lie beyond them.
- **Badge:** for example, "ringed town, designed by the council · walls 6 of 10 sections".
- **New art:** wall modules for five grades and two axes, timber and stone gatehouses and towers, and a small shrine.
- **Fixtures:** `observer/tests/fixtures/run-town` is a small rules-3 run, generated by `tests/town_fixture.py`, whose first capital is designed on day 0 and has six palisade sections up by day 18.

### Defaults taken

The user can change any of these:
- A storm's losses stay a quarter of the houses; "outside first" decides which ones, and is reported.
- A wall order is checked against the plan as it stands at the council; if a plan order in the same envelope changes the ring, the work follows the new line.
- Towers need a complete ring; gaps from catapults do not topple the towers already standing.
- `wall_sections` larger than the sections still below the grade raises all of them.
- The rules-2 charter's text is unchanged apart from its reply schema, which now shows the new order and fields.

## The shown year

**The run:** seed 21, 48×48, rules 3, the scripted baseline councils, 365 days, with no AI calls.
- It records in 12 s and verifies through day 365.
- It exports in 4 s (3.3 MB, not committed: `observer/data/runs/baseline-21-r3`).

**The designs:** all four capitals keep the plain plan until day 30, when the baseline's design lands: ringed, keep at the centre, market by the store, gates east and west. Three of them put their craft quarter by the water, and one, with no water beside it, by the store.

**Walls (T1b):**
- All four capitals start their walls on day 60.
- The two without timbercraft have complete earthwork rings by day 76; the two with it, complete palisade rings by day 105.

**Growth:** the year ends at 141 people, as the rules-2 year did. The designs and walls cost the baseline nothing in food or growth; timber ends at 400 and 380 at the palisade capitals instead of 440 and 420.

**In the observer:**
- A capital shows its hall at the centre, the market's well by the store and its houses in the first ward block, all inside its finished wall: earthwork, or a palisade with gatehouses east and west.
- The badge reads, for example, "ringed town, designed by the council · walls 10 of 10 sections".
- `?run=tests/fixtures/run-town&day=18` shows a walled one: six palisade sections with both gatehouses, the four still to build dashed, and the shrine outside.

**Validation:**
- Full Python suite: 767 passed.
- Soak matrices after C2 (siege, war, seed, cession, endings): 132 passed.
- Observer suite: 79 of 79.

## Tests

- `tests/test_town_plans.py` covers:
  - the design model;
  - the plain plan at founding and for settlers;
  - cession and elimination;
  - validation;
  - the plan order and each refusal.
- `tests/test_wall_rings.py` covers:
  - section costs equal to a tenth of each step;
  - gates and towers;
  - the defence shares;
  - bombardment;
  - salvage;
  - section-by-section building in the engine;
  - order checks;
  - redesign stopping work;
  - the keep, hill fort, workshop and market;
  - a ceded ring and a ruin's walls;
  - storms;
  - determinism.
- `tests/test_baseline_walls.py` covers the baseline's walls:
  - none before the design, or under older rules;
  - a crew of two idle hands outside the reserved ones;
  - palisade or earthwork by skill;
  - waiting for an open job, the reserve and a crew;
  - raising an earthwork ring, leaving a palisade one, mending a battered section;
  - every capital walled within four months with nobody hungry.
- `tests/test_town_plan_councils.py` covers the council-6 rule, the baseline's design landing on day 30, and a model's design rederiving to the same hash.
- `tests/observer/test_run_export.py` covers export version 2 and both committed fixtures.
- `observer/tests/settlement-plan.test.mjs` and `observer/tests/town.test.mjs` cover the layout and the town in the browser.
