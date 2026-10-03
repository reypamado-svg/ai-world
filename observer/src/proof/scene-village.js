// SAMPLE vignette for the close-zoom art proof.
//
// Everything here is invented sample data for a visual review: the layout,
// the buildings, the citizens, their routines. It is not simulation output
// and proves nothing about what the engine records. Positions are presentation
// metres on the ground plane.

import { rngFor } from '../sim/rng.js';
import { WalkGraph, makeSchedule, offsetPolyline } from '../sim/paths.js';
import { personLabel } from '../data/naming.js';
import { APPEARANCE_COUNT } from '../render/art/paint/people.js';

const BUILDINGS = [
  { id: 'b-house-a', asset: 'building.house.timber_a', x: -20, y: -7, label: 'Timber house' },
  { id: 'b-house-f', asset: 'building.house.timber_a', x: -32, y: -8, label: 'Timber house' },
  { id: 'b-storehouse', asset: 'building.storehouse', x: 2, y: -8, label: 'Storehouse' },
  { id: 'b-house-c', asset: 'building.house.stone_a', x: 16, y: -7.5, label: 'Stone house' },
  { id: 'b-hall', asset: 'building.hall', x: 6, y: -24, label: 'Community hall' },
  { id: 'b-house-d', asset: 'building.house.log', x: -16, y: -22, label: 'Log house' },
  { id: 'b-workshop', asset: 'building.workshop.main', x: -4, y: 9, label: 'Workshop' },
  {
    id: 'b-workshop-wing',
    asset: 'building.workshop.wing',
    x: -5.75,
    y: 13.5,
    label: 'Workshop (open wing)',
    partOf: 'b-workshop',
  },
  { id: 'b-house-b', asset: 'building.house.timber_b', x: 6, y: 10, label: 'Plank house' },
  { id: 'b-house-e', asset: 'building.house.stone_b', x: 22, y: 9, label: 'Stone-and-timber house' },
  { id: 'b-house-g', asset: 'building.house.log', x: -34, y: 9, label: 'Log house' },
  { id: 'b-construction', asset: 'building.construction', x: 34, y: -8, label: 'House under construction' },
  { id: 'b-well', asset: 'building.well', x: -0.5, y: -14.6, label: 'Well' },
];

const PROPS = [
  { id: 'p-barrels', asset: 'prop.barrels', x: 6.8, y: -3.6 },
  { id: 'p-crates', asset: 'prop.crates', x: -2.6, y: -3.7 },
  { id: 'p-logpile', asset: 'prop.logpile', x: -10.5, y: 8.5 },
  { id: 'p-planks', asset: 'prop.planks', x: 40.5, y: -8 },
  { id: 'p-stones', asset: 'prop.stones', x: 29, y: -8.5 },
  { id: 'p-sacks', asset: 'prop.sacks', x: 24.2, y: 13.6 },
  { id: 'p-hay-1', asset: 'prop.haystack', x: 43.5, y: 18 },
  { id: 'p-hay-2', asset: 'prop.haystack', x: 43.8, y: 22.5 },
  { id: 'p-anvil', asset: 'prop.anvil', x: -2.6, y: 14.0 },
  { id: 'p-barrels-2', asset: 'prop.barrels', x: 19.6, y: -3.8 },
];

const FIELD = { x0: 16.2, y0: 15.6, x1: 39.8, y1: 29.6 };

export const CIV = { village: 0, envoy: 1, caravan: 3 };

