// Three-band observer tests: spatial continuity, selection, follow, and the
// independence of presentation positions from the camera.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { chromium } from 'playwright';
import { serve } from './serve.mjs';

let server;
let browser;
let page;

before(async () => {
  server = await serve(0);
  browser = await chromium.launch({
    args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'],
  });
  page = await browser.newPage({ viewport: { width: 1280, height: 720 }, deviceScaleFactor: 1 });
  page.on('pageerror', (e) => console.log('[pageerror]', e.message));
  await page.goto(`http://127.0.0.1:${server.address().port}/index.html`);
  await page.waitForFunction(() => window.__observer?.ready || window.__observerError, null, { timeout: 240000 });
  assert.equal(await page.evaluate(() => window.__observerError ?? null), null);
  await page.evaluate(() => window.__observer.setPaused(true));
});

after(async () => {
  await browser?.close();
  server?.close();
});

test('zooming keeps the ground point under the cursor fixed through all three bands', async () => {
  const r = await page.evaluate(() => {
    const o = window.__observer;
    o.viewVillage(0.02);
    const cx = 610;
    const cy = 330;
    const start = o.canvasToPlane(cx, cy);
    const bands = new Set([o.band()]);
    let worst = 0;
    while (o.camera().zoom < 2.2) {
      o.zoomAt(cx, cy, 1.18);
      bands.add(o.band());
      const p = o.canvasToPlane(cx, cy);
      worst = Math.max(worst, Math.hypot(p.x - start.x, p.y - start.y));
    }
    return { bands: [...bands], worst };
  });
  assert.deepEqual(r.bands, ['atlas', 'regional', 'settlement']);
  assert.ok(r.worst < 0.01, `cursor point drifted ${r.worst} m`);
});

test('every visible citizen can be selected by clicking them (cycling through overlaps), including beside a building', async () => {
  const r = await page.evaluate(() => {
    const o = window.__observer;
    o.setTime(120);
    o.viewVillage(1.4);
    let tried = 0;
    let reached = 0;
    let firstWasCitizen = 0;
    const misses = [];
    for (const id of o.peopleIds()) {
      const pt = o.personCanvasPoint(id);
      if (!pt || pt.x < 20 || pt.y < 20 || pt.x > 1260 || pt.y > 640) continue;
      tried += 1;
      o.pickAt(5, 5); // reset the crowd-cycling memory
      const seen = [];
      for (let k = 0; k < 10 && !seen.includes(id); k += 1) seen.push(o.pickAt(pt.x, pt.y));
      if (seen.includes(id)) reached += 1;
      else misses.push([id, seen]);
      if (seen[0] && seen[0].startsWith('sample-person')) firstWasCitizen += 1;
      if (tried >= 150) break;
    }
    // The smith works at the anvil right beside the workshop wing.
    const v = o.villageInfo().origin;
    const smith = o.peopleIds().find((id) => {
      const p = o.positionOf(id);
      return Math.hypot(p.x - v.x - -1.9, p.y - v.y - 14.35) < 0.2;
    });
    o.view(v.x - 2, v.y + 13, 1.8);
    const sp = o.personCanvasPoint(smith);
    o.pickAt(5, 5);
    const smithPicks = [];
    for (let k = 0; k < 10 && sp && !smithPicks.includes(smith); k += 1) smithPicks.push(o.pickAt(sp.x, sp.y));
    return { tried, reached, firstWasCitizen, misses: misses.slice(0, 5), smith, smithPicks };
  });
  assert.ok(r.tried >= 8, `only ${r.tried} citizens on screen`);
  assert.ok(r.reached / r.tried >= 0.97, `reached ${r.reached}/${r.tried}; e.g. ${JSON.stringify(r.misses)}`);
  assert.equal(r.firstWasCitizen, r.tried, 'a click on a citizen never selects a building first');
  assert.ok(r.smith, 'smith found');
  assert.ok(r.smithPicks.includes(r.smith), `smith not reachable: ${JSON.stringify(r.smithPicks)}`);
});

