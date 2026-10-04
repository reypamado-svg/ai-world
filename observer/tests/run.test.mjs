// O2 C5: a recorded run in the observer (?run=...): the committed fixture is the export of
// the engine's format-1 test run. The crowd draws exactly the people the engine counted at
// each settlement, days can be stepped, and people picked show their engine ids.
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

test('a recorded run shows its people, day by day, with their engine ids', async () => {
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 }, deviceScaleFactor: 1 });
  try {
    const errors = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await page.goto(`http://127.0.0.1:${server.address().port}/index.html?run=tests/fixtures/run-small&day=0`);
    await page.waitForFunction(() => window.__observer?.ready || window.__observerError, null, { timeout: 600000 });
    assert.equal(await page.evaluate(() => window.__observerError ?? null), null);
    assert.equal(await page.locator('#source-chip').textContent(), 'RECORDED RUN');
    for (const day of [0, 5, 2]) {
      const info = await page.evaluate(async (d) => {
        const o = window.__observer;
        o.setPaused(true);
        await o.loadDay(d);
        o.frame();
        return { ...o.runInfo(), crowd: o.crowdCounts(), url: location.search };
      }, day);
      assert.equal(info.day, day);
      assert.ok(info.url.includes(`day=${day}`));
      assert.deepEqual(info.drawnResidents, info.residents, `day ${day}`);
      assert.equal(info.crowd.worldPopulation, info.counts.at_home, `day ${day}`);
      assert.equal(info.overlays.travellers, info.counts.away, `day ${day}`);
      assert.ok(info.overlays.settlements >= 4);
    }
    // Pick someone at the first capital and read their engine id.
    const picked = await page.evaluate(() => {
      const o = window.__observer;
      o.setTime(1800);
      const id = o.crowdBusiest();
      o.viewPerson(id, 1.0);
      const pt = o.personCanvasPoint(id);
      let got = null;
      for (let c = 0; c < 12 && got !== id && pt; c += 1) got = o.pickAt(pt.x, pt.y);
      return { id, got, inspector: document.getElementById('inspector').textContent };
    });
    assert.ok(picked.id.startsWith('person:'));
    assert.equal(picked.got, picked.id);
    assert.ok(picked.inspector.includes(picked.id), 'the inspector shows the engine id');
    assert.ok(picked.inspector.includes('recorded run'));
    assert.ok(picked.inspector.includes('room for 5'));
    assert.deepEqual(errors, []);
  } finally {
    await page.close();
  }
});
