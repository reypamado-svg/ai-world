// Close-up decoration: tree, bush and rock sprites from the shared atlas
// (PROTOTYPE ARTWORK), placed in 50 m ground cells from the engine values of
// the tile beneath (timber for trees, stone for rocks). Placement is seeded
// per cell, so the same place always shows the same trees. Cells are built on
// demand, kept in a bounded cache, and each cell's sprites are positioned
// relative to the cell (the local-origin rule).
//
// Nothing grows in water, in a river channel or inside the SAMPLE village and
// its field ring.
//
// Limitation (prototype): sprites are depth-sorted within a cell and cells by
// their position, not against travellers, who are drawn above them.

import { K, project, unproject } from '../world/coords.js';
import { planeToHex } from '../world/hex.js';
import { LruCache } from '../world/chunks.js';
import { riverLine } from '../world/rivers.js';
import { hashString, mulberry32 } from '../sim/rng.js';
import { ART } from './art/paint/iso.js';
import { OPEN, ROCK, SCRUB, WOOD } from '../world/interior.js';

/** Side of a decoration cell, in ground metres. */
export const CELL_M = 50;
/** Decoration shows from this zoom (a tree is then a few screen pixels tall). */
export const DECOR_MIN_ZOOM = 0.2;

/**
 * What grows in a cell of this tile: counts of trees (and the share that are pines), bushes and
 * rocks to try. With land cover each is tried at a full density and kept only where its cover
 * class shows (trees in woods, rocks on rock, bushes on scrub and open ground), so how many
 * appear follows the engine's shares.
 */
export function cellPlan(t) {
  const out = { trees: 0, pines: 0, bushes: 0, rocks: 0 };
  if (!t || t.terrain === 0) return out;
  if (t.cover) {
    const cold = t.terrain === 5 || t.terrain === 3 || t.terrain === 7;
    out.trees = t.cover[WOOD] > 0 ? 14 : 0;
    out.pines = cold ? 1 : t.terrain === 6 ? 0.5 : 0.3;
    out.bushes = (t.cover[SCRUB] > 0 ? 3 : 0) + (t.cover[OPEN] >= 5000 ? 1 : 0);
    out.rocks = t.cover[ROCK] > 0 ? 4 : 0;
    return out;
  }
  if (t.terrain === 2) {
    out.trees = 4 + Math.floor(t.timber / 100);
    out.pines = 0.3;
  } else if (t.terrain === 1) {
    out.trees = t.timber > 600 ? Math.floor((t.timber - 600) / 150) : 0;
    out.bushes = 1;
  } else if (t.terrain === 5) {
    out.trees = t.timber > 700 ? 1 : 0;
    out.pines = 1;
  } else if (t.terrain === 6) {
    out.bushes = 1;
  }
  if (t.stone > 600) out.rocks = Math.floor((t.stone - 600) / 200) + (t.terrain === 3 || t.terrain === 6 ? 1 : 0);
  return out;
}

/** The cover classes each kind of decoration stands on (nothing grows in wetland). */
const GROWS_ON = {
  oak: [WOOD],
  pine: [WOOD],
  bush: [SCRUB, OPEN],
  rock: [ROCK],
};

/** Shortest distance from (x, y) to a polyline. */
function distanceToLine(x, y, pts) {
  let best = Infinity;
  for (let k = 1; k < pts.length; k += 1) {
    const a = pts[k - 1];
    const b = pts[k];
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const len2 = dx * dx + dy * dy || 1;
    const f = Math.max(0, Math.min(1, ((x - a.x) * dx + (y - a.y) * dy) / len2));
    best = Math.min(best, Math.hypot(x - a.x - dx * f, y - a.y - dy * f));
  }
  return best;
}

export class DecorLayer {
  /**
   * terrain: the TerrainLayer (tile lookup, rivers, interior field);
   * clear: [{ x, y, r }] ground circles kept free of decoration (the village and its fields).
   */
  constructor({ PIXI, atlas, terrain, clear = [], maxCells = 256 }) {
    this.PIXI = PIXI;
    this.atlas = atlas;
    this.terrain = terrain;
    this.clear = clear;
    this.container = new PIXI.Container();
    this.container.sortableChildren = true;
    this.cache = new LruCache({
      maxBytes: Infinity,
      maxEntries: maxCells,
      onEvict: (key, c) => c.destroy({ children: true }),
    });
    this.sprites = 0;
  }

