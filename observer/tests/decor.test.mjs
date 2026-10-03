// Pure tests of the decoration plan (no browser).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { cellPlan } from '../src/render/decor.js';

const tile = { terrain: 1, cover: [6000, 3000, 500, 300, 200, 0, 0] };

test('the reserved fauna argument changes nothing yet', () => {
  assert.deepEqual(cellPlan(tile, {}), cellPlan(tile));
  assert.deepEqual(cellPlan(tile, { grazers: 10 }), cellPlan(tile));
});
