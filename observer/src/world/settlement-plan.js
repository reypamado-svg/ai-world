// Settlement plans for large populations (Phase 5 S7). PRESENTATION ONLY.
//
// The engine records how many people live at a settlement and how many houses
// it has, never where in the settlement they stand. A plan lays the houses out
// in wards of 64 m blocks, in rings around the settlement's core, until there
// is a house for every five residents; the fields lie in a ring beyond the
// wards. Streets run between the blocks.
//
// Everyone follows one of a few shared daily routines (by duty), sampled once
// into tables of a phase per minute of the day. A person's position is then a
// table lookup plus a short walk along the streets: a pure function of the
// person, the plan and the display clock, cheap enough for 100,000 people.

import { DUTIES, DUTY } from '../data/population.js';
import { hash2 } from '../sim/rng.js';
import { facingFor, STRIDE_M } from '../sim/paths.js';

export const BLOCK_M = 64;
export const HOUSES_PER_BLOCK = 16; // eight lots facing the street on each long side
export const HOUSEHOLD = 5;
const LOT_M = (BLOCK_M - 8) / 8;
const SETBACK_M = 6; // house centre from the street centreline
const FIELD_GAP_M = 16;
const FIELD_DEPTH_M = 340;
export const WALK_MPS = 1.3;
const DEPART_SPREAD_S = 1800; // people leave up to half an hour apart

/** Phases of a day. */
export const PHASE = { home: 0, go: 1, work: 2, back: 3, shuttle: 4, inside: 5, out: 6 };
const PHASE_NAMES = Object.keys(PHASE);

/** Each duty's day: [hour it starts, phase]. A day starts at midnight at home. */
export const ROUTINES = {
  child: [
    [0, 'home'],
    [7.5, 'out'],
    [12, 'home'],
    [13, 'out'],
    [18.5, 'home'],
  ],
  elder: [
    [0, 'home'],
    [9, 'out'],
    [12, 'home'],
    [14, 'out'],
    [17, 'home'],
  ],
  farmer: [
    [0, 'home'],
    [6, 'go'],
    [7.5, 'work'],
    [18, 'back'],
    [19.5, 'home'],
  ],
  carrier: [
    [0, 'home'],
    [7, 'shuttle'],
    [17, 'home'],
  ],
  builder: [
    [0, 'home'],
    [6.5, 'go'],
    [7.5, 'work'],
    [17, 'back'],
    [18, 'home'],
  ],
  woodcutter: [
    [0, 'home'],
    [6, 'go'],
    [7.5, 'work'],
    [17, 'back'],
    [18.5, 'home'],
  ],
  crafter: [
    [0, 'home'],
    [7, 'go'],
    [8, 'inside'],
    [17, 'back'],
    [18, 'home'],
  ],
  scholar: [
    [0, 'home'],
    [7.5, 'go'],
    [8.5, 'inside'],
    [16, 'back'],
    [17, 'home'],
  ],
  guard: [
    [0, 'home'],
    [6, 'shuttle'],
    [22, 'home'],
  ],
};

const WORK_ANIM = { farmer: 'hoe', builder: 'hammer', woodcutter: 'axe' };
const ACTIVITY = {
  child: { out: 'Playing near home' },
  elder: { out: 'Sitting out near home' },
  farmer: { go: 'Walking to the fields', work: 'Hoeing in the fields', back: 'Walking home from the fields' },
  carrier: { shuttle: 'Carrying goods between the stores and the ward' },
  builder: { go: 'Walking to a building site', work: 'Building', back: 'Walking home' },
  woodcutter: { go: 'Walking to the woods', work: 'Felling timber', back: 'Carrying timber home' },
  crafter: { go: 'Walking to the workshops', inside: 'At work in the workshops', back: 'Walking home' },
  scholar: { go: 'Walking to the hall', inside: 'At study in the hall', back: 'Walking home' },
  guard: { shuttle: 'On watch round the ward' },
};

