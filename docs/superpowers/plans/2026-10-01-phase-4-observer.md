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
- **Control channel.** It accepts only pause, resume and lookahead, and carries no data; speed
  is a presentation clock in the page (as built in O4: only `pause` and `resume` reach the
  runner).
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

## O4 as built: running the world from the observer, and replaying it

Planned with Fable 5.1 after O3 and its review. The user said "Start O4". Built in five commits: C0 484b3cb, C1 d5b4316, C2 09984fb, C3 d59c77b (with 816f79d) and C4 ca34d58, plus these docs. The engine is unchanged: golden hashes and parity hold.

**The runner** (`runner.py`, `sovereign-world run --controlled`):
- `run_days` is the loop `run` always had, moved as it was: replay the latest verified state, advance a day, save it and its councils, and save a checkpoint at the end.
- A `RunControl` may hold the loop between days. Nothing about a pause reaches the world, its random streams or its journal.
- With `--controlled`, the run starts paused and reads `pause` and `resume` lines on standard input. It reports `start`, `paused`, `running`, `day N` and `done N` on standard output. The end of its input stops it after the day under way.

**The observer holds it** (`observe --run-days N`, `observer/runner_link.py`):
- **The link.** The observer starts the runner as its child, with its own environment minus its token, and writes only `pause` and `resume` to it.
- **The gate** (`RunService._gate`). The runner may go on only while:
  - the page plays;
  - a day is shown;
  - the newest saved day is fewer than `lookahead` days (default 3) ahead of it.

  Only changes are sent to the runner. A new history stops it: the runner is terminated at once, with no checkpoint, because its world and its store's journal tail are the old history's, and is reported as `stopped`. The control routes go on answering (review fix).
- **Routes.** `GET` and `POST /api/control` (paused, lookahead 1–30, the day shown) sit behind the token, like every `/api` route. The token test now checks every method.
- **One writer.** The runner is the only writer. The observer still never builds a store, and the runner never sees the observer or its token (a source check).

**The day records carry the run's own hash** (export version 4, C2):
- Each day record carries the `state_hash` the run saved for it: the journal record's, or the verified checkpoint's for the first day.
- A test checks that every day served (the format-1 fixture, the designed town, and days saved while it is followed) is byte for byte `day_record(project_day(replay_run(store, D)))` with that hash.
- In the page, the day label's tooltip shows the hash, and the browser test checks it against `sovereign-world replay --day D`.

**The replay timeline** (C3):
- **Playing.** When the display clock passes a whole day (86,400 display seconds; 14.4 minutes at 100×), the next recorded day is shown and the extra time is carried over. At the last day it holds: "waiting for day N" live, the end of the recording for an export.
- **Moving around.** A slider skips to any recorded day. Follow latest keeps jumping to the newest.
- **Labels.**
  - The clock: "Engine day 12 (recorded) · display 14:05 (presentation)".
  - The live chip: "playing at 100×", "following newest", "waiting for day N" or "paused".
  - The runner chip: "RUNNER · running · day 15 of 365 · 3 ahead", "finished at day N", "stopped: the run's history changed" or "exited (code)".
- **Only the newest day load is shown** (review fix). Each day load is numbered; one that a later load overtook shows nothing and posts nothing. Control posts go one at a time, so the last day asked is the one the runner is gated on.
- **Opening.** With a runner attached, the page opens paused.

**Following a party** (C4):
- **Follow party** in the inspector's party list. The camera eases to where the party stands each day (its first traveller's tile, else how far its route had come), and its route stays drawn.
- When the party is no longer on the road, the follow ends with "The journey ended before day N (last seen on day M)".
- Moving the camera by hand, or selecting a person, stops it.

**Tests:**
- **`tests/test_runner.py`:**
  - a run held twice across a council day saves the same journal (prompt hashes included) and checkpoints as `run --days 31`;
  - a stopped run saves nothing;
  - the controlled runner waits, reports each day, stops after the day under way at the end of its input, and verifies.
- **`tests/observer/test_runner_link.py`:**
  - the gate table;
  - a new history stops the runner (a fake one, and a real one that had saved days of its own: the child is gone, and the journal bytes and checkpoints are unchanged);
  - the control route's checks;
  - the runner's environment without the token;
  - a real runner started and finished;
  - **a run driven from a scripted page** through `observe --run-days 31`, with a pause at day 10 and lookahead 5 from day 20: it saves the same journal bytes, prompt hashes, checkpoints and files as `run --days 31`, and the token is in no output.
