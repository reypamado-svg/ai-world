// Export painted sprites as an art pack (O6).
//
//   node tests/art-export.mjs [NAME] [KEY ...]
//
// Writes `art/packs/NAME/` (default `sample`): one PNG per key and `pack.json`. Without keys it
// writes the sample pack, a well, an oak and a citizen's first idle frame, with the well's
// shadow and the citizen's accent mask. A pack made this way is the painters' own art; it is a
// starting point for an artist and the pack the tests load.
import { mkdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import { serve } from './serve.mjs';

export const SAMPLE_KEYS = [
  'building.well',
  'building.well#shadow',
  'nature.oak.0',
  'person.0.idle.front.0',
  'person.0.idle.front.0#mask',
];

const PACKS = fileURLToPath(new URL('../art/packs/', import.meta.url));

/** Paint the keys in a page of the observer's origin; each as a PNG data URL and its anchor. */
async function paintKeys(browser, port, keys) {
  const page = await browser.newPage();
  try {
    await page.goto(`http://127.0.0.1:${port}/art/`);
    return await page.evaluate(async (wanted) => {
      const { CIV_COLORS, staticAssetPainters } = await import('/src/render/art/registry.js');
      const { accentMask, personFrameKey } = await import('/src/render/art/bake.js');
      const { ANIMATIONS, dressLook, personFrameCanvas } = await import('/src/render/art/paint/people.js');
      const painted = new Map();
      const painters = staticAssetPainters(CIV_COLORS[0]);
      for (const key of wanted) {
        const id = key.split('#')[0];
        if (painted.has(key) || !painters[id]) continue;
        const r = painters[id].paint();
        painted.set(id, r);
        if (r.shadow) painted.set(`${id}#shadow`, r.shadow);
      }
      for (const key of wanted) {
        const match = /^person\.(\d+)\.([a-z_]+)\.([a-z]+)\.(\d+)(#mask)?$/.exec(key);
        if (!match || painted.has(key)) continue;
        const [, a, anim, facing, i] = match;
        const spec = ANIMATIONS[anim];
        const t = Number(i) / spec.frames;
        const f = personFrameCanvas(dressLook(Number(a), '#ffffff'), anim, facing, t);
        const g = personFrameCanvas(dressLook(Number(a), '#000000'), anim, facing, t);
        const frameKey = personFrameKey(Number(a), anim, facing, Number(i));
        painted.set(frameKey, f);
        const mask = accentMask(f.canvas, g.canvas, f.anchor);
        if (mask) painted.set(`${frameKey}#mask`, mask);
      }
      const out = {};
      for (const key of wanted) {
        const r = painted.get(key);
        if (!r) throw new Error(`no painter for ${key}`);
        out[key] = { png: r.canvas.toDataURL('image/png'), anchor: { x: r.anchor.x, y: r.anchor.y } };
      }
      return out;
    }, keys);
  } finally {
    await page.close();
  }
}

/** The file a key is saved under: the key, with `#` written as `--`. */
export function fileOf(key) {
  return `${key.replace('#', '--')}.png`;
}

/** Write a pack of painted sprites to `art/packs/NAME/` (or `dir`). */
export async function exportPack(browser, port, name, keys, dir = `${PACKS}${name}`) {
  const painted = await paintKeys(browser, port, keys);
  await mkdir(dir, { recursive: true });
  const entries = {};
  for (const key of keys) {
    const { png, anchor } = painted[key];
    await writeFile(`${dir}/${fileOf(key)}`, Buffer.from(png.replace(/^data:image\/png;base64,/, ''), 'base64'));
    entries[key] = { file: fileOf(key), anchor };
  }
  const pack = { name, license: "generated from this repository's painters; no external assets", entries };
  await writeFile(`${dir}/pack.json`, `${JSON.stringify(pack, null, 2)}\n`);
  return pack;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const [name = 'sample', ...keys] = process.argv.slice(2);
  const server = await serve(0);
  const browser = await chromium.launch();
  try {
    const pack = await exportPack(browser, server.address().port, name, keys.length ? keys : SAMPLE_KEYS);
    console.log(`art/packs/${name}: ${Object.keys(pack.entries).length} sprites`);
  } finally {
    await browser.close();
    server.close();
  }
}
