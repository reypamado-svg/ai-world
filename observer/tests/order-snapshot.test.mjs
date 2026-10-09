// Guards refactors of the proof renderer: the draw order and sprite count for
// the pinned depth scene and for the village at a fixed time must not change.
// Regenerate deliberately with UPDATE_SNAPSHOT=1.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { chromium } from 'playwright';
import { serve } from './serve.mjs';

const fixture = new URL('./fixtures/order-snapshot.json', import.meta.url);
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

async function capture(query, setup) {
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 }, deviceScaleFactor: 1 });
  await page.goto(`http://127.0.0.1:${server.address().port}/proof.html${query}`);
  await page.waitForFunction(() => window.__proof?.ready || window.__proofError, null, { timeout: 180000 });
  const result = await page.evaluate(setup);
  await page.close();
  return result;
}

test('draw order matches the recorded snapshot', async () => {
  const depth = await capture('?scene=depth', () => {
    const p = window.__proof;
    p.setFootprints(false);
    p.frame();
    return { order: p.order(), drawnSprites: p.stats().drawnSprites };
  });
  const village = await capture('', () => {
    const p = window.__proof;
    p.setPaused(true);
    p.setTime(30);
    p.view(2, 2, 1);
    return { order: p.order(), drawnSprites: p.stats().drawnSprites };
  });
  const current = { depth, village };
  if (process.env.UPDATE_SNAPSHOT) {
    await writeFile(fixture, JSON.stringify(current, null, 1) + '\n');
    return;
  }
  const recorded = JSON.parse(await readFile(fixture, 'utf8'));
  assert.deepEqual(current, recorded);
});
