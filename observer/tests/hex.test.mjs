// Pure tests of the hex layout (no browser).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  hexCentre,
  hexCorners,
  planeToHex,
  chunkOf,
  hexRadiusOf,
  tileScreenPx,
  zoomForTilePx,
} from '../src/world/hex.js';
import { project, unproject } from '../src/world/coords.js';

// Tile centres lie 25 km apart (the engine's world rule).
const R = 25000 / Math.sqrt(3);

test('every tile centre maps back to its own tile', () => {
  for (let r = 0; r < 100; r += 1) {
    for (let q = 0; q < 100; q += 1) {
      const c = hexCentre(q, r, R);
      assert.deepEqual(planeToHex(c.x, c.y, R), { q, r });
      // Points well inside the hex also map to it.
      for (const corner of hexCorners(q, r, R)) {
        const p = { x: c.x + (corner.x - c.x) * 0.8, y: c.y + (corner.y - c.y) * 0.8 };
        assert.deepEqual(planeToHex(p.x, p.y, R), { q, r });
      }
    }
  }
});

test('neighbouring centres are R*sqrt(3) metres apart (metres mean the same everywhere)', () => {
  const c = hexCentre(10, 10, R);
  for (const [dq, dr] of [
    [1, 0],
    [1, -1],
    [0, -1],
    [-1, 0],
    [-1, 1],
    [0, 1],
  ]) {
    const n = hexCentre(10 + dq, 10 + dr, R);
    assert.ok(Math.abs(Math.hypot(n.x - c.x, n.y - c.y) - 25000) < 1e-6);
  }
});

test('tile rows project to horizontal screen rows', () => {
  const a = project(hexCentre(3, 7, R).x, hexCentre(3, 7, R).y);
  const b = project(hexCentre(9, 7, R).x, hexCentre(9, 7, R).y);
  assert.ok(Math.abs(a.y - b.y) < 1e-6);
});

test('chunks partition the map', () => {
  const seen = new Map();
  for (let r = 0; r < 48; r += 1)
    for (let q = 0; q < 48; q += 1) {
      const { cq, cr } = chunkOf(q, r, 8);
      assert.ok(cq * 8 <= q && q < cq * 8 + 8 && cr * 8 <= r && r < cr * 8 + 8);
      seen.set(`${cq},${cr}`, (seen.get(`${cq},${cr}`) ?? 0) + 1);
    }
  assert.equal(seen.size, 36);
  assert.ok([...seen.values()].every((n) => n === 64));
});

test('projection round-trips on the ground plane', () => {
  for (const [x, y] of [
    [0, 0],
    [123.4, -56.7],
    [5000, 3000],
  ]) {
    const p = project(x, y);
    const g = unproject(p.x, p.y);
    assert.ok(Math.abs(g.x - x) < 1e-6 && Math.abs(g.y - y) < 1e-6);
  }
});

test('the hex radius comes from the manifest world rule, and screen sizes follow from it', () => {
  assert.ok(Math.abs(hexRadiusOf({ engine: { travel: { tile_spacing_m: 25000 } } }) - R) < 1e-9);
  assert.throws(() => hexRadiusOf({ engine: {} }), /tile_spacing_m/);
  // One tile at zoom 1 is 25 km * sqrt(2) * 16 px/m wide on screen.
  assert.ok(Math.abs(tileScreenPx(R) - 565685.42) < 0.01);
  assert.ok(Math.abs(zoomForTilePx(256, R) - 4.5255e-4) < 1e-7);
  assert.ok(Math.abs(zoomForTilePx(256, R) * tileScreenPx(R) - 256) < 1e-9);
});
