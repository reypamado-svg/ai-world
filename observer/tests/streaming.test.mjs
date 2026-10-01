// Large-world streaming test (R4), separate from citizen count.
// A TEST-ONLY synthetic 4096 x 4096 world with 0-400 ms request latency:
// rapid camera jumps, stale requests, bounded CPU/GPU caches, no leaks.
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
  await page.goto(`http://127.0.0.1:${server.address().port}/index.html?source=synthetic&latency=400`);
  await page.waitForFunction(() => window.__observer?.ready || window.__observerError, null, { timeout: 120000 });
  assert.equal(await page.evaluate(() => window.__observerError ?? null), null);
});

after(async () => {
  await browser?.close();
  server?.close();
});

test('rapid jumps across a huge world stay within budgets and leave nothing behind', async () => {
  const result = await page.evaluate(async () => {
    const o = window.__observer;
    const start = { q: 2048, r: 2048, zoom: 0.03 };
    // Let the canvas reach its final size before taking the baseline.
    await new Promise((r) => setTimeout(r, 500));
    o.viewHex(start.q, start.r, start.zoom);
    const first = await o.settle(800);
    o.releaseHidden();
    o.frame();
    const baseline = { ...o.textureCount(), visible: o.terrainStats().visibleChunks };
    const minZoom = o.camera().minZoom;
    // 60 deterministic jumps in about 5 seconds.
    let seed = 12345;
    const rnd = () => (seed = (seed * 1103515245 + 12345) >>> 0) / 4294967296;
    const samples = [];
    const t0 = performance.now();
    for (let i = 0; i < 60; i += 1) {
      const zoom = minZoom + rnd() * (0.12 - minZoom);
      o.viewHex(Math.floor(rnd() * 4096), Math.floor(rnd() * 4096), zoom);
      const s = o.terrainStats();
      samples.push({ inFlight: s.loader.inFlight, cpu: s.cpu.bytes, gpu: s.gpu.bytes, gpuEntries: s.gpu.entries });
      await new Promise((r) => setTimeout(r, 80));
      const s2 = o.terrainStats();
      samples.push({ inFlight: s2.loader.inFlight, cpu: s2.cpu.bytes, gpu: s2.gpu.bytes, gpuEntries: s2.gpu.entries });
    }
    const jumpSeconds = (performance.now() - t0) / 1000;
    const settled = await o.settle(800);
    const after = o.terrainStats();
    // Far corner: positions are large; rendering must still settle.
    o.viewHex(4095, 4095, 0.05);
    const corner = await o.settle(800);
    // Return to the start view and release everything off screen.
    o.viewHex(start.q, start.r, start.zoom);
    const back = await o.settle(800);
    o.releaseHidden();
    o.frame();
    const end = { ...o.textureCount(), visible: o.terrainStats().visibleChunks };
    return { first, baseline, samples, jumpSeconds, settled, after, corner, back, end, final: o.terrainStats() };
  });
  assert.ok(result.first.complete, 'initial view settles');
  assert.ok(result.jumpSeconds < 8, `jumps took ${result.jumpSeconds}s`);
  const { cpu, gpu, loader } = result.after;
  for (const s of result.samples) {
    assert.ok(s.inFlight <= 4, `in-flight ${s.inFlight} exceeds 4`);
    assert.ok(s.cpu <= cpu.maxBytes, `CPU cache ${s.cpu} over budget`);
    assert.ok(s.gpu <= gpu.maxBytes, `GPU cache ${s.gpu} over budget`);
    assert.ok(s.gpuEntries <= gpu.maxEntries, `GPU entries ${s.gpuEntries} over budget`);
  }
  assert.ok(loader.peakInFlight <= 4);
  assert.ok(
    cpu.peakBytes <= cpu.maxBytes && gpu.peakBytes <= gpu.maxBytes && gpu.peakEntries <= gpu.maxEntries,
    'peaks within budget',
  );
  assert.ok(loader.aborted + loader.discardedStale > 0, 'stale requests were aborted or discarded');
  assert.equal(loader.failed, 0);
  assert.ok(result.settled.complete && result.settled.frames <= 400, `settled after ${result.settled.frames} frames`);
  assert.ok(result.corner.complete, 'far corner settles');
  assert.ok(result.back.complete, 'start view settles again');
  assert.equal(result.end.visible, result.baseline.visible);
  assert.equal(result.end.terrain, result.baseline.terrain, 'terrain textures back to baseline');
  if (result.baseline.pixiManaged !== null)
    assert.equal(result.end.pixiManaged, result.baseline.pixiManaged, 'GPU textures back to baseline');
  assert.equal(result.final.liveTextures, result.final.gpu.entries, 'every live texture is accounted for in the cache');
});
