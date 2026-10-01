# Phase 4 Plan: The Observer

Slice O0 is this document. O1a (close-zoom art proof) comes first, then O1 to O6.

Phase 3 is complete and merged. This plan covers Phase 4. It gives the creator a read-only
window on the world: a layered atlas that zooms continuously into animated settlements, with
citizens who can be inspected and followed. It follows the roadmap's rule that each phase is
planned only after the one before it passes its gate.

## Why, and what binds the design

**Roadmap scope** (`docs/superpowers/plans/2026-09-21-ai-civilization-world-roadmap.md:27-31`).
A read-only web application showing:
- the live map, settlements, families, people and projects;
- knowledge, commands, diplomacy, wars, metrics and the chronicle.

It also adds pause, resume, display-speed control, checkpoint recovery and replay inspection,
with no live state editing.

**Exit gate (:31):** "all observer operations leave the authoritative world hash unchanged,
except advancing or restoring already committed history."

**Spec rules that bind the design** (`docs/superpowers/specs/2026-09-21-ai-civilization-world-design.md`):
- **§4.3:** the observer may inspect complete state because it belongs to the creator, not an
  inhabitant. It cannot edit commands, heal people, spawn resources, alter maps or change
  outcomes.
- **§15 (Creator Covenant):** creator capabilities are limited to observation, pause, resume,
  speed control, export, checkpoint recovery and termination. None of these may change
  simulated state or provide information to an inhabitant.
- **§16:** the first release includes read-only live observation, chronicles, checkpoints and
  replay. It excludes 3D graphics and generated art, so the observer uses a 2D sprite renderer
  with original procedural art now and a licensed pack later.

**Decisions taken with the user (2026-10-01):**
- **Artwork:** original code-painted isometric sprites now and a licensed pack later, behind a
  strict asset contract (IDs, sizes, ground anchors, footprints, frames, civilization-colour
  masks). All prototype art is labelled.
- **Names:** observer-assigned display labels derived from persistent IDs, for people,
  civilizations and settlements.
  - They are always tagged as observer-assigned.
  - The naming module is separate from rendering.
  - Family comes only from `parent_ids`.
  - Spouses, households and cultural affiliations beyond the recorded IDs show "Not recorded".
- **Terrain:** the prototype uses real engine world generation through an exporter that
  advances no days and touches no saves. The village and citizens are labelled SAMPLE.
- **Branch:** `claude/blissful-heisenberg-5q66cf` (an approved exception to `codex/`).

**Binding revisions (user, 2026-10-01):**
- **R1** An early close-zoom art proof (O1a).
- **R2** The terrain contract includes stone and separates engine units from presentation scale.
- **R3** Robust depth ordering, with visual tests.
- **R4** A separate large-world streaming test.
- **R5** Indoor citizens are shown through building highlight and occupancy, never through roofs.
- **R6** Chronicle locations come from historical state, and journal handling is safe.
- **R7** The server choice stays open until O3; any departure from FastAPI is explained.
- **R8** Only changed files are formatted; benchmarks run on disposable scripted worlds with no
  provider calls.
- **R9** Resident, outdoor and visible citizen counts are reported separately.
- **R10** A real access boundary between sovereigns and observer endpoints, not just import rules.

## Data inventory (verified against code)

| Brief needs | What exists | Gap / observer plan |
|---|---|---|
| Terrain, rivers | `Tile(coord, terrain, elevation, moisture, temperature, soil, timber, stone, ore, river)`, six terrains (`hexmap.py:26-46`). Axial parallelogram map; width and height ≥ 24 (`config.py:19-20`); CLI default 24×24. Rivers are per-tile flags on four fixed q-columns (`worldgen.py:76-81`). | No river edges and no pixel layout. The observer draws a ribbon inside flagged tiles only. The exporter emits every tile field, stone included. |
| Settlements | `Settlement(settlement_id, civilization_id, tile, founded_day, capital)` (`territory.py:29-38`), one hex tile each. | No names and no internal layout. |
| Buildings | `Storehouse(grade)`, `Walls(grade, towers)` (no tile), `Institution(kind, tile)`, `ConstructionProject(location, adds_capacity, status)`, storehouse and wall jobs, `TollPost(tile)`, `Ruin(tile)`. | **No footprints, positions inside a tile, or houses.** Housing capacity comes from the growth decree (`engine.py:5800`), so homes are never derived from shelter projects. The layout is a labelled presentation derivation (O2). |
| Farms | Food is produced per store from its known tiles' soil and water, limited by workers (`engine.py:5676-5684`). | Fields are drawn only on contributing tiles of a store that produced food that day. |
| People | `Person` (`people.py:46-83`): id, civilization, sex, age, tile location, `parent_ids`, health, skills, death, captivity, allegiances, language, culture, ancestry. Dead people are kept. | No name, household, spouse, occupation, activity or destination. Activity is derived from work, journey, expedition, garrison, institution and ambassador assignments. Destination comes only from a route. |
| Movement | `Journey` (13 kinds; route, `route_index`, progress in tenths of a day), messages, expeditions, `Road(tile, grade)`. | Tile-level, daily. Street movement is a labelled visual approximation. |
| Events | `DomainEvent(day, phase, sequence, kind, actor_id, subject_id, payload)`. | About 16 call sites carry `q`/`r` or `tile_q`/`tile_r`; the rest are ID-only and are resolved against recorded state (O3). |
| Knowledge | Known tiles, dated observations, contacts, messages, reports; `build_council_report` (`commands.py:489`); `hide_unseen` and `assert_no_hidden_knowledge` (tests). | The civilization perspective uses `CouncilReport` only (O5). |
| Storage | Hash-chained `journal.jsonl` (transition and council records) and the WAL-mode SQLite checkpoint store. | `read_records` rehashes everything, `append_record` truncates, and `manifest()` opens SQLite read-write, so the observer never constructs `WorldStore` (O2). |
| Live control | `cli run` is a blocking loop. | A single-writer runner with pause, resume and speed (O4). |

