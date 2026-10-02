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
//
// Local-origin rule: canvas paths are float32, so every bake paints relative
// to its own anchor (the chunk's or tile's centre) and the sprite, positioned
// in float64, carries the anchor. Far tiles then paint exactly like near ones.

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
import { landOutline, paintHexDetail } from './hex-detail.js';
import { riverEdgesOf, riverLine, riverWidthM } from '../world/rivers.js';
import { ChunkLoader, LruCache } from '../world/chunks.js';
import { hash2 } from '../sim/rng.js';
import { makeInterior, tileBaseColor } from '../world/interior.js';

/** Chunk texture resolutions, as screen pixels per tile, coarse to fine. */
export const CHUNK_TILE_PX = [32, 64, 128, 256];
/** From this tile size on screen, each visible tile gets its own detailed texture. */
export const HEX_MODE_TILE_PX = 256;
/** Per-tile texture resolutions, as pixels per tile. */
export const HEX_TILE_PX = [512, 1024];
const MAX_DETAIL_HEXES = 120;

/** A tile's land-cover shares (seven basis points), or null for water and older exports. */
function coverOf(chunk, i) {
  if (!chunk.hasCover) return null;
  const shares = Array.from(chunk.cover.subarray(i * 7, i * 7 + 7));
  return shares.some((v) => v > 0) ? shares : null;
}

/** A tile's map colour: its interior's base colour, with a slight per-tile variation. */
function tileColor(tile, q, r, lake) {
  const f = 0.97 + (hash2(q, r, 5) - 0.5) * 0.06;
  return tileBaseColor(tile, lake).map((c) => c * f);
}

