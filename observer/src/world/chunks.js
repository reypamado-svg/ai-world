// Chunk loading and bounded caches.
//
// ChunkSource: { manifest, load(cq, cr, signal) -> Promise<chunk> }.
// ChunkLoader keeps at most `maxInFlight` requests, nearest first. When the
// wanted set changes, requests for chunks no longer wanted are aborted, and
// any result that arrives for a chunk no longer wanted is discarded rather
// than inserted. ChunkCache (CPU data) and TextureCache (GPU textures) are
// LRU caches bounded by bytes (and count, for textures); evicted textures are
// destroyed.

export class LruCache {
  constructor({ maxBytes, maxEntries = Infinity, onEvict = () => {} }) {
    this.maxBytes = maxBytes;
    this.maxEntries = maxEntries;
    this.onEvict = onEvict;
    this.map = new Map();
    this.bytes = 0;
    this.peakBytes = 0;
    this.peakEntries = 0;
    this.evictions = 0;
  }

  get(key) {
    const e = this.map.get(key);
    if (!e) return undefined;
    this.map.delete(key);
    this.map.set(key, e);
    return e.value;
  }

  has(key) {
    return this.map.has(key);
  }

  /** Insert, then evict least-recently-used entries not in `pinned` until within budget. */
  set(key, value, bytes, pinned = new Set()) {
    if (this.map.has(key)) this.delete(key);
    this.map.set(key, { value, bytes });
    this.bytes += bytes;
    this.trim(pinned);
  }

  trim(pinned = new Set()) {
    for (const [k] of this.map) {
      if (this.bytes <= this.maxBytes && this.map.size <= this.maxEntries) break;
      if (pinned.has(k)) continue;
      this.delete(k);
      this.evictions += 1;
    }
    this.peakBytes = Math.max(this.peakBytes, this.bytes);
    this.peakEntries = Math.max(this.peakEntries, this.map.size);
  }

  delete(key) {
    const e = this.map.get(key);
    if (!e) return;
    this.map.delete(key);
    this.bytes -= e.bytes;
    this.onEvict(key, e.value);
  }

  clear() {
    for (const k of [...this.map.keys()]) this.delete(k);
  }

  stats() {
    return {
      bytes: this.bytes,
      entries: this.map.size,
      maxBytes: this.maxBytes,
      maxEntries: this.maxEntries,
      peakBytes: this.peakBytes,
      peakEntries: this.peakEntries,
      evictions: this.evictions,
    };
  }
}

export class ChunkLoader {
  constructor(source, cache, { maxInFlight = 4 } = {}) {
    this.source = source;
    this.cache = cache;
    this.maxInFlight = maxInFlight;
    this.wanted = [];
    this.wantedSet = new Set();
    this.generation = 0;
    this.inFlight = new Map(); // key -> { controller, generation }
    this.stats = { started: 0, completed: 0, aborted: 0, discardedStale: 0, failed: 0, peakInFlight: 0 };
  }

  /** keys: chunk keys "cq,cr" ordered nearest first. */
  want(keys) {
    const next = new Set(keys);
    let changed = next.size !== this.wantedSet.size;
    if (!changed) for (const k of next) if (!this.wantedSet.has(k)) changed = true;
    this.wanted = keys;
    if (!changed) return;
    this.generation += 1;
    this.wantedSet = next;
    for (const [key, req] of this.inFlight) {
      if (!next.has(key)) {
        req.controller.abort();
        this.inFlight.delete(key);
        this.stats.aborted += 1;
      }
    }
  }

  pump() {
    for (const key of this.wanted) {
      if (this.inFlight.size >= this.maxInFlight) break;
      if (this.cache.has(key) || this.inFlight.has(key)) continue;
      const [cq, cr] = key.split(',').map(Number);
      const controller = new AbortController();
      const req = { controller, generation: this.generation };
      this.inFlight.set(key, req);
      this.stats.started += 1;
      this.stats.peakInFlight = Math.max(this.stats.peakInFlight, this.inFlight.size);
      this.source
        .load(cq, cr, controller.signal)
        .then((chunk) => {
          if (this.inFlight.get(key) !== req) return; // aborted and replaced
          this.inFlight.delete(key);
          if (!this.wantedSet.has(key)) {
            this.stats.discardedStale += 1;
            return;
          }
          this.stats.completed += 1;
          this.cache.set(key, chunk, chunk.bytes, this.wantedSet);
        })
        .catch((err) => {
          if (this.inFlight.get(key) === req) this.inFlight.delete(key);
          if (err?.name !== 'AbortError') this.stats.failed += 1;
        });
    }
  }

  get idle() {
    return this.inFlight.size === 0 && this.wanted.every((k) => this.cache.has(k));
  }
}
