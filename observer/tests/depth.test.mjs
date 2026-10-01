// Depth-ordering visual tests on the pinned ?scene=depth page (R3).
// Each case is checked twice: the draw order from the depth sorter, and the
// actual pixel at a point where both sprites are opaque (flat-tint mode).
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { chromium } from 'playwright';
import { mkdir } from 'node:fs/promises';
import { serve } from './serve.mjs';

let server;
let browser;
let page;
const shots = process.env.OBSERVER_SHOTS;

before(async () => {
  server = await serve(0);
  browser = await chromium.launch({
    args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'],
  });
  page = await browser.newPage({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 1 });
  page.on('pageerror', (e) => console.log('[pageerror]', e.message));
  await page.goto(`http://127.0.0.1:${server.address().port}/proof.html?scene=depth`);
  await page.waitForFunction(() => window.__proof?.ready || window.__proofError, null, { timeout: 180000 });
  assert.equal(await page.evaluate(() => window.__proofError ?? null), null);
  if (shots) {
    await mkdir(shots, { recursive: true });
    await page.screenshot({ path: `${shots}/depth-scene.png` });
  }
});

after(async () => {
  await browser?.close();
  server?.close();
});

test('every depth case is ordered and rendered correctly', async () => {
  const cases = await page.evaluate(() => window.__proof.cases);
  assert.ok(cases.length >= 12, 'expected the full set of depth cases');
  const failures = [];
  for (const c of cases) {
    if (c.hidden) continue;
    // Frame the case so both objects are large on screen.
    const result = await page.evaluate((c) => {
      const p = window.__proof;
      p.setFootprints(false);
      p.setFlat(false);
      const order = p.order();
      const front = order.indexOf(c.front);
      const back = order.indexOf(c.back);
      p.setFlat(true);
      const probe = p.overlapProbe(c.front, c.back);
      const top = probe ? p.probe(probe.x, probe.y) : null;
      p.setFlat(false);
      return { front, back, probe, top };
    }, c);
    if (!(result.front > result.back && result.back >= 0))
      failures.push(`${c.name}: draw order front=${result.front} back=${result.back}`);
    if (!result.probe) failures.push(`${c.name}: sprites do not overlap on screen, so the case tests nothing`);
    else if (result.top !== c.front) failures.push(`${c.name}: pixel shows ${result.top}, expected ${c.front}`);
  }
  assert.deepEqual(failures, []);
});

test('a citizen who went through a door is not drawn over the building', async () => {
  const cases = await page.evaluate(() => window.__proof.cases.filter((c) => c.hidden));
  assert.ok(cases.length >= 1);
  for (const c of cases) {
    const r = await page.evaluate((c) => {
      const p = window.__proof;
      const pos = p.positionOf(c.hidden);
      p.setFlat(true);
      const at = p.worldToCanvas(pos.x, pos.y, 1.0);
      const top = p.probe(at.x, at.y);
      p.setFlat(false);
      return { drawn: p.isDrawn(c.hidden), inside: pos.inside, top };
    }, c);
    assert.equal(r.drawn, false);
    assert.equal(r.inside, c.building);
    assert.notEqual(r.top, c.hidden);
  }
});

test('selecting an indoor citizen highlights their building instead of drawing them', async () => {
  const r = await page.evaluate(() => {
    const p = window.__proof;
    p.select('d4-p');
    const s = p.selection();
    p.select(null);
    return s;
  });
  assert.equal(r.inside, 'd4-house');
  assert.equal(r.drawn, false);
  assert.equal(r.highlighted, true);
});

test('no depth cycles and every asset meets the footprint contract', async () => {
  const r = await page.evaluate(() => ({
    stats: window.__proof.stats(),
    bad: window.__proof.contract.filter((c) => !c.ok),
  }));
  assert.equal(r.stats.depthCycles, 0);
  assert.deepEqual(r.bad, []);
});

test('negative control: naive centre-depth ordering fails the long-storehouse case', async () => {
  const p2 = await browser.newPage({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 1 });
  await p2.goto(`http://127.0.0.1:${server.address().port}/proof.html?scene=depth&depth=centre`);
  await p2.waitForFunction(() => window.__proof?.ready, null, { timeout: 180000 });
  const top = await p2.evaluate(() => {
    const p = window.__proof;
    p.setFootprints(false);
    const c = p.cases.find((c) => c.front === 'd6-store');
    p.setFlat(true);
    const probe = p.overlapProbe(c.front, c.back);
    return probe ? p.probe(probe.x, probe.y) : null;
  });
  await p2.close();
  assert.equal(top, 'd6-p', 'the centre-depth sorter should wrongly draw the citizen over the storehouse');
});
