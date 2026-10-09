// S7 C3: the crowd layer at 5,000 and 50,000 synthetic people. Counts stay
// consistent in every band, full sprites stay within the crowd budget,
// people can be picked exactly (full sprites and still particles alike),
// anyone can be followed, a local-band click lists a cell's people, and the
// memory and per-frame cost stay small.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { chromium } from 'playwright';
import { serve } from './serve.mjs';

let server;
let browser;

before(async () => {
  server = await serve(0);
  browser = await chromium.launch({
    // --expose-gc: the memory test collects garbage before reading the heap, so it measures
    // what is kept rather than what has not been collected yet.
    args: [
      '--use-angle=swiftshader',
      '--enable-unsafe-swiftshader',
      '--ignore-gpu-blocklist',
      '--enable-precise-memory-info',
      '--js-flags=--expose-gc',
    ],
  });
});

after(async () => {
  await browser?.close();
  server?.close();
});

/** Open the observer; `use(page, errors)` runs and the page is always closed (an open page keeps rendering). */
async function withPage(query, use) {
  const { page, errors } = await open(query);
  try {
    return await use(page, errors);
  } finally {
    await page.close();
  }
}

async function open(query) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 }, deviceScaleFactor: 1 });
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto(`http://127.0.0.1:${server.address().port}/index.html${query}`);
  await page.waitForFunction(() => window.__observer?.ready || window.__observerError, null, { timeout: 600000 });
  assert.equal(await page.evaluate(() => window.__observerError ?? null), null);
  await page.evaluate(() => window.__observer.setPaused(true));
  return { page, errors };
}

/** Rows of the synthetic frame are ids 1..N, settlement by settlement. */
function idOf(n) {
  return `person:${String(n).padStart(10, '0')}`;
}

for (const n of [5000, 50000]) {
  test(`counts and budgets are consistent at ${n.toLocaleString('en')} people`, async () => {
    await withPage(`?people=${n}&citizens=0&quality=low`, async (page, errors) => {
      const rows = await page.evaluate(() => {
        const o = window.__observer;
        const out = [];
        for (const [t, zoom] of [
          [0, 1.0],
          [600, 0.6],
          [3600, 0.2],
          [5400, 0.05],
          [7200, 0.004],
        ]) {
          o.setTime(t);
          o.viewPerson(o.crowdBusiest(), zoom);
          out.push({ t, zoom, budget: o.crowdBudget(), ...o.crowdCounts() });
        }
        return out;
      });
      for (const s of rows) {
        const where = `t=${s.t} zoom=${s.zoom}`;
        assert.equal(s.worldPopulation, n, where);
        assert.equal(s.indoor + s.outdoor, n, where);
        assert.ok(s.indoor > 0 && s.outdoor > 0, `${where}: ${s.indoor} indoor, ${s.outdoor} outdoor`);
        assert.equal(s.visible, s.visibleFull + s.visibleSimplified, where);
        assert.ok(s.visibleFull <= s.budget + 1, `${where}: ${s.visibleFull} full sprites, budget ${s.budget}`);
        if (s.zoom >= 0.5) assert.ok(s.visible > 0, `${where}: nobody in view`);
        if (s.zoom < 0.5 && s.zoom >= 0.02) assert.ok(s.dots > 0, `${where}: no dots`);
        if (s.zoom < 0.5) assert.equal(s.visibleFull, 0, where);
      }
      assert.deepEqual(errors, []);
    });
  });
}

