// S7 C2: settlement plans (no browser). Enough houses for everyone, wards that
// grow with the square root of the population and stay clear of the core and
// the fields, routine tables that agree with the routines, and walks that keep
// to the streets at a walking pace.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { DUTIES, DUTY } from '../src/data/population.js';
import { syntheticPopulation } from '../src/data/synthetic/people.js';
import {
  BLOCK_M,
  CrowdLayout,
  HOUSEHOLD,
  HOUSES_PER_BLOCK,
  PHASE,
  ROUTINE_TABLES,
  SettlementPlan,
  WALK_MPS,
  plansFor,
  routinePhase,
} from '../src/world/settlement-plan.js';

const CAPITALS = [
  { settlement_id: 'settlement:0000000001-0001', tile: [81, 27] },
  { settlement_id: 'settlement:0000000002-0001', tile: [95, 95] },
  { settlement_id: 'settlement:0000000003-0001', tile: [4, 25] },
  { settlement_id: 'settlement:0000000004-0001', tile: [9, 90] },
];

const onStreet = (v) =>
  Math.abs((((v / BLOCK_M - 0.5) % 1) + 1) % 1) < 1e-3 || Math.abs(((((v / BLOCK_M - 0.5) % 1) + 1) % 1) - 1) < 1e-3;

test('a plan has a house for every five residents', () => {
  for (const residents of [1, 32, 999, 25000, 50000]) {
    const plan = new SettlementPlan({ residents });
    assert.ok(plan.houseCount * HOUSEHOLD >= residents);
    assert.ok(plan.blocks.length * HOUSES_PER_BLOCK >= plan.houseCount);
    assert.ok((plan.blocks.length - 1) * HOUSES_PER_BLOCK < plan.houseCount, 'no spare block');
    const lots = new Set();
    for (let h = 0; h < plan.houseCount; h += 1) {
      lots.add(`${plan.houseX[h].toFixed(2)},${plan.houseY[h].toFixed(2)}`);
      assert.ok(onStreet(plan.doorY[h]), 'doors open on a street');
    }
    assert.equal(lots.size, plan.houseCount, 'one house to a lot');
  }
});

test('wards grow with the square root of the population', () => {
  const small = new SettlementPlan({ residents: 6250 }).wardRadius;
  const big = new SettlementPlan({ residents: 25000 }).wardRadius;
  const huge = new SettlementPlan({ residents: 100000 }).wardRadius;
  assert.ok(big / small > 1.7 && big / small < 2.2, `${small} -> ${big}`);
  assert.ok(huge / big > 1.8 && huge / big < 2.1, `${big} -> ${huge}`);
  assert.ok(big < 1200, `25,000 people within ${big.toFixed(0)} m`);
});

test('blocks keep clear of the core, houses of the fields', () => {
  const plan = new SettlementPlan({ residents: 25000, coreRadius: 64 });
  for (const [i, j] of plan.blocks) {
    const nx = Math.max(Math.abs(i * BLOCK_M) - BLOCK_M / 2, 0);
    const ny = Math.max(Math.abs(j * BLOCK_M) - BLOCK_M / 2, 0);
    assert.ok(Math.hypot(nx, ny) >= 64, `block ${i},${j} in the core`);
  }
  for (let h = 0; h < plan.houseCount; h += 1) {
    assert.ok(Math.hypot(plan.houseX[h], plan.houseY[h]) < plan.fieldRing.r0);
  }
  const f = syntheticPopulation(20000, CAPITALS);
  const plans = plansFor(f);
  const layout = new CrowdLayout(f, plans);
  for (let i = 0; i < f.length; i += 1) {
    if (f.duty[i] !== DUTY.farmer) continue;
    const r = Math.hypot(layout.workX[i], layout.workY[i]);
    const ring = plans[f.settlement[i]].fieldRing;
    assert.ok(r >= ring.r0 && r <= ring.r1, `farmer ${i} at ${r}`);
  }
});

test('the routine tables agree with the routines', () => {
  DUTIES.forEach((duty, d) => {
    for (let m = 0; m < 1440; m += 7) {
      const tod = m * 60 + 30;
      assert.equal(ROUTINE_TABLES[d][m * 2], PHASE[routinePhase(duty, tod)], `${duty} at minute ${m}`);
    }
  });
});

test('everyone sleeps at home, and walks keep to the streets at a walking pace', () => {
  const f = syntheticPopulation(8000, CAPITALS);
  const layout = new CrowdLayout(f, plansFor(f));
  const a = {};
  const b = {};
  for (let i = 0; i < f.length; i += 1) {
    assert.equal(layout.sample(i, 3 * 3600, a).inside, true, 'at home at 3 am');
  }
  let walking = 0;
  for (const hour of [6.4, 7.2, 12, 17.3, 18.2]) {
    const t = hour * 3600;
    for (let i = 0; i < f.length; i += 3) {
      layout.sample(i, t, a);
      layout.sample(i, t + 1, b);
      if (!a.inside && !b.inside && a.moving) {
        walking += 1;
        const step = Math.hypot(b.x - a.x, b.y - a.y);
        assert.ok(step <= WALK_MPS * 1.01 + 1e-6, `row ${i} moved ${step} m in a second`);
        const lastLeg = Math.abs(a.y - layout.workY[i]) < 1e-3;
        assert.ok(onStreet(a.x) || onStreet(a.y) || lastLeg, `row ${i} off the streets at ${a.x},${a.y}`);
      }
    }
  }
  assert.ok(walking > 500, `${walking} people walking`);
  // Mid-morning: farmers who have arrived hoe in the fields.
  let hoeing = 0;
  for (let i = 0; i < f.length; i += 1) {
    if (f.duty[i] === DUTY.farmer && layout.sample(i, 10 * 3600, a).anim === 'hoe') hoeing += 1;
  }
  assert.ok(hoeing > 0);
});

test('placing 100,000 people takes well under a frame', () => {
  const f = syntheticPopulation(100000, CAPITALS);
  const layout = new CrowdLayout(f, plansFor(f));
  const out = {};
  let best = Infinity;
  for (let rep = 0; rep < 5; rep += 1) {
    const t0 = performance.now();
    for (let i = 0; i < f.length; i += 1) layout.sample(i, 8 * 3600 + rep * 97, out);
    best = Math.min(best, performance.now() - t0);
  }
  console.log(`sample 100,000: ${best.toFixed(1)} ms (${((best * 1e6) / f.length).toFixed(0)} ns a person)`);
  assert.ok(best < 60, `${best.toFixed(1)} ms`);
});
