// Review material for the observer prototype (O1b: 25 km tiles).
//   node tests/capture-observer.mjs <outdir> [--clip]
// Zooms are given as screen pixels per tile where the tile is the subject, so
// the shots keep their framing if the tile size changes.
// The clip steps display time and zoom deterministically (24 fps), so it is
// smooth even under slow software rendering.
import { chromium } from 'playwright';
import { mkdir, rm } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import { serve } from './serve.mjs';

const out = process.argv[2] ?? 'captures';
const wantClip = process.argv.includes('--clip');
await mkdir(out, { recursive: true });
const server = await serve(0);
const base = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch({
  args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'],
});

async function open(viewport) {
  const page = await browser.newPage({ viewport, deviceScaleFactor: 1 });
  page.on('pageerror', (e) => console.log('[pageerror]', e.message));
  await page.goto(`${base}/index.html?citizens=400&quality=high`);
  await page.waitForFunction(() => window.__observer?.ready || window.__observerError, null, { timeout: 300000 });
  await page.evaluate(() => window.__observer.setPaused(true));
  return page;
}

const page = await open({ width: 1600, height: 900 });
const shots = [
  ['o1b-01-world-atlas', (o) => o.home()],
  ['o1b-02-atlas-capital', (o) => o.viewVillage(o.zoomForTilePx(60))],
  ['o1b-03-regional-tiles', (o) => o.viewVillage(o.zoomForTilePx(600))],
  ['o1b-04-regional-patches', (o) => o.viewVillage(0.002)],
  ['o1b-05-local-3km', (o) => o.viewVillage(0.02)],
  ['o1b-06-local-fields', (o) => o.viewVillage(0.07)],
  ['o1b-07-settlement', (o) => o.viewVillage(0.65)],
  ['o1b-08-settlement-close', (o) => o.viewVillage(1.3)],
];
for (const [name, fn] of shots) {
  await page.evaluate(`(${fn.toString()})(window.__observer)`);
  await page.evaluate(() => window.__observer.setTime(150));
  await page.evaluate(() => window.__observer.settle());
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${out}/${name}.png` });
}
// Inspector and following the courier: camped on the first night, days from home.
await page.evaluate(async () => {
  const o = window.__observer;
  const id = o.courierId();
  const camp = o.courierPlan().camps[0];
  o.setTime(camp.t0 + 600);
  o.select(id);
  o.follow(true);
  const p = o.positionOf(id);
  o.view(p.x, p.y, 0.9);
  for (let i = 0; i < 20; i += 1) o.step(0.5);
  await o.settle();
});
await page.waitForTimeout(700);
await page.screenshot({ path: `${out}/o1b-09-follow-courier-camp.png` });
await page.evaluate(async () => {
  const o = window.__observer;
  o.follow(false);
  const p = o.positionOf(o.courierId());
  o.view(p.x, p.y, 0.05);
  await o.settle();
});
await page.waitForTimeout(500);
await page.screenshot({ path: `${out}/o1b-10-courier-pin-local.png` });
await page.evaluate(() => document.getElementById('btn-measure').click());
await page.waitForTimeout(300);
await page.screenshot({ path: `${out}/o1b-11-measurements-panel.png` });
await page.close();

if (wantClip) {
  const frames = `${out}/frames`;
  await rm(frames, { recursive: true, force: true });
  await mkdir(frames, { recursive: true });
  const clip = await open({ width: 1280, height: 720 });
  const fps = 24;
  let n = 0;
  const shot = async () => {
    await clip.screenshot({ path: `${frames}/f${String(n).padStart(5, '0')}.png` });
    n += 1;
  };
  await clip.evaluate(async () => {
    const o = window.__observer;
    o.setTime(100);
    o.home();
    await o.settle();
  });
  for (let i = 0; i < fps * 2; i += 1) {
    await clip.evaluate((dt) => window.__observer.step(dt), 1 / fps);
    await shot();
  }
  // Zoom from the whole world into the village: exponential zoom, centre eased.
  const steps = fps * 9;
  const start = await clip.evaluate(() => window.__observer.camera());
  for (let i = 0; i < steps; i += 1) {
    const k = i / (steps - 1);
    await clip.evaluate(
      async ([k, i, dt, start]) => {
        const o = window.__observer;
        const v = o.villageCamera();
        const zoom = Math.exp(Math.log(start.zoom) + (Math.log(1.1) - Math.log(start.zoom)) * k);
        // The centre reaches the village well before the zoom finishes.
        const e = Math.min(1, k * 1.6);
        const ease = e * e * (3 - 2 * e);
        o.setCamera(start.x + (v.x - start.x) * ease, start.y + (v.y - start.y) * ease, zoom);
        o.step(dt);
        if (i % 4 === 0) await o.settle(25);
      },
      [k, i, 1 / fps, start],
    );
    await shot();
  }
  // Settle in the village, then follow a citizen.
  for (let i = 0; i < fps * 3; i += 1) {
    await clip.evaluate((dt) => window.__observer.step(dt), 1 / fps);
    await shot();
  }
  await clip.evaluate(() => {
    const o = window.__observer;
    o.select(o.peopleIds()[1]);
    o.follow(true);
  });
  for (let i = 0; i < fps * 5; i += 1) {
    await clip.evaluate((dt) => window.__observer.step(dt), 1 / fps);
    await shot();
  }
  await clip.close();
  const r = spawnSync('ffmpeg', [
    '-y',
    '-loglevel',
    'error',
    '-framerate',
    String(fps),
    '-i',
    `${frames}/f%05d.png`,
    '-c:v',
    'libx264',
    '-pix_fmt',
    'yuv420p',
    '-crf',
    '21',
    '-movflags',
    '+faststart',
    `${out}/o1b-zoom-through.mp4`,
  ]);
  if (r.status !== 0) console.log('ffmpeg failed:', String(r.stderr));
  else console.log(`clip: ${n} frames`);
  await rm(frames, { recursive: true, force: true });
}
await browser.close();
server.close();
