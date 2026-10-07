// O6: an art pack replaces painted sprites key by key. `?art=sample` draws the sample pack's
// PNGs where it has them and painted art everywhere else, within the asset contract; a sprite
// that breaks the contract is counted; a pack that is not there, that reaches outside its folder
// or whose sprite is too large is not loaded at all, and says so; the pack checker passes the
// sample and catches a broken pack, by the same rules as the page.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { cp, mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { crc32, deflateSync } from 'node:zlib';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import { serve } from './serve.mjs';
import { checkPack } from './art-pack-check.mjs';

const SAMPLE = fileURLToPath(new URL('../art/packs/sample', import.meta.url));
const CHECKER = fileURLToPath(new URL('./art-pack-check.mjs', import.meta.url));

let server;
let browser;

/** A transparent PNG of w × h pixels (RGBA, 8 bits). */
function blankPng(w, h) {
  const chunk = (type, data) => {
    const head = Buffer.alloc(8);
    head.writeUInt32BE(data.length, 0);
    head.write(type, 4, 'latin1');
    const crc = Buffer.alloc(4);
    crc.writeUInt32BE(crc32(Buffer.concat([head.subarray(4), data])) >>> 0, 0);
    return Buffer.concat([head, data, crc]);
  };
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(w, 0);
  ihdr.writeUInt32BE(h, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 6; // RGBA
  const rows = Buffer.alloc((w * 4 + 1) * h); // each row: filter 0, then zeros
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', ihdr),
    chunk('IDAT', deflateSync(rows)),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

/** Serve a pack under `art/packs/NAME/` that is not on disk: its pack.json and its files. */
async function routePack(page, name, entries, files) {
  await page.route(`**/art/packs/${name}/pack.json`, (route) =>
    route.fulfill({ contentType: 'application/json', body: JSON.stringify({ name, license: 'test', entries }) }),
  );
  for (const [file, body] of Object.entries(files)) {
    await page.route(`**/art/packs/${name}/${file}`, (route) => route.fulfill({ contentType: 'image/png', body }));
  }
}

/** Open the page with an art pack that must be refused whole; what it says and draws. */
async function refused(query, name, entries, files) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 }, deviceScaleFactor: 1 });
  try {
    const errors = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await routePack(page, name, entries, files);
    await page.goto(`http://127.0.0.1:${server.address().port}/index.html?${query}`);
    await page.waitForFunction(() => window.__observer?.ready || window.__observerError, null, { timeout: 600000 });
    assert.equal(await page.evaluate(() => window.__observerError ?? null), null);
    const seen = await page.evaluate(() => ({
      pack: window.__observer.artPack(),
      chip: document.getElementById('art-chip').textContent,
      title: document.getElementById('art-chip').title,
      keys: window.__observer.atlasKeys(),
      bakes: window.__observer.crowdSheetBakes(),
    }));
    assert.deepEqual(errors, []);
    assert.deepEqual(seen.pack.fromPack, []);
    assert.equal(seen.chip, `PROTOTYPE ARTWORK · art pack “${name}” not loaded`);
    assert.ok(seen.title.includes(seen.pack.error), seen.title);
    return seen;
  } finally {
    await page.close();
  }
}

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

async function open(query) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 }, deviceScaleFactor: 1 });
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto(`http://127.0.0.1:${server.address().port}/index.html?${query}`);
  await page.waitForFunction(() => window.__observer?.ready || window.__observerError, null, { timeout: 600000 });
  assert.equal(await page.evaluate(() => window.__observerError ?? null), null);
  return { page, errors };
}

