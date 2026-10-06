// O4 C3: the replay timeline on a recorded run's static export. Playing steps through the
// recorded days, one per display day, carrying the time over; it holds at the last day; the
// slider and ‹ › only ever show recorded days; each day shows the hash the run saved for it.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { chromium } from 'playwright';
import { serve } from './serve.mjs';

const DAY_S = 86400;
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

test('playing steps through the recorded days and holds at the last', async () => {
  const page = await browser.newPage({ viewport: { width: 1024, height: 640 }, deviceScaleFactor: 1 });
  try {
    const errors = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await page.goto(`http://127.0.0.1:${server.address().port}/index.html?run=tests/fixtures/run-small&day=0`);
    await page.waitForFunction(() => window.__observer?.ready || window.__observerError, null, { timeout: 600000 });
    assert.equal(await page.evaluate(() => window.__observerError ?? null), null);
    let info = await page.evaluate(() => {
      window.__observer.setPaused(true);
      return window.__observer.timelineInfo();
    });
    assert.deepEqual(info.days, [0, 1, 2, 3, 4, 5]);
    assert.equal(info.day, 0);
    assert.deepEqual(info.slider, { value: 0, max: 5 });
    const startT = info.dayT;
    const hash0 = await page.evaluate(() => window.__observer.runInfo().hash);
    assert.match(hash0, /^[0-9a-f]{64}$/);
    assert.match(await page.locator('#day-label').getAttribute('title'), new RegExp(hash0));

    // Not yet a whole day: still day 0.
    await page.evaluate((s) => window.__observer.play(s), DAY_S - 10 - startT);
    assert.equal((await page.evaluate(() => window.__observer.timelineInfo())).day, 0);
    // Past the end of the day: the next recorded day, with the extra time carried over.
    await page.evaluate((s) => window.__observer.play(s), 40);
    await page.waitForFunction(() => window.__observer.timelineInfo().day === 1);
    info = await page.evaluate(() => window.__observer.timelineInfo());
    assert.ok(Math.abs(info.dayT - 30) < 1e-6, String(info.dayT));
    assert.equal(info.slider.value, 1);
    const hash1 = await page.evaluate(() => window.__observer.runInfo().hash);
    assert.notEqual(hash1, hash0);

    // The slider skips to a recorded day.
    await page.evaluate(() => {
      const slider = document.getElementById('day-slider');
      slider.value = '4';
      slider.dispatchEvent(new Event('input'));
    });
    await page.waitForFunction(() => window.__observer.timelineInfo().day === 4);
    // Day 4 → 5, then the end of the recording: it holds on day 5.
    for (let k = 0; k < 3; k += 1) await page.evaluate(() => window.__observer.advanceDay());
    info = await page.evaluate(() => window.__observer.timelineInfo());
    assert.equal(info.day, 5);
    assert.equal(info.waiting, true);
    assert.equal(info.end, true);
    assert.deepEqual(errors, []);
  } finally {
    await page.close();
  }
});
