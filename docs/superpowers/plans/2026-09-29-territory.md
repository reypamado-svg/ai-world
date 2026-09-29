# Territory Plan

**Status:** Proposed for review. Nothing here is implemented yet.

**Goal:** Territory is a derived fact, not a painted claim. Each day the engine works out who effectively controls each tile from real presence, terrain and supply. Control changes slowly and deterministically. Each civilization sees only the control it has observed, and claims are kept as history only. This is the groundwork for war (occupation and supply lines), surrender, allegiance transfer, and the last-civilization-standing ending.

**Spec:** `specs/2026-09-27-civilization-layer-design.md`, Slice C:

> Control is computed from settlements, residents, roads, supply, patrols, administrative reach, and defensibility. A command may record a claimed border for historical purposes, but it cannot directly create controlled tiles.

## What the simulation has today

| Building block | Current state | Consequence for territory |
|---|---|---|
| Settlements | One implicit starting tile per civilization (`start_center`); no settlement record; no way to found another | Control from presence alone would be a fixed disc around each capital |
| Residents | Everyone lives on the starting tile except people on journeys or expeditions | Nothing but travel moves people off it |
| Terrain | Six terrain types, soil, rivers | Used for farming and foraging only |
| Movement | Every tile costs one day, **including water and mountains** | There is no friction of terrain |
| Roads, patrols, garrisons | None | — |
| Unused commands | `relocate_group`, `request_survey` and the `settlement_radius` decree are accepted but do nothing | Territory can give them real meaning, or they can be removed |
| Starting distances | At least 12 tiles apart on 24×24 (12–21 in a sample), 14–41 on 48×48 | Capitals' control areas will rarely touch without expansion |

## Research summary

A full report with sources is summarised here.