function buildGraph(doors) {
  const g = new WalkGraph();
  const streets = [
    {
      pts: [
        [-56, 0],
        [-38, 0],
        [-32, 0],
        [-29.5, 0],
        [-20, 0],
        [-10.5, 0],
        [-6, 0],
        [1.5, 0],
        [2, 0],
        [10.4, 0],
        [12, 0],
        [16, 0],
        [27, 0],
        [34, 0],
        [40.5, 0],
        [56, 0],
      ],
      width: 5,
      ruts: true,
    },
    {
      pts: [
        [-6, 0],
        [-6, -18.6],
        [-6, -21.925],
        [-6, -36],
      ],
      width: 3.2,
    },
    {
      pts: [
        [-6, -18.6],
        [6, -18.6],
        [10.4, -18.6],
      ],
      width: 2.6,
    },
    {
      pts: [
        [10.4, -18.6],
        [10.4, 0],
      ],
      width: 2.8,
    },
    {
      pts: [
        [12, 0],
        [12, 10.1],
        [12, 32],
      ],
      width: 3.4,
      ruts: true,
    },
    {
      pts: [
        [27, 0],
        [27, 9.0],
        [27, 13.6],
        [27, 15.3],
        [27, 16.3],
      ],
      width: 2.2,
    },
    {
      pts: [
        [17, 16.3],
        [27, 16.3],
        [39, 16.3],
      ],
      width: 1.2,
      field: true,
    },
    {
      pts: [
        [-38, 0],
        [-38, 14],
      ],
      width: 2.0,
    },
  ];
  for (const s of streets) g.addPolyline(s.pts);
  const stubs = [];
  for (const d of doors) {
    const pts = [[d.x, d.y], ...(d.via ?? [])];
    g.addPolyline(pts);
    stubs.push(pts);
  }
  return { graph: g, streets, stubs };
}

/** Door points must match the painted assets; listed here for the walk graph. */
const DOORS = [
  { building: 'b-house-a', x: -20, y: -4.1, via: [[-20, 0]] },
  { building: 'b-house-f', x: -32, y: -5.1, via: [[-32, 0]] },
  { building: 'b-storehouse', x: 2, y: -4.1, via: [[2, 0]] },
  { building: 'b-house-c', x: 16, y: -4.6, via: [[16, 0]] },
  { building: 'b-hall', x: 6, y: -19.6, via: [[6, -18.6]] },
  { building: 'b-house-d', x: -13.1, y: -21.925, via: [[-6, -21.925]] },
  {
    building: 'b-workshop',
    x: -2.3,
    y: 11.9,
    via: [
      [1.5, 11.9],
      [1.5, 0],
    ],
  },
  { building: 'b-house-b', x: 8.9, y: 10.1, via: [[12, 10.1]] },
  { building: 'b-house-e', x: 25.9, y: 9.0, via: [[27, 9.0]] },
  {
    building: 'b-house-g',
    x: -31.1,
    y: 9.075,
    via: [
      [-29.5, 9.075],
      [-29.5, 0],
    ],
  },
  { building: 'b-construction', x: 34, y: -5.1, via: [[34, 0]] },
  { building: 'p-logpile', x: -10.5, y: 7.4, via: [[-10.5, 0]] },
  { building: 'p-planks', x: 40.5, y: -7.0, via: [[40.5, 0]] },
];

function doorOf(id) {
  const d = DOORS.find((d) => d.building === id);
  return [d.x, d.y];
}

function placeScatter(rng, n, region, avoid, minGap = 2.4) {
  const out = [];
  let guard = 0;
  while (out.length < n && guard < n * 60) {
    guard += 1;
    const x = region.x0 + rng() * (region.x1 - region.x0);
    const y = region.y0 + rng() * (region.y1 - region.y0);
    if (avoid(x, y)) continue;
    if (out.some((p) => Math.hypot(p.x - x, p.y - y) < minGap)) continue;
    out.push({ x, y });
  }
  return out;
}

function segDist(px, py, a, b) {
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  const l2 = dx * dx + dy * dy || 1;
  const t = Math.max(0, Math.min(1, ((px - a[0]) * dx + (py - a[1]) * dy) / l2));
  return Math.hypot(px - (a[0] + dx * t), py - (a[1] + dy * t));
}

