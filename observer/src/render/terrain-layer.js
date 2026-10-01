// Terrain layer: engine tiles drawn as hexes, streamed by chunk.
//
// Chunk textures are baked at a resolution matched to the zoom (texture
// pixels per world-screen pixel), kept in a bounded GPU cache and destroyed
// on eviction. While a finer texture is missing, any coarser one already
// cached for that chunk is shown instead, so zooming never opens holes.
//
// Terrain values are independent per engine tile; the painter never smooths
// across tiles. Decoration (tree and peak glyphs) is seeded per tile and only
// appears where the tile's own values support it.

import { project, unproject } from '../world/coords.js';
import { chunkScreenBounds, hexCentre, hexCorners, planeToHex, worldScreenBounds } from '../world/hex.js';
import { ChunkLoader, LruCache } from '../world/chunks.js';
import { hash2 } from '../sim/rng.js';
import { css, mix } from './art/paint/color.js';

/** Texture pixels per world-screen pixel, coarse to fine. */
export const CHUNK_LEVELS = [0.0125, 0.025, 0.05, 0.1];

const LAND = {
  1: '#86a24f', // grassland
  2: '#4c7a3a', // forest
  3: '#8c8476', // mountain
  4: '#d2b97f', // desert
  5: '#c9cfc8', // tundra
};

function tileColor(terrain, elevation, q, r) {
  if (terrain === 0) return mix('#264f78', '#4a80ae', Math.min(1, elevation / 120));
  const base = LAND[terrain] ?? '#888';
  const f = 0.84 + (elevation / 1000) * 0.3 + (hash2(q, r, 5) - 0.5) * 0.06;
  return terrain === 3 ? mix(base, '#b8b0a2', Math.max(0, (elevation - 820) / 180)).map((c) => c * f) : css(base, f);
}

export class TerrainLayer {
  constructor({ PIXI, source, gpuBytes = 96e6, gpuEntries = 96, cpuBytes = 24e6, maxInFlight = 4, bakesPerFrame = 3 }) {
    this.PIXI = PIXI;
    this.source = source;
    this.R = source.manifest.presentation.hex_radius_m;
    this.ct = source.manifest.presentation.chunk_tiles;
    this.width = source.width;
    this.height = source.height;
    this.container = new PIXI.Container();
    this.chunks = new LruCache({ maxBytes: cpuBytes });
    this.loader = new ChunkLoader(source, this.chunks, { maxInFlight });
    this.sprites = new Map();
    this.textures = new LruCache({
      maxBytes: gpuBytes,
      maxEntries: gpuEntries,
      onEvict: (key, value) => {
        value.sprite.destroy();
        value.texture.destroy(true);
        this.sprites.delete(key);
        this.liveTextures -= 1;
      },
    });
    this.bakesPerFrame = bakesPerFrame;
    this.liveTextures = 0;
    this.baked = 0;
    this.visibleKeys = [];
    this.level = CHUNK_LEVELS[0];
    this.complete = false;
    this.world = worldScreenBounds(this.width, this.height, this.R);
    this.boundsCache = new Map();
  }

  chunkBounds(cq, cr) {
    const k = `${cq},${cr}`;
    if (!this.boundsCache.has(k)) {
      this.boundsCache.set(k, chunkScreenBounds(cq, cr, this.ct, this.R, this.width, this.height));
      if (this.boundsCache.size > 20000) this.boundsCache.clear();
    }
    return this.boundsCache.get(k);
  }

  /** Chunk keys whose screen bounds meet the view rect, nearest first. */
  visibleChunks(view) {
    const corners = [
      unproject(view.x0, view.y0),
      unproject(view.x1, view.y0),
      unproject(view.x0, view.y1),
      unproject(view.x1, view.y1),
    ];
    const hexes = corners.map((c) => planeToHex(c.x, c.y, this.R));
    const qs = hexes.map((h) => h.q);
    const rs = hexes.map((h) => h.r);
    const nq = Math.ceil(this.width / this.ct);
    const nr = Math.ceil(this.height / this.ct);
    const cq0 = Math.max(0, Math.floor((Math.min(...qs) - 1) / this.ct));
    const cq1 = Math.min(nq - 1, Math.floor((Math.max(...qs) + 1) / this.ct));
    const cr0 = Math.max(0, Math.floor((Math.min(...rs) - 1) / this.ct));
    const cr1 = Math.min(nr - 1, Math.floor((Math.max(...rs) + 1) / this.ct));
    const cx = (view.x0 + view.x1) / 2;
    const cy = (view.y0 + view.y1) / 2;
    const out = [];
    for (let cr = cr0; cr <= cr1; cr += 1) {
      for (let cq = cq0; cq <= cq1; cq += 1) {
        const b = this.chunkBounds(cq, cr);
        if (b.x1 < view.x0 || b.x0 > view.x1 || b.y1 < view.y0 || b.y0 > view.y1) continue;
        const d = Math.hypot((b.x0 + b.x1) / 2 - cx, (b.y0 + b.y1) / 2 - cy);
        out.push([d, `${cq},${cr}`]);
      }
    }
    out.sort((a, b) => a[0] - b[0]);
    return out.map((e) => e[1]);
  }

