# AI World observer (browser)

Phase 4 observer client. **Current state: O1b visual prototype at 25 km
tiles** (plus the O1a close-zoom art proof).

Everything on screen says where it comes from:

- **ENGINE TERRAIN · DAY 0.** Terrain, rivers, lakes, land cover, the
  capitals (two to four civilizations; seed 21 has four, 26 tiles apart) and
  the ore deposits, quarries, ancient ruins and troves are the engine's own
  world generation (generator version 3) for seed 21 (100 × 100 tiles),
  exported by `python -m sovereign_world.observer.terrain_export`. Land cover
  gives each tile's share of open ground, wood, scrub, wetland, rock, sand and
  snowfield; where the groves, ponds and outcrops lie inside the tile is a
  presentation, their area follows the shares.
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

- `http://127.0.0.1:8765/` (or `/index.html`) — the observer prototype (world atlas →
  regional → local → settlement in one camera).
  - `?citizens=2000` (up to 5000) adds SAMPLE residents to load the renderer.
  - `?people=100000` (up to 200,000) loads a SYNTHETIC population shared equally among
    the capitals, each living in wards of houses around its capital (Phase 5 S7). Add
    `&citizens=0` to leave out the SAMPLE village's clones.
  - `?people=100000&measure=auto` measures your machine: after loading it spends 10 s
    in each band (settlement, local, regional, atlas; `&tourSeconds=N` to change) and
    then shows a table of frame times, update time, visible people and memory, with
    **Copy** for the full JSON. Please send the copied text back. Keep your hands off the
    mouse until the table appears: the camera is held during each stop, and a stop that was
    disturbed is marked `touched`. The JSON also has the loading times (`load`) and, per stop,
    the band measured, cache evictions and the heap at its start and end.
  - `?quality=high|medium|low|auto` (default auto).
- `http://127.0.0.1:8765/?run=NAME&day=N` — a recorded run (O2), exported first with

  ```sh
  .venv/bin/sovereign-world init runs/baseline-21 --seed 21 --width 48 --height 48   # rules 3: councils design their towns
  .venv/bin/sovereign-world run runs/baseline-21 --days 365
  PYTHONPATH=src .venv/bin/python -m sovereign_world.observer.run_export runs/baseline-21 \
      --out observer/data/runs/baseline-21          # [--stride N] [--days 0,30,60] [--replace]
  ```

  The export reads the run without writing to it (the journal is followed by byte offset;
  SQLite is opened read-only, through the writer's own log while a run is still being
  written, else as an unchanging file) and holds the run's own terrain, every person
  id, and for each day the settlements, houses by grade, house work, travellers, borders
  and every living person (about 310 KB a day at 100,000 people). Exports live in
  `observer/data/runs/`, which git ignores. In the page, **‹ ›** step through the exported
  days; the camera stays put. What is recorded and what is presentation is labelled:
  who lives where, their houses, duties, age and health are the engine's; where in a
  settlement they stand and walk is a visual approximation; names are observer-assigned.

  In a rules-3 run each settlement also has its council's town plan and its wall ring
  (export version 2). A designed town fills the ring with wards first, puts the keep (with
  a hall), market, shrine and craft quarter (with a workshop) where the plan says, and
  draws each built section of wall in its grade with gatehouses and towers; sections not
  yet built are a dashed line, and battered ones are tinted. The badge reads, for example,
  "ringed town, designed by the council · walls 6 of 10 sections". The committed
  `tests/fixtures/run-town` shows one: `?run=tests/fixtures/run-town&day=18`.
  Export version 3 adds a town's defences: towers stand on the sections the engine
  placed them on (beside the gate on a gate section), a fortified gate is drawn as a
  gatehouse, a ditch is a dark line half a block outside the wall (a moat, blue), stakes
  are short marks between the two, and a citadel is a wall round the keep's block with
  its gate towards the store. A town with a standing defence order adds "· defence set"
  to its badge. The fixture's second capital has all of these from day 0.
  Houses are drawn by recorded grade (hut, house, stone house) with building sites for
  houses under construction; a house holding more than five people carries an amber
  badge, and the inspector says "12 people, room for 5 (crowded)". People counted at no
  settlement (on the road) are drawn as counted dots at their tiles.

### A run served live (`sovereign-world observe`, O3)

The observer can also follow a run while it is being recorded, with no export step. Install
the server's extra once, then point it at a run:

```sh
uv pip install --python .venv/bin/python -e ".[observer]"     # FastAPI and uvicorn
.venv/bin/sovereign-world observe runs/baseline-21              # [--host 127.0.0.1] [--port 8766]
```

It prints an address such as `http://127.0.0.1:8766/?run=live#token=…`; open it. The
token is made fresh each time the server starts (or taken from
`SOVEREIGN_WORLD_OBSERVER_TOKEN` in the server's own environment, in which case add
`#token=…` to the address yourself). The part after `#` is never sent to the server, and
the page reads the token from it once and then removes it from the address, so neither the
address bar, the history nor a restored session keeps it: it is held in the page's memory and
sent only in an `Authorization` header, and no file or log holds it. Reloading the page (F5)
therefore asks for the token again: open the printed address once more. Every `/api` address
needs the token; the page's own code does not. Recorded exports under
`data/runs/` and the browser tests are not served.

