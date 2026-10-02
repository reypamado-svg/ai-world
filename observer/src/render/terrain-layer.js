// Terrain layer: engine tiles drawn as hexes, streamed by chunk.
//
// Chunk textures are baked at a resolution matched to the zoom (texture
// pixels per world-screen pixel), kept in a bounded GPU cache and destroyed
// on eviction. While a finer texture is missing, any coarser one already
// cached for that chunk is shown instead, so zooming never opens holes.
//
// Decoration (tree, hill and peak glyphs) is seeded per tile and only appears
// where the tile's own values support it. Rivers run along tile borders, as
// the engine records them, wider where more water has gathered.

import { project, unproject } from '../world/coords.js';
import {
  chunkOf,
  chunkScreenBounds,
  hexCentre,
  hexCorners,
  hexRadiusOf,
  planeToHex,
  sharedCorners,
  screenBoundsOfTiles,
  worldScreenBounds,
  zoomForTilePx,
} from '../world/hex.js';
import { paintHexDetail } from './hex-detail.js';
import { ChunkLoader, LruCache } from '../world/chunks.js';
import { hash2 } from '../sim/rng.js';
import { css, mix } from './art/paint/color.js';

/** Chunk texture resolutions, as screen pixels per tile, coarse to fine. */
export const CHUNK_TILE_PX = [32, 64, 128, 256];
/** From this tile size on screen, each visible tile gets its own detailed texture. */
export const HEX_MODE_TILE_PX = 256;
/** Per-tile texture resolutions, as pixels per tile. */
export const HEX_TILE_PX = [512, 1024];
const MAX_DETAIL_HEXES = 120;

const LAND = {
  1: '#86a24f', // grassland
  2: '#4c7a3a', // forest
  3: '#8c8476', // mountain
  4: '#d2b97f', // desert
  5: '#b9bfa8', // tundra
  6: '#9c9461', // hills
  7: '#e9edf0', // snow
};

function tileColor(terrain, elevation, q, r, lake = false) {
  if (terrain === 0)
    return lake
      ? mix('#2f6f8a', '#4f97ad', Math.min(1, elevation / 150))
      : mix('#1f4670', '#3f76a6', Math.min(1, elevation / 150));
  if (terrain === 7) return css(LAND[7], 0.94 + (hash2(q, r, 5) - 0.5) * 0.06);
  const base = LAND[terrain] ?? '#888';
  const f = 0.84 + (elevation / 1000) * 0.3 + (hash2(q, r, 5) - 0.5) * 0.06;
  return terrain === 3 ? mix(base, '#b8b0a2', Math.max(0, (elevation - 820) / 180)).map((c) => c * f) : css(base, f);
}

/** River ribbon width in world-screen pixels at zoom 1 (presentation). */
export function riverWidth(flow, R) {
  return R * 16 * (0.035 + 0.022 * Math.log2(1 + flow));
}

export class TerrainLayer {
  constructor({ PIXI, source, gpuBytes = 96e6, gpuEntries = 96, cpuBytes = 24e6, maxInFlight = 4, bakesPerFrame = 3 }) {
    this.PIXI = PIXI;
    this.source = source;
    this.R = hexRadiusOf(source.manifest);
    // Texture pixels per world-screen pixel for each level; the same tile sizes at any scale.
    this.chunkLevels = CHUNK_TILE_PX.map((px) => zoomForTilePx(px, this.R));
    this.hexModeZoom = zoomForTilePx(HEX_MODE_TILE_PX, this.R);
    this.hexLevels = HEX_TILE_PX.map((px) => zoomForTilePx(px, this.R));
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
    this.level = this.chunkLevels[0];
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
    return this.chunkLevels.find((s) => s >= zoom) ?? this.chunkLevels[this.chunkLevels.length - 1];
  }

