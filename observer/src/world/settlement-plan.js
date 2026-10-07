// Settlement plans for large populations (Phase 5 S7). PRESENTATION ONLY.
//
// The engine records how many people live at a settlement and how many houses
// it has, never where in the settlement they stand. A plan lays the houses out
// in wards of 64 m blocks, in rings around the settlement's core, until there
// is a house for every five residents; the fields lie in a ring beyond the
// wards. Streets run between the blocks.
//
// Under rules version 3 the engine also records the council's design (town
// plan) and its walls, section by section along the planned ring. A plan then
// lays the wards inside the ring first, puts the keep, market, shrine and craft
// quarter where the design says, and draws the wall line: a square of side
// 2r+1 blocks, two block-sides a section, a gate in each gate section and
// towers where they stand. With the defensive works (export version 3) a gate may
// be a fortified gatehouse, towers stand on the sections the engine placed them
// on, and a ditch or moat, stakes and a citadel round the keep are drawn too.
//
// Everyone follows one of a few shared daily routines (by duty), sampled once
// into tables of a phase per minute of the day. A person's position is then a
// table lookup plus a short walk along the streets: a pure function of the
// person, the plan and the display clock, cheap enough for 100,000 people.

import { DUTIES, DUTY } from '../data/population.js';
import { hash2 } from '../sim/rng.js';
import { WALL_GRADES, WALL_MODULE_M, WALL_STRENGTH } from '../render/art/paint/walls.js';
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

/** House grades as the engine names them, meanest first; a lot's grade is its index here. */
export const GRADES = ['hut', 'house', 'stone_house'];
/** The grade code of a lot where houses are being built. */
export const SITE = 3;
/** The grade code of a house in a plan sized from residents alone (synthetic people). */
export const UNGRADED = -1;

/** The engine's plain plan: a settlement nobody has designed yet. */
export function isPlainPlan(design) {
  return (
    !design ||
    (design.style === 'open' &&
      design.keep === 'edge' &&
      design.wall_ring === 2 &&
      design.gates.length === 1 &&
      design.gates[0] === 0 &&
      !design.market &&
      !design.shrine &&
      !design.craft_quarter)
  );
}

/** The institution a place's building needs before it is drawn (the rest are drawn as designed). */
const PLACE_NEEDS = { keep: 'hall', craft_quarter: 'workshop' };
/** Buildings drawn for a design's places. */
export const PLACE_ASSETS = {
  keep: 'building.hall',
  market: 'building.well',
  shrine: 'building.shrine',
  craft_quarter: 'building.workshop.main',
};

/**
 * A point on a ring's line, `s` metres along it: from the middle of its east side, then
 * round by the north, west and south sides. Returns [x, y, axis of the side].
 */
export function ringPoint(halfSide, s) {
  const h = halfSide;
  const p = ((s % (8 * h)) + 8 * h) % (8 * h);
  if (p < h) return [h, 0 - p, 'y'];
  if (p < 3 * h) return [h - (p - h), -h, 'x'];
  if (p < 5 * h) return [-h, -h + (p - 3 * h), 'y'];
  if (p < 7 * h) return [-h + (p - 5 * h), h, 'x'];
  return [h, h - (p - 7 * h), 'y'];
}

