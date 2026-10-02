// Four-band observer tests: spatial continuity, selection, follow, and the
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

test('zooming keeps the ground point under the cursor fixed through all four bands', async () => {
  const r = await page.evaluate(() => {
    const o = window.__observer;
    o.viewVillage(o.zoomForTilePx(120));
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
  assert.deepEqual(r.bands, ['atlas', 'regional', 'local', 'settlement']);
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

test('follow keeps the selected courier across a chunk boundary, days into the journey', async () => {
  const r = await page.evaluate(() => {
    const o = window.__observer;
    const id = o.courierId();
    const centre = [640, (720 - 72) / 2];
    const lagOf = () => {
      const p = o.positionOf(id);
      const at = o.planeToCanvas(p.x, p.y, 1);
      return Math.hypot(at.x - centre[0], at.y - centre[1]);
    };
    const key = (c) => `${c.cq},${c.cr}`;
    // At 40 s the courier is still on the village roads.
    o.setTime(40);
    o.select(id);
    o.follow(true);
    o.viewVillage(0.8);
    for (let i = 0; i < 20; i += 1) o.step(0.5);
    const first = key(o.cameraChunk());
    // Jump to five minutes before the courier reaches the far tile, days later.
    const plan = o.courierPlan();
    const arrive = plan.legs.at(-1).arriveT;
    o.setTime(arrive - 300);
    let maxLag = 0;
    for (let i = 0; i < 60; i += 1) {
      o.step(0.5);
      if (i >= 40) maxLag = Math.max(maxLag, lagOf());
    }
    const second = key(o.cameraChunk());
    // An hour after arriving the courier waits at the far tile's centre.
    o.setTime(arrive + 3600);
    const p = o.positionOf(id);
    const info = o.villageInfo();
    const far = info.courierTile;
    return {
      first,
      second,
      expected: [
        `${Math.floor(info.tile[0] / 8)},${Math.floor(info.tile[1] / 8)}`,
        `${Math.floor(far[0] / 8)},${Math.floor(far[1] / 8)}`,
      ],
      lastLeg: [plan.legs.at(-1).q, plan.legs.at(-1).r],
      far,
      days: arrive / 86400,
      selection: o.selection(),
      maxLag,
      waiting: o.activityOf(id),
      fromCentre: o.distanceToHexCentre(p.x, p.y, far[0], far[1]),
    };
  });
  assert.notEqual(r.expected[0], r.expected[1]);
  assert.equal(r.first, r.expected[0]);
  assert.equal(r.second, r.expected[1]);
  assert.deepEqual(r.lastLeg, r.far);
  assert.ok(r.days > 1, `the journey took only ${r.days} days`);
  assert.deepEqual(r.selection, { id: await page.evaluate(() => window.__observer.courierId()), following: true });
  assert.ok(r.maxLag < 200, `camera lagged ${r.maxLag}px behind the courier`);
  assert.ok(r.fromCentre < 30, `the waiting courier is ${r.fromCentre} m from the far tile centre`);
  assert.match(r.waiting, /^Waiting/);
});

test('ground patches and trees settle within their budgets while zooming into the village', async () => {
  const r = await page.evaluate(async () => {
    const o = window.__observer;
    o.select(null);
    const v = o.villageInfo().origin;
    const rows = [];
    // From where patches begin (a tile about 1,800 px wide) to the settlement band.
    for (let zoom = o.zoomForTilePx(1800); zoom < 1.2; zoom *= 2) {
      // Look 600 m east of the village, off its own ground.
      o.view(v.x + 600, v.y, zoom);
      const settled = await o.settle(1500);
      const p = o.patchStats();
      rows.push({
        zoom,
        complete: settled.complete,
        size: p.size,
        entries: p.entries,
        bytes: p.bytes,
        decor: o.stats().decorSprites,
      });
    }
    return rows;
  });
  let last = Infinity;
  for (const row of r) {
    assert.ok(row.complete, `zoom ${row.zoom} did not settle`);
    assert.ok(row.size <= last, `patch size grew to ${row.size} at zoom ${row.zoom}`);
    last = row.size;
    assert.ok(row.entries <= 320 && row.bytes <= 96e6, `patch cache over budget: ${JSON.stringify(row)}`);
  }
  assert.ok(r[0].size >= 1600, `first patches are ${r[0].size} m`);
  assert.ok(r.at(-1).size <= 50, `last patches are ${r.at(-1).size} m`);
  assert.ok(r.at(-1).decor > 0, 'bushes and trees appear up close');
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
