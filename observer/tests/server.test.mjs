// O3 C3: the observer opens a run served live by `sovereign-world observe` (?run=live), with
// the token from the address's fragment. Days can be stepped, a wrong token is refused with a
// plain message, a day the run saves later appears by itself, and the run is never written.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { spawn, execFile } from 'node:child_process';
import { cp, mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import { gunzipSync } from 'node:zlib';
import { chromium } from 'playwright';

const run = promisify(execFile);
const repo = fileURLToPath(new URL('../..', import.meta.url));
const cli = join(repo, '.venv', 'bin', 'sovereign-world');
const fixture = join(repo, 'tests', 'fixtures', 'format-one');
const TOKEN = 'browser-test-token-0123456789abcdef';
const DAYS = 5;

let browser;
let observer;
let dir;
let port;
let logs = '';
let pristine;

function freePort() {
  return new Promise((resolve) => {
    const probe = createServer().listen(0, '127.0.0.1', () => {
      const { port: free } = probe.address();
      probe.close(() => resolve(free));
    });
  });
}

async function status() {
  try {
    const res = await fetch(`http://127.0.0.1:${port}/api/status`, { headers: { Authorization: `Bearer ${TOKEN}` } });
    return res.ok ? res.json() : null;
  } catch {
    return null;
  }
}

async function until(check, ms, what) {
  const end = Date.now() + ms;
  for (;;) {
    const value = await check();
    if (value) return value;
    if (Date.now() > end) throw new Error(`timed out waiting for ${what}\n${logs}`);
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
}

/** A journal's records with every gzipped state unpacked (format-1 gzip stamps the time). */
async function journalContent(root) {
  const lines = (await readFile(join(root, 'journal.jsonl'), 'utf8')).trim().split('\n');
  return lines.map((line) => {
    const { type, payload } = JSON.parse(line);
    if (typeof payload?.state_gzip_base64 !== 'string') return { type, payload };
    const { state_gzip_base64: blob, ...rest } = payload;
    return { type, payload: rest, state: gunzipSync(Buffer.from(blob, 'base64')).toString('utf8') };
  });
}

/** Stop an observer and wait until it (and the runner it closes) has exited, so nothing is
 * left running into the next test file. */
async function stop(child) {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  const exited = new Promise((resolve) => child.once('exit', resolve));
  child.kill();
  await exited;
}

async function files(root) {
  const out = {};
  for (const name of (await readdir(root)).sort()) out[name] = (await readFile(join(root, name))).toString('base64');
  return out;
}

before(async () => {
  dir = await mkdtemp(join(tmpdir(), 'observe-'));
  await cp(fixture, join(dir, 'run'), { recursive: true });
  pristine = await files(join(dir, 'run'));
  port = await freePort();
  observer = spawn(cli, ['observe', join(dir, 'run'), '--port', String(port)], {
    env: { ...process.env, SOVEREIGN_WORLD_OBSERVER_TOKEN: TOKEN },
  });
  observer.stdout.on('data', (d) => (logs += d));
  observer.stderr.on('data', (d) => (logs += d));
  await until(async () => (await status())?.ready === DAYS + 1, 60000, 'the server to walk the run');
  browser = await chromium.launch({
    args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'],
  });
});

after(async () => {
  await browser?.close();
  await stop(observer);
  if (dir) await rm(dir, { recursive: true, force: true });
});

async function open(page, token) {
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto(`http://127.0.0.1:${port}/?run=live#token=${token}`);
  await page.waitForFunction(() => window.__observer?.ready || window.__observerError, null, { timeout: 600000 });
  return errors;
}

test('a wrong token is refused with a plain message', async () => {
  const page = await browser.newPage({ viewport: { width: 800, height: 600 } });
  try {
    await open(page, 'not-the-token');
    const error = await page.evaluate(() => window.__observerError);
    assert.match(error, /refused the token \(401\)/);
    assert.match(await page.locator('#loading').textContent(), /refused the token/);
  } finally {
    await page.close();
  }
});

test('a live run opens on its newest day, steps, and follows days saved later', async () => {
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 }, deviceScaleFactor: 1 });
  try {
    const errors = await open(page, TOKEN);
    assert.equal(await page.evaluate(() => window.__observerError ?? null), null);
    assert.equal(await page.locator('#source-chip').textContent(), 'LIVE RUN');
    let live = await page.evaluate(() => window.__observer.liveInfo());
    assert.equal(live.following, true);
    assert.equal(live.latest, DAYS);
    assert.equal(await page.evaluate(() => window.__observer.runInfo().day), DAYS);
    // The token stays in the fragment: never in the query, which the server would see.
    assert.ok(!(await page.evaluate(() => location.search)).includes(TOKEN));
    // Read once, the token leaves the address: not in the bar, the history or a restored session.
    assert.equal(await page.evaluate(() => location.hash), '');
    // Step back: following stops; the day is the engine's.
    const info = await page.evaluate(async () => {
      const o = window.__observer;
      o.setPaused(true);
      await o.loadDay(2);
      o.frame();
      return { ...o.runInfo(), crowd: o.crowdCounts() };
    });
    assert.equal(info.day, 2);
    assert.deepEqual(info.drawnResidents, info.residents);
    assert.equal(info.crowd.worldPopulation, info.counts.at_home);
    await page.click('#btn-day-next');
    live = await page.evaluate(() => window.__observer.liveInfo());
    assert.equal(live.following, false);
    assert.match(await page.locator('#live-chip').textContent(), /LIVE RUN · paused/);
    await page.click('#btn-day-follow');
    await page.waitForFunction((d) => window.__observer.runInfo().day === d, DAYS);
    assert.equal((await page.evaluate(() => window.__observer.liveInfo())).following, true);

    // Serving and viewing wrote nothing to the run: the same files, byte for byte.
    assert.deepEqual(await files(join(dir, 'run')), pristine);

    // The run saves another day: it appears and is followed, within a few seconds.
    await run(cli, ['run', join(dir, 'run'), '--days', '1']);
    const saved = Date.now();
    await page.waitForFunction((d) => window.__observer.runInfo().day === d, DAYS + 1, { timeout: 15000 });
    assert.ok(Date.now() - saved < 5000, `took ${Date.now() - saved} ms`);
    live = await page.evaluate(() => window.__observer.liveInfo());
    assert.equal(live.latest, DAYS + 1);
    assert.equal(live.error, null);
    // The page is paused (the test holds the display clock), on the newest of the recorded days.
    assert.match(
      await page.locator('#live-chip').textContent(),
      new RegExp(`day ${DAYS + 1} \\(${DAYS + 2} recorded\\)`),
    );
    assert.deepEqual(errors, []);
  } finally {
    await page.close();
  }
});

