// Texture atlas: packs painted (or, later, licensed) sprite canvases into a
// few pages so the GPU can batch them. Keeps the CPU copy of each page for
// pixel-accurate picking and builds silhouette pages for depth tests.
//
// Art is painted at twice its screen size (ART = 2). Entries added with
// `{ art: 1 }` are kept at half that, screen size, which quarters their
// texture memory; buildings and the proof page keep the full 2x art.
// Pages are packed when `finalize()` is called, tallest entries first, so
// rows waste little space; pages have no mipmaps (Phase 5 S7).

const PAD = 2;

export class Atlas {
  /**
   * @param {object} PIXI
   * @param {number} pageSize square page edge in pixels
   * @param {{ fullArt?: boolean, mipmaps?: boolean }} options `fullArt` keeps every
   *   entry at 2x (the proof page); `mipmaps` builds mip chains for every page.
   */
  constructor(PIXI, pageSize = 2048, { fullArt = false, mipmaps = false } = {}) {
    this.PIXI = PIXI;
    this.pageSize = pageSize;
    this.fullArt = fullArt;
    this.mipmaps = mipmaps;
    this.pages = [];
    this.entries = new Map();
    this.pending = [];
    this.missing = new Set();
  }

  _newPage() {
    const canvas = document.createElement('canvas');
    canvas.width = this.pageSize;
    canvas.height = this.pageSize;
    this.pages.push({
      canvas,
      ctx: canvas.getContext('2d'),
      x: PAD,
      y: PAD,
      rowH: 0,
      source: null,
      silhouette: null,
      pixels: null,
    });
    return this.pages[this.pages.length - 1];
  }

  /**
   * Add a canvas under an asset key; anchor is in that canvas's pixels. With
   * `art: 1` the canvas (painted at 2x) is kept at half size.
   */
  add(key, canvas, anchor, meta = {}, { art = 2 } = {}) {
    if (this.entries.has(key)) return this.entries.get(key);
    let source = canvas;
    let stored = { ...anchor };
    let scale = 2;
    if (art === 1 && !this.fullArt) {
      const w = Math.max(1, Math.ceil(canvas.width / 2));
      const h = Math.max(1, Math.ceil(canvas.height / 2));
      source = document.createElement('canvas');
      source.width = w;
      source.height = h;
      const ctx = source.getContext('2d');
      ctx.imageSmoothingQuality = 'high';
      ctx.drawImage(canvas, 0, 0, canvas.width, canvas.height, 0, 0, canvas.width / 2, canvas.height / 2);
      stored = { x: anchor.x / 2, y: anchor.y / 2 };
      scale = 1;
    }
    const entry = {
      key,
      pageIndex: -1,
      x: 0,
      y: 0,
      w: source.width,
      h: source.height,
      anchor: stored,
      art: scale,
      meta,
      texture: null,
      _canvas: source,
      _order: this.entries.size,
    };
    this.entries.set(key, entry);
    this.pending.push(entry);
    return entry;
  }

  /** The entry under a key. Missing keys are remembered, except optional `#` extras (masks, shadows). */
  get(key) {
    const entry = this.entries.get(key);
    if (!entry && !key.includes('#')) this.missing.add(key);
    return entry;
  }

  /** Shelf-pack the entries added since the last call, tallest first, onto open pages. */
  _pack() {
    const todo = this.pending.sort((a, b) => b.h - a.h || b.w - a.w || a._order - b._order);
    this.pending = [];
    let page = this.pages.at(-1);
    if (!page || page.source) page = this._newPage();
    for (const e of todo) {
      if (page.x + e.w + PAD > this.pageSize) {
        page.x = PAD;
        page.y += page.rowH + PAD;
        page.rowH = 0;
      }
      if (page.y + e.h + PAD > this.pageSize) {
        page = this._newPage();
      }
      page.ctx.drawImage(e._canvas, page.x, page.y);
      e.pageIndex = this.pages.length - 1;
      e.x = page.x;
      e.y = page.y;
      e._canvas = null;
      page.x += e.w + PAD;
      page.rowH = Math.max(page.rowH, e.h);
    }
  }

