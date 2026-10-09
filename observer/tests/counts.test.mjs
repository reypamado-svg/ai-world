// R9: resident, indoor, away, outdoor and visible counts are defined
// separately and stay consistent at several population sizes, in every band,
// including when the crowd budget simplifies far citizens.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { chromium } from 'playwright';
import { serve } from './serve.mjs';

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

async function countsFor(query) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 }, deviceScaleFactor: 1 });
  await page.goto(`http://127.0.0.1:${server.address().port}/index.html${query}`);
  await page.waitForFunction(() => window.__observer?.ready || window.__observerError, null, { timeout: 300000 });
  const result = await page.evaluate(() => {
    const o = window.__observer;
    o.setPaused(true);
    const out = [];
    for (const [t, zoom] of [
      [100, 1.0],
      [400, 0.6],
      [700, 0.2],
      [900, 0.03],
      [1200, 0.004],
      [1500, 0.0002],
    ]) {
      o.setTime(t);
      o.viewVillage(zoom);
      out.push({ t, zoom, band: o.band(), ...o.stats() });
    }
    return out;
  });
  await page.close();
  return result;
}

for (const [label, query, n] of [
  ['400 citizens', '?citizens=400', 400],
  ['2,000 citizens, low quality', '?citizens=2000&quality=low', 2000],
]) {
  test(`counts are consistent with ${label}`, async () => {
    const rows = await countsFor(query);
    for (const s of rows) {
      const where = `t=${s.t} zoom=${s.zoom}`;
      // The scene adds a caravan driver to the sample residents and visitors.
      assert.equal(s.worldPopulation, n + 1, where);
      assert.equal(s.indoor + s.away + s.outdoor, s.worldPopulation, where);
      assert.ok(s.resident <= s.worldPopulation, where);
      assert.ok(s.visible <= s.outdoor, where);
      assert.equal(s.visibleFull + s.visibleSimplified, s.visible, where);
      if (s.band !== 'settlement') assert.equal(s.visibleFull, 0, `${where}: only dots outside the settlement band`);
    }
    if (n === 2000) {
      const settlement = rows.find((r) => r.zoom === 1.0);
      assert.ok(settlement.visibleSimplified > 0, 'the low-quality crowd budget simplifies far citizens');
      assert.ok(settlement.visibleFull <= 150, `full-detail citizens ${settlement.visibleFull} exceed the low budget`);
    }
  });
}

test('lower quality refreshes citizen animation less often', async () => {
  const recomputes = async (quality) => {
    const page = await browser.newPage({ viewport: { width: 1280, height: 720 }, deviceScaleFactor: 1 });
    await page.goto(`http://127.0.0.1:${server.address().port}/index.html?citizens=400&quality=${quality}`);
    await page.waitForFunction(() => window.__observer?.ready || window.__observerError, null, { timeout: 300000 });
    const n = await page.evaluate(() => {
      const o = window.__observer;
      o.setPaused(true);
      o.setTime(100);
      o.viewVillage(1.0);
      const start = o.animRecomputes();
      // One second of 60 Hz frames with the camera still.
      for (let i = 0; i < 60; i += 1) o.step(1 / 60);
      return o.animRecomputes() - start;
    });
    await page.close();
    return n;
  };
  const high = await recomputes('high');
  const low = await recomputes('low');
  assert.ok(high >= 55, `high quality recomputed ${high} times in 60 frames`);
  assert.ok(low <= 17, `low quality (15 Hz) recomputed ${low} times in 60 frames`);
});