test('the chronicle lists the day, and Go and Follow move the camera', async () => {
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 }, deviceScaleFactor: 1 });
  try {
    const errors = await open(page, TOKEN);
    await page.evaluate(async () => {
      const o = window.__observer;
      o.setPaused(true);
      await o.loadDay(1);
    });
    await page.waitForFunction(() => window.__observer.chronicleInfo()?.day === 1);
    await page.click('#btn-chronicle');
    let info = await page.evaluate(() => window.__observer.chronicleInfo());
    assert.equal(info.open, true);
    assert.ok(info.items.length > 0 && info.items.length < info.total, 'routine events are hidden');
    assert.ok(
      info.items.every((item) => item.go),
      'every listed event on day 1 is placed',
    );
    await page.check('#chk-routine');
    info = await page.evaluate(() => window.__observer.chronicleInfo());
    assert.equal(info.items.length, info.total);
    // Go: the camera moves to the event's tile.
    const placed = await page.evaluate(() => {
      const li = document.querySelector('#chronicle-list li');
      li.querySelector('button.go').click();
      const [q, r] = li
        .querySelector('.where')
        .textContent.match(/tile (-?\d+),(-?\d+)/)
        .slice(1)
        .map(Number);
      return { camera: window.__observer.cameraInfo(), target: window.__observer.hexCamera(q, r) };
    });
    assert.ok(Math.abs(placed.camera.x - placed.target.x) < 1e-6 && Math.abs(placed.camera.y - placed.target.y) < 1e-6);
    assert.ok(placed.camera.zoom >= 0.6);
    // Follow: someone the day names, if they are at a settlement that day.
    const person = info.items.find((item) => item.canFollow)?.follow;
    if (person) {
      await page.click(`#chronicle-list button.follow[data-person="${person}"]`);
      const camera = await page.evaluate(() => window.__observer.cameraInfo());
      assert.equal(camera.selected, person);
      assert.equal(camera.follow, true);
    }
    assert.deepEqual(errors, []);
  } finally {
    await page.close();
  }
});