**How far power reaches.**
- Power weakens with distance from its base. Kenneth Boulding called this the "loss-of-strength gradient".
- Terrain adds friction: states held flat, farmable land and struggled in hills, forests and swamps (James C. Scott, *The Art of Not Being Governed*).
- Control depends on logistical reach (Michael Mann's "infrastructural power").
- Control fades outward in rings from the centre (Tambiah's "galactic polity").
- Pre-modern armies marched about 24–30 km a day and could carry at most about 14 days of food (Engels; Stanford ORBIS).
- So on our map, where one tile is one day's walk, unsupplied control should reach about **2–5 tiles** from a settlement, and much less over mountains.

**Spatial methods.**
- **Voronoi polygons** are cheap, but every tile gets an owner, so there is no frontier, and terrain is ignored.
- **Gravity models** add up influence from every source. They never fall to zero, and they reward swarms of tiny hamlets over one real centre.
- **Cost-distance fields** spread each source's strength outward over terrain costs, with a cutoff. They are deterministic, integer-friendly and terrain-aware, and they leave unclaimed frontier land where nobody reaches (the archaeologists' XTENT model; game-AI "influence maps").

**Game precedents.**
- **Europa Universalis V:** control decays from the capital along the cheapest path; roads and rivers cut the cost; current control *drifts slowly* toward its maximum.
- **Europa Universalis IV:** the legal owner and the occupying controller are separate.
- **Civilization IV:** tiles need strictly more culture to change hands, and cities never flip on culture alone.
- **Old World:** borders grow deterministically.
- **Crusader Kings III:** legal title drifts only after long holding.
- **Dwarf Fortress:** sites are occupied by presence.
- **Hearts of Iron IV:** supply along a route is limited by its weakest link.

**Shared lessons.**
1. Keep **claims, legal title and effective control** separate.
2. Leave **unclaimed frontier** land.
3. Prevent **flicker** with slow drift, two thresholds and a rule that the incumbent wins ties.
4. Mobile units must not "paint" territory.

## Proposed design

### 1. Settlements become records

- A new `Settlement` record holds: an id, a civilization, a tile, the founding day, and its residents (people whose home is that settlement).
- The starting tile becomes each civilization's first settlement, its capital.
- Later slices add founding new settlements and relocating people.

### 2. Influence field (daily, per civilization)

**Terrain costs.** Entering a tile costs, in tenths of a day:
- grassland 10;
- forest, desert or tundra 15;
- mountain 30;
- water impassable, until navigation exists.

A future road tile will cost 6.

**Sources.** Only settlements and supplied garrisons are sources:
- a settlement has strength 40 + 8 × isqrt(residents), which is 80 for 32 people;
- a garrison, in a later slice, has strength 25.
- Explorers, ambassadors, carriers and migrants add **nothing**. They observe; they never control.

**Influence.** A civilization's influence on a tile is the best of (source strength − cheapest travel cost to the tile) across its sources, floored at 0. It is computed with a bucket-queue Dijkstra over integer costs.
- A 32-person capital projects influence 40 or more about 4 grassland tiles out, but barely 1 tile across mountains.

**Cost.** Four civilizations × about 2 300 tiles × 6 neighbours is about 55 000 integer steps a day, cheap enough to recompute in full every day.

### 3. Held control, with drift and hysteresis

- Each tile keeps a *held* value per civilization that drifts toward its current influence by at most +3 a day while rising and −2 a day while falling. Only non-zero values are stored.
- **Ownership:**
  - An unowned tile is taken when a civilization's held value reaches 40.
  - It is lost when that value falls below 25.
  - A rival takes an owned tile only after beating the owner by at least 15 for 7 consecutive days.
  - Exact ties go to the incumbent.
- A tile with no owner is **frontier**. A tile where two or more civilizations hold 25 or more is **contested**.
- A settlement's own tile always belongs to its civilization while it has living residents. Taking it will need occupation (war slice) or surrender.
- Ownership changes emit `control_gained` and `control_lost` events.

### 4. Supply

A settlement or garrison that cannot reach its capital through owned or frontier tiles is *cut off*, and its strength is halved. Nothing can cut a route until war exists, but the rule is defined now so war can use it. It will also emit `route_severed`.

### 5. Claims

- A `claim_border` order records the tiles claimed, the day and the claimant, and emits `claim_recorded`.
- The control computation **never reads claims**. They exist for diplomacy and history.

### 6. Private knowledge

- A civilization always knows the ownership of tiles it currently holds, and of the tiles its settlements can see (radius 1).
- Everything else comes from observations. When one of its people observes a tile, the observation records the tile's owner as seen that day.
- A council report shows each observed tile's owner with its observation date. Like the rest of the map, this goes stale.
- The report never shows raw influence values: a bulge in a rival's influence would reveal an unseen settlement.
- The report does not reveal that one of the civilization's own tiles has become contested until someone observes it. The contesting rival's influence is hidden information until then.

### 7. Survival is not territory

A civilization survives while it has living members, as the spec says. Territory lags reality because of the drift rule, so it must not decide elimination.

## Open decision for you: should terrain slow travel too?

This design makes control **terrain-aware**, but today **travel is not**. Every tile, water included, costs a traveller one day. Leaving it that way means control would treat a mountain as three days wide while caravans cross it in one, and supply lines and campaigns (the war slice) would inherit that contradiction.

- **Option A (recommended): make travel terrain-aware first, as its own small slice.**
  - Journeys, expeditions and ambassadors spend the same entry costs: 1, 1.5 or 3 days per tile, and water is impassable.
  - Territory, supply and war then share one notion of distance.
  - Cost: every travel timing shifts, so the timelines in existing tests and seeded histories change.
- **Option B: terrain-aware control only.** It's faster to ship, but the contradiction stays until someone fixes travel.

## Proposed delivery, in slices

1. **Terrain-aware travel.** Only if you choose Option A.
2. **Settlements, influence and control.** Settlement records, the influence field, held control with hysteresis, frontier and contested tiles, claims as history, private observed control, events, and reports.
   - With only capitals this produces fixed territories, but it lays down every rule the next slices use.
3. **Expansion.**
   - `found_settlement`: settlers travel with provisions to a tile, as migrants do, and become residents of a new settlement.
   - Garrisons, or patrols: people stationed on a tile and supplied from a settlement.
   - This gives the three unused commands real meaning, or removes them.
4. **Roads.** A construction project on a tile that lowers its entry cost for travel and control, plus supply connectivity.
5. **War, occupation and surrender.** Occupation becomes a separate layer, as in Europa Universalis IV. It takes precedence over administrative control but not over ownership.

## Traps and how this design avoids them

| Trap | Guard |
|---|---|
| One explorer "claims" huge areas | Travellers have zero source strength |
| Control flickers daily | Drift caps, two thresholds, a 7-day challenge, and the incumbent wins ties |
| The control map leaks hidden rivals | Foreign ownership only as dated observations; no influence values in reports |
| Many hamlets outscore a real centre | Influence uses the strongest source, not a sum |
| Everything gets an owner | A cutoff leaves frontier land |
| Accelerated stress tests skip 364 days a year, so daily drift barely moves | Territory stress tests run real consecutive days |
| Nondeterminism | Integer costs, `isqrt`, fixed iteration order, no floats |

## Validation

- **Unit tests:**
  - Terrain costs, influence values and the Dijkstra cutoff.
  - Drift caps and hysteresis thresholds.
  - The 7-day challenge and incumbent ties.
  - Frontier and contested tiles.
  - Claims never change control.
  - Privacy: reports contain only observed or visible ownership.
- **Acceptance scenario:** two civilizations' control areas meet, a contested border forms, and the history replays to identical hashes.
- **Stress tests:** real-day histories check every day that each settlement tile is owned by its civilization, that no tile has two owners, and that ownership changes respect the drift and dwell limits.

## Slice 2 as built

These are the details the design above left open, settled during implementation:

- **Residents.** A settlement's residents are its civilization's living people standing on its tile, not counting anyone away exploring, on an embassy or on a journey. With no one home, a settlement projects nothing and is no longer anchored.
- **Timing.** Control is updated at the end of each day, after travel, eating, births and deaths.
- **Formation.** Held control starts at zero, so a new capital's land forms over about two weeks: at +3 a day, tiles reach the 40 threshold on day 13. A 32-person capital holds tiles out to 4 grassland days, where influence is exactly 40.
- **Own knowledge.** A civilization always knows which tiles it currently holds, and notices when it loses one, because its administrators do. It is not told who took the tile. It sees current ownership within 1 tile of its inhabited settlements; everything else comes from the owner that explorers recorded, with the date they saw it.
- **Supply.** A settlement cut off from its capital is halved, as designed. It can only happen once there are several settlements, so the `route_severed` event arrives with expansion (slice 3).
- **Events.** `control_gained` and `control_lost` name the civilization and the tile; a transfer also records who held the tile before. A claim emits `claim_recorded`.
- **Checked behaviour.**
  - In a sampled 48×48 world, the four capitals held 16–25 tiles each by day 60, depending on the terrain around them.
  - The whole simulation took about 0.03 seconds per day.
  - Natural starts are at least 12 tiles apart, so capitals' land does not meet until expansion.
