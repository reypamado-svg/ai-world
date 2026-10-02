// Settlement-band decoration for tiles around the village: tree and rock
// sprites from the shared atlas, placed from each tile's own engine values
// (timber, stone). Built per tile on demand, kept in a bounded cache.
//
// Limitation (prototype): these sprites are depth-sorted among themselves,
// not against travellers, who are drawn above them.

import { project } from '../world/coords.js';
import { chunkOf } from '../world/hex.js';
import { LruCache } from '../world/chunks.js';
import { ART } from './art/paint/iso.js';
import { tileDecor } from './hex-detail.js';

export class DecorLayer {
  constructor({ PIXI, atlas, terrain, skip = new Set(), maxTiles = 48 }) {
    this.PIXI = PIXI;
    this.atlas = atlas;
    this.terrain = terrain;
    this.skip = skip;
    this.container = new PIXI.Container();
    this.container.sortableChildren = true;
    this.cache = new LruCache({
      maxBytes: Infinity,
      maxEntries: maxTiles,
      onEvict: (key, c) => c.destroy({ children: true }),
    });
  }

  _build(q, r) {
    const ct = this.terrain.ct;
    const { cq, cr } = chunkOf(q, r, ct);
    const chunk = this.terrain.chunks.get(`${cq},${cr}`);
    if (!chunk) return null;
    let i = 0;
    while (i < chunk.n && !(chunk.q[i] === q && chunk.r[i] === r)) i += 1;
    const t = { q, r, terrain: chunk.terrain[i], timber: chunk.timber[i], stone: chunk.stone[i] };
    const c = new this.PIXI.Container();
    c.sortableChildren = true;
    for (const d of tileDecor(t, this.terrain.R)) {
      const key = d.kind === 'rock' ? `nature.rock.${d.variant % 4}` : `nature.${d.kind}.${d.variant % 6}`;
      const e = this.atlas.get(key);
      if (!e) continue;
      const s = new this.PIXI.Sprite(e.texture);
      s.anchor.set(e.anchor.x / e.w, e.anchor.y / e.h);
      s.scale.set(1 / ART);
      const p = project(d.x, d.y);
      s.position.set(p.x, p.y);
      s.zIndex = d.x + d.y;
      c.addChild(s);
    }
    c.zIndex = 0;
    return c;
  }

  update(zoom) {
    // Upright trees and rocks only once single trees are worth drawing (a few metres on screen).
    const show = this.terrain.hexMode && zoom >= 0.2;
    this.container.visible = show;
    if (!show) return;
    const wanted = new Set();
    for (const [, q, r] of this.terrain.visibleHexes(this.terrain.lastView)) {
      const key = `${q},${r}`;
      if (this.skip.has(key)) continue;
      wanted.add(key);
      if (!this.cache.has(key)) {
        const c = this._build(q, r);
        if (c) {
          this.container.addChild(c);
          this.cache.set(key, c, 0, wanted);
        }
      }
    }
    for (const [key, entry] of this.cache.map) entry.value.visible = wanted.has(key);
    void zoom;
  }
}