/** Thinnest a river is drawn on the map, in texture pixels: 1 for a stream, up to 3 for the largest. */
export function riverMinPx(flow) {
  return 1 + 2 * Math.min(1, Math.log2(1 + flow) / Math.log2(110));
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
    this.streamFlow = source.manifest.engine.travel.stream_flow;
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
    this.features = [];
    this.edgeCache = new Map();
    this.tileAt = this.tileAt.bind(this);
    this._interior = null;
  }

  /**
   * Engine values of tile (q, r): null off the map, undefined while its chunk
   * is not loaded. Objects are built once per loaded chunk.
   */
  tileAt(q, r) {
    if (q < 0 || r < 0 || q >= this.width || r >= this.height) return null;
    const { cq, cr } = chunkOf(q, r, this.ct);
    const chunk = this.chunks.map.get(`${cq},${cr}`)?.value;
    if (!chunk) return undefined;
    if (!chunk.tiles) {
      chunk.tiles = new Map();
      for (let i = 0; i < chunk.n; i += 1)
        chunk.tiles.set(`${chunk.q[i]},${chunk.r[i]}`, {
          q: chunk.q[i],
          r: chunk.r[i],
          terrain: chunk.terrain[i],
          elevation: chunk.elevation[i],
          moisture: chunk.moisture[i],
          soil: chunk.soil[i],
          timber: chunk.timber[i],
          stone: chunk.stone[i],
          temperature: chunk.temperature[i],
          river: !!chunk.river[i],
          lake: !!this.source.rivers?.lakes.has(`${chunk.q[i]},${chunk.r[i]}`),
          cover: coverOf(chunk, i),
        });
    }
    return chunk.tiles.get(`${q},${r}`);
  }

  /** Whether a tile and its six neighbours are loaded (needed to blend across its borders). */
  neighbourhoodLoaded(q, r) {
    for (const [dq, dr] of [
      [0, 0],
      [1, 0],
      [1, -1],
      [0, -1],
      [-1, 0],
      [-1, 1],
      [0, 1],
    ])
      if (this.tileAt(q + dq, r + dr) === undefined) return false;
    return true;
  }

  /** River borders of a tile, in ground-plane metres (cached). */
  riverEdges(q, r) {
    const key = `${q},${r}`;
    if (!this.edgeCache.has(key)) {
      if (this.edgeCache.size > 4096) this.edgeCache.clear();
      this.edgeCache.set(key, riverEdgesOf(q, r, this.source.rivers, this.R, this.streamFlow));
    }
    return this.edgeCache.get(key);
  }

  /** Presentation features painted into the ground (the SAMPLE field ring). */
  setFeatures(features) {
    this.features = features;
    this._interior = null;
  }

  /** The ground colour field over the loaded tiles. */
  interior() {
    if (!this._interior)
      this._interior = makeInterior({
        R: this.R,
        tileAt: (q, r) => this.tileAt(q, r) ?? null,
        lakes: this.source.rivers?.lakes ?? new Set(),
        features: this.features,
      });
    return this._interior;
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
    // Detailed tiles blend with their neighbours, so load the chunks around them too.
    const hexes = this.hexMode ? this.visibleHexes(view) : [];
    const around = new Set(keys);
    for (const [, q, r] of hexes)
      for (const [dq, dr] of [
        [1, 0],
        [1, -1],
        [0, -1],
        [-1, 0],
        [-1, 1],
        [0, 1],
      ]) {
        const [nq, nr] = [q + dq, r + dr];
        if (nq < 0 || nr < 0 || nq >= this.width || nr >= this.height) continue;
        const c = chunkOf(nq, nr, this.ct);
        around.add(`${c.cq},${c.cr}`);
      }
    this.loader.want([...around]);
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
    if (this.hexMode) complete = this._updateHexes(hexes, zoom, show, wantedTex) && complete;
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

  _updateHexes(hexes, zoom, show, pinned) {
    const s = this.hexLevels.find((l) => l >= zoom * 0.8) ?? this.hexLevels[this.hexLevels.length - 1];
    this.hexLevel = s;
    this.visibleHexCount = hexes.length;
    for (const [, q, r] of hexes) pinned.add(`h${q},${r}@${s}`);
    let complete = true;
    let bakes = 0;
    for (const [, q, r, b] of hexes) {
      const key = `h${q},${r}@${s}`;
      if (!this.textures.has(key)) {
        const { cq, cr } = chunkOf(q, r, this.ct);
        const chunk = this.chunks.get(`${cq},${cr}`);
        if (chunk && bakes < 2 && this.neighbourhoodLoaded(q, r)) {
          this._bakeHex(key, q, r, b, s, pinned);
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

  _bakeHex(key, q, r, b, s, pinned) {
    const { PIXI } = this;
    const tile = this.tileAt(q, r);
    if (!tile) return;
    const pad = 0.04 * (b.x1 - b.x0);
    const x0 = b.x0 - pad;
    const y0 = b.y0 - pad;
    const w = Math.ceil((b.x1 - b.x0 + pad * 2) * s);
    const h = Math.ceil((b.y1 - b.y0 + pad * 2) * s);
    const canvas = document.createElement('canvas');
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext('2d');
    // Anchor: the tile centre. Canvas coordinates are relative to it.
    const origin = hexCentre(q, r, this.R);
    const A = project(origin.x, origin.y);
    ctx.setTransform(s, 0, 0, s, -(x0 - A.x) * s, -(y0 - A.y) * s);
    paintHexDetail(ctx, tile, this.R, s, {
      glyphs: true,
      riverEdges: this.riverEdges(q, r),
      origin,
      interior: this.interior(),
      outline: landOutline(q, r, (tq, tr) => (this.tileAt(tq, tr)?.terrain ?? 0) !== 0),
    });
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

  _bake(key, chunk, s, pinned) {
    const { PIXI } = this;
    const b = this.chunkBounds(chunk.cq, chunk.cr);
    const w = Math.max(2, Math.ceil((b.x1 - b.x0) * s) + 2);
    const h = Math.max(2, Math.ceil((b.y1 - b.y0) * s) + 2);
    const canvas = document.createElement('canvas');
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext('2d');
    // Anchor: the centre tile of the chunk. Canvas coordinates are relative to it.
    const mid = hexCentre(chunk.cq * this.ct + this.ct / 2, chunk.cr * this.ct + this.ct / 2, this.R);
    const A = project(mid.x, mid.y);
    ctx.setTransform(s, 0, 0, s, -(b.x0 - A.x) * s + 1, -(b.y0 - A.y) * s + 1);
    this.paintChunk(ctx, chunk, s, A);
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

  /**
   * Paint a chunk's hexes in world-screen units relative to the anchor `A`
   * (a world-screen point; the caller scales and places it).
   */
  paintChunk(ctx, c, s, A = { x: 0, y: 0 }) {
    const R = this.R;
    const px = 1 / s; // one texture pixel in world-screen units
    const P = (x, y) => {
      const p = project(x, y);
      return { x: p.x - A.x, y: p.y - A.y };
    };
    for (let i = 0; i < c.n; i += 1) {
      const q = c.q[i];
      const r = c.r[i];
      const pts = hexCorners(q, r, R).map((p) => P(p.x, p.y));
      ctx.beginPath();
      pts.forEach((p, k) => (k ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
      ctx.closePath();
      const tile = { terrain: c.terrain[i], elevation: c.elevation[i], moisture: c.moisture[i] };
      Object.assign(tile, { timber: c.timber[i], temperature: c.temperature[i] });
      const col = tileColor(tile, q, r, this.source.rivers?.lakes.has(`${q},${r}`));
      ctx.fillStyle = `rgb(${col.map(Math.round).join(',')})`;
      ctx.fill();
      // Hex grid line, about one texture pixel.
      ctx.strokeStyle = 'rgba(20,24,20,0.16)';
      ctx.lineWidth = px;
      ctx.stroke();
    }
    // Rivers: along the borders the engine records, wider downstream.
    ctx.lineCap = 'round';
    const rivers = this.source.rivers;
    ctx.lineJoin = 'round';
    for (const edge of rivers?.byChunk.get(`${c.cq},${c.cr}`) ?? []) {
      const [p1, p2] = sharedCorners(edge.aq, edge.ar, edge.bq, edge.br, R);
      if (!p2) continue;
      const line = riverLine({ p1, p2, key: `${edge.aq},${edge.ar},${edge.bq},${edge.br}` }, 8).map((p) => P(p.x, p.y));
      ctx.strokeStyle = edge.flow >= rivers.deepFlow ? '#2f6b9e' : '#4f8fbf';
      const metres = riverWidthM(edge.flow, { streamFlow: this.streamFlow, deepFlow: rivers.deepFlow });
      ctx.lineWidth = Math.max(metres * 16 * 1.1, riverMinPx(edge.flow) * px);
      ctx.beginPath();
      line.forEach((p, k) => (k ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
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
        const p = P(centre.x + Math.cos(a) * d, centre.y + Math.sin(a) * d);
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
