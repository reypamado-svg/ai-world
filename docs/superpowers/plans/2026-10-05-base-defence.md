# D — Base defence: the councils design their own defences (rules version 3)

Planned with Fable 5.1 in five slices, D0–D4 (checked at 0a66268, f0f1bd3, dc2a4e2, 5e1ec91 and 787a0bb); built on `claude/blissful-heisenberg-5q66cf`.

## What the user asked for

> Have the baseline build towers too. Give free reign on designing their defenses to the AI. They are free to employ tactics on their base defence.

**Decisions**
- All four groups of tactics:
  1. who fights and how;
  2. where to build;
  3. extra defensive works;
  4. active defence.
- Each is a structured order with a real cost, checked by the engine, so runs stay deterministic and replayable.
- The charter explains the battle rules to the councils.

**Rules version.** Everything joins rules 3 under a new flag, `Rules.town_defence` (on from rules 3), because no model-played rules-3 run had been recorded yet.
- New state is left out of the dump when empty.
- The battle's new parameters default to exactly the old random draws.
- Rules 1 and 2 are byte-identical: the reference hashes and the parity fixtures are unchanged.

## As built

### 1. Who fights and how: `set_defence` (D0, `defence.py`)

A `set_defence` order gives a settlement and a `defence`. It costs nothing, and a settlement's defence is set once a council. The order stands in `CivilizationState.defence_orders` and is shown in the council report.

| Field | Values | Effect |
|---|---|---|
| `posture` | `everyone` (default) | every able person fights |
| | `fighters` | only those with arms 10 or more fight; if fewer than 4, everyone does |
| | `craftsmen_back` | anyone with a building or making skill stays out: never hurt, never captured |
| `reserve_bp` | 0–5,000 | that share of the line, the least practised, joins at round 3, or at once when the line would break |
| `tower_crews` | `any` / `drilled` | `drilled`: the best fighters man the towers; a tower of two veterans hits 20% of the time instead of 15% |
| `arms_priority` | `any` / `veterans` | `veterans`: the best kits go to the most practised first |

- **Supplies:** a home side whose store cannot feed its line and reserve for a day fights short of supplies, which costs up to a quarter of its resolve.
- **Events:** `defence_set`, `held_back`, `reserve_joined`.

### 2. Where to build (D1)

- **Named sections.** `build_walls`, `repair_walls` and `build_towers` take `section_ids`: exactly those sections, in that order. Naming sections and also giving `wall_sections` is refused.
- **Towers.** Towers stand on named sections (`WallRing.tower_sections`). Without names they go to the gates first, then are spread round the ring. When battering lowers the cap, the highest-numbered towers fall first.
- **Gatehouses.** `build_works` with `work: gatehouse` on gate sections. Each costs what a tower on that grade costs, and falls with its section.
- **The assault point.** Each section is worth its grade's bonus after the attackers' engines:

  | Condition | Change |
  |---|---|
  | A tower on the section or beside it | +5% |
  | A gate without a gatehouse | −10% |

  Attackers press the weakest section, so the walls are worth halfway between the average section and the weakest one. That is then scaled by the houses inside the ring, as before.

### 3. Extra works (D2, `build_works`)

| Work | Cost | Needs | Effect |
|---|---|---|---|
| Ditch | 3 person-days a section | half the ring standing | a ram does only what ladders do: halves low walls, leaves high ones whole |
| Moat | 6 person-days a section | a ditch, and water on or beside the town | no ladders at all; a ram leaves low walls whole and halves high ones |
| Stakes | 2 timber and 1 person-day a section | timbercraft, half the ring standing | every standing section 5% harder in the next battle at home, then spent |
| Citadel | four sections of a grade from nothing (a palisade citadel: 16 timber, 36 person-days) | keep at the centre, a complete ring of radius 2 or more | see below |

**The citadel.**
- If the town is lost, its defenders fall back into it: nobody is pursued or taken captive.
- Raiders carry off at most half of the store.
- Once no wall section stands, catapults batter the citadel before they wound people.
- It moves with a ceded town.

**Events:** `works_started`, `works_built`, `stakes_cleared`, `fell_back_to_citadel`, `citadel_damaged`, `citadel_fell`.

### 4. Active defence (D3)

- **The town's own catapults.** Catapults in a settlement's store fight in a home battle. Each is crewed by 6 of the line, who fight at half strength, and hits the attackers 30% of the time before every round (event `engines_manned`).
- **Counter-battery.** While a camp besieges a town that has seen it, the town's crewed catapults fire at the camp every day.
  - Each catapult makes exactly three draws (does it hit, whom, how hard), after the camp's own draws on the camp's stream.
  - Hits wound or kill besiegers (event `camp_bombarded`).
- **A covered sally.** A war party sent from a besieged town against its camp is covered by the manned towers on the sections facing the camp.
  - Each tower hits 7.5% of the time in the opening volley and before every round, whichever side strikes first.
  - Behind a complete ring, a routed sally is chased half as far (5% instead of 10%).
  - Event: `sally_covered`.
- **Siege stores.** The council report's `siege_days_of_food` shows how long each besieged settlement's store lasts at one food a resident a day.

### The charter (council-7)

`defence_rule()` is written from the engine's tables and told only to rules-3 worlds. It covers:
- rounds and resolve;
- who fights;
- towers, gates and the assault point;
- engines, and what a ditch, moat or stakes do to them;
- the citadel;
- defender catapults, counter-battery and covered sallies;
- siege stores;
- every order's shape and cost.

Council-6 runs must be forked to carry on with a model sovereign.

### The baseline (D4, `scripted.py`)

