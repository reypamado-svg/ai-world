// O5: a civilization's perspective. Part 1 (no browser): the record built from its council
// report becomes a frame of its own people as the council counts them, and an overlay record
// in civilization numbers. Part 2 (a browser, on a run served by `sovereign-world observe`):
// the page seen as one civilization, with the camera kept, fog where it knows nothing, its
// own people as counted, the world's sites and chronicle hidden, and nothing written to the run.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { spawn, execFile } from 'node:child_process';
import { mkdtemp, readFile, readdir, rm } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import { chromium } from 'playwright';
import { councilDay, describeNews, perspectiveDay, perspectiveDayFor } from '../src/data/perspective-source.js';
import { FOG_ALPHA, PerspectiveLayer, fogCorners, veilAlpha } from '../src/render/perspective-layer.js';
import { hexCentre, hexCorners } from '../src/world/hex.js';
import { DUTIES } from '../src/data/population.js';
import { RunSource } from '../src/data/run-source.js';

const CIVS = ['civilization:0000000001', 'civilization:0000000002'];

function record() {
  return {
    day: 45,
    civilization: CIVS[1],
    settlements: [
      {
        id: 'settlement:0000000002',
        civilization: CIVS[1],
        q: 10,
        r: 4,
        capital: true,
        founded_day: 0,
        rank: 'village',
        houses: { hut: 2 },
        slots: 10,
        residents: 7,
        idle_workers: 3,
        house_jobs: [],
        institutions: [],
      },
      {
        id: 'settlement:2-0001',
        civilization: CIVS[1],
        q: 14,
        r: 4,
        capital: false,
        founded_day: 30,
        rank: 'village',
        houses: {},
        slots: 0,
        residents: 2,
        idle_workers: 0,
        house_jobs: [],
        institutions: [],
      },
    ],
    people: {
      population: { living: 9 },
      notable: [
        {
          id: 'person:2-0005',
          sex: 1,
          age: 40,
          health: 90,
          hungry: false,
          settlement: 'settlement:0000000002',
          duty: DUTIES.indexOf('scholar'),
          duty_label: 'scholar:research:1',
          skills: { writing: 20 },
        },
        {
          id: 'person:0000000017',
          sex: 0,
          age: 30,
          health: 100,
          hungry: false,
          settlement: null,
          duty: DUTIES.indexOf('farmer'),
          duty_label: null,
          skills: {},
        },
      ],
    },
    owners: [
      [10, 4, CIVS[1]],
      [3, 3, CIVS[0]],
      [5, 5, 'civilization:0000000009'],
    ],
    travellers: [[12, 4, CIVS[1], 3]],
    counts: { living: 12, at_home: 9, away: 3 },
  };
}

