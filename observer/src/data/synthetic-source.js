// TEST ONLY: a deterministic synthetic world for streaming tests (R4).
// It is labelled synthetic everywhere and never stands for engine data.

import { fbm, hash2, mulberry32 } from '../sim/rng.js';
import { indexHydrology } from './terrain-source.js';

export class SyntheticSource {
  constructor({ width = 4096, height = 4096, chunkTiles = 8, minLatency = 0, maxLatency = 400, seed = 99 } = {}) {
    this.label = `SYNTHETIC test world ${width}×${height}`;
    this.seed = seed;
    this.minLatency = minLatency;
    this.maxLatency = maxLatency;
    this.manifest = {
      source: 'synthetic (test only)',
      engine: { seed, width, height },
      presentation: { hex_radius_m: 64, chunk_tiles: chunkTiles, chunks: [Math.ceil(width / chunkTiles), Math.ceil(height / chunkTiles)] },
    };
    this.rivers = indexHydrology(null, chunkTiles);
    this.rng = mulberry32(seed);
    this.loads = 0;
  }

  get width() {
    return this.manifest.engine.width;
  }

  get height() {
    return this.manifest.engine.height;
  }

  load(cq, cr, signal) {
    this.loads += 1;
    const delay = this.minLatency + this.rng() * (this.maxLatency - this.minLatency);
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => resolve(this._make(cq, cr)), delay);
      signal?.addEventListener('abort', () => {
        clearTimeout(timer);
        reject(new DOMException('aborted', 'AbortError'));
      });
    });
  }

  _make(cq, cr) {
    const ct = this.manifest.presentation.chunk_tiles;
    const q0 = cq * ct;
    const r0 = cr * ct;
    const q1 = Math.min(this.width, q0 + ct);
    const r1 = Math.min(this.height, r0 + ct);
    const n = (q1 - q0) * (r1 - r0);
    const c = { cq, cr, n, q: new Int32Array(n), r: new Int32Array(n), terrain: new Uint8Array(n), elevation: new Uint16Array(n), moisture: new Uint16Array(n), temperature: new Uint16Array(n), soil: new Uint16Array(n), timber: new Uint16Array(n), stone: new Uint16Array(n), ore: new Uint16Array(n), river: new Uint8Array(n) };
    let i = 0;
    for (let r = r0; r < r1; r += 1) {
      for (let q = q0; q < q1; q += 1) {
        const e = Math.round(fbm(q * 0.02, r * 0.02, this.seed, 4) * 1000);
        const m = Math.round(fbm(q * 0.03 + 50, r * 0.03, this.seed + 1, 3) * 1000);
        c.q[i] = q;
        c.r[i] = r;
        c.elevation[i] = e;
        c.moisture[i] = m;
        c.temperature[i] = 500;
        c.timber[i] = Math.round(hash2(q, r, 3) * 1000);
        c.stone[i] = Math.round(hash2(q, r, 4) * 1000);
        c.terrain[i] = e < 380 ? 0 : e > 640 ? 3 : m > 560 ? 2 : 1;
        c.river[i] = q % 97 === 0 && e >= 380 && e <= 640 ? 1 : 0;
        i += 1;
      }
    }
    c.bytes = n * 26 + 64;
    return c;
  }
}
