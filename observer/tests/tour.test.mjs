// S7b: the measurement tour holds the camera. Wheel, drag and the zoom buttons
// during a stop are undone and the stop is marked touched; every row is
// labelled with the band it really measured; per-stop eviction, bake and heap
// figures and the load times are recorded; input works again afterwards.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { chromium } from 'playwright';
import { serve } from './serve.mjs';

let server;
let browser;

before(async () => {
  server = await serve(0);
  browser = await chromium.launch({
    args: [
      '--use-angle=swiftshader',
      '--enable-unsafe-swiftshader',
      '--ignore-gpu-blocklist',
      '--enable-precise-memory-info',
    ],
  });
});

after(async () => {
  await browser?.close();
  server?.close();
});

test('the tour holds the camera, labels rows by band and records load times', async () => {
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 }, deviceScaleFactor: 1 });
  try {
    const errors = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await page.goto(`http://127.0.0.1:${server.address().port}/index.html?people=5000&citizens=0&quality=low`);
    await page.waitForFunction(() => window.__observer?.ready || window.__observerError, null, { timeout: 600000 });
    assert.equal(await page.evaluate(() => window.__observerError ?? null), null);
    // tour() sets its hold before its first await, so the camera is held when this returns.
    await page.evaluate(() => {
      window.__tour = window.__observer.tour(1);
    });
    const camera = () => page.evaluate(() => window.__observer.camera());
    const held = await camera();
    assert.ok(!(await page.locator('#tour-banner').isHidden()), 'the banner asks for hands off');
    await page.mouse.move(640, 360);
    await page.mouse.wheel(0, -800);
    await page.mouse.down();
    await page.mouse.move(900, 500, { steps: 8 });
    await page.mouse.up();
    await page.click('#btn-zoom-out');
    await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
    const now = await camera();
    assert.deepEqual({ x: now.x, y: now.y, zoom: now.zoom }, { x: held.x, y: held.y, zoom: held.zoom });
    assert.equal(
      await page.evaluate(() => window.__observer.selection()),
      null,
      'a click during the tour selects nothing',
    );

    const rows = await page.evaluate(() => window.__tour);
    assert.equal(rows.length, 4);
    assert.equal(rows[0].zoom, 1);
    assert.equal(rows[1].zoom, 0.1);
    for (const row of rows) {
      assert.equal(row.viewBand, row.band, `${row.band} stop measured the ${row.viewBand} band`);
      for (const k of ['patchEvictions', 'terrainEvictions', 'patchBakes', 'patchEntries', 'heapStartMB', 'heapMB'])
        assert.equal(typeof row[k], 'number', `${row.band}: ${k}`);
    }
    assert.deepEqual(
      rows.map((r) => r.touched),
      [true, false, false, false],
    );
    assert.ok(await page.locator('#tour-banner').isHidden(), 'the banner is gone afterwards');

    const m = await page.evaluate(() => window.__observer.measurement());
    assert.ok(m.load.readyMs > 0 && m.load.firstFrameMs > 0, JSON.stringify(m.load));
    assert.ok(m.load.firstFrameMs >= m.load.readyMs - 60000);
    assert.ok(m.load.phases.length > 0 && m.load.phases.every((p) => p.atMs >= 0 && p.status));
    assert.equal(m.tour.length, 4);
    assert.equal(m.caches.patches.maxEntries, m.caches.screen.patchEntries);

    // Hands back: the wheel zooms again (away from the Measurements panel the tour opens).
    const z0 = (await camera()).zoom;
    await page.mouse.move(1100, 200);
    await page.mouse.wheel(0, -400);
    await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
    assert.notEqual((await camera()).zoom, z0);
    assert.deepEqual(errors, []);
  } finally {
    await page.close();
  }
});
