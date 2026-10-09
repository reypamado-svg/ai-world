// Pure tests of the SAMPLE courier's route choice (no browser).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { chooseCourierRoute } from '../src/data/sample/village.js';

const world = { chunkTiles: 8, width: 24, height: 24 };
const walk = (route, [q, r]) => Array.from({ length: route.steps + 1 }, (_, k) => [q + route.dq * k, r + route.dr * k]);

test('the route stays on land, avoids deep rivers and leaves the chunk', () => {
  const water = new Set(['10,9', '9,10', '8,10']);
  const deep = new Set(['9,9|9,8']);
  const route = chooseCourierRoute([9, 9], {
    ...world,
    landAt: (q, r) => !water.has(`${q},${r}`),
    deepBetween: ([aq, ar], [bq, br]) => deep.has(`${aq},${ar}|${bq},${br}`) || deep.has(`${bq},${br}|${aq},${ar}`),
  });
  const path = walk(route, [9, 9]);
  for (let k = 1; k < path.length; k += 1) {
    assert.ok(!water.has(path[k].join(',')), `step ${k} is water`);
    assert.ok(!deep.has(`${path[k - 1].join(',')}|${path[k].join(',')}`), `step ${k} crosses a deep river`);
  }
  const chunk = ([q, r]) => `${Math.floor(q / 8)},${Math.floor(r / 8)}`;
  assert.notEqual(chunk(path.at(-1)), chunk(path[0]));
});

test('with no checked way out there is no route, never an unchecked one', () => {
  const route = chooseCourierRoute([12, 12], { ...world, landAt: () => false, deepBetween: () => false });
  assert.equal(route, null);
});

test('if every route stays in the chunk, a single checked step is used', () => {
  // Land only next to the capital, and not to the east: no route of 2+ steps exists.
  const near = (q, r) => (Math.abs(q - 12) + Math.abs(r - 12) + Math.abs(q - 12 + r - 12)) / 2 <= 1;
  const route = chooseCourierRoute([12, 12], {
    ...world,
    landAt: (q, r) => near(q, r) && !(q === 13 && r === 12),
    deepBetween: () => false,
  });
  assert.deepEqual(route, { dq: 1, dr: -1, steps: 1 });
});
