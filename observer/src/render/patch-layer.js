// Ground patches: the tile interior up close (PROTOTYPE ARTWORK).
//
// A 25 km tile spans five decades of zoom, more than any one texture can.
// From where a tile is about 1,700 px wide, the ground is drawn as square
// ground-plane patches of 3,200 m down to 6.25 m a side, each baked at 256
// texels from the interior colour field (world/interior.js) plus the rivers.
// The patch size follows the zoom so a texel stays about one to two screen
// pixels; coarser cached patches stay visible beneath until the finer ones are
// baked, so zooming never opens holes. Patches overlap their neighbours by one
// texel to hide seams. Each bake paints relative to its own corner (the
// local-origin rule) and the sprite carries the corner in float64.

import { K, project, unproject } from '../world/coords.js';
import { planeToHex } from '../world/hex.js';
import { LruCache } from '../world/chunks.js';
import { riverLine } from '../world/rivers.js';
import { hash2 } from '../sim/rng.js';

/** Patch sides in metres, coarse to fine. */
export const PATCH_SIZES = Array.from({ length: 10 }, (_, i) => 3200 / 2 ** i);
/** Texels per patch side (plus one texel of bleed on each side). */
export const PATCH_PX = 256;
/** A texel is never more than this many screen pixels across. */
const MAX_TEXEL_PX = 1.9;
const NEIGHBOURS = [
  [0, 0],
  [1, 0],
  [1, -1],
  [0, -1],
  [-1, 0],
  [-1, 1],
  [0, 1],
];

/** Tileable value noise on a period-P lattice, in 0..1. */
function periodicNoise(x, y, P, seed) {
  const ix = Math.floor(x);
  const iy = Math.floor(y);
  const sx = (x - ix) * (x - ix) * (3 - 2 * (x - ix));
  const sy = (y - iy) * (y - iy) * (3 - 2 * (y - iy));
  const h = (a, b) => hash2(((a % P) + P) % P, ((b % P) + P) % P, seed);
  const a = h(ix, iy);
  const b = h(ix + 1, iy);
  const c = h(ix, iy + 1);
  const d = h(ix + 1, iy + 1);
  return a + (b - a) * sx + (c - a) * sy + (a - b - c + d) * sx * sy;
}

let grainCanvas = null;

