// Capture review material for the art proof.
//
//   node tests/capture.mjs <outdir>            screenshots of the village
//   node tests/capture.mjs <outdir> --clip     plus a smooth MP4 clip (needs ffmpeg)
//   node tests/capture.mjs <outdir> --depth    plus a contact sheet of the depth cases
//
// The clip is rendered deterministically: the display clock is stepped
// 1/24 s per frame and each frame is captured, so the result is smooth even
// when the browser renders slowly (software GL in a container).
import { chromium } from 'playwright';
import { mkdir, rm } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import { serve } from './serve.mjs';

const out = process.argv[2] ?? 'captures';
const wantClip = process.argv.includes('--clip');
const wantDepth = process.argv.includes('--depth');
await mkdir(out, { recursive: true });
const server = await serve(0);
const base = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch({
  args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'],
});

async function open(path, viewport) {
  const page = await browser.newPage({ viewport, deviceScaleFactor: 1 });
  page.on('pageerror', (e) => console.log('[pageerror]', e.message));
  await page.goto(`${base}/${path}`);
  await page.waitForFunction(() => window.__proof?.ready || window.__proofError, null, { timeout: 240000 });
  const err = await page.evaluate(() => window.__proofError ?? null);
  if (err) throw new Error(err);
  return page;
}

const page = await open('proof.html', { width: 1600, height: 900 });
await page.evaluate(() => window.__proof.setPaused(true));
const views = [
  ['01-village', 2, 3, 1.0, 40],
  ['02-overview', 2, 4, 0.5, 40],
  ['03-centre-close', -1, 2, 2.0, 52],
  ['04-field', 27, 19, 1.5, 61],
  ['05-plaza-hall', 3, -14, 1.5, 70],
  ['06-construction', 34, -6, 1.8, 80],
  ['07-woodland', -36, 16, 1.5, 90],
  ['08-workshop', -3, 11, 2.2, 95],
];
for (const [name, x, y, z, t] of views) {
  await page.evaluate(
    ([x, y, z, t]) => {
      window.__proof.setTime(t);
      window.__proof.view(x, y, z);
    },
    [x, y, z, t],
  );
  await page.waitForTimeout(250);
  await page.screenshot({ path: `${out}/${name}.png` });
}
// Selection + inspector of a carrier, and of an indoor person's building.
const ids = await page.evaluate(() => window.__proof.peopleIds());
await page.evaluate(
  ([id]) => {
    const p = window.__proof;
    p.setTime(120);
    p.select(id);
    const pos = p.positionOf(id);
    p.view(pos.x, pos.y, 1.8);
  },
  [ids[0]],
);
await page.waitForTimeout(700);
await page.screenshot({ path: `${out}/09-inspector-follow.png` });
const indoor = await page.evaluate(() => {
  const p = window.__proof;
  for (let t = 0; t < 400; t += 5) {
    p.setTime(t);
    const id = p.peopleIds().find((id) => {
      const s = p.positionOf(id);
      return s.inside && s.inside !== 'away';
    });
    if (id) return { id, t };
  }
  return null;
});
if (indoor) {
  await page.evaluate(({ id, t }) => {
    const p = window.__proof;
    p.setTime(t);
    p.select(id);
    p.follow(true);
    for (let i = 0; i < 90; i += 1) p.step(0);
  }, indoor);
  await page.evaluate(() => {
    const p = window.__proof;
    p.setZoom(1.6);
    for (let i = 0; i < 60; i += 1) p.step(1 / 30);
  });
  await page.waitForTimeout(700);
  await page.screenshot({ path: `${out}/10-indoor-highlight.png` });
}
console.log(JSON.stringify(await page.evaluate(() => window.__proof.stats())));

