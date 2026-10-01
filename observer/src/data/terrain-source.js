// Engine terrain export, fetched chunk by chunk (never the whole world).

const TERRAINS = ['water', 'grassland', 'forest', 'mountain', 'desert', 'tundra'];

/** Pack tile records into typed arrays (compact and fast to paint). */
export function packChunk(cq, cr, fields, rows) {
  const n = rows.length;
  const col = (name) => fields.indexOf(name);
  const idx = Object.fromEntries(['q', 'r', 'terrain', 'elevation', 'moisture', 'temperature', 'soil', 'timber', 'stone', 'ore', 'river'].map((f) => [f, col(f)]));
  const chunk = {
    cq,
    cr,
    n,
    q: new Int32Array(n),
    r: new Int32Array(n),
    terrain: new Uint8Array(n),
    elevation: new Uint16Array(n),
    moisture: new Uint16Array(n),
    temperature: new Uint16Array(n),
    soil: new Uint16Array(n),
    timber: new Uint16Array(n),
    stone: new Uint16Array(n),
    ore: new Uint16Array(n),
    river: new Uint8Array(n),
  };
  rows.forEach((row, i) => {
    chunk.q[i] = row[idx.q];
    chunk.r[i] = row[idx.r];
    chunk.terrain[i] = TERRAINS.indexOf(row[idx.terrain]);
    for (const f of ['elevation', 'moisture', 'temperature', 'soil', 'timber', 'stone', 'ore']) chunk[f][i] = row[idx[f]];
    chunk.river[i] = row[idx.river] ? 1 : 0;
  });
  chunk.bytes = n * 26 + 64;
  return chunk;
}

export class TerrainSource {
  constructor(base, manifest) {
    this.base = base;
    this.manifest = manifest;
    this.label = `engine worldgen seed ${manifest.engine.seed}`;
  }

  static async open(base) {
    const manifest = await (await fetch(`${base}/manifest.json`)).json();
    return new TerrainSource(base, manifest);
  }

  get width() {
    return this.manifest.engine.width;
  }

  get height() {
    return this.manifest.engine.height;
  }

  async load(cq, cr, signal) {
    const res = await fetch(`${this.base}/chunks/c${cq}_${cr}.json`, { signal });
    if (!res.ok) throw new Error(`chunk ${cq},${cr}: HTTP ${res.status}`);
    const json = await res.json();
    return packChunk(cq, cr, json.fields, json.tiles);
  }

  async overview() {
    return (await fetch(`${this.base}/overview.json`)).json();
  }

  async day0() {
    return (await fetch(`${this.base}/day0.json`)).json();
  }
}

export { TERRAINS };
