// Pure tests of the tile-interior colour field (no browser).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { makeInterior, snowLine, BLEND, WOBBLE, tileBaseColor } from '../src/world/interior.js';
import { hexCentre, sharedCorners } from '../src/world/hex.js';

const R = 25000 / Math.sqrt(3);
const tile = (terrain, extra = {}) => ({
  terrain,
  elevation: 400,
  moisture: 450,
  temperature: 400,
  timber: 300,
  stone: 300,
  ...extra,
});
const close = (a, b, eps = 1e-6) => a.every((v, i) => Math.abs(v - b[i]) < eps);

// Tile (10, 10) is forest and (11, 10) grassland; (10, 11) is a lake; everything else grassland.
const tiles = new Map([
  ['10,10', tile(2, { timber: 800 })],
  ['11,10', tile(1)],
  ['10,11', tile(0)],
]);
const field = makeInterior({
  R,
  tileAt: (q, r) => tiles.get(`${q},${r}`) ?? tile(1, { moisture: 300 }),
  lakes: new Set(['10,11']),
});

test('the field is deterministic', () => {
  const p = hexCentre(10, 10, R);
  assert.deepEqual(field.sample(p.x + 123, p.y - 456), field.sample(p.x + 123, p.y - 456));
});

test('land tiles blend half and half at their border, whichever side is sampled', () => {
  const [a, b] = sharedCorners(10, 10, 11, 10, R);
  const mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
  // Points a hair to either side of the border land in different tiles.
  const ca = hexCentre(10, 10, R);
  const cb = hexCentre(11, 10, R);
  const e = 1e-7;
  const towardA = { x: mid.x + (ca.x - mid.x) * e, y: mid.y + (ca.y - mid.y) * e };
  const towardB = { x: mid.x + (cb.x - mid.x) * e, y: mid.y + (cb.y - mid.y) * e };
  assert.ok(close(field.sample(towardA.x, towardA.y), field.sample(towardB.x, towardB.y), 0.05));
});

test('no blending beyond the blend band', () => {
  const ca = hexCentre(10, 10, R);
  const alone = makeInterior({ R, tileAt: (q, r) => (q === 10 && r === 10 ? tiles.get('10,10') : tile(1)) });
  const forestOnly = makeInterior({ R, tileAt: () => tiles.get('10,10') });
  // Toward (11, 10), stopping just short of the band.
  const cb = hexCentre(11, 10, R);
  // The border is halfway between centres, which are R * sqrt(3) apart.
  const k = 0.5 - (BLEND * 1.05) / Math.sqrt(3);
  const p = { x: ca.x + (cb.x - ca.x) * k, y: ca.y + (cb.y - ca.y) * k };
  assert.ok(close(alone.sample(p.x, p.y), forestOnly.sample(p.x, p.y)));
});

test('water and land do not blend: one wandering shoreline, a beach on land and shallows in the water', () => {
  const land = hexCentre(10, 10, R);
  const lake = hexCentre(10, 11, R);
  const steps = 4000;
  const at = (k) => field.sample(land.x + (lake.x - land.x) * k, land.y + (lake.y - land.y) * k);
  const isWater = (c) => c[2] > c[0] + 30;
  const changes = [];
  for (let i = 1; i <= steps; i += 1)
    if (isWater(at(i / steps)) !== isWater(at((i - 1) / steps))) changes.push(i / steps);
  assert.equal(changes.length, 1, `shoreline crossings at ${changes}`);
  // The shore lies within the wobble of the border, which is halfway between the centres.
  assert.ok(Math.abs(changes[0] - 0.5) <= WOBBLE / Math.sqrt(3) + 1e-3, `shore at ${changes[0]}`);
  // waterAt agrees with the painted shoreline.
  const point = (k) => ({ x: land.x + (lake.x - land.x) * k, y: land.y + (lake.y - land.y) * k });
  const before = point(changes[0] - 2 / steps);
  const after = point(changes[0] + 2 / steps);
  assert.equal(field.waterAt(before.x, before.y), false);
  assert.equal(field.waterAt(after.x, after.y), true);
  const beach = at(changes[0] - 2 / steps);
  const shallows = at(changes[0] + 2 / steps);
  assert.ok(beach[0] > 170 && beach[0] > beach[2], `beach ${beach}`);
  assert.ok(shallows[2] > shallows[0] + 30, `shallows ${shallows}`);
  // Deeper in the lake, no trace of the forest.
  const open = at(0.9);
  assert.ok(open[2] > open[1] && open[2] > open[0] + 40, `lake ${open}`);
});

test('fine patterns fade instead of aliasing when the field is sampled coarsely', () => {
  const snow = makeInterior({ R, tileAt: () => tile(7) });
  const c = hexCentre(40, 40, R);
  // Fine detail: the mean change between samples 40 m apart.
  const roughness = (res) => {
    let sum = 0;
    let last = null;
    for (let i = 0; i < 400; i += 1) {
      const v = snow.sample(c.x + i * 32, c.y - i * 24, res)[0];
      if (last !== null) sum += Math.abs(v - last);
      last = v;
    }
    return sum / 399;
  };
  assert.ok(roughness(400) < roughness(0) / 3, `coarse ${roughness(400)} vs full ${roughness(0)}`);
});

