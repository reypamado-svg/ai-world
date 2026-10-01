# AI World observer (browser)

Phase 4 observer client. **Current state: O1a close-zoom art proof.**

Everything on the proof page is clearly labelled:

- **PROTOTYPE ARTWORK.** All sprites are original, procedurally painted in
  the browser from code (no external or licensed assets). They sit behind an
  asset contract (`src/render/art/registry.js`) so a licensed pack can replace
  them one asset at a time.
- **SAMPLE DATA.** The village, buildings, citizens and their routines are
  invented for visual review. They are not simulation output and prove
  nothing about what the engine records.
- **Visual approximation.** Street-level movement is a deterministic
  presentation of sample routines (`src/sim/paths.js`). It is a pure function
  of person and display time; the camera, zoom, selection and following
  never change it.

## Run it

No build step. Serve the `observer/` folder over HTTP (ES modules do not load
from `file://`):

```sh
node observer/tests/serve.mjs 8765        # then open http://127.0.0.1:8765/proof.html
# or
python3 -m http.server 8765 -d observer
```

- Drag to pan, mouse wheel to zoom, click a person or building to inspect.
- Click again on the same spot to cycle through people standing together.
- **Follow person** in the inspector keeps the camera on them, including
  while they are indoors (their building is highlighted instead).
- **Footprints** shows the ground boxes used for depth sorting.
- **Depth debug** draws every object as a flat colour (used by the tests).
- `proof.html?scene=depth` is the pinned depth-ordering test scene.

## Tests

```sh
cd observer
npm install            # playwright 1.56.1 (uses the installed Chromium) and pixi.js for vendoring
node --test tests/*.test.mjs   # depth-ordering visual tests (R3)
node tests/capture.mjs captures --depth --clip   # review screenshots, depth sheet, MP4 clip
```

## Layout

| Path                         | Purpose                                                                     |
| ---------------------------- | --------------------------------------------------------------------------- |
| `vendor/pixi/`               | PixiJS 8.21.0 (MIT), vendored unmodified from the npm package               |
| `src/world/coords.js`        | One projection for every zoom level (presentation metres, not engine units) |
| `src/render/art/registry.js` | The asset contract and the catalogue of painted assets                      |
| `src/render/art/paint/`      | Procedural painters: materials, buildings, nature, people, vehicles, ground |
| `src/render/art/atlas.js`    | Texture atlas pages (batched drawing), picking alpha, silhouettes           |
| `src/render/depth.js`        | Footprint-based depth ordering (topological sort over screen overlaps)      |
| `src/sim/paths.js`           | Presentation paths and schedules (visual approximation only)                |
| `src/data/naming.js`         | Observer-assigned display labels derived from IDs                           |
| `src/proof/`                 | The art-proof page: sample village, pinned depth scene, renderer            |