- **`tests/observer/test_replay_timeline.py`:** day records equal their replay.
- **`observer/tests/timeline.test.mjs`:** stepping days with time carried over, holding at the end, the slider, the hash; a slow day load that a later one overtakes shows nothing.
- **`observer/tests/server.test.mjs`:**
  - **`observe --run-days 6`:**
    - it opens paused;
    - it runs up to the lookahead and waits;
    - one more day comes when the page moves on;
    - of two day loads in a row, only the last is shown and posted as the day shown;
    - lookahead 1;
    - it finishes;
    - the shown hash equals `replay --day`;
    - `verify` passes;
    - the records equal a plain `run`.
  - **Following the war party:** the camera is on its tile each day until it comes home.

**Found on the way.** A format-1 journal gzips each saved day with the time of saving, so two format-1 runs of the same days are equal in content (days, states, hashes, events, councils) but not byte for byte. Format-2 journals, which new runs use, are equal byte for byte. The format-1 browser test therefore compares content. The old format is left as it is.

**Defaults taken** (the user can change any of these):
- the lookahead is 3 days;
- `--run-days` is required, and the runner stops at it with a checkpoint;
- pausing takes effect between days;
- a runner that crashes is shown as exited, with no restart;
- closing the observer stops the run after the day under way;
- pause stops both the display and the runner;
- speed stays a presentation clock;
- a page with a runner opens paused;
- the runner's stderr goes to the observer's terminal.

**Known**
- Pausing can leave the run one day further ahead than the lookahead, while it finishes the day under way.
- Nothing stops a second `sovereign-world run` on the same run while a runner is attached; the README says so.
- A journal cut back under a live runner stops it and restarts the page; restart `observe --run-days` to run the new history. A day that finishes in the half second before the cut is noticed is still appended, so cut a run only while it is paused. `WorldStore` itself still extends a shortened journal to its cached length (a follow-up).

## O5 as built: the world as one civilization's council knows it

Planned with Fable 5.1 after O4. The user asked "What's next?" and chose "Start O5". Built in four commits: C0 e9bd54f, C1 2d2410e, C2 0e0850f and C3 e1155b0, plus a layering fix and these docs. The engine is unchanged (no file under `src/sovereign_world/` outside `observer/` changed), so golden hashes and parity hold by construction.

**Where the view comes from.** The journal keeps only each council report's id and hash, not its body, so "what the council saw" is rebuilt: `build_council_report(state, civilization)` on the saved day, exactly the call the engine makes for a council. On council days (0 and every 30th) this is the report the council read; the engine's own leak tests already check that rebuilding gives the same report. On other days it is what the council would be told if it sat that day.

**The serializer** (`observer/perspective.py`). `perspective_record(report)` takes a `CouncilReport` and nothing else. A test checks its signature and that the module imports no state, reader or store and never builds a report. The record holds:
- the tiles it knows, their terrain, and for each tile it has observed the day last seen and the owner it saw;
- its own settlements, in the run export's row shape (houses, house work, institutions, plan, walls, defence), with residents and idle workers from the report's population summary;
- its people: the population summary and the notable people (at most 40) with age, sex, health, duty and skills;
- the foreign settlements it has met (its contacts, with first and last seen days), ruins, sites (what is left as last seen), roads, bridges, toll posts and garrisons it knows of, each with its as-of day;
- the borders it knows, its own parties out (spies, couriers, extraction parties, and petitioners at its gates), sieges and wars it knows of;
- its count of its people: living from the population summary, at home the sum of residents (captives held there included, as the council counts them), away the difference (review fix: the per-party sum missed journeys the report does not list);
- its council's news, newest first, at most 60: battles it fought, notices, caught spies, spy reports, delivered messages, treaties, and what struck it.

Not included: stores, research, drills and decrees (council economics, not drawn), rivers (the terrain draws them; fog hides unknown ones), and what a ruin still holds.

**The route.** `GET /api/run/days/{day}/perspective/{civ}`, where `civ` is the civilization's number in the manifest. It is behind the token like every `/api` route and carries the history epoch. `RunService.perspective` is the one place a report is built from a day's world (a test counts it), and answers are cached per day and civilization. An unsaved day or unknown civilization answers 404; a day not yet walked, 409.

**The static export.** `run_export --perspectives` also writes `days/dNNNNNN.perspective.K.json` for each day and civilization, the same bytes the server serves, and says so in the manifest. Without the flag the export is unchanged, and `EXPORT_VERSION` stays 4.