  /** Pack, upload pages and create one sub-texture per entry. */
  finalize() {
    const { CanvasSource, Texture, Rectangle } = this.PIXI;
    if (this.pending.length) this._pack();
    this.pages.forEach((page) => {
      if (!page.source) {
        page.source = new CanvasSource({
          resource: page.canvas,
          autoGenerateMipmaps: this.mipmaps,
          scaleMode: 'linear',
        });
      }
    });
    for (const e of this.entries.values()) {
      if (!e.texture) {
        e.texture = new Texture({ source: this.pages[e.pageIndex].source, frame: new Rectangle(e.x, e.y, e.w, e.h) });
      }
    }
  }

  /** Alpha (0-255) at a pixel of an entry, for precise picking. */
  alphaAt(entry, lx, ly) {
    if (lx < 0 || ly < 0 || lx >= entry.w || ly >= entry.h) return 0;
    const page = this.pages[entry.pageIndex];
    if (!page.pixels) page.pixels = page.ctx.getImageData(0, 0, page.canvas.width, page.canvas.height).data;
    const i = ((entry.y + Math.floor(ly)) * page.canvas.width + entry.x + Math.floor(lx)) * 4 + 3;
    return page.pixels[i];
  }

  /** White silhouette texture for an entry (debug flat-tint mode). */
  silhouette(entry) {
    const { CanvasSource, Texture, Rectangle } = this.PIXI;
    const page = this.pages[entry.pageIndex];
    if (!page.silhouette) {
      const c = document.createElement('canvas');
      c.width = page.canvas.width;
      c.height = page.canvas.height;
      const ctx = c.getContext('2d');
      ctx.drawImage(page.canvas, 0, 0);
      // Hard-edged silhouette so probe pixels are unambiguous.
      const img = ctx.getImageData(0, 0, c.width, c.height);
      for (let i = 0; i < img.data.length; i += 4) {
        const a = img.data[i + 3] > 96 ? 255 : 0;
        img.data[i] = 255;
        img.data[i + 1] = 255;
        img.data[i + 2] = 255;
        img.data[i + 3] = a;
      }
      ctx.putImageData(img, 0, 0);
      page.silhouette = { source: new CanvasSource({ resource: c, scaleMode: 'nearest' }), textures: new Map() };
    }
    if (!page.silhouette.textures.has(entry.key)) {
      page.silhouette.textures.set(
        entry.key,
        new Texture({ source: page.silhouette.source, frame: new Rectangle(entry.x, entry.y, entry.w, entry.h) }),
      );
    }
    return page.silhouette.textures.get(entry.key);
  }

  /** What the atlas holds: pages, how full they are, and pixels by kind of art. */
  stats() {
    const byKind = {};
    let used = 0;
    for (const e of this.entries.values()) {
      const kind = e.key.split('.')[0].split('#')[0];
      const k = (byKind[kind] ??= { entries: 0, pixels: 0 });
      k.entries += 1;
      k.pixels += e.w * e.h;
      used += e.w * e.h;
    }
    const pagePixels = this.pages.length * this.pageSize * this.pageSize;
    return {
      pages: this.pages.length,
      pageSize: this.pageSize,
      mipmaps: this.mipmaps,
      entries: this.entries.size,
      usedPixels: used,
      fill: pagePixels ? Number((used / pagePixels).toFixed(3)) : 0,
      bytes: Math.round(this.textureBytes()),
      byKind,
      missing: [...this.missing].sort(),
    };
  }

  /** GPU bytes of the uploaded pages: RGBA, plus a third for mip chains when built. */
  textureBytes() {
    const uploaded = this.pages.filter((p) => p.source).length;
    return uploaded * this.pageSize * this.pageSize * 4 * (this.mipmaps ? 4 / 3 : 1);
  }
}