export class SettlementPlan {
  /**
   * @param {{ residents: number, houses?: Record<string, number> | null,
   *   jobs?: Array<{ grade: string, count: number, built: number, workers: number }>,
   *   design?: object | null, walls?: object | null, waterAngle?: number,
   *   coreRadius?: number, seed?: number }} options
   *   `houses`: the engine's houses by grade (a recorded run). Without them (or with none),
   *   there is a house for every five residents. `jobs`: houses being built, drawn as sites.
   *   `design`: the council's town plan (rules version 3); `walls`: its ring as built, each
   *   section `[grade or null, strength]`, with `gates` (section indices) and `towers`; from
   *   export version 3, where there are any, `tower_sections`, `gatehouses`, `ditch` (1, or
   *   2 for a moat), `stakes` and `citadel`. `defence`: the standing defence order.
   *   `buildings`: the institution kinds standing there; the keep is drawn only with a hall,
   *   the craft quarter only with a workshop.
   */
  constructor({
    residents,
    houses = null,
    jobs = [],
    design = null,
    walls = null,
    defence = null,
    buildings = [],
    waterAngle = Math.PI / 2,
    coreRadius = 64,
    seed = 0,
  }) {
    this.residents = residents;
    this.coreRadius = coreRadius;
    this.seed = seed;
    this.design = design;
    this.defence = defence;
    this.designed = !!design && !isPlainPlan(design);
    this.buildings = new Set(buildings);
    this.ring = design ? design.wall_ring : 0;
    this.ringHalf = design ? (this.ring + 0.5) * BLOCK_M : 0;
    // Dwellings, best first so the finest stand nearest the core; then the building sites.
    const grades = [];
    const recorded = houses && Object.values(houses).reduce((a, b) => a + b, 0) > 0;
    if (recorded) {
      for (let g = GRADES.length - 1; g >= 0; g -= 1)
        for (let n = 0; n < (houses[GRADES[g]] ?? 0); n += 1) grades.push(g);
    } else {
      for (let n = 0; n < Math.ceil(residents / HOUSEHOLD); n += 1) grades.push(UNGRADED);
    }
    this.recorded = !!recorded;
    const dwellings = grades.length;
    for (const job of jobs) for (let n = 0; n < job.count - job.built; n += 1) grades.push(SITE);
    const lots = grades.length;
    this.grade = Int8Array.from(grades);
    this.sites = lots - dwellings;
    const blocksNeeded = Math.ceil(lots / HOUSES_PER_BLOCK);
    // The design's places take whole blocks; the store stands south of the centre.
    this.places = design ? this._places(design, waterAngle) : [];
    const taken = new Set(this.places.map((p) => `${p.i},${p.j}`));
    if (design) taken.add('0,0').add('0,1');
    // Candidate blocks clear of the core, nearest first (ties by angle, then cell); with a
    // design, those inside the ring first.
    const cand = [];
    const clear = design ? 0 : coreRadius + 8;
    const reach = Math.ceil(Math.sqrt(blocksNeeded + taken.size) + clear / BLOCK_M) + this.ring + 2;
    for (let j = -reach; j <= reach; j += 1) {
      for (let i = -reach; i <= reach; i += 1) {
        const cx = i * BLOCK_M;
        const cy = j * BLOCK_M;
        // Nearest point of the block square to the centre.
        const nx = Math.max(Math.abs(cx) - BLOCK_M / 2, 0);
        const ny = Math.max(Math.abs(cy) - BLOCK_M / 2, 0);
        if (design ? taken.has(`${i},${j}`) : Math.hypot(nx, ny) < clear) continue;
        const outside = design && Math.max(Math.abs(i), Math.abs(j)) > this.ring ? 1 : 0;
        cand.push({ i, j, o: outside, d: Math.hypot(cx, cy), a: Math.atan2(cy, cx) });
      }
    }
    cand.sort((p, q) => (p.o ?? 0) - (q.o ?? 0) || p.d - q.d || p.a - q.a || p.j - q.j || p.i - q.i);
    if (cand.length < blocksNeeded) throw new Error('settlement plan: not enough room');
    this.blocks = cand.slice(0, blocksNeeded).map((c) => [c.i, c.j]);
    this.houseCount = dwellings; // lots people live in
    this.lots = lots; // dwellings, then building sites
    this.houseX = new Float32Array(lots);
    this.houseY = new Float32Array(lots);
    this.doorY = new Float32Array(lots); // the street centreline the door opens on
    this.occupants = new Uint16Array(dwellings);
    let wardRadius = clear;
    for (let h = 0; h < lots; h += 1) {
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
    for (const p of this.places) wardRadius = Math.max(wardRadius, Math.hypot(p.x, p.y) + BLOCK_M / 2);
    // The fields lie beyond the walls' line.
    if (design) wardRadius = Math.max(wardRadius, this.ringHalf * Math.SQRT2 + 8);
    this.wardRadius = wardRadius;
    this.fieldRing = { r0: wardRadius + FIELD_GAP_M, r1: wardRadius + FIELD_GAP_M + FIELD_DEPTH_M };
    this.store = design ? [0, BLOCK_M] : [0, coreRadius];
    this._wallPieces(walls);
  }

  /** Blocks for the keep, market, shrine and craft quarter, where the design puts them. */
  _places(design, waterAngle) {
    const r = design.wall_ring;
    const n = 2 * (2 * r + 1);
    const gateAngle = design.gates.length ? 2 * Math.PI * ((Math.floor((design.gates[0] * n) / 6) + 0.5) / n) : 0;
    const used = new Set(['0,1']);
    const out = [];
    const near = (i, j) => {
      // The nearest free block to (i, j), spiralling out.
      for (let d = 0; d < 6; d += 1) {
        for (let dj = -d; dj <= d; dj += 1) {
          for (let di = -d; di <= d; di += 1) {
            if (Math.max(Math.abs(di), Math.abs(dj)) !== d) continue;
            const key = `${i + di},${j + dj}`;
            if (!used.has(key)) return [i + di, j + dj];
          }
        }
      }
      return [i, j];
    };
    const toward = (angle, dist) => [Math.round(Math.cos(angle) * dist), Math.round(-Math.sin(angle) * dist)];
    ['keep', 'market', 'shrine', 'craft_quarter'].forEach((name, idx) => {
      const place = design[name];
      if (!place) return;
      let want;
      if (place === 'centre') want = [0, 0];
      else if (place === 'by_store') want = [1, 1];
      else if (place === 'by_gate') want = toward(gateAngle, Math.max(1, r - 1));
      else if (place === 'by_water') want = toward(waterAngle, Math.max(1, r));
      else want = toward(Math.PI / 2 + (idx * Math.PI) / 3, r + 1);
      const [i, j] = near(want[0], want[1]);
      used.add(`${i},${j}`);
      out.push({ name, place, i, j, x: i * BLOCK_M, y: j * BLOCK_M, asset: PLACE_ASSETS[name] });
    });
    return out;
  }

  /**
   * The ring's drawable pieces: wall modules of each built section's grade, a gate (or a
   * fortified gatehouse) in the middle of each gate section, towers (on their sections, or
   * corners first, then the section ends), a citadel round the keep, and the places'
   * buildings; the lines of sections not yet built, and of a ditch or moat and stakes.
   */
  _wallPieces(walls) {
    this.pieces = this.places
      .filter((p) => !PLACE_NEEDS[p.name] || this.buildings.has(PLACE_NEEDS[p.name]))
      .map((p) => ({ x: p.x, y: p.y, asset: p.asset, damaged: false }));
    this.planned = [];
    this.sections = [];
    this.ditch = null;
    this.stakes = [];
    this.citadel = null;
    if (!this.design) return;
    const h = this.ringHalf;
    const n = 2 * (2 * this.ring + 1);
    const length = (8 * h) / n;
    const gates = new Set(walls?.gates ?? this.design.gates.map((g) => Math.floor((g * n) / 6)));
    const gatehouses = new Set(walls?.gatehouses ?? []);
    for (let k = 0; k < n; k += 1) {
      const [grade, strength] = walls?.sections?.[k] ?? [null, 0];
      const section = { index: k, grade, strength, gate: gates.has(k) };
      this.sections.push(section);
      const s0 = k * length;
      if (!grade) {
        // Not built yet: the planned line, drawn dashed.
        const pts = [];
        for (let s = s0; s <= s0 + length + 0.01; s += BLOCK_M / 2) pts.push(ringPoint(h, s).slice(0, 2));
        this.planned.push(pts);
        continue;
      }
      const damaged = strength * 2 < (WALL_STRENGTH[grade] ?? 1);
      const stone = WALL_GRADES.indexOf(grade) >= WALL_GRADES.indexOf('drystone_wall');
      const modules = Math.round(length / WALL_MODULE_M);
      for (let m = 0; m < modules; m += 1) {
        const [x, y, axis] = ringPoint(h, s0 + (m + 0.5) * WALL_MODULE_M);
        const gateHere = section.gate && m === Math.floor(modules / 2);
        const gate = gatehouses.has(k) ? 'gatehouse' : 'gate';
        const asset = gateHere ? `wall.${gate}.${stone ? 'stone' : 'timber'}.${axis}` : `wall.${grade}.${axis}`;
        this.pieces.push({ x, y, asset, damaged });
      }
    }
    // Towers: on the sections the engine placed them on, at the middle (beside the gate on
    // a gate section); else the four corners first, then the ends of sections, in ring order.
    const towers = walls?.towers ?? 0;
    const spots = [];
    if (walls?.tower_sections?.length) {
      for (const k of walls.tower_sections) spots.push(k * length + length / 2 - (gates.has(k) ? WALL_MODULE_M : 0));
    } else {
      spots.push(h, 3 * h, 5 * h, 7 * h);
      for (let k = 0; k < n; k += 1) {
        const s = k * length;
        if (!spots.some((c) => Math.abs(c - s) < 1)) spots.push(s);
      }
    }
    const built = this.sections.filter((x) => x.grade);
    const weakest = built.reduce(
      (w, x) => (WALL_GRADES.indexOf(x.grade) < WALL_GRADES.indexOf(w) ? x.grade : w),
      built[0]?.grade ?? 'palisade',
    );
    const stoneTowers = WALL_GRADES.indexOf(weakest) >= WALL_GRADES.indexOf('drystone_wall');
    for (let t = 0; t < Math.min(towers, spots.length); t += 1) {
      const [x, y] = ringPoint(h, spots[t]);
      this.pieces.push({ x, y, asset: `wall.tower.${stoneTowers ? 'stone' : 'timber'}`, damaged: false });
    }
    // A ditch (or moat) half a block outside the wall; stakes between the two.
    if (walls?.ditch) {
      const d = h + BLOCK_M / 2;
      this.ditch = {
        kind: walls.ditch === 2 ? 'moat' : 'ditch',
        line: [
          [d, d],
          [d, -d],
          [-d, -d],
          [-d, d],
          [d, d],
        ],
      };
    }
    if (walls?.stakes) {
      const hs = h + BLOCK_M / 4;
      for (let s = WALL_MODULE_M / 2; s < 8 * hs; s += WALL_MODULE_M) {
        const [x, y, axis] = ringPoint(hs, s);
        const [nx, ny] = axis === 'y' ? [1.5, 0] : [0, 1.5];
        this.stakes.push([
          [x - nx, y - ny],
          [x + nx, y + ny],
        ]);
      }
    }
    // A citadel: a wall round the keep's block, its gate facing the store to the south.
    if (walls?.citadel) {
      this.citadel = walls.citadel;
      const { grade } = walls.citadel;
      const hc = BLOCK_M / 2;
      const modules = Math.round((8 * hc) / WALL_MODULE_M);
      const gateModule = Math.floor((6 * hc) / WALL_MODULE_M);
      const stone = WALL_GRADES.indexOf(grade) >= WALL_GRADES.indexOf('drystone_wall');
      for (let m = 0; m < modules; m += 1) {
        const [x, y, axis] = ringPoint(hc, (m + 0.5) * WALL_MODULE_M);
        const asset = m === gateModule ? `wall.gate.${stone ? 'stone' : 'timber'}.${axis}` : `wall.${grade}.${axis}`;
        this.pieces.push({ x, y, asset, damaged: false });
      }
    }
  }

  /** Sections standing, of all the ring's. */
  wallsBuilt() {
    return { built: this.sections.filter((x) => x.grade).length, of: this.sections.length };
  }

  bytes() {
    return (
      this.houseX.byteLength +
      this.houseY.byteLength +
      this.doorY.byteLength +
      this.grade.byteLength +
      this.occupants.byteLength
    );
  }

  /** A house's people: more than five means crowded (residents beyond the houses' room). */
  crowded(h) {
    return this.occupants[h] > HOUSEHOLD;
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
      // At a building site when there is one, else at a house lot (repairs).
      const h = this.sites ? this.houseCount + Math.floor(u * this.sites) : Math.floor(u * this.houseCount);
      out[0] = this.houseX[h] + (v - 0.5) * 4;
      out[1] = this.doorY[h] + (this.houseY[h] > this.doorY[h] ? 2.5 : -2.5);
    } else if (name === 'guard' && this.ringHalf) {
      // Guards walk the walls' line.
      const [x, y] = ringPoint(this.ringHalf, u * 8 * this.ringHalf);
      out[0] = x;
      out[1] = y;
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
/**
 * Plans for every settlement of a frame, and each resident's house: five to a house in row
 * order, then the overflow shared round the houses (crowded). Writes `frame.house`.
 */
export function plansFor(frame, { coreRadius = 64 } = {}) {
  return frame.settlements.map((s, k) => {
    const plan = new SettlementPlan({
      residents: frame.residents(k),
      houses: s.houses ?? null,
      jobs: s.houseJobs ?? [],
      design: s.plan ?? null,
      walls: s.walls ?? null,
      defence: s.defence ?? null,
      buildings: (s.institutions ?? []).map((item) => item.kind),
      coreRadius,
      seed: k + 1,
    });
    const rows = frame.rowsOf(k);
    const room = plan.houseCount * HOUSEHOLD;
    for (let n = 0; n < rows.length; n += 1) {
      const h = n < room ? Math.floor(n / HOUSEHOLD) : (n - room) % Math.max(1, plan.houseCount);
      frame.house[rows[n]] = h;
      plan.occupants[h] += 1;
    }
    return plan;
  });
}

export { DUTY };