**The page.**
- **See as.** A small bar under the top bar holds the perspective chip and the picker: World (observer) or one civilization, on the day shown or at its last council. `?civ=K` opens in a perspective. Switching keeps the camera.
- **Chip.** "PERSPECTIVE: Elmford realm · what its council knows as of day 180", or "… as of its council on day 30 (shown day 31)", or, in a static export without that council day, "… what its council knows as of day 35 (its council day 30 is not in this export)" (the shown day stands in; review fix).
- **Fog** covers every tile it has never seen, opaque, also on the minimap; each fog hex is drawn 3% larger so no antialiased seam shows the ground (review fix: it was 0.94). Tiles it has not seen lately get a grey veil, from 0.12 a day ago to 0.45 a year or more ago (0.3 when undated). The fog lies above the ground and trees and below its own people and the overlays.
- **Glyphs** mark the foreign settlements, ruins, sites, roads, bridges, toll posts and garrisons its report has, each labelled with the day last seen. The world's site markers and minimap dots are hidden.
- **Its people** are drawn in its own settlements as its council counts them: the notable people its report names, with their duty and skills, and the rest as "counted, not named" rows (placeholders for age, sex and duty, said so in the inspector). The settlement badge reads "as its council counts them".
- **Chronicle.** The world chronicle is hidden in a perspective, since an event can carry a fact the civilization does not know. A **Council news** panel lists the report's own records in plain sentences, with Go for those that name a tile.
- Own parties on the road are drawn and can be picked and followed as before; other peoples' parties are not. A world answer still in flight when the day is seen as a civilization (its parties, its chronicle) is dropped, not shown (review fix).

**Access boundary tests** (`tests/observer/test_perspective.py`):
- the serializer's fence (one parameter, no state, reader or store);
- the record says what the report says, on a recorded war (4 civilizations; the two at war each know one foreign settlement, the others none), and a rules-1 report serializes;
- every served day and civilization (the war run and the format-one fixture) equals the serialized report of that day's replay;
- **noninterference:** what is served equals what would be served from `hide_unseen(state, civilization)`, a world in which everything that civilization cannot know is different; each hidden fact of the engine's own leak tests (an unlearned capture, an unseen siege, an unheard treaty ending, an unseen ruin) leaves the bytes unchanged, and a fact of its own changes them;
- 404, 409, 422 and 401 answers; serving perspectives writes nothing to the run;
- an export with perspectives has the server's bytes; one without is unchanged.

