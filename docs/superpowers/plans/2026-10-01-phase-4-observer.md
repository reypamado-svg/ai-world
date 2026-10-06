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
3. Browser: `node --test --test-concurrency=1 tests/*.test.mjs` in `observer/`, plus screenshots and the capture script.
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

## O1 as built

O1 was planned with Fable 5.1 (decisions below) and built in seven steps, one commit each.

**Decisions**
- **Presentation scale.** `hex_radius_m = 64`, so one engine tile holds exactly one sample village (inradius 55.4 m). It is recorded in the export's `presentation` section as a display constant.
- **Day-0 data.** The exporter also writes `day0.json` from `build_initial_state`: capitals, founder counts, known-tile radius and start strengths. No day is advanced and no store is opened. Everything drawn from it is tagged "engine day 0".
- **Village placement.** The SAMPLE village sits on the first civilization's capital tile (21,28), a forest tile with a lake next door.
  - The tile has no river, so there is no bridge, and the UI says so.
  - Sample objects that would fall outside the tile are dropped.

**Step 1: exporter** (`src/sovereign_world/observer/terrain_export.py`).
- Writes chunked JSON with every `Tile` field verbatim, an overview for the minimap, and the day-0 capitals.
- The manifest keeps `engine` and `engine_day0` (authoritative) apart from `presentation` (display only).
- The seed-21 48×48 export is committed (164 KB).
- Tests: byte-identical repeat exports; every tile round-trips in exactly one chunk; day-0 capitals match the engine starts; only the output directory is written; the engine never imports the observer package.

**Step 2: refactor.** The proof renderer is split into reusable modules (`render/art/bake.js`, `render/scene-renderer.js` with an origin offset, `camera.js`, `ui/inspector.js`, `ui/perf.js`, `debug-api.js`). A draw-order snapshot test proves the split changed nothing.

**Step 3: streaming and the world atlas.**
- `world/hex.js` lays out pointy-top hexes in a frame rotated 45°, so tile rows are horizontal on screen.
- `world/chunks.js` has the loader: at most 4 requests in flight, nearest first, with abort and discard of stale requests. It also has the bounded LRU caches; evicted textures are destroyed.
- `render/terrain-layer.js` bakes chunk textures at a resolution matched to the zoom, showing coarser cached textures until finer ones are ready.
- Markers for day-0 capitals, and a minimap.

**Step 4: bands.**
- From zoom 0.1, each visible tile gets its own detailed texture (`render/hex-detail.js`), painted only from that tile's own engine values. At settlement zoom it uses a tiling ground texture in the village style.
- Neighbouring tiles get tree and rock sprites at settlement zoom.
- `render/village-layer.js` places the village and crossfades the bands: atlas shows a badge, regional shows buildings and group dots, settlement shows animated citizens.
- The selected citizen keeps a ring or a pin in every band, and follow works across chunks.
- Picking prefers a citizen over a building in front of them; clicking again cycles to the building.

**Step 5: scale and quality.**
- `?citizens=N` (up to 5,000) adds SAMPLE residents: cloned, time-shifted routines with new stable IDs.
- Beyond the full-detail budget, far citizens are drawn as still frames.
- Quality High, Medium, Low or Auto.
- A Measurements panel that copies the numbers.

**Step 6: measurements.** `observer/tests/measure.mjs` and `tests/observer/throughput.py`.
- Measuring exposed per-frame waste, which was removed.
- Outside the settlement band, citizens are no longer placed or depth-sorted as sprites.
- Citizen pairs are skipped before any sort bookkeeping.

