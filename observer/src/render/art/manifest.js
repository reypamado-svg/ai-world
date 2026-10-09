// The art manifest (O6): every key the renderer can ask the atlas for, with what a sprite under
// that key must be. It is what an art-pack author reads, and it is generated from the painters
// themselves (`node tests/art-manifest.mjs`), so it cannot drift from what is drawn.
//
// For each key:
//   category   building | nature | prop | person | vehicle | shadow | mask
//   art        how the atlas keeps it: 2 (twice screen size, buildings) or 1 (screen size)
//   size       the painted sprite in art pixels (2x screen pixels at zoom 1), for a pack to match
//   anchor     the art pixel that sits on the ground point
//   footprint  the ground box in metres around the anchor (static assets; fixed by the
//              registry, used for depth order, and checked against the sprite's width)
//   height     roof height in metres (buildings), to the centimetre
// and, for keys ending `#mask`, `size: null`: a mask is cropped to the painted accents, so a pack
// may give any size, anchored to the same ground point as its frame.

import { CIV_COLORS } from './registry.js';
import { bakeSceneActors, bakeStaticAssets } from './bake.js';
import { APPEARANCE_COUNT } from './paint/people.js';

export const MANIFEST_VERSION = 1;

/** An atlas stand-in that records what is added instead of packing it. */
class RecordingAtlas {
  constructor() {
    this.entries = new Map();
  }

  add(key, canvas, anchor, meta = {}, { art = 2 } = {}) {
    if (this.entries.has(key)) return this.entries.get(key);
    const entry = { key, art, size: { w: canvas.width, h: canvas.height }, anchor: { ...anchor }, meta };
    this.entries.set(key, entry);
    return entry;
  }
}

function categoryOf(key, statics) {
  if (key.endsWith('#mask')) return 'mask';
  if (key.endsWith('#shadow') || key.startsWith('shadow.')) return 'shadow';
  if (statics.has(key)) return statics.get(key).category;
  if (key.startsWith('person.') || key.startsWith('envoy.')) return 'person';
  return 'vehicle';
}

/** The manifest, painted fresh: every static asset, every citizen design, envoys of every
 * civilization, the ox and the wagons. */
export async function artManifest() {
  const atlas = new RecordingAtlas();
  const { assetInfo } = await bakeStaticAssets(atlas, CIV_COLORS[0]);
  const people = Array.from({ length: APPEARANCE_COUNT }, (_, appearance) => ({ appearance }));
  const envoys = CIV_COLORS.map((_, civ) => ({ envoy: true, appearance: 0, civ }));
  await bakeSceneActors(atlas, { people: [...people, ...envoys], caravan: null });
  const keys = {};
  for (const key of [...atlas.entries.keys()].sort()) {
    const e = atlas.entries.get(key);
    const category = categoryOf(key, assetInfo);
    const info = assetInfo.get(key);
    keys[key] = {
      category,
      art: e.art,
      size: category === 'mask' ? null : e.size,
      anchor: category === 'mask' ? null : e.anchor,
      ...(info?.footprint ? { footprint: info.footprint } : {}),
      ...(info?.height !== undefined ? { height: Math.round(info.height * 100) / 100 } : {}),
    };
  }
  return {
    manifest_version: MANIFEST_VERSION,
    conventions: {
      art_pixels: 'Sizes and anchors are in art pixels, twice screen pixels at zoom 1. A pack draws at that size.',
      anchor: 'The pixel that sits on the ground point.',
      mask: "`KEY#mask` holds a frame's civilization accents in white; it is tinted with the civilization's colour and drawn over the frame, anchored to the same ground point. A frame from a pack without its mask is drawn in its own colours.",
      shadow: '`KEY#shadow` is an optional soft shadow drawn under the sprite.',
      footprint:
        'Fixed by the registry, not by a pack: the ground box used for depth order. A sprite must not be wider than its projected footprint plus a small overhang.',
      civilization_colours: CIV_COLORS,
      buildings: 'Building colours (banners, pennants) are painted in; buildings have no mask.',
    },
    keys,
  };
}
