// Pure tests of ground-patch level choice (no browser).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { PATCH_SIZES, PATCH_PX, patchLevelFor } from '../src/render/patch-layer.js';
import { K } from '../src/world/coords.js';

test('patch sizes halve from 3,200 m to 6.25 m', () => {
  assert.equal(PATCH_SIZES[0], 3200);
  assert.equal(PATCH_SIZES.at(-1), 6.25);
  for (let i = 1; i < PATCH_SIZES.length; i += 1) assert.equal(PATCH_SIZES[i], PATCH_SIZES[i - 1] / 2);
});

test('zooming in never picks a coarser patch, and texels stay under two screen pixels', () => {
  let last = -1;
  for (let zoom = 3e-3; zoom <= 2.6; zoom *= 1.07) {
    const level = patchLevelFor(zoom);
    assert.ok(level >= last, `zoom ${zoom} went coarser`);
    last = level;
    const texelPx = (PATCH_SIZES[level] / PATCH_PX) * K * Math.SQRT2 * zoom;
    // At the finest level the texel may grow past the limit only beyond its range.
    if (level < PATCH_SIZES.length - 1) assert.ok(texelPx <= 1.9 + 1e-9, `zoom ${zoom}: ${texelPx} px per texel`);
    assert.ok(texelPx > 0.4 || level === 0, `zoom ${zoom}: texels needlessly small (${texelPx} px)`);
  }
  assert.equal(patchLevelFor(2.6), PATCH_SIZES.length - 1);
});