  /** Cells (i, j) whose screen bounds, widened for tall sprites, meet the view. */
  visibleCells(view) {
    const corners = [
      unproject(view.x0, view.y0),
      unproject(view.x1, view.y0),
      unproject(view.x0, view.y1),
      unproject(view.x1, view.y1),
    ];
    const i0 = Math.floor(Math.min(...corners.map((c) => c.x)) / CELL_M) - 1;
    const i1 = Math.floor(Math.max(...corners.map((c) => c.x)) / CELL_M) + 1;
    const j0 = Math.floor(Math.min(...corners.map((c) => c.y)) / CELL_M) - 1;
    const j1 = Math.floor(Math.max(...corners.map((c) => c.y)) / CELL_M) + 1;
    // Trees stand up to ~20 m tall: let cells just below the view count.
    const tall = 20 * 14;
    const out = [];
    for (let j = j0; j <= j1; j += 1) {
      for (let i = i0; i <= i1; i += 1) {
        const x0 = i * CELL_M;
        const y0 = j * CELL_M;
        const sx0 = (x0 - (y0 + CELL_M)) * K;
        const sx1 = (x0 + CELL_M - y0) * K;
        const sy0 = ((x0 + y0) * K) / 2 - tall;
        const sy1 = ((x0 + y0 + 2 * CELL_M) * K) / 2;
        if (sx1 < view.x0 || sx0 > view.x1 || sy1 < view.y0 || sy0 > view.y1) continue;
        out.push([i, j]);
      }
    }
    return out;
  }

  /** Build a cell's sprites; null while its tile is not loaded. */
  _build(i, j) {
    const x0 = i * CELL_M;
    const y0 = j * CELL_M;
    const cx = x0 + CELL_M / 2;
    const cy = y0 + CELL_M / 2;
    const h = planeToHex(cx, cy, this.terrain.R);
    const t = this.terrain.tileAt(h.q, h.r);
    if (t === undefined) return null;
    const c = new this.PIXI.Container();
    c.sortableChildren = true;
    const origin = project(x0, y0);
    c.position.set(origin.x, origin.y);
    c.zIndex = i + j;
    const plan = cellPlan(t);
    if (plan.trees + plan.bushes + plan.rocks === 0) return c;
    if (this.clear.some((z) => Math.hypot(cx - z.x, cy - z.y) < z.r + CELL_M)) return c;
    // River channels nearby: keep their banks clear.
    const rivers = this.terrain
      .riverEdges(h.q, h.r)
      .map((e) => ({ e, pts: riverLine(e, 96) }))
      .filter(({ e, pts }) => distanceToLine(cx, cy, pts) < e.widthM / 2 + CELL_M);
    const field = this.terrain.interior();
    const rng = mulberry32(hashString(`cell:${i},${j}`));
    const items = [];
    const place = (kind, variants) => {
      const x = x0 + rng() * CELL_M;
      const y = y0 + rng() * CELL_M;
      const variant = Math.floor(rng() * variants);
      if (field.waterAt(x, y)) return;
      const shown = field.classAt(x, y);
      if (shown >= 0 && !GROWS_ON[kind].includes(shown)) return;
      if (rivers.some(({ e, pts }) => distanceToLine(x, y, pts) < e.widthM / 2 + 6)) return;
      items.push({ kind, variant, x, y });
    };
    for (let k = 0; k < plan.trees; k += 1) place(rng() < plan.pines ? 'pine' : 'oak', 6);
    for (let k = 0; k < plan.bushes; k += 1) place('bush', 4);
    for (let k = 0; k < plan.rocks; k += 1) place('rock', 4);
    for (const d of items) {
      const e = this.atlas.get(`nature.${d.kind}.${d.variant}`);
      if (!e) continue;
      const s = new this.PIXI.Sprite(e.texture);
      s.anchor.set(e.anchor.x / e.w, e.anchor.y / e.h);
      s.scale.set(1 / ART);
      const p = project(d.x - x0, d.y - y0);
      s.position.set(p.x, p.y);
      s.zIndex = d.x - x0 + (d.y - y0);
      c.addChild(s);
    }
    return c;
  }

  update(view, zoom) {
    const show = this.terrain.hexMode && zoom >= DECOR_MIN_ZOOM;
    this.container.visible = show;
    if (!show) return;
    const wanted = new Set();
    for (const [i, j] of this.visibleCells(view)) {
      const key = `${i},${j}`;
      wanted.add(key);
      if (this.cache.has(key)) {
        this.cache.get(key);
        continue;
      }
      const c = this._build(i, j);
      if (c) {
        this.container.addChild(c);
        this.cache.set(key, c, 0, wanted);
      }
    }
    let sprites = 0;
    for (const [key, entry] of this.cache.map) {
      entry.value.visible = wanted.has(key);
      if (entry.value.visible) sprites += entry.value.children.length;
    }
    this.sprites = sprites;
  }
}