export function villageScene(assetFootprint) {
  const rng = rngFor('sample-village');
  const { graph, streets, stubs } = buildGraph(DOORS);
  const statics = [];
  const occupied = [];
  for (const b of BUILDINGS) {
    statics.push({ ...b, kind: 'building' });
    const fp = assetFootprint(b.asset);
    occupied.push({
      minX: b.x + fp.minX - 1.4,
      minY: b.y + fp.minY - 1.4,
      maxX: b.x + fp.maxX + 1.4,
      maxY: b.y + fp.maxY + 1.4,
    });
  }
  for (const p of PROPS) {
    statics.push({ ...p, kind: 'prop', label: p.asset.replace('prop.', '').replace(/^\w/, (c) => c.toUpperCase()) });
    const fp = assetFootprint(p.asset);
    occupied.push({
      minX: p.x + fp.minX - 1,
      minY: p.y + fp.minY - 1,
      maxX: p.x + fp.maxX + 1,
      maxY: p.y + fp.maxY + 1,
    });
  }
  occupied.push({ minX: FIELD.x0 - 1.5, minY: FIELD.y0 - 1.5, maxX: FIELD.x1 + 1.5, maxY: FIELD.y1 + 1 });
  occupied.push({ minX: -41, minY: 13, maxX: -36, maxY: 25 }); // woodcutters' clearing
  const allPaths = streets
    .map((s) => ({ pts: s.pts, half: s.width / 2 }))
    .concat(stubs.map((pts) => ({ pts, half: 0.7 })));
  const avoid = (x, y, margin = 2.2) =>
    occupied.some((o) => x > o.minX && x < o.maxX && y > o.minY && y < o.maxY) ||
    allPaths.some(({ pts, half }) => pts.some((p, i) => i > 0 && segDist(x, y, pts[i - 1], p) < half + margin));

  // Trees chosen by woodcutters (fixed so the clearing reads correctly).
  const cutTrees = [
    { id: 't-cut-1', asset: 'nature.oak.1', x: -42, y: 17 },
    { id: 't-cut-2', asset: 'nature.pine.2', x: -41.6, y: 22.6 },
  ];
  for (const t of cutTrees) statics.push({ ...t, kind: 'tree', label: 'Tree' });
  const trees = [
    ...placeScatter(rng, 26, { x0: -56, y0: 4, x1: -38, y1: 46 }, avoid, 3.6).map((p, i) => ({
      ...p,
      asset: i % 3 === 0 ? `nature.pine.${i % 6}` : `nature.oak.${i % 6}`,
    })),
    ...placeScatter(rng, 12, { x0: -38, y0: 22, x1: -24, y1: 46 }, avoid, 3.8).map((p, i) => ({
      ...p,
      asset: `nature.oak.${(i + 2) % 6}`,
    })),
    ...placeScatter(rng, 16, { x0: -56, y0: -46, x1: -36, y1: -16 }, avoid, 3.2).map((p, i) => ({
      ...p,
      asset: `nature.pine.${i % 6}`,
    })),
    ...placeScatter(rng, 10, { x0: 40, y0: -46, x1: 56, y1: -18 }, avoid, 3.4).map((p, i) => ({
      ...p,
      asset: `nature.pine.${(i + 3) % 6}`,
    })),
    ...placeScatter(rng, 9, { x0: -30, y0: -40, x1: 40, y1: -30 }, avoid, 6).map((p, i) => ({
      ...p,
      asset: `nature.oak.${(i + 4) % 6}`,
    })),
    ...placeScatter(rng, 6, { x0: 14, y0: 32, x1: 50, y1: 46 }, avoid, 6).map((p, i) => ({
      ...p,
      asset: `nature.oak.${(i + 1) % 6}`,
    })),
    { x: -26, y: -14, asset: 'nature.oak.3' },
    { x: 24, y: -16, asset: 'nature.oak.4' },
    { x: 47, y: 9, asset: 'nature.oak.5' },
  ];
  trees.forEach((t, i) => statics.push({ id: `t-${i}`, kind: 'tree', label: 'Tree', ...t }));
  const bushes = placeScatter(rng, 18, { x0: -50, y0: -30, x1: 50, y1: 40 }, (x, y) => avoid(x, y, 1.2), 4);
  bushes.forEach((p, i) =>
    statics.push({ id: `s-${i}`, kind: 'tree', label: 'Bush', asset: `nature.bush.${i % 4}`, ...p }),
  );
  const rocks = [
    ...placeScatter(rng, 7, { x0: 40, y0: -44, x1: 56, y1: -14 }, avoid, 2.5),
    ...placeScatter(rng, 5, { x0: -56, y0: -14, x1: -40, y1: 4 }, avoid, 2.5),
    { x: 30.2, y: -12.8 },
  ];
  rocks.forEach((p, i) =>
    statics.push({ id: `r-${i}`, kind: 'rock', label: 'Rock', asset: `nature.rock.${i % 4}`, ...p }),
  );

  // Field: wheat rows to the north, cabbage rows to the south, gap at y = 23.
  for (let row = 17; row <= 29; row += 1) {
    if (row === 23) continue;
    const crop = row < 23 ? 'wheat' : 'cabbage';
    for (let x = 18; x <= 38; x += 2) {
      statics.push({
        id: `c-${row}-${x}`,
        kind: 'crop',
        label: crop === 'wheat' ? 'Wheat' : 'Cabbages',
        asset: `crop.${crop}.${(row + x) % 3}`,
        x,
        y: row,
      });
    }
  }
  // Fence around the field with a gate at x 25.4..28.6.
  for (let x = 16.4; x < 25.4; x += 2)
    statics.push({ id: `f-n-${x}`, kind: 'fence', label: 'Fence', asset: 'prop.fence.x', x, y: 15.3 });
  for (let x = 29.6; x < 41; x += 2)
    statics.push({ id: `f-n-${x}`, kind: 'fence', label: 'Fence', asset: 'prop.fence.x', x, y: 15.3 });
  for (let y = 16.4; y < 30; y += 2) {
    statics.push({ id: `f-w-${y}`, kind: 'fence', label: 'Fence', asset: 'prop.fence.y', x: 15.3, y });
    statics.push({ id: `f-e-${y}`, kind: 'fence', label: 'Fence', asset: 'prop.fence.y', x: 40.7, y });
  }

  // Ground features.
  const ground = {
    roads: streets
      .filter((s) => !s.field)
      .flatMap((s) => s.pts.slice(1).map((p, i) => ({ a: s.pts[i], b: p, width: s.width, ruts: s.ruts })))
      .concat(stubs.flatMap((pts) => pts.slice(1).map((p, i) => ({ a: pts[i], b: p, width: 1.5 })))),
    worn: [
      { x: 2, y: -15.5, r: 6.5, strength: 0.9 },
      { x: 2, y: -3.4, r: 3.2 },
      { x: 34, y: -7.5, r: 5.5 },
      { x: -10.5, y: 8.5, r: 2.6 },
      { x: -2.2, y: 13.6, r: 2.8 },
      { x: 40.5, y: -7.5, r: 2.4 },
      { x: 26.5, y: 13.8, r: 2.2 },
      { x: 6, y: -19.2, r: 2.2, strength: 0.8 },
      { x: -39.5, y: 19.5, r: 3.2, strength: 0.6 },
    ],
    fields: [{ ...FIELD, rowSpacing: 1.0, rowOrigin: 0 }],
  };

  const people = samplePeople(graph, rng);
  return {
    bounds: { x0: -56, y0: -46, x1: 56, y1: 46 },
    statics,
    ground,
    people,
    caravan: sampleCaravan(),
    focus: { x: 2, y: 2 },
  };
}

