// A recorded run, from its static export (O2: `python -m sovereign_world.observer.run_export`).
//
// The export holds what the engine recorded for each day: settlements with their houses,
// residents and house work, travellers by tile, tile owners, and every living person as
// columns. RunSource reads them and fills a PeopleFrame per day, so the crowd layer draws
// recorded people exactly as it draws synthetic ones. People counted at no settlement (on the
// road, or captives on the march) are not in the frame: they are drawn as counted travellers
// at their tiles.

import { PeopleFrame } from './population.js';
import { TerrainSource } from './terrain-source.js';
import { settlementLabel } from './naming.js';

const DTYPES = {
  '<u4': { bytes: 4, Array: Uint32Array },
  '<u2': { bytes: 2, Array: Uint16Array },
  '<i2': { bytes: 2, Array: Int16Array },
  u1: { bytes: 1, Array: Uint8Array },
};

async function fetchBytes(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  return new Uint8Array(await res.arrayBuffer());
}

async function gunzip(bytes) {
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

/** Split a people file into its columns (little-endian; copied so each column is aligned). */
export function decodePeople(raw, layout) {
  const width = layout.reduce((sum, [, dtype]) => sum + DTYPES[dtype].bytes, 0);
  const n = raw.byteLength / width;
  if (!Number.isInteger(n)) throw new Error('people file size does not fit its layout');
  const columns = {};
  let at = 0;
  for (const [name, dtype] of layout) {
    const { bytes, Array } = DTYPES[dtype];
    const copy = raw.slice(at, at + n * bytes);
    columns[name] = new Array(copy.buffer, copy.byteOffset, n);
    at += n * bytes;
  }
  return { n, columns };
}

export class RunSource {
  /**
   * @param {string} base URL of the export directory
   * @param {{ load?: (url: string) => Promise<Uint8Array> }} options how to read a file (tests read from disk)
   */
  static async open(base, { load = fetchBytes } = {}) {
    const json = async (path) => JSON.parse(new TextDecoder().decode(await load(`${base}/${path}`)));
    const manifest = await json('manifest.json');
    if (manifest.kind !== 'recorded run') throw new Error(`${base} is not a recorded run export`);
    const ids = await json(manifest.files.ids);
    const source = new RunSource(base, manifest, ids, load);
    return source;
  }

  constructor(base, manifest, ids, load) {
    this.base = base;
    this.manifest = manifest;
    this.ids = ids;
    this.load = load;
    this.days = manifest.days;
    this.label = `recorded run ${manifest.run_id.slice(0, 8)}`;
  }

  /** The run's own terrain (browser only: TerrainSource fetches). */
  terrain() {
    return TerrainSource.open(`${this.base}/terrain`);
  }

  _path(kind, day) {
    return this.manifest.files[kind].replace('{day:06d}', String(day).padStart(6, '0'));
  }

  /** The nearest exported day at or before `day` (or the first). */
  nearestDay(day) {
    let best = this.days[0];
    for (const d of this.days) if (d <= day) best = d;
    return best;
  }

  /**
   * One day: the engine's record and a PeopleFrame of everyone at a settlement.
   * @returns {Promise<{ day: number, record: object, frame: PeopleFrame }>}
   */
  async day(day) {
    const text = new TextDecoder().decode(await this.load(`${this.base}/${this._path('day', day)}`));
    const record = JSON.parse(text);
    const raw = await gunzip(await this.load(`${this.base}/${this._path('people', day)}`));
    const { n, columns } = decodePeople(raw, this.manifest.people_layout);
    const away = this.manifest.away;
    const settlements = record.settlements.map((s) => ({
      id: s.id,
      civ: s.civilization,
      q: s.q,
      r: s.r,
      label: settlementLabel(s.id),
      capital: s.capital,
      rank: s.rank,
      houses: s.houses,
      slots: s.slots,
      residents: s.residents,
      houseJobs: s.house_jobs,
      institutions: s.institutions,
    }));
    let home = 0;
    for (let i = 0; i < n; i += 1) if (columns.settlement[i] !== away) home += 1;
    const frame = new PeopleFrame(home, settlements, {
      names: this.ids,
      provenance: 'recorded run',
      note: `Engine day ${day} of ${this.label}`,
    });
    const perSettlement = new Uint32Array(settlements.length);
    let row = 0;
    for (let i = 0; i < n; i += 1) {
      const s = columns.settlement[i];
      if (s === away) continue;
      frame.id[row] = columns.id[i];
      frame.civ[row] = columns.civilization[i];
      frame.q[row] = columns.q[i];
      frame.r[row] = columns.r[i];
      frame.settlement[row] = s;
      frame.sex[row] = columns.sex[i];
      frame.age[row] = columns.age[i];
      frame.health[row] = columns.health[i];
      frame.duty[row] = columns.duty[i];
      // A design from the id (stable across days), odd designs for women.
      frame.appearance[row] = 2 * (columns.id[i] % 5) + columns.sex[i];
      // Five to a house in row order; the settlement plan places the overflow (crowded houses).
      frame.house[row] = Math.floor(perSettlement[s] / 5);
      perSettlement[s] += 1;
      row += 1;
    }
    frame.index();
    return { day, record, frame };
  }
}