test('200 random people of 50,000 can each be picked, and anyone followed', async () => {
  await withPage('?people=50000&citizens=0&quality=low', async (page, errors) => {
    const result = await page.evaluate(
      ({ ids }) => {
        const o = window.__observer;
        const misses = [];
        let picked = 0;
        let particles = 0;
        o.setTime(1800); // 08:30: most are out of doors
        // A small budget, so people off centre are drawn as still particles.
        o.setCrowdBudget(12);
        for (const [k, id] of ids.entries()) {
          const s = o.crowdState(id);
          if (s.inside) continue;
          o.viewPerson(id, 1.0, k % 2 ? 0 : 300);
          const pt = o.personCanvasPoint(id);
          if (!pt) {
            misses.push(`${id}: not drawn`);
            continue;
          }
          if (o.crowdDrawnAs(id) === 'particle') particles += 1;
          let got = null;
          for (let c = 0; c < 16 && got !== id; c += 1) got = o.pickAt(pt.x, pt.y);
          if (got === id) picked += 1;
          else misses.push(`${id}: picked ${got}`);
        }
        o.setCrowdBudget(null);
        // Follow a walker for a minute of display time.
        o.select(null);
        let walker = null;
        for (const id of ids) if (o.crowdState(id).moving) walker = walker ?? id;
        o.viewPerson(walker, 1.0);
        o.select(walker);
        o.follow(true);
        let worst = 0;
        for (let i = 0; i < 120; i += 1) {
          o.step(0.5);
          const p = o.personCanvasPoint(walker) ?? o.personPinPoint(walker);
          const c = o.canvasCentre();
          worst = Math.max(worst, Math.hypot(p.x - c.x, p.y - c.y));
        }
        return { picked, particles, misses, worst, walker };
      },
      { ids: Array.from({ length: 200 }, (_, k) => idOf(1 + ((k * 7919) % 50000))) },
    );
    assert.deepEqual(result.misses, []);
    assert.ok(result.picked > 100, `${result.picked} picked`);
    assert.ok(result.particles > 0, 'some were picked as still particles');
    assert.ok(result.walker, 'someone was walking');
    assert.ok(result.worst <= 200, `followed person ${result.worst.toFixed(0)} px from the centre`);
    assert.deepEqual(errors, []);
  });
});

test('a local-band click lists the people in a cell', async () => {
  await withPage('?people=50000&citizens=0', async (page) => {
    const r = await page.evaluate(() => {
      const o = window.__observer;
      o.setTime(1800);
      o.viewPerson(o.crowdBusiest(), 0.2);
      const pt = o.crowdDotPoint();
      const list = o.pickList(pt.x, pt.y);
      return { list, shown: !document.getElementById('inspector').hidden };
    });
    assert.ok(r.list && r.list.length > 0, 'a list of people');
    assert.ok(r.shown, 'the inspector shows the list');
  });
});

test('50,000 people cost little memory and little time a frame', async () => {
  const measure = (query) =>
    withPage(query, (page) =>
      page.evaluate(async () => {
        const o = window.__observer;
        await o.settle(60);
        const times = {};
        for (const [band, zoom] of [
          ['settlement', 1.0],
          ['local', 0.1],
        ]) {
          if (o.populationStats()) o.viewPerson(o.crowdBusiest(), zoom);
          else o.viewVillage(zoom);
          const ts = [];
          for (let i = 0; i < 60; i += 1) {
            const t = performance.now();
            o.step(1 / 60);
            ts.push(performance.now() - t);
          }
          times[band] = ts.reduce((a, b) => a + b, 0) / ts.length;
        }
        for (let i = 0; i < 3; i += 1) {
          window.gc();
          await new Promise((r) => setTimeout(r, 50));
        }
        return { heap: performance.memory.usedJSHeapSize, times };
      }),
    );
  const base = await measure('?citizens=0');
  const big = await measure('?people=50000&citizens=0');
  const perPerson = (big.heap - base.heap) / 50000;
  console.log(`heap ${perPerson.toFixed(0)} bytes a person; update ${JSON.stringify(big.times)}`);
  assert.ok(perPerson <= 400, `${perPerson.toFixed(0)} bytes a person`);
  for (const [band, ms] of Object.entries(big.times)) assert.ok(ms <= 25, `${band}: ${ms.toFixed(1)} ms`);
});