// ---------------------------------------------------------------------------
// Sample citizens and routines.

let serial = 0;
function nextId() {
  serial += 1;
  return `sample-person-${String(serial).padStart(4, '0')}`;
}

function route(graph, a, b, offset) {
  return offsetPolyline(graph.route(a, b), offset);
}

function person(rng, role, civ, segments, extra = {}) {
  const id = nextId();
  const appearanceIndex = Math.floor(rng() * APPEARANCE_COUNT);
  const female = appearanceIndex % 2 === 1;
  const age = 16 + Math.floor(rng() * 44);
  return {
    id,
    civ,
    appearance: appearanceIndex,
    schedule: makeSchedule(segments, rng() * 200),
    record: {
      label: personLabel(id),
      sex: female ? 'female' : 'male',
      age,
      role,
      skills: extra.skills ?? {},
      health: extra.health ?? (rng() < 0.85 ? 'Healthy' : 'Recovering from fever'),
      household: 'Not recorded',
      events: extra.events ?? [`Born ${age} years before the sample date`],
    },
    ...extra.flags,
  };
}

function samplePeople(graph, rng) {
  serial = 0;
  const people = [];
  const S = (n) => Math.round(n * 10) / 10;
  const store = doorOf('b-storehouse');
  const pickup = [27, 13.6];

  for (let i = 0; i < 6; i += 1) {
    const o = (rng() - 0.5) * 1.2;
    people.push(
      person(
        rng,
        'Carrier',
        CIV.village,
        [
          {
            type: 'walk',
            path: route(graph, pickup, store, o),
            speed: 1.05,
            anim: 'carry_sack',
            activity: 'Carrying grain to the storehouse',
            destination: 'Storehouse',
          },
          {
            type: 'inside',
            building: 'b-storehouse',
            at: store,
            dur: 5 + rng() * 4,
            activity: 'Unloading inside the storehouse',
          },
          {
            type: 'walk',
            path: route(graph, store, pickup, o),
            speed: 1.3,
            anim: 'walk',
            activity: 'Returning to the field',
            destination: 'Field gate',
          },
          {
            type: 'work',
            at: pickup,
            face: [24.2, 13.6],
            anim: 'idle',
            dur: 3 + rng() * 2,
            activity: 'Loading a sack',
          },
        ],
        { skills: { farming: 30 + Math.floor(rng() * 40) } },
      ),
    );
  }

  const rows = [17.5, 18.5, 20.5, 21.5, 24.5, 25.5, 27.5, 28.5];
  rows.forEach((y, i) => {
    const x0 = 18 + (i % 4) * 4.6 + rng();
    const span = 3.2 + rng();
    people.push(
      person(
        rng,
        'Farmer',
        CIV.village,
        [
          {
            type: 'work',
            at: [x0, y],
            face: [x0, y + 2],
            anim: 'hoe',
            period: 1.3,
            dur: 24,
            drift: [span, 0],
            activity: y < 23 ? 'Weeding the wheat rows' : 'Hoeing cabbage rows',
          },
          {
            type: 'work',
            at: [x0 + span, y],
            face: [x0 + span - 2, y + 1],
            anim: 'idle',
            period: 2.5,
            dur: 4 + rng() * 3,
            activity: 'Resting',
          },
          {
            type: 'work',
            at: [x0 + span, y],
            face: [x0 + span, y + 2],
            anim: 'hoe',
            period: 1.3,
            dur: 24,
            drift: [-span, 0],
            activity: y < 23 ? 'Weeding the wheat rows' : 'Hoeing cabbage rows',
          },
        ],
        { skills: { farming: 40 + Math.floor(rng() * 50) } },
      ),
    );
  });

  const site = [
    [
      [32.2, -5.0],
      [32.2, -6.5],
    ],
    [
      [35.8, -5.0],
      [35.8, -6.5],
    ],
    [
      [37.5, -6.6],
      [36.0, -6.6],
    ],
    [
      [37.5, -9.4],
      [36.0, -9.4],
    ],
  ];
  for (const [at, face] of site) {
    people.push(
      person(
        rng,
        'Builder',
        CIV.village,
        [
          {
            type: 'work',
            at,
            face,
            anim: 'hammer',
            period: 0.9,
            dur: 14 + rng() * 6,
            activity: 'Building a house (construction site)',
          },
          { type: 'work', at, face, anim: 'idle', period: 2, dur: 3, activity: 'Measuring timber' },
        ],
        { skills: { construction: 45 + Math.floor(rng() * 40) } },
      ),
    );
  }
  const plankDoor = doorOf('p-planks');
  const siteDoor = doorOf('b-construction');
  for (let i = 0; i < 2; i += 1) {
    const o = (rng() - 0.5) * 0.8;
    people.push(
      person(
        rng,
        'Builder',
        CIV.village,
        [
          {
            type: 'walk',
            path: route(graph, plankDoor, siteDoor, o),
            speed: 1.0,
            anim: 'carry_plank',
            activity: 'Carrying planks to the site',
            destination: 'Construction site',
          },
          {
            type: 'work',
            at: siteDoor,
            face: [34, -7],
            anim: 'hammer',
            period: 0.9,
            dur: 8,
            activity: 'Fixing planks',
          },
          {
            type: 'walk',
            path: route(graph, siteDoor, plankDoor, o),
            speed: 1.3,
            anim: 'walk',
            activity: 'Fetching planks',
            destination: 'Plank pile',
          },
          {
            type: 'work',
            at: plankDoor,
            face: [40.5, -8],
            anim: 'idle',
            period: 2,
            dur: 3,
            activity: 'Picking up planks',
          },
        ],
        { skills: { construction: 30 + Math.floor(rng() * 30) } },
      ),
    );
  }

  const cutA = [-39.6, 16.6];
  const cutB = [-39.2, 22.0];
  people.push(
    person(
      rng,
      'Woodcutter',
      CIV.village,
      [
        { type: 'work', at: cutA, face: [-42, 17], anim: 'axe', period: 1.4, dur: 30, activity: 'Felling an oak' },
        { type: 'work', at: cutA, face: [-42, 19], anim: 'idle', period: 2, dur: 5, activity: 'Resting' },
      ],
      { skills: { woodcutting: 60 } },
    ),
  );
  people.push(
    person(
      rng,
      'Woodcutter',
      CIV.village,
      [
        { type: 'work', at: cutB, face: [-41.6, 22.6], anim: 'axe', period: 1.5, dur: 26, activity: 'Felling a pine' },
        { type: 'work', at: cutB, face: [-41, 24], anim: 'idle', period: 2, dur: 6, activity: 'Resting' },
      ],
      { skills: { woodcutting: 48 } },
    ),
  );
  const logDoor = doorOf('p-logpile');
  const toWoods = graph.route(logDoor, [-38, 14]).concat([[-38.6, 15.8], cutA]);
  people.push(
    person(
      rng,
      'Woodcutter',
      CIV.village,
      [
        {
          type: 'walk',
          path: toWoods.slice().reverse(),
          speed: 0.95,
          anim: 'carry_log',
          activity: 'Carrying a log to the workshop',
          destination: 'Log pile',
        },
        {
          type: 'work',
          at: logDoor,
          face: [-10.5, 8.5],
          anim: 'idle',
          period: 2,
          dur: 3,
          activity: 'Stacking the log',
        },
        {
          type: 'walk',
          path: toWoods,
          speed: 1.3,
          anim: 'walk',
          activity: 'Walking to the woodland',
          destination: 'Woodland',
        },
        { type: 'work', at: cutA, face: [-42, 18], anim: 'idle', period: 2, dur: 4, activity: 'Lifting a log' },
      ],
      { skills: { woodcutting: 35 } },
    ),
  );

  people.push(
    person(
      rng,
      'Smith',
      CIV.village,
      [
        {
          type: 'work',
          at: [-1.9, 14.35],
          face: [-2.6, 14.0],
          anim: 'hammer',
          period: 0.75,
          dur: 18,
          activity: 'Forging tools at the anvil',
        },
        {
          type: 'work',
          at: [-1.9, 14.35],
          face: [-1, 15],
          anim: 'idle',
          period: 2,
          dur: 4,
          activity: 'Cooling the iron',
        },
      ],
      { skills: { smithing: 70 } },
    ),
  );

  const homes = [
    'b-house-a',
    'b-house-f',
    'b-house-c',
    'b-house-d',
    'b-house-b',
    'b-house-e',
    'b-house-g',
    'b-hall',
    'b-storehouse',
    'b-workshop',
  ];
  const labels = {
    'b-house-a': 'Timber house',
    'b-house-f': 'Timber house',
    'b-house-c': 'Stone house',
    'b-house-d': 'Log house',
    'b-house-b': 'Plank house',
    'b-house-e': 'Stone-and-timber house',
    'b-house-g': 'Log house',
    'b-hall': 'Community hall',
    'b-storehouse': 'Storehouse',
    'b-workshop': 'Workshop',
  };
  for (let i = 0; i < 10; i += 1) {
    const stops = [];
    let prev = homes[Math.floor(rng() * homes.length)];
    stops.push(prev);
    for (let k = 0; k < 3; k += 1) {
      let next = prev;
      while (next === prev) next = homes[Math.floor(rng() * homes.length)];
      stops.push(next);
      prev = next;
    }
    const o = (rng() - 0.5) * 1.1;
    const segs = [];
    for (let k = 0; k < stops.length; k += 1) {
      const a = stops[k];
      const b = stops[(k + 1) % stops.length];
      segs.push({ type: 'inside', building: a, at: doorOf(a), dur: 6 + rng() * 14, activity: `Inside: ${labels[a]}` });
      segs.push({
        type: 'walk',
        path: route(graph, doorOf(a), doorOf(b), o),
        speed: 1.15 + rng() * 0.3,
        anim: 'walk',
        activity: 'Running an errand',
        destination: labels[b],
      });
    }
    people.push(person(rng, 'Villager', CIV.village, segs));
  }

  const spots = [
    [
      [2.1, -13.6],
      [1.0, -14.9],
    ],
    [
      [2.4, -16.0],
      [1.0, -15.0],
    ],
    [
      [-2.6, -16.1],
      [-1.4, -15.2],
    ],
    [
      [-1.6, -17.2],
      [-1.4, -15.6],
    ],
  ];
  for (const [at, face] of spots) {
    people.push(
      person(rng, 'Villager', CIV.village, [
        { type: 'work', at, face, anim: 'talk', period: 1.6, dur: 6 + rng() * 4, activity: 'Talking at the well' },
        { type: 'work', at, face, anim: 'idle', period: 2.4, dur: 4 + rng() * 3, activity: 'Listening' },
      ]),
    );
  }

  // An ambassador from another civilization (visually distinct).
  const hall = doorOf('b-hall');
  const edge = [56, 0];
  people.push({
    ...person(
      rng,
      'Ambassador',
      CIV.envoy,
      [
        {
          type: 'walk',
          path: route(graph, edge, hall, -0.6),
          speed: 0.95,
          anim: 'walk',
          activity: 'Travelling to the community hall',
          destination: 'Community hall',
        },
        { type: 'inside', building: 'b-hall', at: hall, dur: 30, activity: 'Meeting in the community hall' },
        {
          type: 'walk',
          path: route(graph, hall, edge, -0.6),
          speed: 0.95,
          anim: 'walk',
          activity: 'Leaving the settlement',
          destination: 'Beyond the sample area',
        },
        { type: 'inside', building: 'away', at: edge, dur: 20, activity: 'Travelling beyond the sample area' },
      ],
      { events: ['Arrived as an envoy (sample)'], skills: { diplomacy: 75 } },
    ),
    envoy: true,
  });
  void S;
  return people;
}

