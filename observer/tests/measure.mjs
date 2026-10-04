// Browser rendering measurements at 400, 2,000 and 5,000 SAMPLE citizens (or --citizens=a,b,c).
// Usage: node tests/measure.mjs <outdir> [--quality=high] [--citizens=1000,5000]
//
// In this container Chromium renders with software GL, so frame times are NOT
// representative of a real GPU. The JS update time (`jsUpdateMs`, our own frame()
// over 60 timed steps with the clock paused) and the memory figures are. On your
// own machine, open index.html?citizens=N and use the Measurements panel instead.
import { chromium } from 'playwright';
import { mkdir, writeFile } from 'node:fs/promises';
import { serve } from './serve.mjs';

const out = process.argv[2] ?? 'captures';
const quality = (process.argv.find((a) => a.startsWith('--quality=')) ?? '--quality=high').split('=')[1];
const citizens = (process.argv.find((a) => a.startsWith('--citizens=')) ?? '--citizens=400,2000,5000')
  .split('=')[1]
  .split(',')
  .map(Number);
await mkdir(out, { recursive: true });
const server = await serve(0);
const browser = await chromium.launch({
  args: [
    '--use-angle=swiftshader',
    '--enable-unsafe-swiftshader',
    '--ignore-gpu-blocklist',
    '--enable-precise-memory-info',
  ],
});
const rows = [];
const atlases = [];
for (const n of citizens) {
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 }, deviceScaleFactor: 1 });
  const t0 = Date.now();
  await page.goto(`http://127.0.0.1:${server.address().port}/index.html?citizens=${n}&quality=${quality}`);
  await page.waitForFunction(() => window.__observer?.ready || window.__observerError, null, { timeout: 600000 });
  const loadMs = Date.now() - t0;
  atlases.push({ citizens: n, ...(await page.evaluate(() => window.__observer.atlasStats())) });
  // Zooms: settlement 1.0, local 0.1, regional with a tile 3,000 px wide, atlas the whole world.
  for (const [view, zoom] of [
    ['settlement', 1.0],
    ['local', 0.1],
    ['regional', 'tile-3000'],
    ['atlas', 'home'],
  ]) {
    await page.evaluate((z) => {
      const o = window.__observer;
      o.setPaused(false);
      if (z === 'home') o.home();
      else o.viewVillage(z === 'tile-3000' ? o.zoomForTilePx(3000) : z);
    }, zoom);
    await page.evaluate(() => window.__observer.settle());
    await page.waitForTimeout(6000); // let the ticker run in real time
    const m = await page.evaluate(() => window.__observer.measurement());
    // Our own per-frame work, timed with the clock paused: comparable across machines.
    const js = await page.evaluate(() => {
      const o = window.__observer;
      o.setPaused(true);
      const times = [];
      for (let i = 0; i < 60; i += 1) {
        const t = performance.now();
        o.step(1 / 60);
        times.push(performance.now() - t);
      }
      times.sort((a, b) => a - b);
      return {
        jsUpdateMs: Number((times.reduce((a, b) => a + b, 0) / times.length).toFixed(2)),
        jsUpdateP95: Number(times[Math.floor(times.length * 0.95)].toFixed(2)),
      };
    });
    rows.push({ citizens: n, view, loadMs, ...m.stats, ...js });
    console.log(
      n,
      view,
      `frame ${m.stats.frameMsAvg} ms (p95 ${m.stats.frameMsP95})`,
      `update ${m.stats.updateMsAvg} ms`,
      `visible ${m.stats.visible}`,
    );
  }
  await page.close();
}
await browser.close();
server.close();
const cols = [
  'citizens',
  'view',
  'frameMsAvg',
  'frameMsP95',
  'updateMsAvg',
  'updateMsP95',
  'jsUpdateMs',
  'jsUpdateP95',
  'loadMs',
  'worldPopulation',
  'resident',
  'indoor',
  'away',
  'outdoor',
  'visible',
  'visibleFull',
  'visibleSimplified',
  'drawnSprites',
  'loadedChunks',
  'gpuBytes',
  'patchSize',
  'visiblePatches',
  'patchBytes',
  'patchBakeMsAvg',
  'decorSprites',
  'atlasBytes',
  'jsHeapBytes',
  'quality',
];
const md = [
  `Browser measurements (software GL in a container: NOT representative of a real GPU). Quality: ${quality}.`,
  '',
  `| ${cols.join(' | ')} |`,
  `| ${cols.map(() => '---').join(' | ')} |`,
  ...rows.map(
    (r) =>
      `| ${cols.map((c) => (typeof r[c] === 'number' && r[c] > 1e5 ? `${(r[c] / 1e6).toFixed(1)} MB` : r[c])).join(' | ')} |`,
  ),
].join('\n');
await writeFile(`${out}/measurements.json`, JSON.stringify({ rows, atlases }, null, 1));
console.log('atlas', JSON.stringify(atlases, null, 1));
await writeFile(`${out}/measurements.md`, md + '\n');
console.log(md);
