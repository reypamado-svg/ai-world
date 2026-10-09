// GPU memory budgets that scale with the screen (Phase 5 S7).
//
// A bigger screen shows more ground at once, so it needs more ground patches
// and terrain textures to stay sharp. The caps scale with the screen's device
// pixels relative to 1600 x 900, up to 2.33 times (2560 x 1440 and above):
//   patches  96 MB x f; 192 px patches from 3.5 M device pixels; 320 x f entries at
//            256 px, x (256/px)^2 for smaller patches, so the byte cap is the one that binds;
//   terrain 128 MB x f, at most 192 MB (256 x f entries, at most 384).
// They are recomputed when the window is resized.

export const BASE_PIXELS = 1600 * 900;
export const MAX_FACTOR = 2.33;
const PATCH_BYTES = 96e6;
const PATCH_ENTRIES = 320;
const TERRAIN_BYTES = 128e6;
const TERRAIN_MAX_BYTES = 192e6;
const TERRAIN_ENTRIES = 256;
const TERRAIN_MAX_ENTRIES = 384;
const SMALL_PATCHES_FROM = 3.5e6;
const BASE_PATCH_PX = 256;

/** The screen factor: device pixels against 1600 x 900, clamped to 1 - 2.33. */
export function screenFactor(width, height, dpr = 1) {
  const px = width * height * dpr * dpr;
  return Math.min(MAX_FACTOR, Math.max(1, px / BASE_PIXELS));
}

/** Caps for a screen of `width` x `height` CSS pixels at device pixel ratio `dpr`. */
export function budgetsFor(width, height, dpr = 1) {
  const f = screenFactor(width, height, dpr);
  const pixels = width * height * dpr * dpr;
  const patchPx = pixels >= SMALL_PATCHES_FROM ? 192 : BASE_PATCH_PX;
  return {
    factor: Number(f.toFixed(3)),
    devicePixels: Math.round(pixels),
    patchBytes: Math.round(PATCH_BYTES * f),
    patchEntries: Math.round(PATCH_ENTRIES * f * (BASE_PATCH_PX / patchPx) ** 2),
    patchPx,
    terrainBytes: Math.round(Math.min(TERRAIN_MAX_BYTES, TERRAIN_BYTES * f)),
    terrainEntries: Math.round(Math.min(TERRAIN_MAX_ENTRIES, TERRAIN_ENTRIES * f)),
  };
}
