// O6: the committed art manifest is the painters' own. A painter that changes a sprite's size or
// anchor, or a new key, must come with `node tests/art-manifest.mjs`, so art-pack authors and
// the pack checker never work from a stale list.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { chromium } from 'playwright';
import { serve } from './serve.mjs';
import { MANIFEST_FILE, freshManifest, manifestText } from './art-manifest.mjs';

let server;
let browser;

before(async () => {
  server = await serve(0);
  browser = await chromium.launch();
});

after(async () => {
  await browser?.close();
  server?.close();
});

test('the committed art manifest equals a fresh one', async () => {
  const fresh = await freshManifest(browser, server.address().port);
  const committed = await readFile(MANIFEST_FILE, 'utf8');
  assert.deepEqual(JSON.parse(committed), fresh, 'run `node tests/art-manifest.mjs` and commit art/manifest.json');
  assert.equal(committed, manifestText(fresh));
  const keys = Object.entries(fresh.keys);
  // Every sprite has a size and an anchor; masks have neither (they are cropped to their accents).
  for (const [key, spec] of keys) {
    if (spec.category === 'mask') {
      assert.ok(key.endsWith('#mask') && spec.size === null && spec.anchor === null, key);
    } else {
      assert.ok(spec.size.w > 0 && spec.size.h > 0 && Number.isInteger(spec.anchor.x), key);
    }
  }
  const of = (category) => keys.filter(([, spec]) => spec.category === category).length;
  assert.ok(of('building') >= 20 && of('nature') >= 20 && of('person') >= 900 && of('vehicle') >= 32);
  // Citizen frames have a mask each; envoys are painted in their colours and have none.
  for (const [key, spec] of keys) {
    if (spec.category === 'person' && key.startsWith('person.')) assert.ok(`${key}#mask` in fresh.keys, key);
  }
});
