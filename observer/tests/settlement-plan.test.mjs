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

test('a recorded settlement lays out exactly its houses, best nearest, then its building sites', async () => {
  const { GRADES, SITE } = await import('../src/world/settlement-plan.js');
  const plan = new SettlementPlan({
    residents: 26,
    houses: { hut: 3, house: 2, stone_house: 1 },
    jobs: [{ grade: 'hut', count: 3, built: 1, workers: 2 }],
  });
  assert.equal(plan.houseCount, 6);
  assert.equal(plan.lots, 8);
  assert.equal(plan.sites, 2);
  assert.deepEqual([...plan.grade], [2, 1, 1, 0, 0, 0, SITE, SITE]);
  assert.equal(GRADES[plan.grade[0]], 'stone_house');
  const d = (h) => Math.hypot(plan.houseX[h], plan.houseY[h]);
  assert.ok(d(0) <= d(5) + BLOCK_M, 'the finest house is not further out than the meanest');
  // Builders work at the sites.
  const out = [0, 0];
  plan.workPoint(DUTY.builder, 0, 12345, out);
  const atSite = [6, 7].some((h) => Math.abs(out[0] - plan.houseX[h]) < 3);
  assert.ok(atSite, 'a builder works at a building site');
});

test('more residents than room crowd into the houses there are', async () => {
  const { PeopleFrame } = await import('../src/data/population.js');
  const n = 47;
  const frame = new PeopleFrame(n, [{ id: 's', civ: 0, q: 0, r: 0, houses: { hut: 4, house: 2 }, houseJobs: [] }]);
  for (let i = 0; i < n; i += 1) {
    frame.id[i] = i + 1;
    frame.age[i] = 30;
    frame.duty[i] = DUTY.farmer;
  }
  frame.index();
  const [plan] = plansFor(frame);
  assert.equal(plan.houseCount, 6);
  assert.equal(
    [...plan.occupants].reduce((a, b) => a + b, 0),
    n,
  );
  assert.ok([...frame.house].every((h) => h < 6));
  // 30 room, 17 more shared round the six houses: every house crowded, none by more than three.
  assert.ok([...plan.occupants].every((k) => k > 5 && k <= 5 + 3));
  assert.ok(plan.crowded(0));
  // A synthetic settlement (no houses recorded) has room for everyone.
  const frame2 = new PeopleFrame(n, [{ id: 't', civ: 0, q: 0, r: 0 }]);
  for (let i = 0; i < n; i += 1) frame2.id[i] = i + 1;
  frame2.index();
  const [plain] = plansFor(frame2);
  assert.equal(plain.recorded, false);
  assert.ok([...plain.occupants].every((k) => k <= 5));
});

test('a designed town fills its walls first and draws its ring, gates, towers and places', async () => {
  const { isPlainPlan, ringPoint } = await import('../src/world/settlement-plan.js');
  const design = {
    style: 'ringed',
    keep: 'centre',
    market: 'by_store',
    shrine: 'edge',
    craft_quarter: 'by_gate',
    wall_ring: 2,
    gates: [0, 3],
  };
  const sections = Array.from({ length: 10 }, (_, k) => (k < 7 ? ['palisade', k === 3 ? 10 : 25] : [null, 0]));
  const plan = new SettlementPlan({
    residents: 900,
    houses: { hut: 150, house: 30 },
    design,
    walls: { ring: 2, gates: [0, 5], sections, towers: 2 },
    buildings: ['hall'],
  });
  assert.equal(plan.designed, true);
  // The keep's hall stands; the craft quarter has no workshop yet, so its block stays empty.
  const drawn = plan.pieces.filter((p) => p.asset.startsWith('building.')).map((p) => p.asset);
  assert.deepEqual(drawn.sort(), ['building.hall', 'building.shrine', 'building.well']);
  assert.equal(isPlainPlan(design), false);
  // 180 houses need 12 blocks: the 21 inside ring 2 (25, less the keep, the store, the market
  // and the craft quarter) come first.
  const inside = plan.blocks.filter(([i, j]) => Math.max(Math.abs(i), Math.abs(j)) <= 2);
  assert.equal(inside.length, plan.blocks.length);
  // The keep at the centre, the market by the store, the shrine beyond the walls.
  const at = Object.fromEntries(plan.places.map((p) => [p.name, p]));
  assert.deepEqual([at.keep.i, at.keep.j], [0, 0]);
  assert.ok(Math.max(Math.abs(at.market.i), Math.abs(at.market.j)) <= 1);
  assert.ok(Math.max(Math.abs(at.shrine.i), Math.abs(at.shrine.j)) > 2, 'the shrine stands outside');
  assert.ok(!plan.blocks.some(([i, j]) => plan.places.some((p) => p.i === i && p.j === j)));
  // Seven sections stand (16 modules each, one a gatehouse), three are a planned line.
  assert.deepEqual(plan.wallsBuilt(), { built: 7, of: 10 });
  assert.equal(plan.planned.length, 3);
  const walls = plan.pieces.filter((p) => p.asset.startsWith('wall.palisade.'));
  const gates = plan.pieces.filter((p) => p.asset.startsWith('wall.gate.'));
  const towers = plan.pieces.filter((p) => p.asset.startsWith('wall.tower.'));
  assert.equal(walls.length + gates.length, 7 * 16);
  assert.equal(gates.length, 2);
  assert.equal(towers.length, 2);
  assert.ok(
    gates.every((p) => p.asset.startsWith('wall.gate.timber')),
    'palisade gates are timber',
  );
  assert.equal(plan.pieces.filter((p) => p.damaged).length, 16, 'the battered section');
  // Every wall piece lies on the ring's line, half a block beyond the outer wards.
  const h = 2.5 * BLOCK_M;
  for (const p of [...walls, ...gates, ...towers]) {
    assert.ok(Math.abs(Math.max(Math.abs(p.x), Math.abs(p.y)) - h) < 1e-6, `${p.asset} at ${p.x},${p.y}`);
  }
  // The fields lie beyond the walls; guards walk them.
  assert.ok(plan.fieldRing.r0 > h * Math.SQRT2);
  const out = [0, 0];
  plan.workPoint(DUTY.guard, 0, 4242, out);
  assert.ok(Math.abs(Math.max(Math.abs(out[0]), Math.abs(out[1])) - h) < 1e-6);
  // The ring's line runs round once in eight half-sides.
  assert.deepEqual(ringPoint(h, 0), [h, 0, 'y']);
  assert.deepEqual(ringPoint(h, 8 * h).slice(0, 2), [h, 0]);
  assert.deepEqual(ringPoint(h, 2 * h).slice(0, 2), [0, -h]);
});

test('the plain plan and plans without a design keep their old layout', async () => {
  const { isPlainPlan } = await import('../src/world/settlement-plan.js');
  const plain = { style: 'open', keep: 'edge', wall_ring: 2, gates: [0] };
  assert.equal(isPlainPlan(plain), true);
  assert.equal(isPlainPlan(null), true);
  const before = new SettlementPlan({ residents: 400, houses: { hut: 80 } });
  assert.deepEqual(before.pieces, []);
  assert.equal(before.ringHalf, 0);
  const planned = new SettlementPlan({ residents: 400, houses: { hut: 80 }, design: plain });
  assert.equal(planned.designed, false);
  assert.deepEqual(planned.wallsBuilt(), { built: 0, of: 10 });
  assert.equal(planned.planned.length, 10);
});