test('a perspective fills a frame with the council’s count of its people', () => {
  const { day, record: overlay, frame } = perspectiveDay(record(), CIVS);
  assert.equal(day, 45);
  assert.equal(frame.length, 9);
  assert.equal(frame.residents(0), 7);
  assert.equal(frame.residents(1), 2);
  // The named person comes first at their settlement, with what the report says of them.
  const named = frame.indexOf('person:2-0005');
  assert.equal(frame.rowsOf(0)[0], named);
  assert.equal(frame.dutyOf(named), 'scholar');
  const rec = frame.record(named);
  assert.equal(rec.provenance, 'council report');
  assert.deepEqual(rec.skills, { writing: 20 });
  assert.match(rec.events[0], /Named in its council's report for day 45/);
  // The others are counted, not identified.
  const other = frame.indexOf('council:settlement:2-0001:1');
  assert.ok(other >= 0);
  assert.match(frame.record(other).events[0], /not identified/);
  assert.equal(frame.record(other).label, 'Counted, not named');
  // Someone away is not drawn at a settlement.
  assert.equal(frame.indexOf('person:0000000017'), -1);
  for (let i = 0; i < frame.length; i += 1) assert.equal(frame.civ[i], 1);
  // The overlay record is in civilization numbers; owners of unknown peoples are left out.
  assert.deepEqual(overlay.owners, [
    [10, 4, 1],
    [3, 3, 0],
  ]);
  assert.deepEqual(overlay.travellers, [[12, 4, 1, 3]]);
  assert.equal(overlay.settlements[0].civilization, 1);
  assert.deepEqual(overlay.counts, { living: 12, at_home: 9, away: 3 });
});

test('the last council falls on day 0 and every 30th day', () => {
  assert.equal(councilDay(0), 0);
  assert.equal(councilDay(29), 0);
  assert.equal(councilDay(30), 30);
  assert.equal(councilDay(45), 30);
});

test('council news reads as plain sentences', () => {
  assert.match(
    describeNews({
      kind: 'battle',
      won: true,
      enemy: CIVS[0],
      own_fighters: 8,
      own_dead: 1,
      own_wounded: 2,
      own_captured: 0,
      enemy_fighters_estimate: 6,
      enemy_losses_estimate: 3,
    }),
    /^Won a battle against .* realm: 8 of ours fought, 1 died, 2 wounded; about 6 of theirs, about 3 lost\.$/,
  );
  assert.equal(describeNews({ kind: 'crisis', text: 'War was declared.' }), 'War was declared.');
  assert.match(describeNews({ kind: 'treaty', treaty: 'trade', counterparty: CIVS[0] }), /trade treaty with/);
});

test('at its last council loads the council day only when the export holds it', () => {
  // A stride-7 export: day 35's council (day 30) is not in it, so day 35 stands in, not day 28.
  assert.deepEqual(perspectiveDayFor(35, [0, 7, 14, 21, 28, 35], 'council'), { day: 35, councilMissing: true });
  assert.deepEqual(perspectiveDayFor(45, [0, 30, 45], 'council'), { day: 30, councilMissing: false });
  assert.deepEqual(perspectiveDayFor(30, [0, 30], 'council'), { day: 30, councilMissing: false });
  assert.deepEqual(perspectiveDayFor(45, [0, 30, 45], 'shown'), { day: 45, councilMissing: false });
});

test('fog is opaque and its hexes overlap their neighbours', () => {
  assert.equal(FOG_ALPHA, 1);
  const R = 10;
  const c = hexCentre(3, 2, R);
  const plain = hexCorners(3, 2, R);
  fogCorners(3, 2, R).forEach((p, k) => {
    const ratio = Math.hypot(p.x - c.x, p.y - c.y) / Math.hypot(plain[k].x - c.x, plain[k].y - c.y);
    assert.ok(Math.abs(ratio - 1.03) < 1e-9, String(ratio));
  });
});

test('the perspective layer covers unknown tiles opaquely and veils old sightings', () => {
  // A stand-in for PIXI that records what is filled.
  const fills = [];
  class Container {
    constructor() {
      this.children = [];
      this.visible = true;
      this.position = { set() {} };
      this.scale = { set() {} };
    }
    addChild(...items) {
      this.children.push(...items);
    }
    removeChildren() {
      const out = this.children;
      this.children = [];
      return out;
    }
    destroy() {}
  }
  class Graphics extends Container {
    poly() {
      return this;
    }
    rect() {
      return this;
    }
    circle() {
      return this;
    }
    moveTo() {
      return this;
    }
    lineTo() {
      return this;
    }
    stroke() {
      return this;
    }
    fill(style) {
      fills.push(style);
      return this;
    }
  }
  class Text extends Container {}
  const layer = new PerspectiveLayer({ Container, Graphics, Text }, 10, {
    width: 3,
    height: 3,
    chunkTiles: 8,
    labelZoom: 1,
  });
  const empty = { foreign_settlements: [], ruins: [], sites: [], roads: [], bridges: [], tolls: [], garrisons: [] };
  layer.setDay(
    { ...empty, known_tiles: [[0, 0]], tile_dates: [[1, 1, 0, null]] },
    { shownDay: 5, civilizations: ['c'] },
  );
  const fog = fills.filter((f) => f.color === 0x0b0d10);
  assert.equal(fog.length, 7);
  assert.ok(fog.every((f) => f.alpha === 1));
  const veils = fills.filter((f) => f.color !== 0x0b0d10);
  // The known tile with no date gets the fixed veil; the one seen on day 0 is veiled by age.
  assert.deepEqual(veils, [
    { color: 0x8a8f96, alpha: veilAlpha(null, 5) },
    { color: 0x8a8f96, alpha: veilAlpha(0, 5) },
  ]);
  assert.equal(layer.counts().fogTiles, 7);
  assert.equal(layer.counts().staleTiles, 2);
  assert.equal(layer.counts().known, 2);
});

test('an export made without perspectives says so', async () => {
  const base = fileURLToPath(new URL('./fixtures/run-small', import.meta.url));
  const run = await RunSource.open(base, { load: async (path) => new Uint8Array(await readFile(path)) });
  assert.equal(run.hasPerspectives, false);
  await assert.rejects(run.perspective(0, 0), /export it with --perspectives/);
});

// ------------------------------------------------------------------ part 2: the page
const exec = promisify(execFile);
const repo = fileURLToPath(new URL('../..', import.meta.url));
const cli = join(repo, '.venv', 'bin', 'sovereign-world');
const TOKEN = 'perspective-test-token-0123456789abcdef';
const WAR_DAYS = 40;

let browser;
let observer;
let dir;
let war;
let port;
let logs = '';

function freePort() {
  return new Promise((resolve) => {
    const probe = createServer().listen(0, '127.0.0.1', () => {
      const { port: free } = probe.address();
      probe.close(() => resolve(free));
    });
  });
}

async function ask(path) {
  try {
    const res = await fetch(`http://127.0.0.1:${port}${path}`, { headers: { Authorization: `Bearer ${TOKEN}` } });
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

async function files(root) {
  const out = {};
  for (const name of (await readdir(root)).sort()) out[name] = (await readFile(join(root, name))).toString('base64');
  return out;
}

before(async () => {
  dir = await mkdtemp(join(tmpdir(), 'perspective-'));
  war = join(dir, 'war');
  await exec(
    join(repo, '.venv', 'bin', 'python'),
    [join(repo, 'tests', 'observer', 'war_run.py'), war, String(WAR_DAYS)],
    {
      cwd: repo,
      env: { ...process.env, PYTHONPATH: `${join(repo, 'src')}:${join(repo, 'tests')}` },
    },
  );
  port = await freePort();
  observer = spawn(cli, ['observe', war, '--port', String(port)], {
    env: { ...process.env, SOVEREIGN_WORLD_OBSERVER_TOKEN: TOKEN },
  });
  observer.stdout.on('data', (d) => (logs += d));
  observer.stderr.on('data', (d) => (logs += d));
  await until(async () => (await ask('/api/status'))?.ready === WAR_DAYS + 1, 120000, 'the war run to be walked');
  browser = await chromium.launch({
    args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'],
  });
});

after(async () => {
  await browser?.close();
  if (observer && observer.exitCode === null && observer.signalCode === null) {
    const exited = new Promise((resolve) => observer.once('exit', resolve));
    observer.kill();
    await exited;
  }
  if (dir) await rm(dir, { recursive: true, force: true });
});

test('the page sees the world as one civilization, and back', async () => {
  const pristine = await files(war);
  const manifest = await ask('/api/run/manifest');
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 }, deviceScaleFactor: 1 });
  try {
    const errors = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await page.goto(`http://127.0.0.1:${port}/?run=live&day=30#token=${TOKEN}`);
    await page.waitForFunction(() => window.__observer?.ready || window.__observerError, null, { timeout: 600000 });
    assert.equal(await page.evaluate(() => window.__observerError ?? null), null);
    const world = await page.evaluate(() => {
      const o = window.__observer;
      o.setPaused(true);
      return { run: o.runInfo(), camera: o.cameraInfo(), info: o.perspectiveInfo() };
    });
    assert.equal(world.run.day, 30);
    assert.equal(world.info.civ, null);
    assert.equal(world.info.enabled, true);
    assert.match(world.info.chip, /^Perspective: Observer/);
    assert.equal(world.info.worldChronicleHidden, false);

    // Seen as the first civilization: its council's knowledge on day 30; the camera stays.
    await page.evaluate(() => window.__observer.setPerspective(0));
    let info = await page.evaluate(() => window.__observer.perspectiveInfo());
    const camera = await page.evaluate(() => window.__observer.cameraInfo());
    assert.deepEqual([camera.x, camera.y, camera.zoom], [world.camera.x, world.camera.y, world.camera.zoom]);
    assert.equal(info.civilization, manifest.civilizations[0]);
    assert.match(info.chip, /^PERSPECTIVE: .* what its council knows as of day 30$/);
    assert.equal(info.fogTiles, manifest.width * manifest.height - info.known);
    assert.ok(info.fogTiles > 0 && info.known > 0);
    assert.deepEqual(info.drawnResidents, info.residents);
    assert.ok(info.residents.length < world.run.residents.length, 'only its own settlements');
    assert.ok(info.foreign >= 1, 'it knows the settlement of the people it fought');
    assert.equal(info.sitesHidden, true);
    assert.equal(info.worldChronicleHidden, true);
    assert.equal(info.councilButton, true);
    assert.equal(new URL(page.url()).searchParams.get('civ'), '0');
    // Its people: the named carry what the report says; the others are counted, not named.
    await page.evaluate((id) => window.__observer.select(id), info.named);
    assert.match(await page.evaluate(() => window.__observer.inspectorText()), /council report/);
    if (info.counted) {
      await page.evaluate((id) => window.__observer.select(id), info.counted);
      assert.match(await page.evaluate(() => window.__observer.inspectorText()), /not identified/);
    } else {
      // A small people: its report names everyone (up to 40), so nobody is merely counted.
      assert.ok(info.residents.reduce((a, b) => a + b, 0) <= 40);
    }
    // Council news lists its own records.
    await page.click('#btn-council');
    info = await page.evaluate(() => window.__observer.perspectiveInfo());
    assert.equal(info.councilItems, Math.max(1, info.news));

    // The next day, then as of its last council.
    await page.evaluate(() => window.__observer.loadDay(31));
    info = await page.evaluate(() => window.__observer.perspectiveInfo());
    assert.match(info.chip, /as of day 31$/);
    await page.evaluate(() => window.__observer.setPerspectiveAsOf('council'));
    info = await page.evaluate(() => window.__observer.perspectiveInfo());
    assert.equal(info.day, 31);
    assert.equal(info.perspectiveDay, 30);
    assert.match(info.chip, /as of its council on day 30 \(shown day 31\)$/);
    const still = await page.evaluate(() => window.__observer.cameraInfo());
    assert.deepEqual([still.x, still.y, still.zoom], [world.camera.x, world.camera.y, world.camera.zoom]);

    // Back to the world: everyone recorded again.
    await page.evaluate(() => window.__observer.setPerspective(null));
    info = await page.evaluate(() => window.__observer.perspectiveInfo());
    const back = await page.evaluate(() => window.__observer.runInfo());
    assert.match(info.chip, /^Perspective: Observer/);
    assert.equal(info.worldChronicleHidden, false);
    assert.equal(info.councilButton, false);
    assert.equal(back.residents.length, world.run.residents.length);
    assert.deepEqual(back.drawnResidents, back.residents);

    // A world answer still in flight when the day is seen as a civilization is dropped: day 1's
    // world routes (with the first civilization's war party) and chronicle answer late.
    const dayOne = await ask('/api/run/days/1/routes');
    assert.ok(dayOne.parties.some((party) => party.civilization === 0));
    const late = (url) => /\/days\/1\/routes$|chronicle\?day=1$/.test(url.href);
    await page.route(late, async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 1500));
      await route.continue();
    });
    await page.evaluate(async () => {
      const o = window.__observer;
      o.setPerspectiveAsOf('shown');
      await o.loadDay(1);
      await o.setPerspective(1);
    });
    await new Promise((resolve) => setTimeout(resolve, 2500));
    info = await page.evaluate(() => window.__observer.perspectiveInfo());
    assert.equal(info.perspectiveDay, 1);
    assert.ok(
      info.overlayParties.every((civ) => civ === 1),
      'only its own parties survive the late world answer',
    );
    assert.equal(info.overlayParties.length, info.parties);
    assert.notEqual((await page.evaluate(() => window.__observer.chronicleInfo()))?.day, 1);
    await page.unroute(late);
    await page.evaluate(() => window.__observer.setPerspective(null));
    assert.deepEqual(errors, []);
    // Nothing was written to the run.
    assert.deepEqual(await files(war), pristine);
  } finally {
    await page.close();
  }
});