/** Per duty, a Float32Array of [phase, phase start in seconds] for each minute of the day. */
export const ROUTINE_TABLES = DUTIES.map((duty) => {
  const steps = ROUTINES[duty];
  const table = new Float32Array(1440 * 2);
  let k = 0;
  for (let m = 0; m < 1440; m += 1) {
    while (k + 1 < steps.length && steps[k + 1][0] * 60 <= m) k += 1;
    table[m * 2] = PHASE[steps[k][1]];
    table[m * 2 + 1] = steps[k][0] * 3600;
  }
  return table;
});

/** The phase a duty's routine gives at a time of day (seconds), read from the definition. */
export function routinePhase(duty, tod) {
  const steps = ROUTINES[duty];
  let phase = steps[0][1];
  for (const [h, p] of steps) if (h * 3600 <= tod) phase = p;
  return phase;
}

function streetLine(v) {
  return (Math.round(v / BLOCK_M - 0.5) + 0.5) * BLOCK_M;
}

export class SettlementPlan {
  /**
   * @param {{ residents: number, coreRadius?: number, seed?: number }} options
   */
  constructor({ residents, coreRadius = 64, seed = 0 }) {
    this.residents = residents;
    this.coreRadius = coreRadius;
    this.seed = seed;
    const houses = Math.ceil(residents / HOUSEHOLD);
    const blocksNeeded = Math.ceil(houses / HOUSES_PER_BLOCK);
    // Candidate blocks clear of the core, nearest first (ties by angle, then cell).
    const cand = [];
    const clear = coreRadius + 8;
    const reach = Math.ceil(Math.sqrt(blocksNeeded) + clear / BLOCK_M) + 2;
    for (let j = -reach; j <= reach; j += 1) {
      for (let i = -reach; i <= reach; i += 1) {
        const cx = i * BLOCK_M;
        const cy = j * BLOCK_M;
        // Nearest point of the block square to the centre.
        const nx = Math.max(Math.abs(cx) - BLOCK_M / 2, 0);
        const ny = Math.max(Math.abs(cy) - BLOCK_M / 2, 0);
        if (Math.hypot(nx, ny) < clear) continue;
        cand.push({ i, j, d: Math.hypot(cx, cy), a: Math.atan2(cy, cx) });
      }
    }
    cand.sort((p, q) => p.d - q.d || p.a - q.a || p.j - q.j || p.i - q.i);
    if (cand.length < blocksNeeded) throw new Error('settlement plan: not enough room');
    this.blocks = cand.slice(0, blocksNeeded).map((c) => [c.i, c.j]);
    this.houseCount = houses;
    this.houseX = new Float32Array(houses);
    this.houseY = new Float32Array(houses);
    this.doorY = new Float32Array(houses); // the street centreline the door opens on
    let wardRadius = clear;
    for (let h = 0; h < houses; h += 1) {
      const [i, j] = this.blocks[Math.floor(h / HOUSES_PER_BLOCK)];
      const k = h % HOUSES_PER_BLOCK;
      const north = k < 8;
      const street = j * BLOCK_M + (north ? -BLOCK_M / 2 : BLOCK_M / 2);
      this.houseX[h] = i * BLOCK_M - BLOCK_M / 2 + 4 + LOT_M * ((k % 8) + 0.5);
      this.houseY[h] = street + (north ? SETBACK_M : -SETBACK_M);
      this.doorY[h] = street;
    }
    for (const [i, j] of this.blocks) {
      wardRadius = Math.max(
        wardRadius,
        Math.hypot(Math.abs(i * BLOCK_M) + BLOCK_M / 2, Math.abs(j * BLOCK_M) + BLOCK_M / 2),
      );
    }
    this.wardRadius = wardRadius;
    this.fieldRing = { r0: wardRadius + FIELD_GAP_M, r1: wardRadius + FIELD_GAP_M + FIELD_DEPTH_M };
    this.store = [0, coreRadius];
  }

