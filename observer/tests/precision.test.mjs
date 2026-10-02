// Local-origin rule: anything painted or tessellated in float32 is drawn
// relative to a nearby anchor, so far from the world origin nothing is
// quantised: which nearby anchor is used makes no visible difference.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { chromium } from 'playwright';
import { serve } from './serve.mjs';

let server;
let browser;
let base;

before(async () => {
  server = await serve(0);
  base = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({
    args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'],
  });
});

after(async () => {
  await browser?.close();
  server?.close();
});

test('a far tile paints the same whichever nearby anchor its texture uses', async () => {
  const page = await browser.newPage();
  await page.goto(`${base}/tests/fixtures/blank.html`);
  const r = await page.evaluate(async () => {
    const { paintHexDetail } = await import('/src/render/hex-detail.js');
    const { hexCentre, sharedCorners } = await import('/src/world/hex.js');
    const { riverPoint } = await import('/src/world/rivers.js');
    const R = 25000 / Math.sqrt(3);
    const [q, r] = [99, 99];
    const tile = {
      q,
      r,
      terrain: 1,
      elevation: 300,
      moisture: 400,
      soil: 600,
      timber: 700,
      stone: 650,
      temperature: 400,
    };
    const [p1, p2] = sharedCorners(q, r, q + 1, r, R);
    const edge = { p1, p2, flow: 6, deep: false, widthM: 104, key: `${q},${r},${q + 1},${r}` };
    // A 256 px window on the river, 226 m across, at a scale where float32 world coordinates would show.
    const s = 0.05;
    const mid = riverPoint(edge, 0.5);
    const paint = (origin) => {
      const canvas = document.createElement('canvas');
      canvas.width = 256;
      canvas.height = 256;
      const ctx = canvas.getContext('2d');
      // Screen offset of the window centre from the anchor, then the anchor-relative transform.
      const dx = mid.x - origin.x;
      const dy = mid.y - origin.y;
      const cx = (dx - dy) * 16;
      const cy = ((dx + dy) * 16) / 2;
      ctx.setTransform(s, 0, 0, s, 128 - cx * s, 128 - cy * s);
      paintHexDetail(ctx, { ...tile, river: true, lake: false }, R, s, { origin, riverEdges: [edge] });
      return ctx.getImageData(0, 0, 256, 256).data;
    };
    const centre = hexCentre(q, r, R);
    const a = paint(centre);
    const b = paint({ x: centre.x + 3000, y: centre.y - 2000 });
    // The absolute world origin as the anchor: what the rule forbids (reported, not asserted).
    const c = paint({ x: 0, y: 0 });
    const diff = (u, w) => {
      let n = 0;
      for (let i = 0; i < u.length; i += 4)
        if (Math.abs(u[i] - w[i]) + Math.abs(u[i + 1] - w[i + 1]) + Math.abs(u[i + 2] - w[i + 2]) > 6) n += 1;
      return n;
    };
    let water = 0;
    for (let i = 0; i < a.length; i += 4) if (a[i + 2] > a[i] + 40 && a[i + 2] > a[i + 1]) water += 1;
    return { anchored: diff(a, b), absolute: diff(a, c), water };
  });
  await page.close();
  console.log(`pixels differing: nearby anchors ${r.anchored}, world-origin anchor ${r.absolute}`);
  assert.ok(r.water > 500, `the river should fill part of the window (${r.water} water pixels)`);
  assert.ok(r.anchored <= 20, `${r.anchored} pixels differ between two nearby anchors`);
});

test('village dots and the selection pin are drawn relative to the village, even 50 km out', async () => {
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 }, deviceScaleFactor: 1 });
  page.on('pageerror', (e) => console.log('[pageerror]', e.message));
  await page.goto(`${base}/index.html`);
  await page.waitForFunction(() => window.__observer?.ready || window.__observerError, null, { timeout: 240000 });
  const r = await page.evaluate(() => {
    const o = window.__observer;
    o.setPaused(true);
    const id = o.courierId();
    o.setTime(o.courierPlan().legs.at(-1).arriveT);
    o.select(id);
    const p = o.positionOf(id);
    o.view(p.x, p.y, 0.1);
    return { extent: o.graphicsExtent(), away: o.distanceToHexCentre(p.x, p.y, ...o.villageInfo().tile) };
  });
  await page.close();
  assert.ok(r.away > 40000, `the courier is only ${r.away} m from home`);
  assert.ok(r.extent.pin > 0, 'the pin is drawn');
  // 50 km is about 1.1e6 screen px at zoom 1; absolute coordinates here would be near 8e7.
  assert.ok(r.extent.max < 2e6, `graphics coordinates reach ${r.extent.max}`);
});