**Browser tests** (`observer/tests/perspective.test.mjs`): the frame from a perspective (named people first, the rest counted; overlays in civilization numbers; unknown peoples' borders left out), council days, news sentences, an export without perspectives; and, on a served war run, the page seen as the first civilization and back: chip, kept camera, fog tiles = all tiles − known tiles, drawn residents equal the record's, only its own settlements, at least one foreign settlement, sites and chronicle hidden, the inspector saying "council report", the next day, "at its last council", back to the world with everyone drawn again, no page errors, and the run's files unchanged.

**Timings.** Measured here through FastAPI's test client, on the last day of each run, per civilization.

| | Baseline year (seed 21, 48×48, 141 people, day 365) | 100,000 people (48×48, rules 2, day 10) |
|---|---|---|
| First ask | 56–89 ms | 0.60–0.75 s |
| Cached | about 3 ms | about 3 ms |
| Size | 12 KB | 12 KB |

**Defaults taken** (the user can change any of these):
1. Rebuilt from the shown day's saved state, with an "at its last council" choice; when a static export lacks the council day, the shown day stands in and the chip says so.
2. Civilizations addressed by their number in the manifest.
3. The export carries perspectives only when asked (`--perspectives`).
4. Its people drawn in its own settlements only: the notable named, the rest counted.
5. Opaque fog; known tiles veiled by how long ago they were seen.
6. The world chronicle hidden in a perspective; Council news instead.
7. Foreign settlements from its contacts only; spy estimates in news, not on the map.
8. Council economics (stores, research, decrees) not shown.
9. 64 cached answers.
10. A settlement with no listed rank shown as a village.
11. A site never visited shown "as made".

**Known**
- A small people's report names everyone (up to 40), so in small runs nobody is merely counted.
- The report cannot tell captives held at home from those marching with its war parties, so with captives at home `counts.at_home + away` exceeds `living` by that many.
- The first ask at 100,000 people takes most of a second (building the report); later asks are cached.
- ~~The top bar is one row: at 1440 px wide the day stepper and runner controls run off its right edge.~~ Fixed in O6: they live in the run bar, with the perspective controls.

## O6 as built: the Phase 4 exit

Planned with Fable 5.1 after O5 and its review. The user asked "What is next activity?" and chose "Start O6". Built in four commits: C0 70e5d36, C3 41d865a, C1–C2 e216c56 and C4 2a702ab, plus these docs with a fix the measurements found (the crowd sheet baked once). The engine is unchanged: golden hashes and parity hold.

**Acceptance** (C0).
- `docs/observer-acceptance.md` maps every promise of this plan and the roadmap's exit gate to the tests that prove it: the exit gate, the creator covenant, R1–R10, journal safety, the access boundary, "what is shown is what was recorded", the civilization perspective, and O6's own. `tests/acceptance/test_phase_four_checklist.py` checks that every test it names exists, so the list cannot quietly go stale.
- `tests/acceptance/test_phase_four_exit.py` takes one fresh run end to end, in process: `init` (seed 21, 24×24, the current rules) and `run --days 12`; export with perspectives; serve it. Every `/api` route answers 401 without the token; every answer equals the export's bytes; each day equals its replay, record and hash; each perspective equals the serialized council report of the replay and of `hide_unseen`; changes, routes and chronicle places lie on the map; control answers 409 with no runner; and the run's files and times are unchanged, with `verify` passing.

**The run bar** (C1). The day stepper and runner chip ran off the one-row top bar's right edge at 1440 px. They moved into a run bar under it, with the perspective picker; the bar wraps, and the inspector, chronicle, council news and tour banner start below it however many rows it has. `layout.test.mjs` checks every run control is on screen and uncut at 1440 × 900 and 1280 × 720.

**The tour measures a run** (C2).
- The tour never resumes a runner the observer started: it measures the display with the runner kept paused, and its banner says so (a test checks the runner stays paused and saves no day).
- `measurement()` records what was measured: the run (export or live, runner or not, the day of how many, the civilization and as-of), the day loads, from the ask to the day shown (count, average, maximum, last; review fix: they stopped before the population was built) and the last perspective switch. The Measurements panel shows a run line.
- `measure.mjs` gains `--run`, `--day` and `--civ`, and times a step to the next day, a jump to the first day and the perspective switch.

**Server timings** (C3). `tests/observer/route_timings.py RUN_DIR | --baseline | --people N` prints each route's first and cached answer through FastAPI's test client, with the walk per day. It strips provider keys from its environment, so it never calls a model.

**Art packs** (C4).
- **The contract, key by key.** `observer/art/manifest.json` lists every key the renderer can ask the atlas for (2,039: 1,073 sprites, 900 citizen masks, 66 shadows), each with its category, the painted size and anchor in art pixels, how the atlas keeps it, and a static asset's footprint and height. It is generated from the painters (`node tests/art-manifest.mjs`); a test keeps it equal to a fresh one.
- **Loading.** `?art=NAME` loads `art/packs/NAME/pack.json` and its PNGs into the atlas before the painters run. The atlas keeps the first sprite under a key, so painted art fills every key the pack lacks and no drawing code changes. All images are fetched and checked before any is added, so a pack that fails part way adds nothing. The page and the checker share the rules (`src/render/art/pack-rules.js`) and refuse a pack that breaks any of them, whole (review fix): a file outside the pack's folder; a sprite more than twice its painted size each way, or larger than an atlas page; a mask or shadow without its sprite; a mask that, placed by its anchor, lies outside its frame; an anchor outside its image. Pack sprites of static assets are checked against their footprint as painted ones are; a citizen frame from a pack takes only the pack's own mask. The crowd sheet holds every still at twice its painted size (1,010 of its 1,024 px) and, should a future asset set not fit, is baked again at 2,048.
- **Saying so.** The chip reads "ART PACK: sample · 3 of 1073 keys from PNG", adding "· N outside their footprint" when the contract fails. A pack that cannot be loaded leaves every sprite painted, and the chip says "PROTOTYPE ARTWORK · art pack "NAME" not loaded" with the reason. `__observer.artPack()` gives the details.
- **Tools.** `tests/art-pack-check.mjs` checks a pack in Node (PNG files and sizes, anchors, keys the manifest knows, masks and shadows with their sprites, files inside the pack) and prints its coverage; `tests/art-export.mjs` writes painted sprites as a pack, a starting point for an artist.
- **The sample pack** (`art/packs/sample/`): a well and its shadow, an oak, and a citizen's first idle frame with its mask, exported from the painters; no external assets.

**Defaults taken** (the user can change any of these):
1. The checklist is its own document, kept honest by a test.
2. The exit test runs in process on a 24×24, 12-day run; the subprocess determinism tests stay where they were.
3. The tour never resumes a runner.
4. A pack is one PNG per key plus `pack.json`, anchors required, files inside the pack's folder; a sprite may be up to twice its painted size each way (any other size draws a warning); footprints stay fixed by the registry.
5. Building colours stay painted in; buildings have no mask.
6. The sample pack is a building, a tree and a citizen frame with its mask.
7. The manifest is committed and checked fresh.
8. Phase 4 is declared complete with the "Your PC" columns blank, to be filled from the user's numbers.

**Codex review of dcbdf86 (O6): four P2 findings, all real, fixed in the commit that follows it.**
- An art pack could load files from a sibling pack (`../sample/x.png`), which the checker refused but the page did not. Files are now resolved against the pack's folder and a pack reaching outside it is refused whole.
- A pack frame of any size was accepted, and a 2048-pixel citizen frame made the crowd sheet overflow so the page did not start. Sprites may now be at most twice their painted size, by rules the page and the checker share, and the crowd sheet grows to 2,048 rather than fail.
- Day loads were timed before the day's people were built and drawn; they are now timed to the day shown.
- `route_timings.py` failed on a run of one day; it now leaves out the "changes" row there.

While validating, three tests failed once each. One found a small real bug: the art proof's indoor highlight pulses its building's tint, and at the bottom of each pulse the tint rounded to white, so for about a tenth of the time the highlight blinked out (and a test reading it then saw no highlight); the pulse now keeps a floor. The other two came from timing in the tests themselves. The camera-hold tour test's wheel, drag and click spilled past a 1-second first stop under software GL; it now holds each stop for 3 seconds. The reader's "writes nothing" test could see SQLite fold away the write-ahead files of the store that recorded its run, if the collector freed that store mid-test; it now collects before taking its snapshot, as the exit test does.

### Performance report

Measured here: a Linux container, headless Chromium with software GL (so frame times say nothing about a real GPU; JS times, memory and server times do). Your PC's columns are for the commands below.

**A recorded year in the page.** The baseline year (seed 21, 48 × 48, rules 3, 365 days, 141 people), exported with perspectives and opened at day 180 at 1600 × 900: `node tests/measure.mjs OUT --run=data/runs/o6-baseline --day=180 [--civ=0]`. Each figure is the range over the four bands.

| Measure | Target | Here, world | Here, civilization 0 | Your PC, world | Your PC, civilization 0 |
|---|---|---|---|---|---|
| A step to the next day (asking to shown) | ≤ 250 ms from an export, ≤ 500 ms live | 16–28 ms | 16–33 ms | | |
| A jump to day 0 | — | 15–24 ms | 14–23 ms | | |
| Switching to the perspective | ≤ 1 s first, ≤ 250 ms cached | — | 143 ms | | |
| JS update (our own frame work, clock paused) | ≤ 8 ms | 0.05–0.11 ms | 0.05–0.19 ms | | |
| JS heap | ≤ 150 MB | 20–22 MB | 33–42 MB | | |
| Art atlas | ≤ 60 MB | 50.3 MB | 50.3 MB | | |
| Ready | ≤ 10 s | 38–41 s (painting the art in software) | 38–41 s | | |
| Frame interval | ≤ 20 ms p95 | not meaningful here (software GL) | | | |

Measuring this found a real cost, fixed in these docs' commit: every day shown built a new crowd layer, and with it re-baked the crowd's texture sheet. The re-bake drew from the atlas pages and read the sheet's pixels back for picking, which waits for the GPU. It cost about 0.2 s a day with nothing drawing, 0.6–2.9 s under this container's slow frames, and left the old sheet's texture behind each time. The sheet now holds every citizen design and town piece the atlas has. It is baked once per page (1024 px instead of 512) and kept on the CPU for reading. `tour.test.mjs` checks that a run's days share one sheet.

**The server** (`route_timings.py` on the same year, through FastAPI's test client; ms, first ask then the median of cached repeats):

| Measure | Target | Here, first | Here, cached | Your PC, first | Your PC, cached |
|---|---|---|---|---|---|
| Walk, per day (once, on start) | ≤ 0.5 s at 100K | 6.0 | | | |
| Status, manifest, id table, a day's record and people, changes | ≤ 10 ms cached | 2.6–3.6 | 2.7–3.3 | | |
| Routes | ≤ 10 ms cached | 82.8 | 3.4 | | |
| Chronicle | ≤ 10 ms cached | 37.2 | 3.3 | | |
| Perspective (civilization 0) | ≤ 1 s first at 100K | 75.2 | 3.2 | | |
| Terrain manifest | ≤ 10 ms cached | 1,224 | 3.2 | | |
| A day dropped from the cache, asked again | — | 12.4 | | | |

At 100,000 people (from the O3 and O5 write-ups, measured here) the walk takes about 0.3 s a day and a first perspective 0.60–0.75 s; cached answers stay about 3 ms.

**100,000 synthetic people** (`?people=100000&measure=auto`): see "S7 as built" in the Phase 5 plan. Your PC's second run met every target (4.3 ms frames in every band, JS update at most 1.3 ms average, heap 44–120 MB, first frame 3.8 s). A third run after O6 checks that nothing has regressed.

**Measure on your PC** (Windows PowerShell, from the repository root, with the venv as before):

```powershell
# 1. The synthetic 100,000 people again: open the page and let the tour run, hands off.
node observer\tests\serve.mjs 8765
#    then open http://127.0.0.1:8765/?people=100000&measure=auto and copy the table.

# 2. A baseline year served live, as the world and as civilization 0.
& .venv\Scripts\sovereign-world.exe init work\o6 --seed 21 --width 48 --height 48
& .venv\Scripts\sovereign-world.exe run work\o6 --days 365
& .venv\Scripts\sovereign-world.exe observe work\o6
#    It prints "Open http://127.0.0.1:8766/?run=live#token=..."; open it twice, inserting before #token=:
#      http://127.0.0.1:8766/?run=live&measure=auto#token=...
#      http://127.0.0.1:8766/?run=live&civ=0&measure=auto#token=...
#    and copy each table from the Measurements panel.

# 3. The server's own times on that run.
& .venv\Scripts\python.exe tests\observer\route_timings.py work\o6
```

## Phase 4 complete

The observer is done to the plan: a read-only window on the world that zooms from the whole map into animated settlements, reads a recorded or live run without ever writing to it, runs the world under the creator's pause and lookahead, replays any recorded day exactly, shows the world as one civilization knows it, and can take an artist's sprites key by key.

| Slice | What it gave | Commits |
|---|---|---|
| O1a | Close-zoom art proof: asset contract, painters, atlas, depth order with pinned tests | see "O1a as built" |
| O1 | Terrain export, streamed hex world in three bands, minimap, sample village, measurements | see "O1 as built" |
| G1–G5 | World geography: rivers on borders, hills and snow, crossings, bridges, the 25 km world rule | see "Geography G1–G2", "G4 and G5" |
| O1b | The observer at 25 km tiles: interiors, ground patches, local origins, the courier's walk | see "O1b as built" |
| O2 | Read-only journal tail and reader, the day projection, run export, the page in run mode | 2ec7624 … 612516f |
| O3 | `observe`: a run served live behind a token, the chronicle placed from history, travellers | 9a9bb6b … 6f3c61b, review 2a4f646 |
| O4 | The runner started and held by the observer, the replay timeline, each day's hash, following a party | 484b3cb … 54edb84, review d3d5251 |
| O5 | The world as one civilization's council knows it, with noninterference on what is served | e9bd54f … 1359720, review 817193e |
| O6 | Acceptance checklist and exit test, run bar, tour on a run, server timings, art packs | 70e5d36, 41d865a, e216c56, 2a702ab, dcbdf86, and its review fix |

Gate: all observer operations leave the world hash unchanged (the exit test, and the attached-observer and page-driven determinism tests), and the observer's promises each have a test (`docs/observer-acceptance.md`).

### Known limits carried forward
- Explorers are counted at home in a settlement's residents (the projection follows the engine's residents).
- The id table is about 3 MB at 100,000 people, sent whole on first load.
- ~~`WorldStore` extends a journal cut back under it~~ and ~~a second `sovereign-world run` alongside the observer's runner is not prevented~~: both closed by the sealed trial's slice J (a cut-back journal is refused, and a run has one writer, held by a lock).
- Pausing takes effect between days, so the runner can end one day beyond the lookahead.
- With captives held at home, a perspective's `at_home + away` exceeds `living` by that many.
- Building colours are painted in, not a tinted mask, so a pack's buildings keep whatever colours they are drawn in.
- Format-1 journals' gzip records embed the time they were saved, so two format-1 runs of the same world differ in those bytes (format 2 sets the time to zero).