/** A 256 px tileable grey grain (soil, grass and stone texture), made once. */
function grain() {
  if (grainCanvas) return grainCanvas;
  const N = 256;
  grainCanvas = document.createElement('canvas');
  grainCanvas.width = N;
  grainCanvas.height = N;
  const ctx = grainCanvas.getContext('2d');
  const img = ctx.createImageData(N, N);
  for (let y = 0; y < N; y += 1) {
    for (let x = 0; x < N; x += 1) {
      const n =
        periodicNoise(x / 32, y / 32, 8, 1) * 0.45 + periodicNoise(x / 8, y / 8, 32, 2) * 0.35 + hash2(x, y, 3) * 0.2;
      const v = 70 + n * 115;
      const o = (y * N + x) * 4;
      img.data[o] = v;
      img.data[o + 1] = v;
      img.data[o + 2] = v;
      img.data[o + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0);
  return grainCanvas;
}

/** Grain periods in metres: one 256 px tile of grain covers this much ground. */
const GRAIN_PERIODS = [64, 12];

/** Screen pixels per ground metre along a plane axis at this zoom. */
const pxPerMetre = (zoom) => K * Math.SQRT2 * zoom;

/** Index into PATCH_SIZES for a zoom: the coarsest patch whose texels stay small enough on screen. */
export function patchLevelFor(zoom) {
  const limit = (MAX_TEXEL_PX * PATCH_PX) / pxPerMetre(zoom);
  const i = PATCH_SIZES.findIndex((s) => s <= limit);
  return i < 0 ? PATCH_SIZES.length - 1 : i;
}

export class PatchLayer {
  /**
   * terrain: the TerrainLayer (tile lookup, rivers, R);
   * interior(): the current interior colour field (world/interior.js makeInterior);
   * minZoom: patches are drawn from this zoom on.
   */
  constructor({ PIXI, terrain, interior, minZoom, gpuBytes = 96e6, maxEntries = 320, bakesPerFrame = 2 }) {
    this.PIXI = PIXI;
    this.terrain = terrain;
    this.interior = interior;
    this.minZoom = minZoom;
    this.bakesPerFrame = bakesPerFrame;
    this.container = new PIXI.Container();
    this.container.sortableChildren = true;
    this.cache = new LruCache({
      maxBytes: gpuBytes,
      maxEntries,
      onEvict: (key, value) => {
        value.sprite.destroy();
        value.texture.destroy(true);
      },
    });
    this.complete = true;
    this.visible = 0;
    this.baked = 0;
    this.bakeMs = 0;
    this.level = null;
  }

  /** Patches (i, j) of a level whose screen bounds meet the view, nearest first. */
  visiblePatches(view, level) {
    const size = PATCH_SIZES[level];
    const corners = [
      unproject(view.x0, view.y0),
      unproject(view.x1, view.y0),
      unproject(view.x0, view.y1),
      unproject(view.x1, view.y1),
    ];
    const i0 = Math.floor(Math.min(...corners.map((c) => c.x)) / size);
    const i1 = Math.floor(Math.max(...corners.map((c) => c.x)) / size);
    const j0 = Math.floor(Math.min(...corners.map((c) => c.y)) / size);
    const j1 = Math.floor(Math.max(...corners.map((c) => c.y)) / size);
    const cx = (view.x0 + view.x1) / 2;
    const cy = (view.y0 + view.y1) / 2;
    const out = [];
    for (let j = j0; j <= j1; j += 1) {
      for (let i = i0; i <= i1; i += 1) {
        const x0 = i * size;
        const y0 = j * size;
        // Screen bounds of the projected square (a diamond).
        const sx0 = (x0 - (y0 + size)) * K;
        const sx1 = (x0 + size - y0) * K;
        const sy0 = ((x0 + y0) * K) / 2;
        const sy1 = ((x0 + y0 + 2 * size) * K) / 2;
        if (sx1 < view.x0 || sx0 > view.x1 || sy1 < view.y0 || sy0 > view.y1) continue;
        out.push([Math.hypot((sx0 + sx1) / 2 - cx, (sy0 + sy1) / 2 - cy), i, j]);
      }
    }
    out.sort((a, b) => a[0] - b[0]);
    return out.map(([, i, j]) => [i, j]);
  }

  /** Every tile a patch touches, and their neighbours, is loaded (a bake needs them for blending). */
  _ready(x0, y0, size) {
    const R = this.terrain.R;
    const seen = new Set();
    for (const [fx, fy] of [
      [0, 0],
      [1, 0],
      [0, 1],
      [1, 1],
      [0.5, 0.5],
    ]) {
      const h = planeToHex(x0 + fx * size, y0 + fy * size, R);
      for (const [dq, dr] of NEIGHBOURS) {
        const key = `${h.q + dq},${h.r + dr}`;
        if (seen.has(key)) continue;
        seen.add(key);
        if (this.terrain.tileAt(h.q + dq, h.r + dr) === undefined) return false;
      }
    }
    return true;
  }

  update(view, zoom) {
    const show = zoom >= this.minZoom;
    this.container.visible = show;
    if (!show) {
      this.complete = true;
      this.visible = 0;
      this.level = null;
      return;
    }
    const level = patchLevelFor(zoom);
    this.level = level;
    const wanted = this.visiblePatches(view, level);
    this.visible = wanted.length;
    const pinned = new Set();
    const shown = new Set();
    let complete = true;
    let bakes = 0;
    for (const [i, j] of wanted) {
      const key = `${level}:${i},${j}`;
      pinned.add(key);
      if (!this.cache.has(key)) {
        const size = PATCH_SIZES[level];
        if (bakes < this.bakesPerFrame && this._ready(i * size, j * size, size)) {
          this._bake(key, level, i, j, pinned);
          bakes += 1;
        }
      }
      if (this.cache.has(key)) {
        this.cache.get(key);
        shown.add(key);
        continue;
      }
      complete = false;
      // Fallback: the nearest coarser patch already baked that covers this one.
      for (let k = 1; k <= level; k += 1) {
        const alt = `${level - k}:${Math.floor(i / 2 ** k)},${Math.floor(j / 2 ** k)}`;
        if (this.cache.has(alt)) {
          shown.add(alt);
          pinned.add(alt);
          break;
        }
      }
    }
    for (const [key, entry] of this.cache.map) entry.value.sprite.visible = shown.has(key);
    this.shown = shown;
    this.complete = complete;
  }

  _bake(key, level, i, j, pinned) {
    const t0 = performance.now();
    const { PIXI } = this;
    const size = PATCH_SIZES[level];
    const m = size / PATCH_PX; // metres per texel
    const W = PATCH_PX + 2;
    // Texel 0 lies one texel outside the patch (the bleed); everything is drawn relative to it.
    const ox = i * size - m;
    const oy = j * size - m;
    const canvas = document.createElement('canvas');
    canvas.width = W;
    canvas.height = W;
    const ctx = canvas.getContext('2d');
    // The colour field on a lattice, scaled up smoothly.
    const n = size >= 800 ? 65 : 33;
    const step = (W * m) / (n - 1);
    const lattice = document.createElement('canvas');
    lattice.width = n;
    lattice.height = n;
    const lctx = lattice.getContext('2d');
    const img = lctx.createImageData(n, n);
    const field = this.interior();
    for (let b = 0; b < n; b += 1) {
      for (let a = 0; a < n; a += 1) {
        const c = field.sample(ox + a * step, oy + b * step, step);
        const o = (b * n + a) * 4;
        img.data[o] = c[0];
        img.data[o + 1] = c[1];
        img.data[o + 2] = c[2];
        img.data[o + 3] = 255;
      }
    }
    lctx.putImageData(img, 0, 0);
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = 'high';
    const cell = step / m; // texels between lattice points
    ctx.drawImage(lattice, 0, 0, n, n, -cell / 2, -cell / 2, n * cell, n * cell);
    // Grain at ground scale, aligned to the world so neighbouring patches match.
    ctx.setTransform(1 / m, 0, 0, 1 / m, 0, 0);
    for (const P of GRAIN_PERIODS) {
      // Only where its coarse blotches (an eighth of the period) span at least two texels.
      if (P / 8 < 2 * m) continue;
      const pattern = ctx.createPattern(grain(), 'repeat');
      const mod = (v) => ((v % P) + P) % P;
      pattern.setTransform(new DOMMatrix().translate(-mod(ox), -mod(oy)).scale(P / 256));
      ctx.save();
      ctx.globalCompositeOperation = 'soft-light';
      ctx.globalAlpha = 0.55;
      ctx.fillStyle = pattern;
      ctx.fillRect(0, 0, W * m, W * m);
      ctx.restore();
    }
    // Rivers, in ground metres relative to texel 0.
    this._rivers(ctx, ox, oy, W * m, m);
    const texture = new PIXI.Texture({
      source: new PIXI.CanvasSource({ resource: canvas, autoGenerateMipmaps: true, scaleMode: 'linear' }),
    });
    const sprite = new PIXI.Sprite(texture);
    const A = project(ox, oy);
    // Texel (u, v) is the ground point (ox + u m, oy + v m), projected.
    sprite.setFromMatrix(new PIXI.Matrix(m * K, (m * K) / 2, -m * K, (m * K) / 2, A.x, A.y));
    sprite.zIndex = level;
    this.container.addChild(sprite);
    this.cache.set(key, { sprite, texture }, W * W * 4 * 1.34, pinned);
    this.baked += 1;
    this.bakeMs += performance.now() - t0;
  }

  /** River channels crossing the patch, in metres relative to (ox, oy). */
  _rivers(ctx, ox, oy, span, m) {
    const R = this.terrain.R;
    const edges = new Map();
    for (const [fx, fy] of [
      [0, 0],
      [1, 0],
      [0, 1],
      [1, 1],
      [0.5, 0.5],
    ]) {
      const h = planeToHex(ox + fx * span, oy + fy * span, R);
      for (const e of this.terrain.riverEdges(h.q, h.r)) edges.set(e.key, e);
    }
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    for (const e of edges.values()) {
      // Enough points that each straight piece is at most a few texels long on screen.
      const length = Math.hypot(e.p2.x - e.p1.x, e.p2.y - e.p1.y);
      const pts = riverLine(e, Math.min(4096, Math.max(24, Math.ceil(length / (m * 2))))).map((p) => [
        p.x - ox,
        p.y - oy,
      ]);
      // Skip borders nowhere near this patch.
      const near = pts.some(
        ([x, y]) => x > -e.widthM * 3 && y > -e.widthM * 3 && x < span + e.widthM * 3 && y < span + e.widthM * 3,
      );
      if (!near) continue;
      const w = Math.max(e.widthM, 1.5 * m);
      for (const [width, colour] of [
        [w * 2.2, 'rgba(120,100,60,0.35)'],
        [w, e.deep ? '#2f6b9e' : '#4f8fbf'],
        [w * 0.35, 'rgba(170,205,235,0.45)'],
      ]) {
        ctx.strokeStyle = colour;
        ctx.lineWidth = width;
        ctx.beginPath();
        pts.forEach(([x, y], k) => (k ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
        ctx.stroke();
      }
      if (!e.deep) {
        // Ford (presentation): a gravel bar across the middle of the border.
        const mid = pts[Math.floor(pts.length / 2)];
        const angle = Math.atan2(e.p2.y - e.p1.y, e.p2.x - e.p1.x);
        ctx.fillStyle = 'rgba(214,200,160,0.85)';
        ctx.beginPath();
        ctx.ellipse(mid[0], mid[1], w * 0.3, w * 0.7, angle, 0, Math.PI * 2);
        ctx.fill();
      }
    }
  }

  /** Evict every patch not currently shown. */
  releaseHidden() {
    for (const key of [...this.cache.map.keys()]) if (!this.shown?.has(key)) this.cache.delete(key);
  }

  stats() {
    const c = this.cache.stats();
    return {
      level: this.level,
      size: this.level === null ? null : PATCH_SIZES[this.level],
      visible: this.visible,
      entries: c.entries,
      bytes: c.bytes,
      peakBytes: c.peakBytes,
      baked: this.baked,
      bakeMsAvg: this.baked ? this.bakeMs / this.baked : 0,
      complete: this.complete,
    };
  }
}