test('repeated clicks in a crowd cycle through the people there', async () => {
  const r = await page.evaluate(() => {
    const o = window.__observer;
    // Find a moment when two outdoor citizens stand within 0.6 m of each other.
    let pair = null;
    let t = 0;
    for (; t < 900 && !pair; t += 1) {
      o.setTime(t);
      const pts = o
        .peopleIds()
        .map((id) => [id, o.positionOf(id)])
        .filter(([, p]) => !p.inside);
      for (let i = 0; i < pts.length && !pair; i += 1)
        for (let j = i + 1; j < pts.length && !pair; j += 1)
          if (Math.hypot(pts[i][1].x - pts[j][1].x, pts[i][1].y - pts[j][1].y) < 0.6) pair = [pts[i][0], pts[j][0]];
    }
    if (!pair) return { pair };
    const p = o.positionOf(pair[0]);
    o.view(p.x, p.y, 1.2);
    const pt = o.personCanvasPoint(pair[0]);
    o.pickAt(5, 5);
    const ids = [];
    for (let i = 0; i < 4; i += 1) ids.push(o.pickAt(pt.x, pt.y));
    return { pair, ids, t };
  });
  assert.ok(r.pair, 'no two citizens ever stood close together');
  const distinct = new Set(r.ids.filter(Boolean));
  assert.ok(distinct.size >= 2, `clicks returned ${JSON.stringify(r.ids)}`);
  assert.ok(
    r.pair.every((id) => distinct.has(id)),
    `both crowded citizens reachable: ${JSON.stringify(r)}`,
  );
});

test('follow keeps the selected courier across a chunk boundary', async () => {
  const r = await page.evaluate(() => {
    const o = window.__observer;
    const id = o.courierId();
    // At 40 s the courier is on the village main road, walking out east.
    const t = 40;
    o.setTime(t);
    o.select(id);
    o.follow(true);
    o.viewVillage(0.8);
    const chunks = [];
    let maxLag = 0;
    for (let i = 0; i < 400; i += 1) {
      o.step(1);
      const c = o.cameraChunk();
      const key = `${c.cq},${c.cr}`;
      if (chunks[chunks.length - 1] !== key) chunks.push(key);
      const p = o.positionOf(id);
      const at = o.planeToCanvas(p.x, p.y, 1);
      if (i > 20) maxLag = Math.max(maxLag, Math.hypot(at.x - 640, at.y - (720 - 72) / 2));
      if (chunks.length >= 2 && i > 30) break;
    }
    return { chunks, selection: o.selection(), maxLag, personChunk: o.chunkOfPerson(id) };
  });
  assert.ok(r.chunks.length >= 2, `camera stayed in ${r.chunks}`);
  assert.equal(r.chunks[0], '2,3');
  assert.equal(r.chunks[1], '3,3');
  assert.deepEqual(r.selection, { id: await page.evaluate(() => window.__observer.courierId()), following: true });
  assert.ok(r.maxLag < 200, `camera lagged ${r.maxLag}px behind the courier`);
});

test('camera, zoom and follow never change presentation positions; pause freezes them', async () => {
  const r = await page.evaluate(async () => {
    const o = window.__observer;
    o.select(null);
    o.setTime(333);
    const snap = () => JSON.stringify(o.peopleIds().map((id) => o.positionOf(id)));
    const before = snap();
    o.view(0, 0, 0.02);
    o.zoomAt(300, 300, 3);
    o.viewVillage(2);
    o.select(o.peopleIds()[5]);
    o.follow(true);
    o.frame();
    o.follow(false);
    o.select(null);
    const afterCamera = snap();
    o.setPaused(true);
    const t0 = o.time();
    await new Promise((res) => setTimeout(res, 600));
    return { same: before === afterCamera, frozen: o.time() === t0 && snap() === afterCamera };
  });
  assert.ok(r.same, 'camera operations changed positions');
  assert.ok(r.frozen, 'paused display time moved');
});
