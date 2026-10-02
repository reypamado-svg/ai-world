// Engine terrain export, fetched chunk by chunk (never the whole world).

const TERRAINS = ['water', 'grassland', 'forest', 'mountain', 'desert', 'tundra', 'hills', 'snow'];

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

/**
 * River borders and lakes, indexed for painting. A river runs along the border
 * between tiles `a` and `b`; `flow` grows downstream as rivers join.
 */
export function indexHydrology(hydrology, chunkTiles) {
  const byChunk = new Map();
  const byTile = new Map();
  const add = (map, key, edge) => {
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(edge);
  };
  for (const [aq, ar, bq, br, flow, dq, dr] of hydrology?.edges ?? []) {
    const edge = { aq, ar, bq, br, flow, down: dq === null ? null : [dq, dr] };
    for (const [q, r] of [
      [aq, ar],
      [bq, br],
    ]) {
      add(byTile, `${q},${r}`, edge);
      const key = `${Math.floor(q / chunkTiles)},${Math.floor(r / chunkTiles)}`;
      if (!byChunk.get(key)?.includes(edge)) add(byChunk, key, edge);
    }
  }
  const lakes = new Set((hydrology?.lakes ?? []).map(([q, r]) => `${q},${r}`));
  return { byChunk, byTile, lakes, deepFlow: hydrology?.deep_flow ?? Infinity };
}

export class TerrainSource {
  constructor(base, manifest, hydrology = null) {
    this.base = base;
    this.manifest = manifest;
    this.label = `engine worldgen seed ${manifest.engine.seed}`;
    this.rivers = indexHydrology(hydrology, manifest.presentation.chunk_tiles);
  }

  static async open(base) {
    const manifest = await (await fetch(`${base}/manifest.json`)).json();
    const file = manifest.files?.hydrology;
    const hydrology = file ? await (await fetch(`${base}/${file}`)).json() : null;
    return new TerrainSource(base, manifest, hydrology);
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