**Deviations from the plan, and limits**
- **No floating origin.** PixiJS composes transforms on the CPU in double precision, and the streaming test renders the far corner of a 4096×4096 world correctly.
- **Huge worlds can't be shown whole.** Minimum zoom is clamped so visible chunks stay within budget; a whole-world view needs an LOD pyramid (O3). The 48×48 world fits whole.
- **Decor depth.** Settlement-band tree and rock sprites on neighbouring tiles are sorted among themselves, not against travellers, who are drawn above them.
- **No borders.** Civilization borders are not drawn, because the day-0 export has no territory. Borders come with O2/O3 data.
- **Large sprite atlas.** The atlas uses two 4096² pages (about 225 MB of GPU memory including mipmaps) even though they are mostly empty. Tighter packing is a follow-up.
- **Sequential browser tests.** They run one file at a time (`--test-concurrency=1`), because the streaming test is timing-sensitive.

**Tests:** 19 browser tests (depth, order snapshot, hex, streaming, bands, counts) plus 5 Python exporter tests.

**Measurements: browser.** Headless Chromium in a container with software GL. Frame intervals there (about 65–100 ms) say nothing about a real GPU. The update column is our own per-frame CPU work.

| Citizens | View | Update avg / p95 (ms) | Outdoor | Visible (full / simplified) | Drawn sprites |
|---|---|---|---|---|---|
| 400 | settlement | 4.8 / 20.3 | 358 | 327 (327 / 0) | 553 |
| 400 | regional | 3.2 / 7.2 | 367 | 367 (0 / 367) | 255 |
| 2,000 | settlement | 16.2 / 36.8 | 1,813 | 1,664 (801 / 863) | 1,927 |
| 2,000 | regional | 10.0 / 34.0 | 1,811 | 1,811 (0 / 1,811) | 255 |
| 5,000 | settlement | 38.3 / 57.8 | 4,537 | 4,179 (801 / 3,378) | 4,502 |
| 5,000 | regional | 19.0 / 52.6 | 4,519 | 4,519 (0 / 4,519) | 255 |

Real-hardware numbers come from the user's machine, via the Measurements panel or `measure.mjs`.

**Measurements: simulation (Python).** `tests/observer/throughput.py` used a disposable scripted world (seed 21, 48×48, no providers). It ran 365 days in 33 s, about 11 days per second, including process start-up and journal writes; 141 people were alive at the end.

## Geography G1–G2 as built

The user reviewed O1 and asked for realistic terrain: tiles generated from their surroundings, regions such as mountain ranges, logical transitions, snow, deserts and rocky ground, rivers that run across many tiles, and water that cannot be crossed without a boat (shallow rivers excepted). That is an engine change, so it comes before the O1b day-scale work. The plan (Fable 5.1) is in the session plan; user decisions:
- Snow is passable at 5 days per tile.
- Rivers run along tile borders.
- This round adds fords and bridges; rafts come later.
- Forest may border mountains.

**G1: world generator version 2** (`src/sovereign_world/geography.py`).
- **Regions.** The map is split into regions, each with one dominant landscape (highland, plains, woodland, dry basin, cold upland, sea).
- **Fields.** Elevation, temperature and moisture are smooth integer value-noise fields over those regions:
  - temperature falls with elevation and runs from cold north to hot south;
  - a west-wind rain shadow dries the lee of ranges.
- **Two new terrains:** hills and snow. Snow lies only on high, cold mountains.
- **Neighbour table.** A pass removes every pairing that cannot sit side by side. Ranges step down through hills, tundra or forest, never straight onto grassland or desert, and desert never touches forest. Tests assert zero forbidden pairs.
- **Rivers.** They start on wet high ground and follow drainage links found by flood-filling corner heights from the sea, lakes and the map edge.
  - They run along tile borders and join into larger rivers.
  - Flow adds up downstream; at `DEEP_FLOW = 10` a river is deep.
  - A tile's `river` flag now means a river runs along one of its borders, so food, water and forage rules keep their meaning.
- **Resources follow the land:** timber in forest, stone in mountains and hills, and soil in lowland and along rivers.
- **Capitals** go on grassland or forest, all reachable from each other on foot, with riverside sites preferred. Version 2 also tries each good site as a first pick, because regional terrain clusters good sites together.
- **Speed:** a 100×100 world generates in about 1.5 s, against 13–16 s before. The start-region scan now uses precomputed offsets, which also speeds up version 1.

