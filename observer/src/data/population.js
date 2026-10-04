// The people the observer shows, as typed columns (Phase 5 S7).
//
// One row per person: no object per person, so 100,000 people cost a few
// megabytes. The columns hold what the engine records (civilization, tile,
// settlement, sex, age, health, duty); where a person stands in the street is
// presentation, worked out from the settlement plan, the duty, the id and the
// display clock. A synthetic population fills these columns today; the O2
// reader will fill the same columns from a recorded run.

import { personLabel } from './naming.js';

/** Duty classes: what a person does through the day. */
export const DUTIES = ['child', 'elder', 'farmer', 'carrier', 'builder', 'woodcutter', 'crafter', 'scholar', 'guard'];
export const DUTY = Object.fromEntries(DUTIES.map((d, i) => [d, i]));

const ROLE_LABELS = {
  child: 'Child',
  elder: 'Elder',
  farmer: 'Farmer',
  carrier: 'Carrier',
  builder: 'Builder',
  woodcutter: 'Woodcutter',
  crafter: 'Crafter',
  scholar: 'Scholar',
  guard: 'Guard',
};

const ID_DIGITS = 10;

export function personId(n) {
  return `person:${String(n).padStart(ID_DIGITS, '0')}`;
}

export function personNumber(id) {
  const m = /^person:(\d+)$/.exec(id);
  return m ? Number(m[1]) : -1;
}

export class PeopleFrame {
  /**
   * @param {number} n rows
   * @param {Array<{ id: string, civ: number, q: number, r: number, label?: string }>} settlements
   */
  constructor(n, settlements) {
    this.length = n;
    this.settlements = settlements;
    this.id = new Uint32Array(n); // the number in `person:NNNNNNNNNN`, ascending
    this.civ = new Uint8Array(n);
    this.q = new Int16Array(n);
    this.r = new Int16Array(n);
    this.settlement = new Uint16Array(n); // index into `settlements`
    this.sex = new Uint8Array(n); // 0 male, 1 female
    this.age = new Uint8Array(n); // years
    this.health = new Uint8Array(n); // 0-100
    this.duty = new Uint8Array(n); // index into DUTIES
    this.appearance = new Uint8Array(n);
    this.house = new Uint32Array(n); // index into the settlement plan's houses
    this.bySettlement = null; // rows grouped by settlement, set by index()
    this.settlementStart = null;
  }

  /** Group rows by settlement (in row order within each); call once the columns are filled. */
  index() {
    const k = this.settlements.length;
    const counts = new Uint32Array(k + 1);
    for (let i = 0; i < this.length; i += 1) counts[this.settlement[i] + 1] += 1;
    for (let s = 0; s < k; s += 1) counts[s + 1] += counts[s];
    const next = counts.slice(0, k);
    const rows = new Uint32Array(this.length);
    for (let i = 0; i < this.length; i += 1) rows[next[this.settlement[i]]++] = i;
    this.bySettlement = rows;
    this.settlementStart = counts;
    for (let i = 1; i < this.length; i += 1) {
      if (this.id[i] <= this.id[i - 1]) throw new Error('person ids must be unique and ascending');
    }
    return this;
  }

  /** Rows living at a settlement, as a view (no copy). */
  rowsOf(s) {
    return this.bySettlement.subarray(this.settlementStart[s], this.settlementStart[s + 1]);
  }

  residents(s) {
    return this.settlementStart[s + 1] - this.settlementStart[s];
  }

  idOf(i) {
    return personId(this.id[i]);
  }

  /** Row of a person id, or -1 (binary search on the ascending id column). */
  indexOf(id) {
    const n = personNumber(id);
    let lo = 0;
    let hi = this.length - 1;
    while (lo <= hi) {
      const mid = (lo + hi) >>> 1;
      const v = this.id[mid];
      if (v === n) return mid;
      if (v < n) lo = mid + 1;
      else hi = mid - 1;
    }
    return -1;
  }

  dutyOf(i) {
    return DUTIES[this.duty[i]];
  }

  /** A person's record for the inspector (made on demand, never stored). */
  record(i) {
    const id = this.idOf(i);
    const s = this.settlements[this.settlement[i]];
    const h = this.health[i];
    return {
      id,
      label: personLabel(id),
      sex: this.sex[i] ? 'female' : 'male',
      age: this.age[i],
      role: ROLE_LABELS[this.dutyOf(i)],
      health: h >= 80 ? 'Healthy' : h >= 50 ? 'Ailing' : 'Gravely ill',
      settlement: s.label ?? s.id,
      tile: [this.q[i], this.r[i]],
      household: `House ${this.house[i] + 1}`,
      skills: {},
      events: ['Synthetic population (S7): a stand-in for the recorded run'],
      provenance: 'synthetic',
    };
  }

  /** Bytes held by the columns and the settlement index. */
  bytes() {
    let b = 0;
    for (const v of Object.values(this)) if (ArrayBuffer.isView(v)) b += v.byteLength;
    return b;
  }
}