  update(view, zoom) {
    this.lastView = view;
    this.hexMode = zoom >= this.hexModeZoom;
    // In hex mode the chunk textures are only a coarse base under the tiles.
    this.level = this.hexMode ? this.chunkLevels[2] : this.levelFor(zoom);
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
        for (let i = this.chunkLevels.length - 1; i >= 0; i -= 1) {
          const alt = `${key}@${this.chunkLevels[i]}`;
          if (this.textures.has(alt)) {
            show.add(alt);
            break;
          }
        }
      }
    }
    if (this.hexMode) complete = this._updateHexes(view, zoom, show, wantedTex) && complete;
    for (const [k, sprite] of this.sprites) sprite.visible = show.has(k);
    this.shown = show;
    // Coarser fallbacks must draw beneath finer textures.
    this.complete = complete;
  }

  /** Visible tiles in view, nearest first, capped. */
  visibleHexes(view) {
    const corners = [
      unproject(view.x0, view.y0),
      unproject(view.x1, view.y0),
      unproject(view.x0, view.y1),
      unproject(view.x1, view.y1),
    ];
    const hexes = corners.map((c) => planeToHex(c.x, c.y, this.R));
    const q0 = Math.max(0, Math.min(...hexes.map((h) => h.q)) - 1);
    const q1 = Math.min(this.width - 1, Math.max(...hexes.map((h) => h.q)) + 1);
    const r0 = Math.max(0, Math.min(...hexes.map((h) => h.r)) - 1);
    const r1 = Math.min(this.height - 1, Math.max(...hexes.map((h) => h.r)) + 1);
    const cx = (view.x0 + view.x1) / 2;
    const cy = (view.y0 + view.y1) / 2;
    const out = [];
    for (let r = r0; r <= r1; r += 1) {
      for (let q = q0; q <= q1; q += 1) {
        const b = screenBoundsOfTiles([[q, r]], this.R);
        if (b.x1 < view.x0 || b.x0 > view.x1 || b.y1 < view.y0 || b.y0 > view.y1) continue;
        out.push([Math.hypot((b.x0 + b.x1) / 2 - cx, (b.y0 + b.y1) / 2 - cy), q, r, b]);
      }
    }
    out.sort((a, b) => a[0] - b[0]);
    return out.slice(0, MAX_DETAIL_HEXES);
  }

  _updateHexes(view, zoom, show, pinned) {
    const s = this.hexLevels.find((l) => l >= zoom * 0.8) ?? this.hexLevels[this.hexLevels.length - 1];
    this.hexLevel = s;
    const hexes = this.visibleHexes(view);
    this.visibleHexCount = hexes.length;
    for (const [, q, r] of hexes) pinned.add(`h${q},${r}@${s}`);
    let complete = true;
    let bakes = 0;
    for (const [, q, r, b] of hexes) {
      const key = `h${q},${r}@${s}`;
      if (!this.textures.has(key)) {
        const { cq, cr } = chunkOf(q, r, this.ct);
        const chunk = this.chunks.get(`${cq},${cr}`);
        if (chunk && bakes < 2) {
          this._bakeHex(key, chunk, q, r, b, s, pinned);
          bakes += 1;
        }
      }
      if (this.textures.has(key)) {
        this.textures.get(key);
        show.add(key);
      } else {
        complete = false;
        for (const alt of this.hexLevels)
          if (alt !== s && this.textures.has(`h${q},${r}@${alt}`)) show.add(`h${q},${r}@${alt}`);
      }
    }
    return complete;
  }

  _bakeHex(key, chunk, q, r, b, s, pinned) {
    const { PIXI } = this;
    let i = 0;
    while (i < chunk.n && !(chunk.q[i] === q && chunk.r[i] === r)) i += 1;
    if (i >= chunk.n) return;
    const tile = {
      q,
      r,
      terrain: chunk.terrain[i],
      elevation: chunk.elevation[i],
      moisture: chunk.moisture[i],
      soil: chunk.soil[i],
      timber: chunk.timber[i],
      stone: chunk.stone[i],
      temperature: chunk.temperature[i],
      river: !!chunk.river[i],
      lake: !!this.source.rivers?.lakes.has(`${q},${r}`),
    };
    const pad = 0.04 * (b.x1 - b.x0);
    const x0 = b.x0 - pad;
    const y0 = b.y0 - pad;
    const w = Math.ceil((b.x1 - b.x0 + pad * 2) * s);
    const h = Math.ceil((b.y1 - b.y0 + pad * 2) * s);
    const canvas = document.createElement('canvas');
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext('2d');
    ctx.setTransform(s, 0, 0, s, -x0 * s, -y0 * s);
    paintHexDetail(ctx, tile, this.R, s, { glyphs: true, riverEdges: this._riverEdges(q, r) });
    const texture = new PIXI.Texture({
      source: new PIXI.CanvasSource({ resource: canvas, autoGenerateMipmaps: true, scaleMode: 'linear' }),
    });
    const sprite = new PIXI.Sprite(texture);
    sprite.position.set(x0, y0);
    sprite.scale.set(1 / s);
    sprite.zIndex = 10 + this.hexLevels.indexOf(s);
    this.container.addChild(sprite);
    this.sprites.set(key, sprite);
    this.liveTextures += 1;
    this.baked += 1;
    this.textures.set(key, { sprite, texture }, w * h * 4 * 1.34, pinned);
  }

  /** The river borders of one tile, as plane-space segments. */
  _riverEdges(q, r) {
    const rivers = this.source.rivers;
    return (rivers?.byTile.get(`${q},${r}`) ?? []).map((edge) => {
      const [p1, p2] = sharedCorners(edge.aq, edge.ar, edge.bq, edge.br, this.R);
      return {
        p1,
        p2,
        flow: edge.flow,
        deep: edge.flow >= rivers.deepFlow,
        key: `${edge.aq},${edge.ar},${edge.bq},${edge.br}`,
      };
    });
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
    sprite.zIndex = this.chunkLevels.indexOf(s);
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
    for (let i = 0; i < c.n; i += 1) {
      const q = c.q[i];
      const r = c.r[i];
      const pts = hexCorners(q, r, R).map((p) => project(p.x, p.y));
      ctx.beginPath();
      pts.forEach((p, k) => (k ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
      ctx.closePath();
      const col = tileColor(c.terrain[i], c.elevation[i], q, r, this.source.rivers?.lakes.has(`${q},${r}`));
      ctx.fillStyle = typeof col === 'string' ? col : `rgb(${col.map(Math.round).join(',')})`;
      ctx.fill();
      // Hex grid line, about one texture pixel.
      ctx.strokeStyle = 'rgba(20,24,20,0.16)';
      ctx.lineWidth = px;
      ctx.stroke();
    }
    // Rivers: along the borders the engine records, wider downstream.
    ctx.lineCap = 'round';
    const rivers = this.source.rivers;
    for (const edge of rivers?.byChunk.get(`${c.cq},${c.cr}`) ?? []) {
      const [p1, p2] = sharedCorners(edge.aq, edge.ar, edge.bq, edge.br, R).map((p) => project(p.x, p.y));
      if (!p2) continue;
      ctx.strokeStyle = edge.flow >= rivers.deepFlow ? '#2f6b9e' : '#4f8fbf';
      ctx.lineWidth = Math.max(riverWidth(edge.flow, R), 1.4 * px);
      ctx.beginPath();
      ctx.moveTo(p1.x, p1.y);
      ctx.lineTo(p2.x, p2.y);
      ctx.stroke();
    }
    // Glyphs where a hex is big enough to read them (>= ~18 texture px).
    const hexPx = R * Math.sqrt(3) * Math.SQRT2 * 16 * s;
    if (hexPx < 18) return;
    for (let i = 0; i < c.n; i += 1) {
      const t = c.terrain[i];
      const q = c.q[i];
      const r = c.r[i];
      const centre = hexCentre(q, r, R);
      const count =
        t === 2
          ? 5 + Math.floor(c.timber[i] / 200)
          : t === 1 && c.timber[i] > 650
            ? 2
            : t === 3 || t === 7
              ? 2
              : t === 6
                ? 3
                : 0;
      for (let k = 0; k < count; k += 1) {
        const a = hash2(q * 7 + k, r, 11) * Math.PI * 2;
        const d = Math.sqrt(hash2(q, r * 5 + k, 12)) * R * 0.55;
        const p = project(centre.x + Math.cos(a) * d, centre.y + Math.sin(a) * d);
        const size = R * 16 * (0.8 + hash2(q, k, 13) * 0.4);
        if (t === 3 || t === 7) this._peak(ctx, p, size * 0.28, t === 7 || c.temperature[i] < 200);
        else if (t === 6) this._hill(ctx, p, size * 0.16);
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

  _hill(ctx, p, size) {
    ctx.fillStyle = '#7d7650';
    ctx.beginPath();
    ctx.ellipse(p.x, p.y, size * 1.2, size * 0.7, 0, Math.PI, 0);
    ctx.fill();
    ctx.fillStyle = '#a9a274';
    ctx.beginPath();
    ctx.ellipse(p.x - size * 0.3, p.y - size * 0.1, size * 0.6, size * 0.45, 0, Math.PI, 0);
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
      hexMode: this.hexMode,
      hexLevel: this.hexMode ? this.hexLevel : null,
      visibleHexes: this.hexMode ? this.visibleHexCount : 0,
    };
  }
}