**Old runs stay valid.**
- `RunManifest.generator_version` defaults to 1 and is left out of the hash at 1, so every old manifest keeps its hash. New runs record 2, and forks copy their parent's version.
- Version 1 still rebuilds the original worlds; seed 21 at 48×48 gives the same four capitals.
- River borders live in `WorldMap.rivers`, which is left out of saved state when empty, so maps from before keep their state hash.

**G2: preview.**
- **Export.** Version 2 adds `hydrology.json` (river borders with flow and direction, lake tiles, the deep-flow line) and records the generator version. Seed 21 is re-exported at 100×100: 169 chunks, 732 KB.
- **Observer painting:**
  - hills (rolling rises), snowfields, snow on cold peaks, and lakes in a tint of their own;
  - rivers along tile borders, wider downstream, meandering identically from both sides so tiles join up.
- **Courier route.** It is now chosen from engine data: the first straight direction that stays on land, never crosses a deep river, and enters another chunk.
- **Zoom.** The whole engine world can be shown: the old 0.02 zoom floor is gone, and the visible-chunk budget for engine worlds is 192.

**Tests.** `tests/test_geography.py` covers:
- the share of tiles alike their neighbours, and that no forbidden pairs exist;
- that mountains form ranges, the terrain mix is plausible, and snow lies only on high cold ground;
- resource correlation;
- rivers: continuous to water or the edge, mostly downhill, never looping, long courses, deep stretches, and riverside flags that match;
- lowland, reachable starts for seeds 0–11 at 24 and 48;
- determinism and generation time;
- the version 1 golden starts, and that old manifests and river-less maps keep their hashes;
- that malformed rivers are refused.

The exporter test checks `hydrology.json` against the engine. The full suite passed with one expected fix: a test that built its "defaults" manifest with `RunManifest.new`.

## Third-party review (Codex) and G3 crossing rules

The user asked for a third-party model to review the phase. Draft PR #35 (base `main`, not for merging yet) was opened, and Codex reviewed commit c238e55. All three findings were real; each was fixed with a test, answered on its thread and resolved.

| Finding | What changed |
|---|---|
| **P1:** deep rivers blocked only start placement; movement and route checks ignored river borders | Built G3 on the PR (d1fb900, 5451a9b; detail below). |
| **P2:** the courier fell back to an unchecked route | `chooseCourierRoute` falls back to a single checked step, or returns no route, in which case the courier stays in the village (9371e7b, `route.test.mjs`). |
| **P2:** the `animHz` quality setting was never read | Citizens are drawn at a display time stepped to the level's rate, and unchanged schedule updates are skipped. At 15 Hz there are at most 17 recomputes per 60 frames, against at least 55 at high quality (9371e7b, `counts.test.mjs`). |

**G3 rules (`travel.py`).**
- `entry_cost(..., origin=)` adds the border crossing:
  - a stream (flow below 4) adds half a day;
  - a river adds a day;
  - a deep river (flow 10 or more) cannot be crossed on foot.
- `passable` and `travel_days` take `start=`, and `way_to` goes around deep borders.
- `MAX_PROGRESS` covers the dearest crossing. It is only an upper bound, so old data still loads.

**Callers.**
- Every caller passes the tile walked from: journeys and their day counts, roadwork, ambassadors, expeditions, territory influence and supply connection, toll detours, occupation and siege homeward days, and every route check in commands.
- **Expeditions.** One is refused only for a deep river seen from a known tile. An unseen one stops the explorers (BLOCKED) and reveals the far bank.
- **Council reports** carry `known_rivers` with their depth.
- **The baseline survey** stops short of known water and known deep rivers.

**Tests.** `tests/test_crossings.py` has 9 tests. The full suite passed (462 passed, 2 skipped) once three expedition tests were changed to choose a neighbour that really is one day away. The soak seed matrix passed 100/100.

## G4 bridges and G5 world rule, as built