test('travellers on the road can be picked, and their route is drawn', async () => {
  // A short recorded war: a raiding party is on the road from day 1.
  const war = join(dir, 'war');
  await run(join(repo, '.venv', 'bin', 'python'), [join(repo, 'tests', 'observer', 'war_run.py'), war, '6'], {
    cwd: repo,
    env: { ...process.env, PYTHONPATH: `${join(repo, 'src')}:${join(repo, 'tests')}` },
  });
  const warPort = await freePort();
  const second = spawn(cli, ['observe', war, '--port', String(warPort)], {
    env: { ...process.env, SOVEREIGN_WORLD_OBSERVER_TOKEN: TOKEN },
  });
  const ask = async (path) => {
    try {
      const res = await fetch(`http://127.0.0.1:${warPort}${path}`, { headers: { Authorization: `Bearer ${TOKEN}` } });
      return res.ok ? res.json() : null;
    } catch {
      return null;
    }
  };
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 }, deviceScaleFactor: 1 });
  try {
    await until(async () => (await ask('/api/status'))?.ready === 7, 60000, 'the war run to be walked');
    let day = null;
    for (let d = 1; d <= 6 && day === null; d += 1) {
      const routes = await ask(`/api/run/days/${d}/routes`);
      const record = await ask(`/api/run/days/${d}`);
      const dots = new Set(record.travellers.map(([q, r]) => `${q},${r}`));
      if (routes.parties.some((p) => p.kind === 'campaign' && p.tile && dots.has(p.tile.join(',')))) day = d;
    }
    assert.notEqual(day, null, 'a war party is on the road');
    const errors = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await page.goto(`http://127.0.0.1:${warPort}/?run=live&day=${day}#token=${TOKEN}`);
    await page.waitForFunction(() => window.__observer?.ready || window.__observerError, null, { timeout: 600000 });
    assert.equal(await page.evaluate(() => window.__observerError ?? null), null);
    assert.equal((await page.evaluate(() => window.__observer.liveInfo())).following, false);
    await page.waitForFunction(() => window.__observer.runInfo().overlays.parties > 0);
    const shown = await page.evaluate(() => {
      const o = window.__observer;
      o.setPaused(true);
      const t = o.runTravellers()[0];
      o.viewHex(t[0], t[1], 0.02);
      const pt = o.travellerPoint(0);
      o.pickAt(pt.x, pt.y);
      o.frame();
      return { title: o.inspectorTitle(), overlays: o.runInfo().overlays, tile: t };
    });
    assert.match(shown.title, new RegExp(`travelling on tile ${shown.tile[0]},${shown.tile[1]}`));
    assert.ok(shown.overlays.routesShown >= 1, JSON.stringify(shown.overlays));
    // Stepping a day rebuilds its record from the changes since the day shown.
    const stepped = await page.evaluate(
      async ({ d, token }) => {
        const o = window.__observer;
        await o.loadDay(d + 1);
        const full = await (
          await fetch(`/api/run/days/${d + 1}`, { headers: { Authorization: `Bearer ${token}` } })
        ).json();
        const info = o.runInfo();
        return { byChanges: o.recordsByChanges(), counts: info.counts, residents: info.residents, full };
      },
      { d: day, token: TOKEN },
    );
    assert.ok(stepped.byChanges >= 1);
    assert.deepEqual(stepped.counts, stepped.full.counts);
    assert.deepEqual(
      stepped.residents,
      stepped.full.settlements.map((s) => s.residents),
    );
    assert.deepEqual(errors, []);
  } finally {
    await page.close();
    await stop(second);
  }
});

test('a run cut back while the page is open starts the page again, with its token', async () => {
  const page = await browser.newPage({ viewport: { width: 1024, height: 640 }, deviceScaleFactor: 1 });
  try {
    const errors = await open(page, TOKEN);
    assert.equal(await page.evaluate(() => window.__observerError ?? null), null);
    const before = await page.evaluate(() => window.__observer.liveInfo().latest);
    assert.ok(before > 2);
    // Cut the run's journal back to day 2: a new history, whose people may be numbered anew.
    const journal = join(dir, 'run', 'journal.jsonl');
    const lines = (await readFile(journal, 'utf8')).split(/(?<=\n)/);
    const keep = lines.findIndex((line) => line.includes('"type":"transition"') && line.includes('"day":2,'));
    assert.ok(keep > 0);
    await writeFile(journal, lines.slice(0, keep + 1).join(''));
    await page.waitForFunction(() => window.__observer?.ready && window.__observer.liveInfo()?.latest === 2, null, {
      timeout: 60000,
    });
    const after = await page.evaluate(() => ({
      hash: location.hash,
      search: location.search,
      day: window.__observer.runInfo().day,
      live: window.__observer.liveInfo(),
    }));
    assert.equal(after.hash, '');
    assert.ok(!after.search.includes('day='), after.search);
    assert.equal(after.day, 2);
    assert.equal(after.live.following, true);
    assert.deepEqual(errors, []);
    // A reload has no token left: the page says to open the printed address again.
    await page.reload();
    await page.waitForFunction(() => window.__observerError, null, { timeout: 60000 });
    assert.match(await page.locator('#loading-status').textContent(), /needs its token/);
  } finally {
    await page.close();
  }
});

