// The rules an art pack's entries must keep (O6), shared by the page (`bake.js`, which refuses a
// pack that breaks any of them, whole) and the pack checker (`tests/art-pack-check.mjs`), so the
// two never disagree about a pack. No imports: it runs in the browser and in Node.

/** An atlas page, in pixels. The atlas keeps art-2 sprites at full size and halves art-1 ones. */
export const PAGE = 2048;

/** A pack sprite may be finer than the painted one, up to this many times its size each way.
 * The crowd sheet (`crowd-layer.js`) holds every citizen still and town icon with every one at
 * this size (1,010 of its 1,024 px), and grows to 2,048 if a future asset set would not fit. */
export const SIZE_FACTOR = 2;

/**
 * The URL of a pack's file under the pack folder `dir` (a URL ending in `/`), or null when it
 * leaves the folder: `..` (also `%2e%2e`), an absolute path, another origin or scheme, or a query
 * or fragment. Subfolders of the pack are fine.
 */
export function fileInside(file, dir) {
  if (typeof file !== 'string' || !file) return null;
  let url;
  try {
    url = new URL(file, dir);
  } catch {
    return null;
  }
  const inside =
    url.protocol === dir.protocol &&
    url.origin === dir.origin &&
    url.pathname.startsWith(dir.pathname) &&
    url.pathname.length > dir.pathname.length &&
    !url.search &&
    !url.hash;
  return inside ? url : null;
}

/**
 * Why one pack entry may not be used (messages start `KEY: `), or [] when it may.
 * @param {{ key: string, spec: object, size: { w: number, h: number }, anchor: object,
 *   sprite?: { size: { w: number, h: number }, anchor: { x: number, y: number } } }} entry
 *   `spec` is the manifest's for the key; `size` and `anchor` the pack's image and anchor; for a
 *   `KEY#mask` or `KEY#shadow`, `sprite` is the pack's own KEY (undefined when it has none).
 */
export function entryErrors({ key, spec, size, anchor, sprite }) {
  const errors = [];
  const { x, y } = anchor ?? {};
  // A mask is cropped to its accents, so its anchor, the frame's ground point, may lie outside.
  const outside = x < 0 || y < 0 || x > size.w || y > size.h;
  if (!Number.isFinite(x) || !Number.isFinite(y) || (outside && spec.category !== 'mask')) {
    errors.push(`${key}: anchor ${JSON.stringify(anchor ?? null)} is not inside its ${size.w}×${size.h} image`);
  }
  if (spec.size && (size.w > SIZE_FACTOR * spec.size.w || size.h > SIZE_FACTOR * spec.size.h)) {
    errors.push(`${key}: ${size.w}×${size.h} is larger than twice the painted ${spec.size.w}×${spec.size.h}`);
  }
  const fits = spec.art === 2 ? PAGE : PAGE * 2;
  if (size.w > fits || size.h > fits) errors.push(`${key}: ${size.w}×${size.h} is larger than an atlas page`);
  const [base, extra] = key.split('#');
  if (extra && !sprite) {
    errors.push(`${key}: the pack has no ${base}, and a ${extra} is drawn for its own sprite`);
  } else if (extra === 'mask' && Number.isFinite(x) && Number.isFinite(y)) {
    // Drawn over its frame with the two anchors on one point: it must lie within the frame.
    const dx = sprite.anchor.x - x;
    const dy = sprite.anchor.y - y;
    if (dx < 0 || dy < 0 || dx + size.w > sprite.size.w || dy + size.h > sprite.size.h) {
      errors.push(`${key}: placed by its anchor it lies outside its ${sprite.size.w}×${sprite.size.h} frame`);
    }
  }
  return errors;
}
