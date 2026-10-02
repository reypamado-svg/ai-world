# AI World observer (browser)

Phase 4 observer client. **Current state: O1 visual prototype** (plus the
O1a close-zoom art proof).

Everything on screen says where it comes from:

- **ENGINE TERRAIN · DAY 0.** Terrain, rivers, lakes and the four capitals
  are the engine's own world generation (generator version 2) for seed 21
  (100 × 100 tiles), exported by `python -m sovereign_world.observer.terrain_export`.
  No day has been simulated. Rivers run along tile borders, as the engine
  records them. The 64 m per tile is a display scale, not engine data.
- **SAMPLE VILLAGE.** The village, its buildings, citizens and routines are
  invented for visual review and placed on the first civilization's capital
  tile. They prove nothing about what the simulation records or supports.
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
  regional → settlement in one camera).
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
| `hex.test.mjs`            | Hex layout: round trips, spacing, horizontal rows, chunk partition                                                                                                |
| `streaming.test.mjs`      | R4: 60 rapid jumps over a synthetic 4096 × 4096 world with latency stay within request and cache budgets, stale requests are dropped, textures return to baseline |
| `bands.test.mjs`          | Zoom continuity across bands, selection (also beside buildings and in crowds), follow across a chunk boundary, positions independent of the camera, pause         |
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
| `src/render/village-layer.js`, `scene-renderer.js`, `depth.js` | Village drawing, bands, picking, depth order                                             |
| `src/render/art/`                                              | Asset contract, painters, atlas                                                          |
| `src/ui/`                                                      | Inspector, minimap, quality, frame statistics                                            |
| `src/proof/`                                                   | The O1a art proof page                                                                   |