if (wantClip) {
  const frames = `${out}/frames`;
  await rm(frames, { recursive: true, force: true });
  await mkdir(frames, { recursive: true });
  const clip = await open('proof.html', { width: 1280, height: 720 });
  await clip.evaluate(() => window.__proof.setPaused(true));
  const fps = 24;
  let n = 0;
  const shot = async () => {
    await clip.screenshot({ path: `${frames}/f${String(n).padStart(5, '0')}.png` });
    n += 1;
  };
  const people = await clip.evaluate(() => window.__proof.peopleIds());
  // Shot 1: village centre at zoom 1, people going about their routines.
  await clip.evaluate(() => {
    window.__proof.setTime(150);
    window.__proof.view(1, 1, 1.0);
  });
  for (let i = 0; i < fps * 5; i += 1) {
    await clip.evaluate((dt) => window.__proof.step(dt), 1 / fps);
    await shot();
  }
  // Shot 2: smooth zoom into the caravan and storehouse.
  for (let i = 0; i < fps * 4; i += 1) {
    await clip.evaluate(
      ([dt, k]) => {
        const p = window.__proof;
        const e = k * k * (3 - 2 * k);
        p.view(1 + (3 - 1) * e, 1 + (0 - 1) * e, 1.0 + 1.1 * e);
        p.step(dt);
      },
      [1 / fps, i / (fps * 4 - 1)],
    );
    await shot();
  }
  // Shot 3: select a carrier and follow them.
  await clip.evaluate((id) => {
    const p = window.__proof;
    p.select(id);
    p.follow(true);
  }, people[1]);
  for (let i = 0; i < fps * 6; i += 1) {
    await clip.evaluate((dt) => window.__proof.step(dt), 1 / fps);
    if (i % 12 === 0) await clip.waitForTimeout(30);
    await shot();
  }
  await clip.evaluate(() => {
    const p = window.__proof;
    p.follow(false);
    p.select(null);
    p.view(27, 19, 1.6);
  });
  // Shot 4: the field.
  for (let i = 0; i < fps * 4; i += 1) {
    await clip.evaluate((dt) => window.__proof.step(dt), 1 / fps);
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
    '20',
    '-movflags',
    '+faststart',
    `${out}/art-proof-clip.mp4`,
  ]);
  if (r.status !== 0) console.log('ffmpeg failed:', String(r.stderr));
  else console.log(`clip: ${n} frames`);
  await rm(frames, { recursive: true, force: true });
}

if (wantDepth) {
  const d = await open('proof.html?scene=depth', { width: 1600, height: 1000 });
  const groups = await d.evaluate(() => {
    const p = window.__proof;
    p.setFootprints(true);
    const out = new Map();
    for (const c of p.cases) {
      const key = (c.front ?? c.hidden).split('-')[0];
      if (!out.has(key)) out.set(key, { key, names: [], ids: new Set() });
      out.get(key).names.push(c.name);
      for (const id of [c.front, c.back, c.hidden, c.building]) if (id) out.get(key).ids.add(id);
    }
    return [...out.values()].map((g) => {
      const pts = [...g.ids].map((id) => p.centreOf(id));
      const x = pts.reduce((a, q) => a + q.x, 0) / pts.length;
      const y = pts.reduce((a, q) => a + q.y, 0) / pts.length;
      return {
        key: g.key,
        title: g.names[0].replace(/ \(.*\)/, '').replace(/^L-shape: .*/, 'L-shaped workshop (two parts)'),
        x,
        y,
      };
    });
  });
  const images = [];
  for (const g of groups) {
    await d.evaluate(([x, y]) => window.__proof.view(x, y, 1.7), [g.x, g.y]);
    await d.waitForTimeout(150);
    const png = await d.screenshot({ clip: { x: 590, y: 300, width: 420, height: 360 } });
    images.push({ title: g.title, data: png.toString('base64') });
  }
  const sheet = await browser.newPage({ viewport: { width: 1320, height: 900 }, deviceScaleFactor: 1 });
  await sheet.setContent(`<html><body style="margin:0;background:#15181c;color:#ece6d8;font:13px system-ui">
    <div style="padding:10px 14px;font-size:15px">Depth-ordering test scene (pinned; PROTOTYPE ARTWORK). Footprints outlined: red = building, cyan = citizen, white = other.</div>
    <div style="display:grid;grid-template-columns:repeat(3,420px);gap:12px;padding:0 14px 14px">
    ${images.map((i) => `<figure style="margin:0"><img src="data:image/png;base64,${i.data}" width="420" height="360" style="display:block;border:1px solid #333"><figcaption style="padding:4px 2px">${i.title}</figcaption></figure>`).join('')}
    </div></body></html>`);
  const h = await sheet.evaluate(() => document.body.scrollHeight);
  await sheet.setViewportSize({ width: 1320, height: h });
  await sheet.screenshot({ path: `${out}/11-depth-cases.png` });
}

await browser.close();
server.close();
