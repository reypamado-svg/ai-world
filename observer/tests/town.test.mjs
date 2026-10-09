// T1 C4: a council's town in the observer. The committed fixture is a rules-3 run whose first
// capital was designed as a ringed town on day 0 and had six palisade sections raised by
// day 18: its wards fill the ring first, its keep, market and shrine stand where the plan
// puts them, built sections are drawn wall by wall with gatehouses, and the rest of the ring
// is a planned line. The second capital is fortified from day 0 (export version 3): towers
// and gatehouses on both gates, a ditch, stakes, a citadel and a standing defence order.
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

    // Day 0: one capital is fortified; the rest have the plain plan and no walls yet.
    const before = await page.evaluate(() => window.__observer.townInfo());
    assert.ok(before.length >= 2);
    const [fortified] = before.filter((t) => t.designed);
    assert.equal(before.filter((t) => t.designed).length, 1);
    assert.deepEqual(fortified.walls, { built: 10, of: 10 });
    assert.equal(fortified.pieces.gatehouses, 2);
    assert.equal(fortified.pieces.towers, 2);
    assert.equal(fortified.pieces.gates, 1, "the citadel's gate");
    assert.equal(fortified.pieces.walls, 10 * 16 - 2 + 31);
    assert.equal(fortified.ditch, 'ditch');
    assert.ok(fortified.stakes > 0);
    assert.equal(fortified.citadel, true);
    assert.equal(fortified.defence.posture, 'everyone');
    for (const town of before.filter((t) => !t.designed)) {
      assert.deepEqual(town.walls, { built: 0, of: 10 });
      assert.equal(town.planned, 10);
      assert.deepEqual(town.pieces, {});
      assert.equal(town.ditch, null);
    }

    // Day 18: the first capital is a ringed town, six sections up.
    const after = await page.evaluate(async () => {
      const o = window.__observer;
      o.setPaused(true);
      await o.loadDay(18);
      o.frame();
      return o.townInfo();
    });
    const [town] = after.filter((t) => t.designed && t.walls.built < 10);
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
    assert.equal(after.filter((t) => t.designed).length, 2, 'the other capitals keep the plain plan');

    // Close up, the walls are drawn as sprites among the houses, and the badge says so.
    assert.ok(town.wallAt, 'a built wall to look at');
    const close = await page.evaluate(({ x, y }) => {
      const o = window.__observer;
      o.setTime(1800);
      o.view(x, y, 1.0);
      o.frame();
      return o.crowdCounts();
    }, town.wallAt);
    assert.ok(close.townPieces > 0, `${close.townPieces} wall pieces drawn`);
    assert.deepEqual(errors, []);
  } finally {
    await page.close();
  }
});