## Slices (each its own pull request, stacked)

- **O1a: close-zoom art proof.**
  - `observer/proof.html`: a sample village of 13 buildings, including an L-shaped two-part
    workshop and a construction site.
  - Woodland, a fenced field and props.
  - About 40 animated citizens, an envoy and an ox caravan.
  - Built on the real asset registry, atlas baker and depth sorter.
  - A pinned depth-test scene with visual tests.
  - Review screenshots and a clip. Stop for review.
- **O1: visual prototype.**
  - The terrain exporter (engine world generation, chunked, with the units contract).
  - The three-band client (atlas, regional, settlement).
  - Chunk cache, minimap, follow across chunks.
  - The large-world streaming test.
  - Measurements.
- **O2: read-only reader and projection.** A byte-offset journal reader (safety rules below),
  the observer projection, activity derivation, naming, and the presentation layout.
  - Open question for the user: how to depict housing.
  - Exit tests prove the hash and journal bytes are unchanged.
- **O3: server and client integration.**
  - The framework is decided here (FastAPI, Starlette, or the standard library).
  - Chunk endpoints and per-day deltas.
  - The chronicle with jump-to-location.
- **O4: live runner and replay.**
  - A single-writer runner with pause, resume and speed.
  - A replay timeline that only reads recorded days; day D must equal `replay_run(store, D)`.
- **O5: civilization perspective.** A serializer that takes only a `CouncilReport`, plus
  noninterference tests on the endpoint output.
- **O6: exit.** Acceptance tests, a performance report on the user's hardware, and art-pack swap
  readiness.

### Journal safety (O2)

- The reader never constructs `WorldStore` and opens SQLite with `mode=ro`.
- It tails the journal by byte offset. A trailing partial line is ignored until it is complete.
- Each record is verified against the previous hash and sequence.
- On corruption it stops at the last verified record and never repairs.
- On truncation or replacement it re-verifies from the start and bumps `history_epoch`.
- Tests cover a partial line, a flipped byte, truncation, replacement and a concurrent append.
  Each asserts that nothing was written.

### Chronicle locations (O3)

1. Use the event's own coordinates when present.
2. Otherwise resolve by ID against the recorded state at the end of the event's day.
3. For departures and endings, use the state at the end of the day before.
4. Otherwise show "location not recorded".

### Access boundary (O3/O5)

- **Separate process.** The observer runs as its own process with read-only handles and binds to
  localhost by default.
- **Token.** A random token is required by every endpoint and is never given to the runner or
  the sovereigns.
- **Control channel.** It accepts only pause, resume and speed, and carries no data.
- **Typed serializer.** The civilization serializer accepts a `CouncilReport`, never a
  `WorldState`.
- **Tests:**
  - 401 without the token.
  - Noninterference on the civilization endpoint.
  - Identical journal bytes and prompt hashes with and without an observer attached.

## Files

- **New:**
  - `observer/` (browser client, vendored PixiJS, tests);
  - `src/sovereign_world/observer/` (Python: exporter in O1, reader and projection in O2);
  - `tests/observer/`.
- **Modified:** none of the engine. Later slices add CLI commands only.
- **Reused:**
  - `generate_world` and `StableRng` (exporter);
  - `replay_run` and `state_hash` (replay and exit tests);
  - `build_council_report`, `hide_unseen` and `assert_no_hidden_knowledge` (civilization
    perspective).

## Verification (each slice)

1. Python: `ruff check src tests`, `ruff format` on changed files only, `MYPYPATH=src mypy -p sovereign_world`.
2. Python tests: the full `pytest -m "not soak"` suite, never run alongside another suite.
3. Browser: `node --test tests/*.test.mjs` in `observer/`, plus screenshots and the capture script.
4. Browser and Python throughput are reported separately. Container numbers come from software
   rendering and are labelled not representative.
5. Ask before opening any pull request.

## O1a as built

- **Asset contract** (`observer/src/render/art/registry.js`).
  - Fields: id, category, static or dynamic, ground footprint, anchor, size, animation frames
    and facings, civilization-colour mask, door points.
  - `checkContract` checks the depth rule at load: a sprite may be no wider than its projected
    footprint plus a tolerance. Buildings get 1.3 m to cover roof overhangs.
- **Painters** (`observer/src/render/art/paint/`):
  - Materials (planks, stone, logs, plaster and timber frame, thatch, shingles, clay tiles) are
    painted on each face through an affine face transform.
  - Lighting, contact shadow and separate cast-shadow sprites.
  - Shared citizen designs with clothing variations and walk, carry, hoe, hammer, axe, idle
    and talk frames; civilization colour on sash, cap or scarf.
  - An envoy with robe and banner, an ox and a wagon with turning wheels.
  - Ground tiles baked from a colour field: grass, rutted roads, worn patches, tilled furrows,
    upright tufts and pebbles.
- **Depth** (`observer/src/render/depth.js`): a topological sort over screen-overlapping pairs
  using the footprint separating-axis rule, with ties broken by centre depth and id.
- **Indoor people** are removed at the doorstep. When selected, their building is highlighted
  and the inspector shows "Inside: …".
- **Tests** (`observer/tests/depth.test.mjs`), five in total:
  - Ten pinned cases are checked by both draw order and flat-tint pixel probes.
  - The door case is checked for hidden citizens.
  - The indoor highlight.
  - Contract and cycles.
  - A negative control proving the naive centre-depth sorter fails the long-storehouse case.