  bytes() {
    return this.houseX.byteLength + this.houseY.byteLength + this.doorY.byteLength;
  }

  /** Where a person works (local metres), from their duty, house and id; written into out. */
  workPoint(duty, house, id, out) {
    const u = hash2(id, 1, this.seed);
    const v = hash2(id, 2, this.seed);
    const name = DUTIES[duty];
    if (name === 'farmer' || name === 'woodcutter') {
      const { r0, r1 } = this.fieldRing;
      const r = name === 'farmer' ? r0 + 8 + (r1 - r0 - 16) * Math.sqrt(v) : r1 + 20 + 100 * v;
      out[0] = Math.cos(u * Math.PI * 2) * r;
      out[1] = Math.sin(u * Math.PI * 2) * r;
    } else if (name === 'builder') {
      const h = Math.floor(u * this.houseCount);
      out[0] = this.houseX[h] + (v - 0.5) * 4;
      out[1] = this.doorY[h] + (this.houseY[h] > this.doorY[h] ? 2.5 : -2.5);
    } else if (name === 'guard') {
      out[0] = Math.cos(u * Math.PI * 2) * this.wardRadius;
      out[1] = Math.sin(u * Math.PI * 2) * this.wardRadius;
    } else if (name === 'carrier') {
      out[0] = this.store[0] + (v - 0.5) * 6;
      out[1] = this.store[1];
    } else {
      // Children, elders, crafters and scholars: the core (indoor duties) or home.
      out[0] = (u - 0.5) * 8;
      out[1] = (v - 0.5) * 8;
    }
    return out;
  }
}

/**
 * Work points and departure times for every row of a frame, one plan per settlement.
 * About 10 bytes a person.
 */
export class CrowdLayout {
  constructor(frame, plans) {
    this.frame = frame;
    this.plans = plans;
    const n = frame.length;
    this.workX = new Float32Array(n);
    this.workY = new Float32Array(n);
    this.depart = new Uint16Array(n); // seconds after the phase starts
    const p = [0, 0];
    for (let i = 0; i < n; i += 1) {
      const plan = plans[frame.settlement[i]];
      plan.workPoint(frame.duty[i], frame.house[i], frame.id[i], p);
      this.workX[i] = p[0];
      this.workY[i] = p[1];
      this.depart[i] = Math.floor(hash2(frame.id[i], 3, plan.seed) * DEPART_SPREAD_S);
    }
  }

  bytes() {
    return this.workX.byteLength + this.workY.byteLength + this.depart.byteLength;
  }

