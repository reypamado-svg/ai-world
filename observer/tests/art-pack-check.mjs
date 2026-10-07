// Check an art pack against the art manifest (O6), without a browser.
//
//   node tests/art-pack-check.mjs art/packs/NAME [--manifest art/manifest.json]
//
// Errors (exit code 1): a missing or malformed `pack.json`, a key the manifest does not list, a
// file that is missing, not a PNG or not inside the pack's folder, and every rule of
// `src/render/art/pack-rules.js`, which the page applies too (a pack breaking any is not loaded
// at all): an anchor outside its image (except a mask's), a sprite more than twice its painted
// size or larger than an atlas page, a mask or shadow without its own sprite in the pack, a mask
// that, placed by its anchor, lies outside its frame. Warnings: a sprite whose size differs from
// the painted one, a citizen frame without its mask (drawn in its own colours). It prints how
// many of the manifest's sprites the pack covers.
import { readFile } from 'node:fs/promises';
import { basename, resolve, sep } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { entryErrors, fileInside } from '../src/render/art/pack-rules.js';

const MANIFEST = fileURLToPath(new URL('../art/manifest.json', import.meta.url));
const PNG_SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
const NAME = /^[a-z0-9][a-z0-9_-]*$/;

/** A PNG's width and height from its IHDR chunk, or null if the bytes are not a PNG. */
export function pngSize(bytes) {
  if (bytes.length < 24 || !bytes.subarray(0, 8).equals(PNG_SIGNATURE)) return null;
  if (bytes.toString('latin1', 12, 16) !== 'IHDR') return null;
  return { w: bytes.readUInt32BE(16), h: bytes.readUInt32BE(20) };
}

/** Check the pack in `dir`: its errors, warnings and coverage. */
export async function checkPack(dir, manifestPath = MANIFEST) {
  const errors = [];
  const warnings = [];
  const manifest = JSON.parse(await readFile(manifestPath, 'utf8'));
  const root = resolve(dir);
  let pack;
  try {
    pack = JSON.parse(await readFile(`${root}/pack.json`, 'utf8'));
  } catch (err) {
    return { errors: [`pack.json: ${err.message}`], warnings, coverage: null };
  }
  if (!NAME.test(pack.name ?? '')) errors.push(`pack.json: "${pack.name}" is not a pack name (a-z, 0-9, _ and -)`);
  if (pack.name !== basename(root))
    errors.push(`pack.json: name "${pack.name}" is not the folder's, "${basename(root)}"`);
  if (typeof pack.license !== 'string' || !pack.license) errors.push('pack.json: no license');
  const entries = pack.entries;
  if (!entries || typeof entries !== 'object') {
    errors.push('pack.json: no entries');
    return { errors, warnings, coverage: null };
  }
  // Each readable image first, then the shared rules on all of them (a mask needs its frame's).
  const folder = pathToFileURL(root + sep);
  const read = new Map();
  for (const [key, entry] of Object.entries(entries).sort(([a], [b]) => a.localeCompare(b))) {
    const spec = manifest.keys[key];
    if (!spec) {
      errors.push(`${key}: not a key in the manifest`);
      continue;
    }
    const url = fileInside(entry?.file, folder);
    if (!url) {
      errors.push(`${key}: file "${entry?.file}" is not inside the pack`);
      continue;
    }
    let bytes;
    try {
      bytes = await readFile(fileURLToPath(url));
    } catch {
      errors.push(`${key}: ${entry.file} is missing`);
      continue;
    }
    const size = pngSize(bytes);
    if (!size || !size.w || !size.h) {
      errors.push(`${key}: ${entry.file} is not a PNG`);
      continue;
    }
    read.set(key, { spec, size, anchor: entry.anchor });
  }
  for (const [key, { spec, size, anchor }] of read) {
    const [base, extra] = key.split('#');
    // A mask or shadow whose sprite could not be read has had that sprite's error already.
    if (extra && base in entries && !read.has(base)) continue;
    errors.push(...entryErrors({ key, spec, size, anchor, sprite: read.get(base) }));
    if (spec.size && (spec.size.w !== size.w || spec.size.h !== size.h)) {
      warnings.push(`${key}: ${size.w}×${size.h}, painted at ${spec.size.w}×${spec.size.h}`);
    }
    if (!extra && spec.category === 'person' && `${key}#mask` in manifest.keys && !(`${key}#mask` in entries)) {
      warnings.push(`${key}: no mask, so it is drawn in its own colours whatever its civilization`);
    }
  }
  const sprites = Object.keys(manifest.keys).filter((key) => !key.includes('#'));
  const covered = sprites.filter((key) => key in entries);
  const byCategory = {};
  for (const key of covered) {
    const category = manifest.keys[key].category;
    byCategory[category] = (byCategory[category] ?? 0) + 1;
  }
  return { errors, warnings, coverage: { sprites: covered.length, of: sprites.length, byCategory } };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2);
  const at = args.indexOf('--manifest');
  const manifest = at >= 0 ? args.splice(at, 2)[1] : MANIFEST;
  if (args.length !== 1) {
    console.error('usage: node tests/art-pack-check.mjs art/packs/NAME [--manifest art/manifest.json]');
    process.exit(2);
  }
  const { errors, warnings, coverage } = await checkPack(args[0], manifest);
  for (const line of errors) console.log(`error: ${line}`);
  for (const line of warnings) console.log(`warning: ${line}`);
  if (coverage) {
    const parts = Object.entries(coverage.byCategory).map(([c, n]) => `${n} ${c}`);
    console.log(
      `${coverage.sprites} of ${coverage.of} sprites from this pack${parts.length ? ` (${parts.join(', ')})` : ''}`,
    );
  }
  console.log(errors.length ? `${errors.length} error(s)` : 'the pack is valid');
  process.exit(errors.length ? 1 : 0);
}