- The server only reads the run, exactly as the export does, and its answers are byte for
  byte the export's files. Another terminal can keep the run going (`sovereign-world run
runs/baseline-21 --days 30`): new days appear within about two seconds.
- **LIVE RUN · following** shows the newest day as it is saved; stepping back with **‹**
  pauses following, and **Follow latest** resumes it. If the run is cut back or replaced,
  the page starts again with its token; an answer from the run's other history is never
  shown.
- **Chronicle** lists the shown day's events in plain sentences, each with where the
  record puts it (the event's own tile, or the tile of the person, settlement, party or
  battle it names, that day or the day before; a civilization's events at its capital).
  **Go** moves the camera there, **Follow** follows the person named. Routine bookkeeping
  (food eaten, orders accepted, single tiles changing hands) is hidden unless **Routine**
  is ticked.
- Click a traveller dot on the map to see the parties on that tile (war parties,
  expeditions, settlers…), their people, and their route drawn on the map. **Follow
  party** keeps the camera on that party from day to day until its journey ends; a drag or
  any other move of the camera stops it.
- **Playing a recorded run** (live or an export): the display clock runs at the chosen speed,
  and each time it passes a whole day (14.4 minutes at 100×) the next recorded day is shown.
  At the last recorded day it waits. The slider skips to any recorded day. The day label's
  tooltip gives the state hash the run saved for that day; `sovereign-world replay RUN
--day N` prints the same hash.

### Running the world from the observer (`observe --run-days`, O4)

```sh
.venv/bin/sovereign-world observe runs/baseline-21 --run-days 30
```

starts the run as well, as its only writer, for up to 30 more days. The run goes on only
while the page plays, and only up to a few days ahead of the day shown (**Ahead**, 3 by
default, 1 to 30). The page opens paused, since playing may cost AI calls; ▶ starts it.
The **RUNNER** chip says whether it is running, paused, how far it has got and how many
days ahead of the page it is.

- Pausing takes effect between days: the day under way finishes, its councils included.
- What the run saves is exactly what `sovereign-world run` saves for the same days; pausing
  and the lookahead change nothing in it. A run started this way can be carried on later
  with `run` or another `observe --run-days`.
- Closing the observer stops the run after the day under way, with a checkpoint saved.
- If the run is cut back or replaced while the runner is attached, the observer stops the
  runner at once, without a checkpoint (its world belongs to the old history), and the page
  starts again with **RUNNER · stopped: the run's history changed**. Restart
  `observe --run-days` to run the new history. Cut a run only while it is paused: a day
  that finishes in the half second before the observer notices is still saved.
- One runner per run: do not run `sovereign-world run` on the same run at the same time.
- The run is started with the observer's environment (so AI keys set there are used) but
  without the observer's token; the observer tells it only "pause" and "resume".

- `http://127.0.0.1:8765/proof.html` — the O1a close-zoom art proof.
- `http://127.0.0.1:8765/proof.html?scene=depth` — the pinned depth test scene.

Controls: drag to pan, mouse wheel to zoom (around the cursor), click a
person or building to inspect, click again on the same spot to cycle through
people standing together. **Follow person** keeps the camera on them, also
across tiles and while they are indoors. ⌖ goes to the village, ⌂ returns to
the whole world, the minimap moves the camera. **Measurements** shows and
copies frame times, counts, texture memory and loaded chunks for your machine,
with each GPU cache's use against its cap (caps grow with the screen, see below).
The speed buttons (1×, 10×, 25×, 50×, 100×) set how many seconds of sample time
pass per real second; at 100× a day passes in about 14 minutes.

## Zoom bands

A tile is 25 km across, so one camera spans about five decades of zoom.

| Band       | Zoom          | A tile on screen       | What is drawn                                                                                      |
| ---------- | ------------- | ---------------------- | -------------------------------------------------------------------------------------------------- |
| atlas      | below 4.5e-4  | under 256 px           | Chunk textures (32-256 px per tile), rivers by flow, map symbols, capital flags, village badge     |
| regional   | 4.5e-4 – 0.02 | 256 px – 11,000 px     | One texture per tile (512 / 1,024 px), then ground patches from about 1,700 px per tile            |
| local      | 0.02 – 0.5    | 11,000 px – 280,000 px | Ground patches down to 12.5 m, trees and rocks from 0.2, village buildings, people as dots, fields |
| settlement | from 0.5      |                        | Full sprites and animated citizens; patches of 12.5 / 6.25 m around the village ground             |

### A large population (`?people=N`)

The people are typed columns (`src/data/population.js`), about 36 bytes a
person with their plan; nothing per person is a JavaScript object. Where
someone stands is presentation: `src/world/settlement-plan.js` lays out
wards of 64 m blocks (16 houses each, five people to a house) around each
capital and moves its fields beyond them, and everyone follows one of nine
daily routines by duty, read from a table of phases per minute. The crowd
layer (`src/render/crowd-layer.js`) draws by band:

