// S7 C2: the synthetic population's columns (no browser). Totals, shares,
// determinism, unique ids found again by id, and at most 64 bytes a person
// with the layout that places them.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { DUTIES, DUTY, personId, personNumber } from '../src/data/population.js';
import { HOUSEHOLD, MAX_PEOPLE, syntheticPopulation } from '../src/data/synthetic/people.js';
import { CrowdLayout, plansFor } from '../src/world/settlement-plan.js';

const CAPITALS = [
  { settlement_id: 'settlement:0000000001-0001', tile: [81, 27] },
  { settlement_id: 'settlement:0000000002-0001', tile: [95, 95] },
  { settlement_id: 'settlement:0000000003-0001', tile: [4, 25] },
  { settlement_id: 'settlement:0000000004-0001', tile: [9, 90] },
];

test('people are shared equally among the capitals, on their tiles', () => {
  for (const total of [0, 7, 1000, 100001]) {
    const f = syntheticPopulation(total, CAPITALS);
    assert.equal(f.length, total);
    let sum = 0;
    for (let s = 0; s < CAPITALS.length; s += 1) {
      const n = f.residents(s);
      sum += n;
      assert.ok(Math.abs(n - total / CAPITALS.length) < 1, `settlement ${s}: ${n}`);
      for (const i of f.rowsOf(s)) {
        assert.equal(f.settlement[i], s);
        assert.equal(f.civ[i], s);
        assert.deepEqual([f.q[i], f.r[i]], CAPITALS[s].tile);
      }
    }
    assert.equal(sum, total);
  }
  assert.equal(syntheticPopulation(1e9, CAPITALS).length, MAX_PEOPLE);
});

test('the same total gives the same people', () => {
  const a = syntheticPopulation(20000, CAPITALS);
  const b = syntheticPopulation(20000, CAPITALS);
  for (const col of ['id', 'civ', 'sex', 'age', 'health', 'duty', 'appearance', 'house']) {
    assert.deepEqual(a[col], b[col], col);
  }
});

test('ids are unique, ascending and found again', () => {
  const f = syntheticPopulation(50000, CAPITALS);
  const seen = new Set();
  for (let i = 0; i < f.length; i += 1) seen.add(f.idOf(i));
  assert.equal(seen.size, f.length);
  for (const i of [0, 1, 12499, 12500, 33333, 49999]) {
    assert.equal(f.indexOf(f.idOf(i)), i);
  }
  assert.equal(f.indexOf('person:9999999999'), -1);
  assert.equal(f.indexOf('settlement:0000000001-0001'), -1);
  assert.equal(personNumber(personId(42)), 42);
  assert.equal(personId(42), 'person:0000000042');
});

test('ages, duties and households are plausible', () => {
  const f = syntheticPopulation(40000, CAPITALS);
  const byDuty = new Array(DUTIES.length).fill(0);
  let female = 0;
  for (let i = 0; i < f.length; i += 1) {
    byDuty[f.duty[i]] += 1;
    female += f.sex[i];
    assert.equal(f.appearance[i] % 2, f.sex[i], 'odd designs are women');
    if (f.age[i] < 16) assert.equal(f.duty[i], DUTY.child);
    else if (f.age[i] >= 65) assert.equal(f.duty[i], DUTY.elder);
    else assert.ok(f.duty[i] >= DUTY.farmer);
  }
  assert.ok(female / f.length > 0.47 && female / f.length < 0.53);
  const children = byDuty[DUTY.child] / f.length;
  assert.ok(children > 0.3 && children < 0.5, `children ${children}`);
  assert.ok(byDuty[DUTY.farmer] > byDuty[DUTY.crafter]);
  for (let s = 0; s < CAPITALS.length; s += 1) {
    const rows = f.rowsOf(s);
    const houses = new Map();
    for (const i of rows) houses.set(f.house[i], (houses.get(f.house[i]) ?? 0) + 1);
    assert.equal(houses.size, Math.ceil(rows.length / HOUSEHOLD));
    assert.ok([...houses.values()].every((n) => n <= HOUSEHOLD));
  }
  const rec = f.record(123);
  assert.equal(rec.id, f.idOf(123));
  assert.ok(rec.label && rec.role && rec.settlement);
});

test('100,000 people cost at most 64 bytes each at rest', () => {
  const f = syntheticPopulation(100000, CAPITALS);
  const plans = plansFor(f);
  const layout = new CrowdLayout(f, plans);
  const planBytes = plans.reduce((sum, p) => sum + p.bytes(), 0);
  const perPerson = (f.bytes() + layout.bytes() + planBytes) / f.length;
  assert.ok(perPerson <= 64, `${perPerson.toFixed(1)} bytes a person`);
});
