// S7 C4: GPU budgets scale with the screen. Pure tests of the screen-to-cap
// rule, then the observer at 3840 x 2160 and 1280 x 720 keeps its caches
// within the caps it reports, and recomputes them on resize.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { chromium } from 'playwright';
import { serve } from './serve.mjs';
import { budgetsFor, screenFactor } from '../src/ui/budgets.js';
import { patchTextureBytes } from '../src/render/patch-layer.js';

test('caps follow the screen, within their floors and ceilings', () => {
  const small = budgetsFor(1280, 720);
  assert.equal(small.factor, 1);
  assert.equal(small.patchBytes, 96e6);
  assert.equal(small.patchEntries, 320);
  assert.equal(small.patchPx, 256);
  assert.equal(small.terrainBytes, 128e6);
  assert.equal(budgetsFor(1600, 900).patchBytes, 96e6);
  assert.equal(budgetsFor(1600, 900).terrainBytes, 128e6);
  const hd = budgetsFor(1920, 1080);
  assert.equal(hd.factor, 1.44);
  assert.equal(hd.patchBytes, Math.round(96e6 * 1.44));
  assert.equal(hd.patchPx, 256);
  assert.ok(hd.terrainBytes > 128e6 && hd.terrainBytes <= 192e6);
  const qhd = budgetsFor(2560, 1440);
  assert.equal(qhd.patchPx, 192, '3.7 M device pixels take 192 px patches');
  for (const b of [budgetsFor(3840, 2160), budgetsFor(1920, 1080, 2), budgetsFor(7680, 4320)]) {
    assert.equal(b.factor, 2.33);
    assert.equal(b.patchBytes, Math.round(96e6 * 2.33));
    assert.equal(b.patchEntries, Math.round(320 * 2.33 * (256 / 192) ** 2), 'smaller patches, more of them');
    assert.equal(b.terrainBytes, 192e6);
    assert.equal(b.terrainEntries, 384);
    assert.equal(b.patchPx, 192);
  }
  assert.equal(screenFactor(1920, 1080, 2), screenFactor(3840, 2160, 1));
  // Bytes govern: the entry cap never binds before the byte cap (the user's PC hit 746 of 746 entries
  // at 150 of 224 MB before entries followed the patch area).
  for (const [w, h, dpr] of [
    [1280, 720, 1],
    [1920, 1080, 1],
    [2560, 1440, 1],
    [2552, 1283, 1.5],
    [3840, 2160, 1],
  ]) {
    const b = budgetsFor(w, h, dpr);
    assert.ok(b.patchEntries * patchTextureBytes(b.patchPx) >= b.patchBytes, `bytes govern at ${w} x ${h} @ ${dpr}`);
  }
  // Monotonic in screen size.
  let last = 0;
  for (const w of [800, 1280, 1600, 1920, 2560, 3200, 3840]) {
    const b = budgetsFor(w, (w * 9) / 16);
    assert.ok(b.patchBytes >= last);
    last = b.patchBytes;
  }
});

let server;
let browser;

before(async () => {
  server = await serve(0);
  browser = await chromium.launch({
    args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'],
  });
});

after(async () => {
  await browser?.close();
  server?.close();
});

for (const [w, h] of [
  [3840, 2160],
  [1280, 720],
]) {
  test(`the observer at ${w} x ${h} keeps within the caps for its screen`, async () => {
    const page = await browser.newPage({ viewport: { width: w, height: h }, deviceScaleFactor: 1 });
    await page.goto(`http://127.0.0.1:${server.address().port}/index.html?citizens=0`);
    await page.waitForFunction(() => window.__observer?.ready || window.__observerError, null, { timeout: 600000 });
    const r = await page.evaluate(async () => {
      const o = window.__observer;
      o.setPaused(true);
      const peaks = [];
      // Zoom in from the whole world to the village, settling at each step.
      for (const zoom of [0.0004, 0.002, 0.01, 0.05, 0.2, 1.0]) {
        o.viewVillage(zoom);
        await o.settle(200);
        const s = o.stats();
        peaks.push({
          zoom,
          patchBytes: s.patchBytes,
          patchEntries: o.patchStats().entries,
          gpuBytes: s.gpuBytes,
          gpuTextures: s.gpuTextures,
        });
      }
      return { budgets: o.budgets(), peaks, caps: o.caches() };
    });
    const b = r.budgets;
    const expected = budgetsFor(w, h);
    for (const k of ['patchBytes', 'patchEntries', 'patchPx', 'terrainBytes', 'terrainEntries'])
      assert.equal(b[k], expected[k], k);
    assert.equal(r.caps.patches.maxBytes, b.patchBytes);
    assert.equal(r.caps.patches.maxEntries, b.patchEntries);
    assert.equal(r.caps.terrain.maxBytes, b.terrainBytes);
    assert.equal(r.caps.patchPx, b.patchPx);
    for (const p of r.peaks) {
      assert.ok(p.patchBytes <= b.patchBytes, `patches ${p.patchBytes} at zoom ${p.zoom}`);
      assert.ok(p.patchEntries <= b.patchEntries);
      assert.ok(p.gpuBytes <= b.terrainBytes, `terrain ${p.gpuBytes} at zoom ${p.zoom}`);
    }
    // Resizing recomputes the caps.
    await page.setViewportSize({ width: 1600, height: 900 });
    await page.waitForFunction(() => window.__observer.budgets().patchBytes === 96e6, null, { timeout: 10000 });
    const after = await page.evaluate(() => window.__observer.caches());
    assert.equal(after.patches.maxBytes, 96e6);
    assert.equal(after.terrain.maxBytes, 128e6);
    await page.close();
  });
}