| Band               | People                                                                                               | Ward houses             |
| ------------------ | ---------------------------------------------------------------------------------------------------- | ----------------------- |
| settlement         | The quality's crowd budget nearest the centre as animated sprites, the rest as still particles       | Sprites, in depth order |
| local              | One dot per 8, 16 or 32 m cell, sized by headcount; a click lists the cell's people in the inspector | Static particles        |
| regional and atlas | A population badge per capital                                                                       | —                       |

Picking is exact for sprites and particles, repeated clicks cycle, and the
selected person is always drawn in full (or pinned), so anyone can be followed.

### Memory budgets

GPU caches scale with the screen's device pixels against 1600 × 900
(`src/ui/budgets.js`), up to 2.33 times: ground patches 96 MB × f (192 px
patches from 3.5 M device pixels), terrain 128 MB × f up to 192 MB. The art
atlas and village ground are a fixed 42 MB: art is kept at screen size except
buildings, and civilization colours are a tint on an accent mask.

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
node tests/measure.mjs captures --people=1000,100000   # the same with a synthetic population
node tests/capture-observer.mjs captures --clip        # review stills and zoom-through clip
node tests/capture.mjs captures --depth --clip         # art-proof stills, depth sheet, clip
node tests/capture-geography.mjs captures              # terrain stills: world, range, river, desert, lake
```

| Test file                  | What it proves                                                                                                                                                    |
| -------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `depth.test.mjs`           | Ten pinned overlap cases by draw order and by pixel; indoor citizens never drawn; negative control                                                                |
| `order-snapshot.test.mjs`  | Refactors do not change the art proof's draw order                                                                                                                |
| `hex.test.mjs`             | Hex layout: round trips, 25 km spacing, horizontal rows, chunk partition, tile sizes on screen                                                                    |
| `travel-plan.test.mjs`     | Courier days: five hours walking then camp, terrain costs, ford wading time, deep rivers and water refused, continuity                                            |
| `rivers.test.mjs`          | Channel widths by depth class, the same wandering curve from either tile                                                                                          |
| `interior.test.mjs`        | Ground field: determinism, border blending, one wandering shoreline with beach and shallows, snow line, fields, no aliasing, cover patches and ponds by share     |
| `patches.test.mjs`         | Ground patch sizes and the size chosen at each zoom                                                                                                               |
| `precision.test.mjs`       | Local-origin rule: a far tile paints identically from any nearby anchor; village graphics stay local with the courier 50 km out                                   |
| `streaming.test.mjs`       | R4: 60 rapid jumps over a synthetic 4096 × 4096 world with latency stay within request and cache budgets, stale requests are dropped, textures return to baseline |
| `bands.test.mjs`           | Zoom continuity across four bands, selection, follow days into the courier's journey, patches within budget, speeds 1×–100×, sites, camera independence, pause    |
| `counts.test.mjs`          | R9 count identities at 400 and 2,000 citizens in every band                                                                                                       |
| `atlas.test.mjs`           | At 5,000 citizens the atlas is at most three 2048 px pages and 60 MB with the ground, well filled, with every key the renderer asks for and a mask for each frame |
| `population.test.mjs`      | Synthetic people: shares, determinism, unique ids found again, plausible ages and households, at most 64 bytes a person                                           |
| `settlement-plan.test.mjs` | Houses for everyone, wards growing with the square root of the population, clear of core and fields, routine tables, walks on the streets, 100K placed in ms      |
| `crowd.test.mjs`           | At 5,000 and 50,000 people: counts in every band, the crowd budget, 200 people picked exactly, following, cell lists, heap and update time                        |
| `budgets.test.mjs`         | Caps from the screen size; the observer at 3840 × 2160 and 1280 × 720 stays within them and recomputes them on resize                                             |
| `tour.test.mjs`            | The measurement tour holds the camera against wheel, drag and buttons, labels each row with the band it measured, records evictions, heap and load times          |
| `server.test.mjs`          | Live server: wrong token, newest day, Follow latest, later day in 5 s, run unchanged, Go/Follow, travellers, cut-back restart                                     |
| `chronicle.test.mjs`       | Chronicle sentences with observer-assigned names, how each place was found, routine events hidden, the person an event names                                      |
| `server-source.test.mjs`   | Live source: answers from another history refused (also a record and people from two), the token dropped from the address                                         |
| `timeline.test.mjs`        | Replay timeline: days stepped with the time carried over, holding at the last day, the slider, each day's recorded hash                                           |

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
| `src/ui/`                                                      | Inspector, minimap, quality, frame statistics, screen-scaled budgets                     |
| `src/data/population.js`, `src/data/synthetic/people.js`       | People as typed columns; the synthetic population (the O2 reader will fill the same)     |
| `src/world/settlement-plan.js`, `src/render/crowd-layer.js`    | Wards, routines and positions; the crowd drawn by band                                   |
| `src/proof/`                                                   | The O1a art proof page                                                                   |