test('a runner started by the observer plays with the page, stays a few days ahead, and finishes', async () => {
  const root = join(dir, 'runner');
  const plain = join(dir, 'plain');
  await cp(fixture, root, { recursive: true });
  await cp(fixture, plain, { recursive: true });
  const runnerPort = await freePort();
  const observerProcess = spawn(cli, ['observe', root, '--port', String(runnerPort), '--run-days', '6'], {
    env: { ...process.env, SOVEREIGN_WORLD_OBSERVER_TOKEN: TOKEN },
  });
  let output = '';
  observerProcess.stdout.on('data', (d) => (output += d));
  observerProcess.stderr.on('data', (d) => (output += d));
  const status = async () => {
    try {
      const res = await fetch(`http://127.0.0.1:${runnerPort}/api/status`, {
        headers: { Authorization: `Bearer ${TOKEN}` },
      });
      return res.ok ? res.json() : null;
    } catch {
      return null;
    }
  };
  const page = await browser.newPage({ viewport: { width: 1024, height: 640 }, deviceScaleFactor: 1 });
  try {
    await until(async () => (await status())?.runner?.phase === 'paused', 60000, 'the runner to wait');
    const errors = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await page.goto(`http://127.0.0.1:${runnerPort}/?run=live#token=${TOKEN}`);
    await page.waitForFunction(() => window.__observer?.ready || window.__observerError, null, { timeout: 600000 });
    assert.equal(await page.evaluate(() => window.__observerError ?? null), null);
    // It opens paused, on the newest day, with the runner waiting.
    await page.waitForFunction((d) => window.__observer.runnerInfo()?.control?.shown === d, DAYS);
    let info = await page.evaluate(() => window.__observer.runnerInfo());
    assert.equal(info.phase, 'paused');
    assert.equal(info.control.paused, true);
    assert.equal(info.control.shown, DAYS);
    assert.match(info.chip, /^RUNNER · paused · day 5 of 11/);
    assert.equal(await page.locator('#lookahead-group').isHidden(), false);
    // Look at day 5 without following, then play: the run goes 3 days ahead, then waits.
    await page.click('#btn-day-follow');
    assert.equal((await page.evaluate(() => window.__observer.liveInfo())).following, false);
    await page.evaluate(() => window.__observer.setPaused(false));
    await until(
      async () => {
        const s = await status();
        return s?.saved_days[1] >= DAYS + 3 && s.runner.phase === 'paused' && s;
      },
      60000,
      'the runner to reach the lookahead',
    );
    await new Promise((resolve) => setTimeout(resolve, 1500));
    let s = await status();
    assert.ok(s.saved_days[1] - s.control.shown <= s.control.lookahead + 1, JSON.stringify(s));
    // The display reaches the next day: the run may save one more.
    const shown = await page.evaluate(async () => {
      await window.__observer.advanceDay();
      return window.__observer.timelineInfo().day;
    });
    assert.equal(shown, DAYS + 1);
    await until(async () => (await status())?.saved_days[1] >= DAYS + 4, 60000, 'one more day');
    // The shown day's hash is the replay's.
    const hash = await page.evaluate(() => window.__observer.runInfo().hash);
    // Lookahead 1: already far enough ahead, so it waits.
    await page.evaluate(() => window.__observer.setLookahead(1));
    s = await status();
    assert.equal(s.control.lookahead, 1);
    // Follow the newest day again: the run goes on, a day at a time, to its last day.
    await page.click('#btn-day-follow');
    await until(async () => (await status())?.runner?.phase === 'done', 120000, 'the runner to finish');
    await page.waitForFunction(
      () => window.__observer.runnerInfo()?.chip.startsWith('RUNNER · finished at day 11'),
      null,
      {
        timeout: 30000,
      },
    );
    assert.deepEqual(errors, []);
    observerProcess.kill();
    await new Promise((resolve) => observerProcess.on('exit', resolve));
    assert.ok(!output.includes(TOKEN));
    // What the observer's runner saved is what `run` saves, and replays to the hash shown.
    const replayed = await run(cli, ['replay', root, '--day', String(DAYS + 1)]);
    assert.ok(replayed.stdout.includes(`(${hash})`), replayed.stdout);
    await run(cli, ['verify', root]);
    await run(cli, ['run', plain, '--days', '6']);
    // The fixture is a format-1 run, whose saved days are gzipped with the time of saving, so
    // records are compared by what they hold: the same days, states, hashes, events and councils.
    assert.deepEqual(await journalContent(root), await journalContent(plain));
  } finally {
    await page.close();
    await stop(observerProcess);
  }
});