test('snow lies on a mountain only above its snow line, which rises with warmth', () => {
  assert.ok(snowLine(100) < snowLine(400));
  const whiteShare = (temperature) => {
    const m = makeInterior({ R, tileAt: () => tile(3, { elevation: 900, temperature }) });
    const c = hexCentre(20, 20, R);
    let white = 0;
    for (let i = 0; i < 400; i += 1) {
      const p = m.sample(c.x + ((i % 20) - 10) * 500, c.y + (Math.floor(i / 20) - 10) * 500);
      if (p[0] > 200 && p[1] > 200 && p[2] > 200) white += 1;
    }
    return white / 400;
  };
  assert.ok(whiteShare(100) > 0.6, `cold mountain only ${whiteShare(100)} snow`);
  assert.ok(whiteShare(500) < 0.05, `warm mountain ${whiteShare(500)} snow`);
});

test('fields appear only inside the SAMPLE ring', () => {
  const c = hexCentre(30, 30, R);
  const plain = makeInterior({ R, tileAt: () => tile(1) });
  const farmed = makeInterior({
    R,
    tileAt: () => tile(1),
    features: [{ kind: 'fields', x: c.x, y: c.y, r0: 60, r1: 400 }],
  });
  for (const d of [20, 55, 450, 2000])
    assert.deepEqual(farmed.sample(c.x + d, c.y), plain.sample(c.x + d, c.y), `at ${d} m`);
  let changed = 0;
  for (let d = 70; d < 380; d += 10)
    if (!close(farmed.sample(c.x + d, c.y + 3), plain.sample(c.x + d, c.y + 3))) changed += 1;
  assert.ok(changed > 25, `only ${changed} samples farmed`);
});

test('every terrain has a base colour, lakes distinct from ocean', () => {
  for (let t = 0; t < 8; t += 1) assert.equal(tileBaseColor(tile(t)).length, 3);
  assert.notDeepEqual(tileBaseColor(tile(0), true), tileBaseColor(tile(0), false));
});

// Land cover (generator 3): the dominant class is the ground; the others form patches.
const covered = (cover, extra = {}) => tile(1, { cover, ...extra });
const shownShare = (field, cls, c) => {
  let hits = 0;
  for (let i = 0; i < 40; i += 1)
    for (let j = 0; j < 40; j += 1) if (field.classAt(c.x + (i - 20) * 300, c.y + (j - 20) * 300) === cls) hits += 1;
  return hits / 1600;
};

test('wood patches cover about their share of a tile, and none without wood', () => {
  const c = hexCentre(50, 50, R);
  const wooded = makeInterior({ R, tileAt: () => covered([6000, 3000, 500, 300, 200, 0, 0]) });
  const bare = makeInterior({ R, tileAt: () => covered([9300, 0, 500, 0, 200, 0, 0]) });
  const share = shownShare(wooded, 1, c);
  assert.ok(share > 0.18 && share < 0.42, `wood shows on ${share} of a 30% wood tile`);
  assert.equal(shownShare(bare, 1, c), 0);
});

test('wetland makes ponds; a desert without wetland has none', () => {
  const c = hexCentre(60, 60, R);
  const wet = makeInterior({ R, tileAt: () => covered([5500, 1500, 500, 2500, 0, 0, 0]) });
  const dry = makeInterior({ R, tileAt: () => tile(4, { cover: [0, 0, 1000, 0, 2200, 6800, 0] }) });
  let ponds = 0;
  for (let i = 0; i < 60; i += 1)
    for (let j = 0; j < 60; j += 1) if (wet.waterAt(c.x + (i - 30) * 200, c.y + (j - 30) * 200)) ponds += 1;
  assert.ok(ponds > 0, 'some pond water in a quarter-wetland tile');
  assert.equal(shownShare(dry, 3, c), 0);
  assert.equal(dry.classAt(c.x, c.y) === 5 || dry.classAt(c.x, c.y) === 4 || dry.classAt(c.x, c.y) === 2, true);
});

test('tiles without cover paint as before and report no cover class', () => {
  const plain = makeInterior({ R, tileAt: () => tile(1) });
  const c = hexCentre(40, 40, R);
  assert.equal(plain.classAt(c.x, c.y), -1);
  assert.deepEqual(plain.sample(c.x + 10, c.y + 20), plain.sample(c.x + 10, c.y + 20));
  const base = tileBaseColor(tile(1));
  const withCover = tileBaseColor(covered([3000, 6000, 500, 300, 200, 0, 0]));
  assert.equal(withCover.length, 3);
  assert.ok(withCover[1] < base[1], 'a mostly wooded tile reads darker green on the map');
});
