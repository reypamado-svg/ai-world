// Pure tests of the courier's multi-day travel plan (no browser).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { planJourney, depthOf, WALK_SECONDS_PER_DAY, DAY_SECONDS } from '../src/sim/travel-plan.js';

const rules = {
  day_tenths: 10,
  tile_spacing_m: 25000,
  stream_flow: 4,
  deep_flow: 10,
  entry_cost_tenths: {
    water: null,
    grassland: 10,
    forest: 15,
    mountain: 30,
    desert: 15,
    tundra: 15,
    hills: 20,
    snow: 50,
  },
  crossing_cost_tenths: { stream: 5, river: 10, deep: null },
};

// A straight row of tiles 25 km apart along x.
const row = (n) => Array.from({ length: n }, (_, i) => ({ q: i, r: 0, centre: [i * 25000, 0] }));
const plan = (tiles, terrain, flows = {}) =>
  planJourney({
    start: tiles[0].centre,
    tiles,
    terrainAt: (q) => terrain[q],
    flowBetween: (a, b) => flows[`${Math.min(a.q, b.q)}`] ?? 0,
    rules,
  });
const walks = (p) => p.segments.filter((s) => s.type === 'walk');
const distance = (s) => Math.hypot(s.path[1][0] - s.path[0][0], s.path[1][1] - s.path[0][1]);
const timeOf = (s) => (s.type === 'walk' ? distance(s) / s.speed : s.dur);

test('depths follow the engine thresholds', () => {
  assert.equal(depthOf(1, rules), 'stream');
  assert.equal(depthOf(4, rules), 'river');
  assert.equal(depthOf(9, rules), 'river');
  assert.equal(depthOf(10, rules), 'deep');
});

test("a grassland tile is a day's walk: five hours on foot, then camp", () => {
  const p = plan(row(3), ['grassland', 'grassland', 'grassland']);
  assert.equal(p.legs.length, 2);
  // Arrive at the first tile after one day's walking, then camp before the next.
  assert.ok(Math.abs(p.legs[0].arriveT - WALK_SECONDS_PER_DAY) < 1e-6);
  assert.ok(Math.abs(p.legs[1].arriveT - (DAY_SECONDS + WALK_SECONDS_PER_DAY)) < 1e-6);
  assert.equal(p.camps.length, 1);
  assert.match(p.segments.find((s) => s.activity?.startsWith('Camped')).activity, /day 1 of the journey/);
  // The walking pace covers 25 km in five hours.
  for (const s of walks(p)) assert.ok(Math.abs(s.speed - 25000 / WALK_SECONDS_PER_DAY) < 1e-6);
  const total = p.segments.reduce((sum, s) => sum + timeOf(s), 0);
  assert.ok(Math.abs(total - p.duration) < 1e-3);
});

test('rough ground takes longer: hills two days, snow five', () => {
  const hills = plan(row(2), ['grassland', 'hills']);
  assert.ok(Math.abs(hills.duration - (DAY_SECONDS + WALK_SECONDS_PER_DAY)) < 1e-6);
  assert.equal(hills.camps.length, 1);
  const snow = plan(row(2), ['grassland', 'snow']);
  assert.ok(Math.abs(snow.duration - (4 * DAY_SECONDS + WALK_SECONDS_PER_DAY)) < 1e-6);
  assert.equal(snow.camps.length, 4);
  // Each day has exactly five hours of walking.
  let day = 0;
  let walked = 0;
  for (const s of snow.segments) {
    if (s.activity?.startsWith('Camped')) {
      assert.ok(Math.abs(walked - WALK_SECONDS_PER_DAY) < 1e-6, `day ${day} walked ${walked}s`);
      day += 1;
      walked = 0;
    } else walked += timeOf(s);
  }
});

test('a ford is waded at the border and adds its crossing time', () => {
  const stream = plan(row(2), ['grassland', 'grassland'], { 0: 2 });
  assert.equal(stream.fords.length, 1);
  assert.equal(stream.fords[0].depth, 'stream');
  assert.deepEqual(stream.fords[0].at, [12500, 0]);
  const wade = stream.segments.filter((s) => s.activity === 'Wading the stream (ford)');
  assert.ok(Math.abs(wade.reduce((sum, s) => sum + s.dur, 0) - WALK_SECONDS_PER_DAY / 2) < 1e-6);
  assert.ok(Math.abs(stream.duration - (DAY_SECONDS + WALK_SECONDS_PER_DAY / 2)) < 1e-6);
  const river = plan(row(2), ['grassland', 'grassland'], { 0: 6 });
  assert.equal(river.fords[0].depth, 'river');
  assert.ok(Math.abs(river.duration - (DAY_SECONDS + WALK_SECONDS_PER_DAY)) < 1e-6);
});

test('deep rivers and water cannot be crossed on foot', () => {
  assert.throws(() => plan(row(2), ['grassland', 'grassland'], { 0: 12 }), /deep river/);
  assert.throws(() => plan(row(2), ['grassland', 'water']), /cannot be entered/);
});

test('the walk is continuous: each piece starts where the last one ended', () => {
  const p = plan(row(4), ['grassland', 'forest', 'hills', 'grassland'], { 1: 5 });
  let at = row(4)[0].centre;
  for (const s of p.segments) {
    const from = s.type === 'walk' ? s.path[0] : s.at;
    assert.ok(Math.hypot(from[0] - at[0], from[1] - at[1]) < 1e-6);
    at = s.type === 'walk' ? s.path[1] : s.at;
  }
  assert.deepEqual(at, row(4)[3].centre);
  assert.deepEqual(
    p.legs.map((l) => l.q),
    [1, 2, 3],
  );
});