**G4: road crews bridge rivers** (`bridges.py`, Fable 5.1 detailed design).
- **What gets bridged.** A crew raising a road to graded or better bridges every river border on its route, from the near bank, before crossing.

  | River | Labour | Materials | Crew needs |
  |---|---|---|---|
  | Stream or river | 60 person-days | 20 timber | — |
  | Deep river | 150 person-days | 40 timber + 20 stone | a living stoneworker |

  Any traveller then crosses at plain cost. A crew without what it needs stops with `materials` or `no_stoneworker`, as for roads.
- **Travel functions.** `entry_cost`, `passable`, `travel_days`, `way_to`, `detour`, `influence_field`, `supply_connected` and the day functions for journeys, ambassadors and expeditions take an optional set of bridged borders. The engine passes every bridge that stands; commands pass only those the civilization knows (its own, or in sight today).
- **Old saves keep their hash.**
  - `WorldState.bridges` is left out of saves while empty.
  - A crew's part-built bridge is counted in `work_done` with `work_grade` set to None, so the `Journey` schema is unchanged.
  - One deliberate departure from the design: leftover tile labour is not zeroed at the target grade. Zeroing it would change saved crew records and so the hashes of re-run old runs.
- **Planning and reporting.**
  - Estimates count bridge labour and materials; crews walk home over their own bridges.
  - A `bridge_built` event records each bridge.
  - Council reports list known bridges.
- **Learning about others' bridges.** Learning of rival bridges by exploring or exchanging maps is deferred, because it needs a persisted knowledge field. Until then a civilization only plans over bridges it can see, which errs on the safe side.

**G5: the world rule.**
- **Scale.** `travel.TILE_SPACING_M = 25_000`: neighbouring tile centres lie 25 km apart, a day's walk with rest and sleep, so a grassland tile takes one day.
- **Spec text.** The world design spec (§5.1, §6) states the scale, the terrain regions, the day costs, river crossings and bridges. The civilization-layer spec ties delivery to them.
- **Charter `council-3`.** Sovereigns now read a travel rule in their charter, generated from the travel and bridge tables. `SovereignConfig().prompt_version` is `council-3`. A manifest recorded with `council-2` keeps its hash, and continuing it with a model sovereign requires a fork, as designed.
- **Default size.** `init` now makes a 100 × 100 world by default (about 2,500 km across). The demo docs and the two tests that relied on the old default pass explicit sizes.

**Tests.** `tests/test_bridges.py` (7) and `tests/test_charter.py` (3).

**Throughput after G5.** `tests/observer/throughput.py`, seed 21, scripted sovereigns, no providers. It includes process start-up and a journal write every day.

| World | 365 days | Days per second | Living at the end |
|---|---|---|---|
| 48×48 | 52 s | 7.0 | 141 |
| 100×100 | 131 s | 2.8 | 141 |

O1 measured 11 days per second at 48×48 on a version-1 world.

**Where the time goes.**
- **The simulation itself.** Profiling showed most of its time was spent deep-copying the state each day, including the frozen world map. `WorldMap.__deepcopy__` now returns the map itself, which halves an in-memory day. Without journal writes, version-1 and version-2 worlds run at a similar speed.
- **Journal writes now dominate.** Every day the whole state is written. At 48×48 that is 485 KB of JSON: about 50 ms to gzip at the default level 9, 18 ms to hash and 7 ms to serialize.

**Suggested follow-up (not done here).** A lower gzip level, or journal deltas. Either would change the bytes of new journals, so it belongs in its own change.

## O1b as built: the observer at 25 km tiles

Planned with Fable 5.1 and built in six commits (C1–C6).

**Scale from the world rule (C1).**
- The terrain export (version 3) carries the engine's travel tables under `engine.travel`: tile spacing, day length in tenths, entry and crossing costs, and the stream and deep thresholds. The presentation hex radius is gone. The client derives the radius as 25,000 / √3 m and fails loudly if the table is missing.
- Texture levels are set in screen pixels per tile (chunks 32–256, tiles 512 / 1,024), so they hold at any tile size. At 25 km a tile is 565,685 px wide at zoom 1; the whole world fits the screen at zoom 1.4e-5.
- The camera has four bands:

  | Band | Zoom |
  |---|---|
  | atlas | below 4.5e-4 (a tile under 256 px) |
  | regional | 4.5e-4 – 0.02 |
  | local | 0.02 – 0.5 |
  | settlement | from 0.5 |