**Defence order.** Once, at the first council with an order to spare, the baseline sets its capital's defence to `BASELINE_DEFENCE`:
- everyone fights;
- a tenth held in reserve;
- drilled tower crews;
- the best kits to veterans.

**Wall work.** Work on the capital's ring follows repair → raise → towers → gatehouses, with the same two-person crew and the same 150-timber reserve as before. Once every section stands:
1. towers go on the free gate sections, up to the ring's cap;
2. then gatehouses go on the gates;
3. each is ordered only when the crew knows the tower skill for the grade and the store keeps its reserve.

**What each ring gets.**
- **Palisade capitals:** towers on gates 0 and 5 (20 timber), then two gatehouses (20 timber).
- **Earthwork rings:** no towers, since their cap is 0.
- **A crew that knows stoneworking** puts stone gatehouses on earthwork gates (20 stone each) when the stone keeps its reserve. In the 24×24 test world one capital does this.

The baseline builds no ditch, stakes, citadel or catapults.

### The observer (D4)

**Export version 3.** Walls carry the following, each only when present:
- `tower_sections` (the engine's own placement, so the drawing matches the battles);
- `gatehouses`;
- `ditch` (1, or 2 for a moat);
- `stakes`;
- `citadel` {grade, strength}.

Each settlement carries its `defence` order.

**Drawing.**
- **Towers:** in the middle of their sections, one module beside the gate on a gate section. A v2 export keeps the old corners-first placement.
- **Gatehouses:** a fortified gate gets its own gatehouse art: tower-sized piers with a guard chamber over the way.
- **Ditch or moat:** a line half a block outside the wall, in dark earth or water blue.
- **Stakes:** short marks between the ditch and the wall.
- **Citadel:** a wall round the keep's block, its gate facing the store.
- **Badge:** adds "· defence set".
- **Atlas:** gates, gatehouses and towers are stored at 1×, so the atlas keeps to three pages.

**Fixture.** `observer/tests/fixtures/run-town`'s second capital is fortified from day 0 with all of these.

## Defaults taken

The user can change any of these:
- **Rules version:** the mechanics join rules 3 under the new flag.
- **Who fights and how:**
  - `fighters` means arms 10 or more, with at least 4, else everyone.
  - The reserve joins at round 3, or earlier if the line would break.
- **Citadel:** it does not block occupation.
- **Defender catapults and counter-battery:**
  - Catapult crews come from the last of the line, as the attackers' do.
  - Counter-battery is crewed by everyone able at home, whatever the posture.
  - A town fires only once it has seen the camp.
- **Siege stores:** days of food are counted against residents.
- **Sally cover:** each hex direction faces the sections nearest to it (one or two on a ring of 2).
- **Baseline:**
  - Its defence order goes last and only when the council has room.
  - Towers come before gatehouses.
  - It never builds a ditch, stakes, a citadel or catapults.
- **The citadel's cost:** four sections raised from nothing, so a palisade citadel takes 36 person-days, not the 24 the first plan's table said.

## The shown year

**The run:** seed 21, 48×48, rules 3, the scripted baseline councils, 365 days, with no AI calls.
- It records in 13 s and verifies through day 365 (418 records, journal format 2).
- It exports in 4 s (1.9 MB of people, not committed: `observer/data/runs/baseline-21-r3`).

**Timeline** (journal days):

| Day | What happened |
|---|---|
| 31 | All four capitals' defence orders are set |
| 61 | Wall work starts: two earthwork rings and two palisade rings |
| 121 | The two palisade capitals start their towers |
| 125 and 130 | The towers stand on gates 0 and 5 |
| 151 | Gatehouse work starts |
| 155 and 160 | Both gatehouses stand at each palisade capital |

Neither earthwork capital's crew knows stoneworking, so they have no gatehouses.

**Growth:** the year ends at 141 people (34, 34, 36, 37), as before. Timber ends at 360 and 340 at the palisade capitals (400 and 380 without towers and gatehouses).

**In the observer:**
- `?run=data/runs/baseline-21-r3&day=180` shows a palisade capital with both gatehouses and a tower beside each, and the badge "ringed town, designed by the council · walls 10 of 10 sections · defence set".
- `?run=tests/fixtures/run-town&day=0` shows the fortified fixture capital: ditch, stakes and the citadel round the keep.

## Tests

| File | Covers |
|---|---|
| `tests/test_defence.py` | the order and its checks; the battle hooks drawing as before; reserve, posture and craftsmen kept back; supplies; the charter |
| `tests/test_defence_placement.py` | named sections; towers by section and automatic placement; battering; gatehouses; the assault point; dumps without the new fields |
| `tests/test_defence_works.py` | the ditch and moat table; costs and every refusal; works built; stakes spent in a raid; refuge and halved plunder; the citadel battered |
| `tests/test_defence_sieges.py` | defender catapults and cover; halved pursuit; facing sections; counter-battery's exact draw count; stored catapults in a home battle; covered sallies on both sides; a starving town's resolve; `siege_days_of_food` |
| `tests/test_baseline_walls.py` | towers then gatehouses on a complete ring, with reserve and skill limits; the defence order at the first council with room; a 165-day run with towers, gatehouses and defence orders and 32 people each |
| `tests/observer/test_run_export.py` | export version 3's keys and the re-exported fixtures |
| `observer/tests/settlement-plan.test.mjs` | tower, gatehouse, moat, stakes and citadel positions |
| `observer/tests/town.test.mjs` | the fortified fixture capital in the browser |

## Validation

- **D0–D3:**
  - full Python suite at D3: 806 passed;
  - soak matrices after D3: siege 8, war 12, seed 100.
- **D4:**
  - full Python suite: 809 passed;
  - observer suite: 80 of 80, including the atlas at three pages.
