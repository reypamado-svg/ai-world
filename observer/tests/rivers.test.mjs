// Pure tests of river geometry (no browser).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { riverWidthM, riverPoint, riverLine, riverEdgesOf } from '../src/world/rivers.js';
import { indexHydrology } from '../src/data/terrain-source.js';

const R = 25000 / Math.sqrt(3);
const thresholds = { streamFlow: 4, deepFlow: 10 };

test('channel widths grow with flow and stay within each depth class', () => {
  let last = 0;
  for (let flow = 1; flow <= 200; flow += 1) {
    const w = riverWidthM(flow, thresholds);
    assert.ok(w >= last, `flow ${flow} is narrower than flow ${flow - 1}`);
    last = w;
  }
  assert.equal(riverWidthM(1, thresholds), 40);
  assert.equal(riverWidthM(3, thresholds), 60);
  assert.equal(riverWidthM(4, thresholds), 80);
  assert.equal(riverWidthM(9, thresholds), 140);
  assert.equal(riverWidthM(10, thresholds), 150);
  assert.equal(riverWidthM(110, thresholds), 400);
  assert.equal(riverWidthM(500, thresholds), 400);
});

test('a river border wanders between its two corners and both tiles see the same curve', () => {
  const rivers = indexHydrology({ deep_flow: 10, edges: [[5, 5, 6, 5, 6, null, null]] }, 8);
  const [a] = riverEdgesOf(5, 5, rivers, R, 4);
  const [b] = riverEdgesOf(6, 5, rivers, R, 4);
  assert.deepEqual(a, b);
  assert.equal(a.widthM, 104);
  assert.equal(a.deep, false);
  const line = riverLine(a, 24);
  assert.ok(Math.hypot(line[0].x - a.p1.x, line[0].y - a.p1.y) < 1e-6);
  assert.ok(Math.hypot(line[24].x - a.p2.x, line[24].y - a.p2.y) < 1e-6);
  // The wander stays within about a tenth of the border length either side.
  const length = Math.hypot(a.p2.x - a.p1.x, a.p2.y - a.p1.y);
  const mid = { x: (a.p1.x + a.p2.x) / 2, y: (a.p1.y + a.p2.y) / 2 };
  const off = Math.hypot(riverPoint(a, 0.5).x - mid.x, riverPoint(a, 0.5).y - mid.y);
  assert.ok(off > 0.02 * length && off < 0.15 * length, `mid-border offset ${off / length} of the length`);
});
