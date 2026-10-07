// Browser rendering measurements at 400, 2,000 and 5,000 SAMPLE citizens (or --citizens=a,b,c),
// or with --people=a,b,c at that many synthetic people (S7 crowd), centred on a busy ward.
// Usage: node tests/measure.mjs <outdir> [--quality=high] [--citizens=1000,5000] [--people=1000,100000]
//        node tests/measure.mjs <outdir> --run=tests/fixtures/run-town [--day=18] [--civ=0]
// With --run, a recorded run's export is measured (seen as civilization K with --civ=K), and a
// step to the next day, a jump to the first day and a perspective switch are timed too (O6).
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
const peopleArg = process.argv.find((a) => a.startsWith('--people='));
const citizens = (peopleArg ?? process.argv.find((a) => a.startsWith('--citizens=')) ?? '--citizens=400,2000,5000')
  .split('=')[1]
  .split(',')
  .map(Number);
const option = (name) => process.argv.find((a) => a.startsWith(`--${name}=`))?.split('=')[1] ?? null;
const run = option('run');
const runDay = option('day');
const runCiv = option('civ');
const cases = run ? [run] : citizens;
const label = run ? 'run' : peopleArg ? 'people' : 'citizens';
const query = (n) =>
  run
    ? `run=${n}${runDay !== null ? `&day=${runDay}` : ''}${runCiv !== null ? `&civ=${runCiv}` : ''}`
    : peopleArg
      ? `people=${n}&citizens=0`
      : `citizens=${n}`;
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
for (const n of cases) {
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 }, deviceScaleFactor: 1 });
  const t0 = Date.now();
  await page.goto(`http://127.0.0.1:${server.address().port}/index.html?${query(n)}&quality=${quality}`);
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
    await page.evaluate(
      ([z, people]) => {
        const o = window.__observer;
        o.setPaused(false);
        if (people)
          o.viewTour(z); // a crowd: the synthetic people, or a run's
        else if (z === 'home') o.home();
        else o.viewVillage(z === 'tile-3000' ? o.zoomForTilePx(3000) : z);
      },
      [zoom, !!peopleArg || !!run],
    );
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
    // A run: a step to the next recorded day (as playing does) and a jump to the first one, each
    // timed from asking to the day shown, then back.
    const dayLoads = run
      ? await page.evaluate(async () => {
          const o = window.__observer;
          const { days, day } = o.timelineInfo();
          const timed = async (d) => {
            const t = performance.now();
            await o.loadDay(d);
            return Number((performance.now() - t).toFixed(1));
          };
          const next = days[Math.min(days.length - 1, days.indexOf(day) + 1)];
          const dayStepMs = await timed(next);
          const dayJumpMs = await timed(days[0]);
          await o.loadDay(day);
          return { dayStepMs, dayJumpMs };
        })
      : null;
    rows.push({
      [label]: n,
      view,
      loadMs,
      ...m.stats,
      ...js,
      ...(run ? { ...dayLoads, perspectiveSwitchMs: m.perspectiveSwitchMs, civ: m.run?.civ ?? null } : {}),
    });
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
  label,
  ...(run ? ['civ', 'dayStepMs', 'dayJumpMs', 'perspectiveSwitchMs'] : []),
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