- The wheel zooms faster and the buttons double or halve the zoom, for five decades of zoom.

**The courier walks for days (C1).**
- `sim/travel-plan.js` plans the SAMPLE courier's trip from the engine's costs: a tile takes its entry cost in days; each day is five hours of walking and then a camp.
- A ford is waded at the border for its crossing cost. Deep rivers and water are refused.
- On the seed-21 route (two grassland tiles west) the courier walks, camps, arrives on day two, waits a day and walks home. This is a visual approximation, and the inspector says so.

**Local-origin rule (C2).**
- Canvas paths and Pixi graphics are float32, and at this scale the world is about 8e7 screen pixels wide, where float32 steps are 8 px. Every chunk and tile texture now paints relative to its own centre. The village's crowd dots and pin are drawn relative to the village. Sprites and containers carry the anchor in float64.
- The precision test paints a tile at (99, 99) from two nearby anchors: 0 pixels differ. From the world origin, about 870 do.

**Rivers in metres (C2).**
- `world/rivers.js` gives each river border a channel width by depth class: streams 40–60 m, fordable rivers 80–140 m, deep rivers 150–400 m.
- The wandering curve is analytic and seeded by the border, so every level draws the same curve and both tiles agree.
- Shallow borders show a gravel ford bar (presentation). The map keeps a minimum width of 1–3 px by flow.

**Tile interiors (C3).**
- `world/interior.js` is a pure colour field over the plane, painted from each tile's own values:
  - meadow or steppe by moisture;
  - canopy clumps by timber;
  - a snow line on mountains that rises with temperature;
  - dunes and rock in deserts, frost on tundra, wind ridges on snowfields;
  - hillshade whose strength follows the terrain.
- Land tiles blend over 15% of the radius: half and half at a border and a third each at a corner, from either side.
- Where water meets land, the shoreline wanders up to 16% of the radius off the border, corners round off, and a beach and shallows line it.
- Patterns finer than the sampling fade instead of aliasing.
- Map colours, tile textures and patches all sample the same field, so they agree.

**Ground patches and trees (C4).**
- From about 1,700 px per tile, the ground is drawn as square plane patches of 3,200 m down to 6.25 m, each 256 texels.
- Each patch is baked from the field on a 33- or 65-point lattice, scaled up, with a world-aligned grain and the rivers.
- The patch size keeps a texel under two screen pixels. Coarser patches show beneath until finer ones are ready, and a one-texel overlap hides seams.
- Budget: 320 patches or 96 MB, two bakes a frame (one at low quality).
- Trees, bushes and rocks come in 50 m cells from the tile beneath: timber for trees, stone for rocks. They never appear in water, in a river channel or in the village's clear zone.

**Village in its tile (C5).**
- The village stands at the tile centre with a SAMPLE ring of fields 60–400 m out, about 800 m across. The badge says so.
- The footer states the world rule and says whether a river borders the capital tile.

**Measurements (C6).**
- Run with `node tests/measure.mjs`, at quality high and 1600 × 900. The container renders with software GL, so the frame times are NOT representative of a real GPU.
- JS update time per frame, average (p95), in milliseconds:

  | Citizens | settlement 1.0 | local 0.1 | regional (tile 3,000 px) | atlas (world) |
  |---|---|---|---|---|
  | 400 | 6.7 (18) | 5.0 (14) | 0.4 (0.9) | 1.9 (6.5) |
  | 2,000 | 16 (37) | 10 (24) | 1.4 (3.0) | 3.2 (7.7) |
  | 5,000 | 36 (81) | 23 (50) | 3.1 (7.8) | 4.8 (8.6) |