test('the sample pack draws its sprites from PNG and the painters fill the rest', async () => {
  const { page, errors } = await open('art=sample&citizens=400');
  try {
    const seen = await page.evaluate(async () => {
      const o = window.__observer;
      o.setPaused(true);
      for (const hour of [6, 12, 18]) {
        o.setTime(hour * 3600);
        o.viewVillage(1.0);
        for (let i = 0; i < 8; i += 1) o.step(1 / 8);
      }
      const keys = new Set(o.atlasKeys());
      return {
        pack: o.artPack(),
        missing: o.atlasStats().missing,
        chip: document.getElementById('art-chip').textContent,
        painted: ['building.storehouse', 'nature.oak.1', 'person.0.walk.front.0#mask'].every((k) => keys.has(k)),
      };
    });
    assert.equal(seen.pack.error, null);
    assert.deepEqual(seen.pack.fromPack, [
      'building.well',
      'building.well#shadow',
      'nature.oak.0',
      'person.0.idle.front.0',
      'person.0.idle.front.0#mask',
    ]);
    assert.equal(seen.pack.sprites, 3);
    assert.deepEqual(seen.pack.unknown, []);
    assert.deepEqual(seen.pack.contractFailures, []);
    assert.deepEqual(seen.missing, []);
    assert.ok(seen.painted, 'keys the pack lacks are painted');
    assert.match(seen.chip, new RegExp(`^ART PACK: sample · 3 of ${seen.pack.keys} keys from PNG$`));
    assert.deepEqual(errors, []);
  } finally {
    await page.close();
  }
});

test('a pack that is not there leaves the painted art, and says so', async () => {
  const { page, errors } = await open('art=missing-pack&citizens=50');
  try {
    const seen = await page.evaluate(() => ({
      pack: window.__observer.artPack(),
      chip: document.getElementById('art-chip').textContent,
      title: document.getElementById('art-chip').title,
      well: window.__observer.atlasKeys().includes('building.well'),
    }));
    assert.match(seen.pack.error, /HTTP 404/);
    assert.deepEqual(seen.pack.fromPack, []);
    assert.equal(seen.chip, 'PROTOTYPE ARTWORK · art pack “missing-pack” not loaded');
    assert.match(seen.title, /every sprite is painted/);
    assert.ok(seen.well);
    assert.deepEqual(errors, []);
  } finally {
    await page.close();
  }
});

test('a pack sprite outside its footprint is drawn, and counted on the chip', async () => {
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 }, deviceScaleFactor: 1 });
  try {
    const errors = [];
    page.on('pageerror', (e) => errors.push(e.message));
    // A pack that draws the oak where the well stands, anchored at its left edge: it overhangs the
    // well's 2 m footprint far to the right.
    await routePack(
      page,
      'wide',
      { 'building.well': { file: 'oak.png', anchor: { x: 0, y: 194 } } },
      { 'oak.png': await readFile(join(SAMPLE, 'nature.oak.0.png')) },
    );
    await page.goto(`http://127.0.0.1:${server.address().port}/index.html?art=wide&citizens=50`);
    await page.waitForFunction(() => window.__observer?.ready || window.__observerError, null, { timeout: 600000 });
    const seen = await page.evaluate(() => ({
      pack: window.__observer.artPack(),
      chip: document.getElementById('art-chip').textContent,
    }));
    assert.deepEqual(seen.pack.fromPack, ['building.well']);
    assert.deepEqual(seen.pack.contractFailures, ['building.well']);
    assert.match(seen.chip, / · 1 outside their footprint$/);
    assert.deepEqual(errors, []);
  } finally {
    await page.close();
  }
});

test('a pack whose file lies outside its folder is refused whole', async () => {
  const seen = await refused(
    'art=escape&citizens=50',
    'escape',
    {
      'building.well': { file: '../sample/building.well.png', anchor: { x: 98, y: 104 } },
      'nature.oak.0': { file: 'oak.png', anchor: { x: 100, y: 194 } },
    },
    { 'oak.png': await readFile(join(SAMPLE, 'nature.oak.0.png')) },
  );
  assert.match(seen.pack.error, /^building\.well: file "\.\.\/sample\/building\.well\.png" is not inside the pack$/);
  assert.ok(seen.keys.includes('building.well') && seen.keys.includes('nature.oak.0'), 'painted instead');
});

test('a pack whose citizen frame is too large for the crowd sheet is refused whole', async () => {
  // A 2048-pixel frame would be 1024 in the atlas, and 80 copies of it cannot fit the crowd's
  // sheet: before the fix the page did not start ("crowd sheet full").
  const seen = await refused(
    'art=tall&people=1000&citizens=0',
    'tall',
    { 'person.0.idle.front.0': { file: 'frame.png', anchor: { x: 1024, y: 1900 } } },
    { 'frame.png': blankPng(2048, 2048) },
  );
  assert.equal(seen.pack.error, 'person.0.idle.front.0: 2048×2048 is larger than twice the painted 64×104');
  assert.equal(seen.bakes, 1);
});

