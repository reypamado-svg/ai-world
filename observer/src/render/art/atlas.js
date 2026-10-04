// Texture atlas: packs painted (or, later, licensed) sprite canvases into a
// few large pages so the GPU can batch them. Keeps the CPU copy of each page
// for pixel-accurate picking and builds silhouette pages for depth tests.

const PAD = 4;

export class Atlas {
  constructor(PIXI, pageSize = 4096) {
    this.PIXI = PIXI;
    this.pageSize = pageSize;
    this.pages = [];
    this.entries = new Map();
    this._newPage();
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
  }

  /** Add a canvas under an asset key; anchor is in that canvas's pixels. */
  add(key, canvas, anchor, meta = {}) {
    if (this.entries.has(key)) return this.entries.get(key);
    let page = this.pages[this.pages.length - 1];
    if (page.source) {
      this._newPage();
      page = this.pages[this.pages.length - 1];
    }
    const w = canvas.width;
    const h = canvas.height;
    if (page.x + w + PAD > this.pageSize) {
      page.x = PAD;
      page.y += page.rowH + PAD;
      page.rowH = 0;
    }
    if (page.y + h + PAD > this.pageSize) {
      this._newPage();
      page = this.pages[this.pages.length - 1];
    }
    page.ctx.drawImage(canvas, page.x, page.y);
    const entry = {
      key,
      pageIndex: this.pages.length - 1,
      x: page.x,
      y: page.y,
      w,
      h,
      anchor: { ...anchor },
      meta,
      texture: null,
    };
    page.x += w + PAD;
    page.rowH = Math.max(page.rowH, h);
    this.entries.set(key, entry);
    return entry;
  }

  get(key) {
    return this.entries.get(key);
  }

  /** Upload pages and create one sub-texture per entry. */
  finalize() {
    const { CanvasSource, Texture, Rectangle } = this.PIXI;
    this.pages.forEach((page) => {
      if (!page.source) {
        page.source = new CanvasSource({ resource: page.canvas, autoGenerateMipmaps: true, scaleMode: 'linear' });
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
      entries: this.entries.size,
      usedPixels: used,
      fill: pagePixels ? Number((used / pagePixels).toFixed(3)) : 0,
      bytes: Math.round(this.textureBytes()),
      byKind,
    };
  }

  /** Estimated GPU bytes for uploaded pages (RGBA + mip chain). */
  textureBytes() {
    return this.pages.filter((p) => p.source).length * this.pageSize * this.pageSize * 4 * 1.34;
  }
}