- Patch bakes averaged 2.3–4.3 ms.
- Patch memory peaked at 37.5 MB (31 visible 3,200 m patches) and stayed far under the 96 MB budget.
- Terrain textures used 44.5 MB at the whole-world view (169 chunks).

**Tests.**
- New:
  - `travel-plan.test.mjs` (6);
  - `rivers.test.mjs` (2);
  - `interior.test.mjs` (8);
  - `patches.test.mjs` (2);
  - `precision.test.mjs` (2);
  - a patch-budget zoom-through in `bands.test.mjs`.
- `bands.test.mjs` follows the courier days into the journey.
- `counts`, `hex` and `streaming` take zooms in screen pixels per tile.
- All 45 browser tests and the exporter tests pass.

**Deviations from the plan.**
- Fords are drawn on every shallow border, not only on the courier's route (none of the sample route's borders carries a river).
- Hex outlines are drawn only between land tiles.
- Detailed tiles request their neighbours' chunks so they can blend. Without this, the first view at some zooms never finished.
- Decor cells are sorted by cell, not against travellers. This is the same known limitation as before.

**Deferred.**
- Bridge glyphs (the day-0 export has none).
- Roads and territory borders.
- Web Worker bakes, until bakes are measured over 8 ms a frame on real hardware.
- Ocean wave animation.
- Couriers for other capitals.

## O2 as built: a recorded run in the observer

Planned with Fable 5.1 after Phase 5. The user's decisions:
- **Housing:** draw the engine's houses by grade in S7's wards, mark crowded houses, and show houses under construction as building sites.
- **First run:** a fresh baseline year.

Built in seven commits. The engine is unchanged: golden hashes and parity hold.

**Reading without writing** (the journal safety rules above):
- `observer/journal_tail.py` follows a journal by byte offset:
  - it takes complete lines only;
  - it checks each record's sequence, hash-chain link and own hash;
  - it stops at the last good record and never repairs;
  - it starts again with a new `history_epoch` when the journal is cut back or replaced (its first line changes).
- `RunReader` is built on it:
  - it opens SQLite with `mode=ro&immutable=1`, so no WAL or shared-memory file can appear;
  - it never constructs `WorldStore`;
  - it reads payloads from the journal only when needed.
- Tests cover a partial line, a flipped byte, truncation, replacement and a concurrent append, and read every day, event and council of a format-2 run and of the format-1 fixture. Each checks that no file's bytes or modification time changed.

**Projection** (`observer/projection.py`). A day becomes:
- settlements: their houses by grade, slots, house work, institutions, rank and residents;
- every living person as columns: civilization, settlement or away, sex, age, health, duty and tile;
- travellers, counted by tile;
- tile owners.

Residents follow the engine's own rule (`residents_by_settlement`). Duties sort the council's orders and buildings into the observer's nine kinds of day. A 100,000-person day takes 0.11 s.

**Export** (`observer/run_export.py`; the README has the commands):
- the run's own terrain (`export_terrain_for`, checked against the run's map);
- a run-wide id table;
- per day, a JSON record and the people as gzipped little-endian columns: 15 bytes a person, 312 KB a day at 100,000 people.

The same run always exports the same bytes. `observer/tests/fixtures/run-small` is the export of the format-1 fixture, and a test keeps it current.

**Browser run mode** (`?run=NAME&day=N`):
- `RunSource` fills the same `PeopleFrame` S7 built, with the engine's ids, so `CrowdLayer` draws recorded people as it drew synthetic ones.
- `RunOverlays` adds a marker for every settlement (rank, people, houses, engine day), each civilization's border, and travellers as counted dots.
- The day stepper keeps the camera.
- Chips say RECORDED RUN, LAYOUT AND MOVEMENT: VISUAL APPROXIMATION, and observer-assigned names.
- The crowd, inspector and ground features no longer depend on the SAMPLE village.

