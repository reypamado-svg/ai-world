// O6: every control of a recorded run stays on screen at common window sizes. The day stepper,
// the runner chip and the perspective picker live in the run bar under the top bar, which wraps,
// rather than at the end of the one-row top bar, where they ran off its right edge; the side
// panels start below the run bar, so it never covers them.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { chromium } from 'playwright';
import { serve } from './serve.mjs';

let server;
let browser;

before(async () => {
  server = await serve(0);
  browser = await chromium.launch({
    args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'],
  });
});

after(async () => {
  await browser?.close();
  server?.close();
});

const CONTROLS = [
  '#btn-day-prev',
  '#day-label',
  '#btn-day-next',
  '#day-slider',
  '#runner-chip',
  '#perspective-chip',
  '#perspective',
  '#perspective-asof',
  '#btn-pause',
];

for (const [width, height] of [
  [1440, 900],
  [1280, 720],
]) {
  test(`a run's controls are all on screen at ${width} × ${height}`, async () => {
    const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 1 });
    try {
      const errors = [];
      page.on('pageerror', (e) => errors.push(e.message));
      await page.goto(`http://127.0.0.1:${server.address().port}/index.html?run=tests/fixtures/run-town&day=18`);
      await page.waitForFunction(() => window.__observer?.ready || window.__observerError, null, { timeout: 600000 });
      assert.equal(await page.evaluate(() => window.__observerError ?? null), null);
      const boxes = await page.evaluate((selectors) => {
        // A run started by the observer shows its chip here; a static export has none, so show
        // one as long as a busy runner's.
        const chip = document.getElementById('runner-chip');
        chip.hidden = false;
        chip.textContent = 'RUNNER · running · day 365 of 365 · 3 ahead';
        return selectors.map((selector) => {
          const el = document.querySelector(selector);
          const r = el.getBoundingClientRect();
          // Clipped by an ancestor that hides its overflow?
          let clipped = false;
          for (let p = el.parentElement; p; p = p.parentElement) {
            const style = getComputedStyle(p);
            if (style.overflow === 'hidden' || style.overflowX === 'hidden') {
              const b = p.getBoundingClientRect();
              if (r.right > b.right + 0.5 || r.left < b.left - 0.5) clipped = true;
            }
          }
          return {
            selector,
            left: r.left,
            right: r.right,
            top: r.top,
            bottom: r.bottom,
            width: r.width,
            visible: getComputedStyle(el).display !== 'none' && r.width > 0,
            clipped,
          };
        });
      }, CONTROLS);
      // The side panels and the tour banner start below the run bar, however many rows it has.
      const panels = await page.evaluate(async () => {
        await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        const bar = document.getElementById('run-bar').getBoundingClientRect().bottom;
        const tops = ['inspector', 'chronicle', 'council', 'tour-banner'].map((id) => {
          const el = document.getElementById(id);
          return { id, top: parseFloat(getComputedStyle(el).top) };
        });
        return { bar, tops };
      });
      for (const { id, top } of panels.tops) {
        assert.ok(top >= panels.bar, `#${id} starts at ${top} px, below the run bar's ${panels.bar} px`);
      }
      for (const box of boxes) {
        assert.ok(box.visible, `${box.selector} is shown`);
        assert.ok(
          box.left >= 0 && box.right <= width,
          `${box.selector} lies within the window: ${JSON.stringify(box)}`,
        );
        assert.ok(box.bottom <= height, `${box.selector} lies within the window: ${JSON.stringify(box)}`);
        assert.ok(!box.clipped, `${box.selector} is not cut off`);
      }
      assert.deepEqual(errors, []);
    } finally {
      await page.close();
    }
  });
}