function sampleCaravan() {
  const rng = rngFor('sample-caravan');
  const y = 1.6;
  const segs = [
    {
      type: 'walk',
      path: [
        [-58, y],
        [6.5, y],
      ],
      speed: 1.1,
      anim: 'walk',
      activity: 'Caravan arriving with goods',
      destination: 'Storehouse',
    },
    {
      type: 'work',
      at: [6.5, y],
      face: [8, y],
      anim: 'idle',
      period: 2,
      dur: 22,
      activity: 'Unloading at the storehouse',
    },
    {
      type: 'walk',
      path: [
        [6.5, y],
        [58, y],
      ],
      speed: 1.1,
      anim: 'walk',
      activity: 'Caravan departing',
      destination: 'Beyond the sample area',
    },
    { type: 'inside', building: 'away', at: [58, y], dur: 14, activity: 'Travelling beyond the sample area' },
  ];
  serial += 1;
  const driverId = `sample-person-${String(serial).padStart(4, '0')}`;
  return {
    id: 'sample-caravan-01',
    civ: CIV.caravan,
    schedule: makeSchedule(segs, 40),
    driver: {
      id: driverId,
      civ: CIV.caravan,
      appearance: 2,
      record: {
        label: personLabel(driverId),
        sex: 'male',
        age: 41,
        role: 'Caravan driver',
        skills: { trade: 55 },
        health: 'Healthy',
        household: 'Not recorded',
        events: ['Joined a trade caravan (sample)'],
      },
    },
    rng,
  };
}