**Housing by grade:**
- `SettlementPlan` lays out exactly the recorded houses, the finest nearest the core, then one building site for each house under construction; builders work at the sites.
- Residents fill five to a house; the overflow is shared round the houses. A crowded house carries an amber badge, and the inspector says "N people, room for 5 (crowded)".
- Runs without recorded houses (rules 1) and synthetic people keep a house for every five, labelled as presentation.

**The baseline year.** Seed 21, 48×48, rules 2, the scripted baseline councils, 365 days:
- it records in 13 s and verifies;
- it exports in 3.8 s (3.3 MB);
- verification still passes after the export.

It grows from 128 founders to 141 people. The baseline councils grow slowly; that is the engine's behaviour, not the observer's.

**Known**
- People on the road are counted dots at their tiles. They cannot be picked or followed until O3/O4 give them routes.
- People standing behind a row of houses are hidden by the roofs, which is correct depth.
- Exports are static files, refreshed by exporting again; the O3 server will serve the same data live.

## O3 as built: a run served live, with a chronicle

Planned with Fable 5.1 after the base defence slices. The user asked to "Start O3". Built in four commits: C0 9a9bb6b, C1–C5 2ffbf2c, 7d40809 and 63934aa. The engine is unchanged: golden hashes and parity hold.

**The server** (`sovereign-world observe RUN_DIR`, FastAPI and uvicorn as the optional `observer` extra):
- **One reader.** `observer/service.py` holds a single `RunReader` and never constructs `WorldStore`.
  - A follower thread polls the journal every half second and walks new days in order.
  - Walking in order means each person's number in the id table is the one the static export gives.
  - Day bytes are kept in a cache, the 64 most recent.
  - When the history epoch changes, every cache is dropped and the walk starts again.
- **The token** is checked by one router-level dependency on `/api`, so no API route can be added without it.
  - It is made fresh per process with `secrets.token_urlsafe(32)`, or taken from `SOVEREIGN_WORLD_OBSERVER_TOKEN`.
  - It is printed once, in the address's fragment (`#token=…`), which browsers never send. The page reads it once and drops it from the address (after Codex's fourth review), so it lives only in the page's memory; a reload asks for the printed address again.
  - It travels only as `Authorization: Bearer`.
  - It is never written to a file. When it comes from the environment, it is not printed at all.
- **Static files.** The page and its code are served without the token. `data/runs/` and `tests/` are refused.

**Routes.** Every answer carries `X-History-Epoch`.

| Route | Answer |
|---|---|
| `/api/status` | days saved and ready, the epoch, the people seen |
| `/api/run/manifest`, `/ids?from=N`, `/days` | the export's manifest and id table (or the ids from a place on), the ready days |
| `/api/run/days/{d}`, `/days/{d}/people` | the export's day record and people file, byte for byte |
| `/api/run/days/{d}/changes?from=N` | what turns day N's record into day d's: changed and removed settlements, owner changes, counts and travellers |
| `/api/run/days/{d}/routes` | active journeys and expeditions: kind, civilization, people, route, position, where the first traveller stands |
| `/api/run/chronicle?day=d` | the day's events, each with its place |
| `/api/run/terrain/...` | the terrain export's files for the run's own world |

A day not yet walked answers 409.

**Terrain for an edited map.** A run whose map was edited by a scenario is shown from its own recorded map, and the terrain manifest says so. Any other run's terrain is the export's.

**The chronicle** (`observer/chronicle.py`). The rules in "Chronicle locations" above are applied in this order:
1. **The event's own tile:** `q`/`r` or `tile_q`/`tile_r` in its payload.
2. **The ids it names**, looked up in the world as saved that day. The order is the payload's `settlement`, then `battle`, then the subject, then the actor.
   - A person is placed where they stood.
   - A settlement, ruin, site, battle, garrison, toll post or occupation is placed on its tile.
   - A siege is placed at its camp.
   - A journey or expedition is placed where its first traveller stood, or else where its route had reached.
   - A storehouse, institution or piece of work is placed at its settlement or site.
   - A civilization is placed at its capital, and tagged so.