  levelFor(zoom) {
    return CHUNK_LEVELS.find((s) => s >= zoom) ?? CHUNK_LEVELS[CHUNK_LEVELS.length - 1];
  }

  update(view, zoom) {
    this.level = this.levelFor(zoom);
    const keys = this.visibleChunks(view);
    this.visibleKeys = keys;
    this.loader.want(keys);
    this.loader.pump();
    const wantedTex = new Set(keys.map((k) => `${k}@${this.level}`));
    let bakes = 0;
    let complete = this.loader.idle;
    const show = new Set();
    for (const key of keys) {
      const texKey = `${key}@${this.level}`;
      if (!this.textures.has(texKey)) {
        const chunk = this.chunks.get(key);
        if (chunk && bakes < this.bakesPerFrame) {
          this._bake(key, chunk, this.level, wantedTex);
          bakes += 1;
        }
      }
      if (this.textures.has(texKey)) {
        this.textures.get(texKey);
        show.add(texKey);
      } else {
        complete = false;
        // Fallback: the finest other level already cached for this chunk.
        for (let i = CHUNK_LEVELS.length - 1; i >= 0; i -= 1) {
          const alt = `${key}@${CHUNK_LEVELS[i]}`;
          if (this.textures.has(alt)) {
            show.add(alt);
            break;
          }
        }
      }
    }
    for (const [k, sprite] of this.sprites) sprite.visible = show.has(k);
    this.shown = show;
    // Coarser fallbacks must draw beneath finer textures.
    this.complete = complete;
  }

  _bake(key, chunk, s, pinned) {
    const { PIXI } = this;
    const b = this.chunkBounds(chunk.cq, chunk.cr);
    const w = Math.max(2, Math.ceil((b.x1 - b.x0) * s) + 2);
    const h = Math.max(2, Math.ceil((b.y1 - b.y0) * s) + 2);
    const canvas = document.createElement('canvas');
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext('2d');
    ctx.setTransform(s, 0, 0, s, -b.x0 * s + 1, -b.y0 * s + 1);
    this.paintChunk(ctx, chunk, s);
    const texture = new PIXI.Texture({
      source: new PIXI.CanvasSource({ resource: canvas, autoGenerateMipmaps: true, scaleMode: 'linear' }),
    });
    const sprite = new PIXI.Sprite(texture);
    sprite.position.set(b.x0 - 1 / s, b.y0 - 1 / s);
    sprite.scale.set(1 / s);
    sprite.zIndex = CHUNK_LEVELS.indexOf(s);
    this.container.sortableChildren = true;
    this.container.addChild(sprite);
    const texKey = `${key}@${s}`;
    this.sprites.set(texKey, sprite);
    this.liveTextures += 1;
    this.baked += 1;
    this.textures.set(texKey, { sprite, texture }, w * h * 4 * 1.34, pinned);
  }

