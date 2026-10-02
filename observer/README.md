# AI World observer (browser)

Phase 4 observer client. **Current state: O1b visual prototype at 25 km
tiles** (plus the O1a close-zoom art proof).

Everything on screen says where it comes from:

- **ENGINE TERRAIN · DAY 0.** Terrain, rivers, lakes and the four capitals
  are the engine's own world generation (generator version 2) for seed 21
  (100 × 100 tiles), exported by `python -m sovereign_world.observer.terrain_export`.
  No day has been simulated. Rivers run along tile borders, as the engine
  records them. 25 km between tile centres is a world rule
  (`travel.TILE_SPACING_M`): a day's walk on open ground. The observer reads
  it, and the travel costs in days, from the export's `engine.travel` table.
- **SAMPLE VILLAGE.** The village, its buildings, its ring of fields (about
  800 m across), citizens and routines are invented for visual review and
  placed at the centre of the first civilization's capital tile. They prove nothing about what the simulation records or supports.
- **PROTOTYPE ARTWORK.** All sprites are original and procedurally painted in
  the browser from code. They sit behind an asset contract
  (`src/render/art/registry.js`) so a licensed pack can replace them one at a
  time.
- **Visual approximation.** Street-level movement is a deterministic
  presentation of sample routines (`src/sim/paths.js`): a pure function of
  person and display time. Camera, zoom, selection and following never change
  it.

## Run it

No build step. Serve the `observer/` folder over HTTP (ES modules do not load
from `file://`). From the repository root:

```sh
node observer/tests/serve.mjs 8765
# or
python -m http.server 8765 -d observer
```

Then open:

- `http://127.0.0.1:8765/index.html` — the observer prototype (world atlas →
  regional → local → settlement in one camera).
  - `?citizens=2000` (up to 5000) adds SAMPLE residents to load the renderer.
  - `?quality=high|medium|low|auto` (default auto).
- `http://127.0.0.1:8765/proof.html` — the O1a close-zoom art proof.
- `http://127.0.0.1:8765/proof.html?scene=depth` — the pinned depth test scene.

Controls: drag to pan, mouse wheel to zoom (around the cursor), click a
person or building to inspect, click again on the same spot to cycle through
people standing together. **Follow person** keeps the camera on them, also
across tiles and while they are indoors. ⌖ goes to the village, ⌂ returns to
the whole world, the minimap moves the camera. **Measurements** shows and
copies frame times, counts, texture memory and loaded chunks for your machine.

## Zoom bands

A tile is 25 km across, so one camera spans about five decades of zoom.

| Band       | Zoom          | A tile on screen       | What is drawn                                                                                      |
| ---------- | ------------- | ---------------------- | -------------------------------------------------------------------------------------------------- |
| atlas      | below 4.5e-4  | under 256 px           | Chunk textures (32-256 px per tile), rivers by flow, map symbols, capital flags, village badge     |
| regional   | 4.5e-4 – 0.02 | 256 px – 11,000 px     | One texture per tile (512 / 1,024 px), then ground patches from about 1,700 px per tile            |
| local      | 0.02 – 0.5    | 11,000 px – 280,000 px | Ground patches down to 12.5 m, trees and rocks from 0.2, village buildings, people as dots, fields |
| settlement | from 0.5      |                        | Full sprites and animated citizens; patches of 12.5 / 6.25 m around the village ground             |

Rules that keep this working:

- **Local origins.** Canvas paths and Pixi graphics are float32, and the
  world is about 8e7 screen pixels wide at zoom 1. Every texture paints
  relative to its own anchor and every graphic relative to its container;
  only float64 sprite and container positions carry world coordinates.
- **Interior field.** `src/world/interior.js` paints the inside of a tile
  from that tile's engine values only (moisture, timber, temperature,
  elevation, stone), blends land tiles across their borders, and gives
  water a wandering shore. All three texture levels sample it, so they agree.
- **Courier travel.** The SAMPLE courier's trip follows the engine's travel
  costs (`src/sim/travel-plan.js`): five hours of walking a day, then camp;
  fords waded at the border; never across deep rivers or water.

## Tests

```sh
cd observer
npm install                                            # playwright 1.56.1, pixi.js (for vendoring)
node --test --test-concurrency=1 tests/*.test.mjs      # browser tests, one file at a time
node tests/measure.mjs captures                        # rendering measurements (400 / 2,000 / 5,000)
node tests/capture-observer.mjs captures --clip        # review stills and zoom-through clip
node tests/capture.mjs captures --depth --clip         # art-proof stills, depth sheet, clip
node tests/capture-geography.mjs captures              # terrain stills: world, range, river, desert, lake
```

| Test file                 | What it proves                                                                                                                                                    |
| ------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `depth.test.mjs`          | Ten pinned overlap cases by draw order and by pixel; indoor citizens never drawn; negative control                                                                |
| `order-snapshot.test.mjs` | Refactors do not change the art proof's draw order                                                                                                                |
| `hex.test.mjs`            | Hex layout: round trips, 25 km spacing, horizontal rows, chunk partition, tile sizes on screen                                                                    |
| `travel-plan.test.mjs`    | Courier days: five hours walking then camp, terrain costs, ford wading time, deep rivers and water refused, continuity                                            |
| `rivers.test.mjs`         | Channel widths by depth class, the same wandering curve from either tile                                                                                          |
| `interior.test.mjs`       | Ground field: determinism, border blending, one wandering shoreline with beach and shallows, snow line, fields, no aliasing                                       |
| `patches.test.mjs`        | Ground patch sizes and the size chosen at each zoom                                                                                                               |
| `precision.test.mjs`      | Local-origin rule: a far tile paints identically from any nearby anchor; village graphics stay local with the courier 50 km out                                   |
| `streaming.test.mjs`      | R4: 60 rapid jumps over a synthetic 4096 × 4096 world with latency stay within request and cache budgets, stale requests are dropped, textures return to baseline |
| `bands.test.mjs`          | Zoom continuity across four bands, selection, follow days into the courier's journey, patches within budget, positions independent of the camera, pause           |
| `counts.test.mjs`         | R9 count identities at 400 and 2,000 citizens in every band                                                                                                       |

## Layout

| Path                                                           | Purpose                                                                                  |
| -------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| `vendor/pixi/`                                                 | PixiJS 8.21.0 (MIT), vendored unmodified from the npm package                            |
| `data/terrain/`                                                | Committed engine export for seed 21 (chunks, overview, rivers and lakes, day-0 capitals) |
| `src/main.js`, `index.html`                                    | The observer prototype                                                                   |
| `src/world/`                                                   | Projection, hex layout, chunk loader and bounded LRU caches                              |
| `src/data/`                                                    | Terrain source (and the TEST ONLY synthetic source), naming, SAMPLE village and citizens |
| `src/render/terrain-layer.js`, `hex-detail.js`                 | Streamed terrain: chunk textures, then per-tile detail                                   |
| `src/render/patch-layer.js`, `decor.js`                        | Ground patches (3,200 m to 6.25 m) and 50 m cells of trees and rocks                     |
| `src/world/interior.js`, `rivers.js`                           | Ground colour field inside tiles; river widths and wandering curves                      |
| `src/sim/travel-plan.js`                                       | The courier's multi-day walk from the engine's travel costs                              |
| `src/render/village-layer.js`, `scene-renderer.js`, `depth.js` | Village drawing, bands, picking, depth order                                             |
| `src/render/art/`                                              | Asset contract, painters, atlas                                                          |
| `src/ui/`                                                      | Inspector, minimap, quality, frame statistics                                            |
| `src/proof/`                                                   | The O1a art proof page                                                                   |