3. **The day before.** Events that end something (a death, a fall, an elimination) look first in the world as saved the day before. A party that arrives, returns or leaves a site still stands in the day's record (finished journeys stay in it), so it is placed from that day.
4. **The other day.** When the first day looked at lacks the id, the other day is tried, and the place says which day it came from.
5. Otherwise the event is not placed.

Routine bookkeeping is marked, so the page can hide it: food eaten, orders accepted, single tiles gained or lost, tiles observed, councils held, gathering.

In a recorded war:
- battles are placed at the battle tile;
- a ceded colony is placed at its own tile;
- the dead are placed where they stood the day before;
- treaty breaches and wars learned of are placed at the capital.

**The browser** (`?run=live#token=…`):
- `ServerSource` is a `RunSource` that asks the server, with the token.
  - Stepping a day rebuilds its record from `/changes` since the day shown.
  - `TerrainSource` gets the run's terrain through an injected fetch.
- **Live chip.** It reads LIVE RUN · following (or paused) · N days.
  - Every second the page asks for new days and follows the newest.
  - Stepping back pauses following; **Follow latest** resumes it.
  - A new history, or any answer from another history, starts the page again with its token: every answer's `X-History-Epoch` is checked.
  - A wrong token stops loading with "The observer server refused the token (401)".
- **Chronicle panel.** The shown day's events in plain sentences, with observer-assigned names and how each was placed.
  - **Go** moves the camera to the event's tile.
  - **Follow** follows the person named, when they are at a settlement that day.
  - **Routine** shows the hidden bookkeeping.
- **Travellers.** A traveller dot can be clicked. The inspector lists the parties on that tile, with their kind, civilization, people and position on the route, and their routes are drawn on the map.

**Access boundary tests:**
- every `/api` route answers 401 without the token, with a wrong one, with no `Bearer`, or with the token in the address;
- day, people and terrain answers equal the export's bytes, for the format-1 fixture and the designed-town fixture;
- serving every day writes nothing to the run;
- `service.py` and `server.py` never build a store;
- days saved later are picked up, both by the follower thread and in the browser (within 5 s);
- a journal cut back is a new history, with the same bytes as a fresh export of the shorter run;
- **one subprocess test:** a 31-day `run` with the observer process attached and polling, and the same run without it, give identical journal bytes (councils and their prompt hashes included), identical checkpoints and the same files.

**Timings.** Measured here, through FastAPI's test client. A cached answer is the median of four repeats.

| | Baseline year (seed 21, 48×48, 141 people, 366 days) | 100,000 people (48×48, rules 2, 11 days) |
|---|---|---|
| Walk (projecting each day in order) | 5 ms a day; 2.0 s for the year | 0.34 s a day |
| Status, manifest, a day's record, changes | about 3–4 ms | about 3 ms |
| A day's people file | 3 ms (0.5 KB) | 3 ms (310 KB) |
| The id table | 3 ms (3 KB) | 14 ms (3.1 MB) |
| Routes or chronicle, first ask / cached | 40–85 ms / 3 ms | 0.4 s / 3 ms |
| A day dropped from the cache, asked again | 11 ms | 0.26 s |
| Terrain, first file / then | 1.0 s / 3 ms | 1.2 s / 3 ms |

**Defaults taken** (the user can change any of these):
- FastAPI.
- Polling, not server-sent events (those would put the token in an address).
- Record-level changes only; the people file is sent whole.
- Civilization events placed at their capital.
- Routine events hidden behind a toggle.
- Routes shown but not followed across days (O4).
- No level-of-detail pyramid (a 100×100 world already fits).
- Port 8766.

**Known**
- Explorers on a survey are counted at home by the engine's own rule (`residents_by_settlement`), which O2's projection follows. They are drawn in their settlement, not as dots on the road, although `/routes` lists their expedition. Only journeys' people are traveller dots.
- The id table at 100,000 people is 3 MB, fetched whole when the page opens; later refreshes ask only for new ids.
- The walk numbers people in day order and keeps the table in memory. At 100,000 people a long run takes about 0.34 s a day to walk once after the server starts.
