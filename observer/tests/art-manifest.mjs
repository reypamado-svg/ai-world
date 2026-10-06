// Write the art manifest, `art/manifest.json`, from the painters (O6).
//
//   node tests/art-manifest.mjs
//
// The manifest lists every key the renderer can ask the atlas for, with the size and anchor a
// sprite under it must have; an art-pack author works from it, and the pack loader and
// `tests/art-pack-check.mjs` check packs against it. `art-manifest.test.mjs` keeps the
// committed file equal to a fresh one.
import { writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import { serve } from './serve.mjs';

export const MANIFEST_FILE = fileURLToPath(new URL('../art/manifest.json', import.meta.url));

/** The manifest, painted fresh in a page served by `serve.mjs`. */
export async function freshManifest(browser, port) {
  const page = await browser.newPage();
  try {
    // Any page of this origin will do: the module is imported on its own.
    await page.goto(`http://127.0.0.1:${port}/art/`);
    return await page.evaluate(async () => {
      const { artManifest } = await import('/src/render/art/manifest.js');
      return artManifest();
    });
  } finally {
    await page.close();
  }
}

/** The file's text: one line per key, so a change to one sprite is one line of the diff. */
export function manifestText(manifest) {
  const { keys, ...head } = manifest;
  const lines = Object.entries(keys).map(([key, spec]) => `    ${JSON.stringify(key)}: ${JSON.stringify(spec)}`);
  const top = JSON.stringify(head, null, 2).replace(/\n}$/, '');
  return `${top},\n  "keys": {\n${lines.join(',\n')}\n  }\n}\n`;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const server = await serve(0);
  const browser = await chromium.launch();
  try {
    const manifest = await freshManifest(browser, server.address().port);
    await writeFile(MANIFEST_FILE, manifestText(manifest));
    console.log(`${MANIFEST_FILE}: ${Object.keys(manifest.keys).length} keys`);
  } finally {
    await browser.close();
    server.close();
  }
}
