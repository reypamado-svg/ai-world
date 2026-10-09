// S7 C1: the art atlas fits a normal PC. At 5,000 citizens it is at most three
// 2048-pixel pages and 60 MB with the village ground, the pages are well
// filled, and every texture the renderer asks for through a day is there,
// with a civilization accent mask beside every citizen frame.
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

test('the atlas at 5,000 citizens is small, well filled and complete', async () => {
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 }, deviceScaleFactor: 1 });
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto(`http://127.0.0.1:${server.address().port}/index.html?citizens=5000`);
  await page.waitForFunction(() => window.__observer?.ready || window.__observerError, null, { timeout: 600000 });
  const stats = await page.evaluate(async () => {
    const o = window.__observer;
    o.setPaused(true);
    // Walk the day at the settlement zoom so every animation is asked for.
    for (let hour = 0; hour < 24; hour += 1) {
      o.setTime(hour * 3600);
      for (const zoom of [1.0, 0.6]) {
        o.viewVillage(zoom);
        for (let i = 0; i < 12; i += 1) o.step(1 / 8);
      }
    }
    const { ANIMATIONS } = await import('/src/render/art/paint/people.js');
    const { personFrameKey } = await import('/src/render/art/bake.js');
    const s = o.atlasStats();
    const keys = new Set(o.atlasKeys());
    const designs = [...keys].filter((k) => k.startsWith('person.')).map((k) => Number(k.split('.')[1]));
    const absent = [];
    for (const a of new Set(designs)) {
      for (const [anim, spec] of Object.entries(ANIMATIONS)) {
        for (const facing of spec.facings) {
          for (let i = 0; i < spec.frames; i += 1) {
            const key = personFrameKey(a, anim, facing, i);
            if (!keys.has(key)) absent.push(key);
            if (!keys.has(`${key}#mask`)) absent.push(`${key}#mask`);
          }
        }
      }
    }
    return { ...s, designs: new Set(designs).size, absent };
  });
  await page.close();
  assert.deepEqual(errors, []);
  assert.ok(stats.pages <= 3, `pages ${stats.pages}`);
  assert.equal(stats.pageSize, 2048);
  assert.equal(stats.mipmaps, false);
  const total = stats.bytes + stats.groundBytes;
  assert.ok(total <= 60e6, `atlas and ground ${(total / 1e6).toFixed(1)} MB`);
  assert.equal(stats.bytes, stats.pages * 2048 * 2048 * 4);
  assert.ok(stats.fill >= 0.6, `fill ${stats.fill}`);
  assert.ok(stats.designs >= 1);
  assert.deepEqual(stats.absent, []);
  assert.deepEqual(stats.missing, [], 'keys the renderer asked for and the atlas lacks');
});