  /** Paint a chunk's hexes in world-screen coordinates (the caller scales). */
  paintChunk(ctx, c, s) {
    const R = this.R;
    const px = 1 / s; // one texture pixel in world-screen units
    const index = new Map();
    for (let i = 0; i < c.n; i += 1) index.set(`${c.q[i]},${c.r[i]}`, i);
    for (let i = 0; i < c.n; i += 1) {
      const q = c.q[i];
      const r = c.r[i];
      const pts = hexCorners(q, r, R).map((p) => project(p.x, p.y));
      ctx.beginPath();
      pts.forEach((p, k) => (k ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
      ctx.closePath();
      const col = tileColor(c.terrain[i], c.elevation[i], q, r);
      ctx.fillStyle = typeof col === 'string' ? col : `rgb(${col.map(Math.round).join(',')})`;
      ctx.fill();
      // Hex grid line, about one texture pixel.
      ctx.strokeStyle = 'rgba(20,24,20,0.16)';
      ctx.lineWidth = px;
      ctx.stroke();
    }
    // Rivers: a ribbon inside flagged tiles only, joined to river neighbours along the column.
    ctx.lineCap = 'round';
    for (let i = 0; i < c.n; i += 1) {
      if (!c.river[i]) continue;
      const q = c.q[i];
      const r = c.r[i];
      const centre = hexCentre(q, r, R);
      const pc = project(centre.x, centre.y);
      ctx.strokeStyle = '#4f86b4';
      ctx.lineWidth = Math.max(R * 0.2 * 16, 1.5 * px);
      for (const dr of [-1, 1]) {
        const nr = r + dr;
        if (nr < 0 || nr >= this.height) continue;
        const j = index.get(`${q},${nr}`);
        if (j !== undefined && !c.river[j]) continue;
        const nb = hexCentre(q, nr, R);
        const mid = project((centre.x + nb.x) / 2, (centre.y + nb.y) / 2);
        ctx.beginPath();
        ctx.moveTo(pc.x, pc.y);
        ctx.lineTo(mid.x, mid.y);
        ctx.stroke();
      }
    }
    // Glyphs where a hex is big enough to read them (>= ~18 texture px).
    const hexPx = R * Math.sqrt(3) * Math.SQRT2 * 16 * s;
    if (hexPx < 18) return;
    for (let i = 0; i < c.n; i += 1) {
      const t = c.terrain[i];
      const q = c.q[i];
      const r = c.r[i];
      const centre = hexCentre(q, r, R);
      const count = t === 2 ? 5 + Math.floor(c.timber[i] / 200) : t === 1 && c.timber[i] > 650 ? 2 : t === 3 ? 2 : 0;
      for (let k = 0; k < count; k += 1) {
        const a = hash2(q * 7 + k, r, 11) * Math.PI * 2;
        const d = Math.sqrt(hash2(q, r * 5 + k, 12)) * R * 0.55;
        const p = project(centre.x + Math.cos(a) * d, centre.y + Math.sin(a) * d);
        if (t === 3) this._peak(ctx, p, R * 0.28 * 16 * (0.8 + hash2(q, k, 13) * 0.4), c.elevation[i] > 900);
        else this._tree(ctx, p, R * 0.09 * 16 * (0.8 + hash2(k, r, 14) * 0.4));
      }
    }
  }

  _tree(ctx, p, size) {
    ctx.fillStyle = '#1f3a1c';
    ctx.beginPath();
    ctx.ellipse(p.x, p.y - size * 0.9, size * 1.05, size * 0.95, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = '#5f8a42';
    ctx.beginPath();
    ctx.ellipse(p.x - size * 0.25, p.y - size * 1.1, size * 0.55, size * 0.5, 0, 0, Math.PI * 2);
    ctx.fill();
  }

  _peak(ctx, p, size, snow) {
    ctx.fillStyle = '#9a9284';
    ctx.beginPath();
    ctx.moveTo(p.x - size, p.y);
    ctx.lineTo(p.x, p.y - size * 1.1);
    ctx.lineTo(p.x, p.y);
    ctx.closePath();
    ctx.fill();
    ctx.fillStyle = '#6a6458';
    ctx.beginPath();
    ctx.moveTo(p.x, p.y - size * 1.1);
    ctx.lineTo(p.x + size, p.y);
    ctx.lineTo(p.x, p.y);
    ctx.closePath();
    ctx.fill();
    if (snow) {
      ctx.fillStyle = '#eef0f2';
      ctx.beginPath();
      ctx.moveTo(p.x - size * 0.32, p.y - size * 0.75);
      ctx.lineTo(p.x, p.y - size * 1.1);
      ctx.lineTo(p.x + size * 0.32, p.y - size * 0.75);
      ctx.closePath();
      ctx.fill();
    }
  }

  /** Evict every texture that is not currently shown. */
  releaseHidden() {
    for (const key of [...this.textures.map.keys()]) if (!this.shown?.has(key)) this.textures.delete(key);
  }

  stats() {
    return {
      level: this.level,
      visibleChunks: this.visibleKeys.length,
      loadedChunks: this.chunks.stats().entries,
      cpu: this.chunks.stats(),
      gpu: this.textures.stats(),
      liveTextures: this.liveTextures,
      baked: this.baked,
      loader: { ...this.loader.stats, inFlight: this.loader.inFlight.size, generation: this.loader.generation },
      complete: this.complete,
    };
  }
}