  /**
   * Where row i is at time of day `tod` (seconds), in its settlement's local metres.
   * Fills and returns `out` (no allocation): x, y, inside, anim, phase, facing, flip, moving, activity.
   */
  sample(i, tod, out) {
    const f = this.frame;
    const plan = this.plans[f.settlement[i]];
    const duty = f.duty[i];
    const name = DUTIES[duty];
    const table = ROUTINE_TABLES[duty];
    const m = Math.min(1439, Math.max(0, Math.floor(tod / 60)));
    const ph = table[m * 2];
    const since = tod - table[m * 2 + 1] - this.depart[i];
    const h = f.house[i];
    const hx = plan.houseX[h];
    const dy = plan.doorY[h];
    const wx = this.workX[i];
    const wy = this.workY[i];
    out.moving = false;
    out.inside = false;
    out.flip = false;
    out.facing = 'front';
    out.phase = 0;
    out.activity = ACTIVITY[name]?.[PHASE_NAMES[ph]] ?? 'At home';
    if (ph === PHASE.home || (since < 0 && (ph === PHASE.go || ph === PHASE.shuttle || ph === PHASE.out))) {
      out.x = hx;
      out.y = plan.houseY[h];
      out.inside = true;
      out.anim = 'idle';
      out.activity = 'At home';
      return out;
    }
    if (ph === PHASE.out) {
      const u = hash2(f.id[i], 4, plan.seed);
      out.x = hx + (u - 0.5) * 5;
      out.y = dy + (plan.houseY[h] > dy ? 1.5 : -1.5);
      out.anim = u < 0.5 ? 'idle' : 'talk';
      out.phase = (tod / 1.6 + u) % 1;
      return out;
    }
    if (ph === PHASE.shuttle) {
      const len = pathLength(hx, dy, wx, wy);
      const lap = (2 * len) / WALK_MPS;
      const s = (since % lap) / lap;
      const outbound = s < 0.5;
      const d = (outbound ? s * 2 : 2 - s * 2) * len;
      walkAt(hx, dy, wx, wy, d, outbound, out);
      out.anim = name === 'carrier' && outbound ? 'carry_sack' : 'walk';
      return out;
    }
    if (ph === PHASE.back && since < 0) return atWork(name, wx, wy, tod, i, out);
    if (ph === PHASE.go || ph === PHASE.back) {
      const len = pathLength(hx, dy, wx, wy);
      const d = Math.min(len, Math.max(0, since) * WALK_MPS);
      if (d >= len) {
        if (ph === PHASE.back) {
          out.x = hx;
          out.y = plan.houseY[h];
          out.inside = true;
          out.anim = 'idle';
          out.activity = 'At home';
          return out;
        }
        return atWork(name, wx, wy, tod, i, out);
      }
      if (ph === PHASE.go) walkAt(hx, dy, wx, wy, d, true, out);
      else walkAt(hx, dy, wx, wy, len - d, false, out);
      out.anim = name === 'woodcutter' && ph === PHASE.back ? 'carry_log' : 'walk';
      return out;
    }
    return atWork(name, wx, wy, tod, i, out);
  }
}

function atWork(name, wx, wy, tod, i, out) {
  out.x = wx;
  out.y = wy;
  if (name === 'crafter' || name === 'scholar') {
    out.inside = true;
    out.anim = 'idle';
    return out;
  }
  out.anim = WORK_ANIM[name] ?? 'idle';
  out.phase = (tod / 1.2 + (i % 7) / 7) % 1;
  return out;
}

/** Street path from a door (on a street running east-west) to a point: along the street, down the cross street, then across. */
export function pathLength(ax, ay, bx, by) {
  const sx = streetLine(bx);
  return Math.abs(sx - ax) + Math.abs(by - ay) + Math.abs(bx - sx);
}

const FACE = new Map();
function facing(vx, vy) {
  const k = vx * 3 + vy;
  if (!FACE.has(k)) FACE.set(k, facingFor(vx, vy));
  return FACE.get(k);
}

/** Position at distance d along the street path from (ax, ay) to (bx, by); `forward` sets the facing. */
export function walkAt(ax, ay, bx, by, d, forward, out) {
  const sx = streetLine(bx);
  const l1 = Math.abs(sx - ax);
  const l2 = Math.abs(by - ay);
  let vx = 0;
  let vy = 0;
  if (d < l1) {
    vx = Math.sign(sx - ax);
    out.x = ax + vx * d;
    out.y = ay;
  } else if (d < l1 + l2) {
    vy = Math.sign(by - ay);
    out.x = sx;
    out.y = ay + vy * (d - l1);
  } else {
    vx = Math.sign(bx - sx);
    out.x = sx + vx * Math.min(d - l1 - l2, Math.abs(bx - sx));
    out.y = by;
  }
  if (!vx && !vy) vy = 1;
  const fc = forward ? facing(vx, vy) : facing(-vx, -vy);
  out.facing = fc.facing;
  out.flip = fc.flip;
  out.moving = true;
  out.phase = (d / STRIDE_M) % 1;
  return out;
}

/** Plans for every settlement of a frame. */
export function plansFor(frame, { coreRadius = 64 } = {}) {
  return frame.settlements.map(
    (s, k) => new SettlementPlan({ residents: frame.residents(k), coreRadius, seed: k + 1 }),
  );
}

export { DUTY };
