// T1 C4: a council's town in the observer. The committed fixture is a rules-3 run whose first
// capital was designed as a ringed town on day 0 and had six palisade sections raised by
// day 18: its wards fill the ring first, its keep, market and shrine stand where the plan
// puts them, built sections are drawn wall by wall with gatehouses, and the rest of the ring
// is a planned line.
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

test('a designed town is drawn with its walls, gates and places', async () => {
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 }, deviceScaleFactor: 1 });
  try {
    const errors = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await page.goto(`http://127.0.0.1:${server.address().port}/index.html?run=tests/fixtures/run-town&day=0`);
    await page.waitForFunction(() => window.__observer?.ready || window.__observerError, null, { timeout: 600000 });
    assert.equal(await page.evaluate(() => window.__observerError ?? null), null);

    // Day 0: every capital has the plain plan and no walls yet.
    const before = await page.evaluate(() => window.__observer.townInfo());
    assert.ok(before.length >= 2);
    for (const town of before) {
      assert.equal(town.designed, false);
      assert.deepEqual(town.walls, { built: 0, of: 10 });
      assert.equal(town.planned, 10);
      assert.deepEqual(town.pieces, {});
    }

    // Day 18: the first capital is a ringed town, six sections up.
    const after = await page.evaluate(async () => {
      const o = window.__observer;
      o.setPaused(true);
      await o.loadDay(18);
      o.frame();
      return o.townInfo();
    });
    const [town] = after.filter((t) => t.designed);
    assert.ok(town, 'a designed town');
    assert.equal(town.style, 'ringed');
    assert.deepEqual(town.walls, { built: 6, of: 10 });
    assert.equal(town.planned, 4);
    assert.equal(town.pieces.gates, 2, 'both gate sections stand');
    assert.equal(town.pieces.walls + town.pieces.gates, 6 * 16);
    assert.deepEqual(
      town.places.map((p) => [p.name, p.place]),
      [
        ['keep', 'centre'],
        ['market', 'by_store'],
        ['shrine', 'edge'],
      ],
    );
    assert.equal(after.filter((t) => t.designed).length, 1, 'the other capitals keep the plain plan');

    // Close up, the walls are drawn as sprites among the houses, and the badge says so.
    const close = await page.evaluate((id) => {
      const o = window.__observer;
      o.setTime(1800);
      o.viewPerson(id, 1.0);
      o.frame();
      const near = o.crowdCounts();
      o.viewPerson(id, 0.1);
      o.frame();
      return { near, far: o.crowdCounts() };
    }, town.resident);
    assert.ok(close.near.townPieces > 0, `${close.near.townPieces} wall pieces drawn`);
    assert.ok(close.near.houses > 0);
    assert.deepEqual(errors, []);
  } finally {
    await page.close();
  }
});