test('the pack checker passes the sample and catches a broken pack', async () => {
  const sample = await checkPack(SAMPLE);
  assert.deepEqual(sample.errors, []);
  assert.deepEqual(sample.warnings, []);
  assert.deepEqual(sample.coverage.byCategory, { building: 1, nature: 1, person: 1 });
  assert.equal(spawnSync(process.execPath, [CHECKER, SAMPLE]).status, 0);

  const scratch = await mkdtemp(join(tmpdir(), 'art-pack-'));
  try {
    const dir = join(scratch, 'broken');
    await cp(SAMPLE, dir, { recursive: true });
    const pack = JSON.parse(await readFile(join(dir, 'pack.json'), 'utf8'));
    pack.name = 'broken';
    delete pack.entries['person.0.idle.front.0'];
    pack.entries['nature.oak.0'].anchor = { x: 5000, y: 10 };
    pack.entries['building.unicorn'] = { file: 'building.well.png', anchor: { x: 1, y: 1 } };
    pack.entries['building.well'].file = '../sample/building.well.png';
    pack.entries['building.hall'] = { file: 'hall.png', anchor: { x: 1, y: 1 } };
    pack.entries['nature.oak.1'] = { file: 'pack.json', anchor: { x: 1, y: 1 } };
    pack.entries['nature.oak.2'] = { file: '/etc/hostname', anchor: { x: 1, y: 1 } };
    pack.entries['nature.oak.3'] = { file: 'http://example.com/oak.png', anchor: { x: 1, y: 1 } };
    await writeFile(join(dir, 'pack.json'), JSON.stringify(pack));
    const broken = await checkPack(dir);
    const said = (pattern) => broken.errors.some((line) => pattern.test(line));
    assert.ok(said(/^person\.0\.idle\.front\.0#mask: the pack has no person\.0\.idle\.front\.0/), broken.errors);
    assert.ok(said(/^nature\.oak\.0: anchor .* is not inside/), broken.errors);
    assert.ok(said(/^building\.unicorn: not a key in the manifest/), broken.errors);
    assert.ok(said(/^building\.well: file .* is not inside the pack/), broken.errors);
    assert.ok(said(/^building\.hall: hall\.png is missing/), broken.errors);
    assert.ok(said(/^nature\.oak\.1: pack\.json is not a PNG/), broken.errors);
    assert.ok(said(/^nature\.oak\.2: file "\/etc\/hostname" is not inside the pack/), broken.errors);
    assert.ok(said(/^nature\.oak\.3: file "http:\/\/example\.com\/oak\.png" is not inside the pack/), broken.errors);
    assert.equal(spawnSync(process.execPath, [CHECKER, dir]).status, 1);

    // Sizes: a frame too large for the crowd sheet, a mask placed outside its frame, and a well at
    // exactly twice its painted size (allowed, with a warning).
    const tall = join(scratch, 'tall');
    await mkdir(tall);
    await writeFile(join(tall, 'frame.png'), blankPng(2048, 2048));
    await writeFile(join(tall, 'mask.png'), blankPng(64, 104));
    await writeFile(join(tall, 'well.png'), blankPng(392, 312));
    const entries = {
      'person.0.idle.front.0': { file: 'frame.png', anchor: { x: 1024, y: 1900 } },
      'person.0.idle.front.0#mask': { file: 'mask.png', anchor: { x: 2000, y: 0 } },
      'building.well': { file: 'well.png', anchor: { x: 196, y: 208 } },
    };
    await writeFile(join(tall, 'pack.json'), JSON.stringify({ name: 'tall', license: 'test', entries }));
    const sized = await checkPack(tall);
    assert.deepEqual(sized.errors, [
      'person.0.idle.front.0: 2048×2048 is larger than twice the painted 64×104',
      'person.0.idle.front.0#mask: placed by its anchor it lies outside its 2048×2048 frame',
    ]);
    assert.deepEqual(sized.warnings, [
      'building.well: 392×312, painted at 196×156',
      'person.0.idle.front.0: 2048×2048, painted at 64×104',
    ]);
  } finally {
    await rm(scratch, { recursive: true, force: true });
  }
});
