// Town walls, gatehouses and towers (PROTOTYPE ARTWORK).
//
// A settlement's ring is drawn from 8 m wall modules laid end to end along its
// planned line, one look per grade, with a gatehouse at the middle of each gate
// section and towers where the engine says they stand. Each painter returns the
// same contract as the buildings: sprite canvas, ground anchor, footprint.

import { rngFor } from '../../../sim/rng.js';
import { paintShadow, solidBox } from './buildings.js';
import { Sheet } from './iso.js';

/** Length of one wall module along the ring, in metres. */
export const WALL_MODULE_M = 8;

/** Height, thickness and materials of each grade's wall (the engine's grade names). */
export const WALL_LOOKS = {
  earthwork: {
    h: 1.4,
    t: 3.2,
    mat: { kind: 'stone', base: '#7a6646', mortar: '#685739' },
    top: { kind: 'thatch', base: '#6f7a40' },
  },
  palisade: { h: 3.4, t: 0.55, mat: { kind: 'logs', base: '#6c4d2f' } },
  drystone_wall: { h: 2.6, t: 1.4, mat: { kind: 'stone', base: '#9a948a', mortar: '#847e73' } },
  mortared_wall: {
    h: 4.2,
    t: 1.8,
    mat: { kind: 'stone', base: '#b09a7c', mortar: '#857460' },
    crenels: true,
  },
  fortress_wall: {
    h: 6.0,
    t: 2.6,
    mat: { kind: 'stone', base: '#a39d90', mortar: '#7d766a' },
    crenels: true,
  },
};
export const WALL_GRADES = Object.keys(WALL_LOOKS);
/** Strength of each grade when whole, as the engine's table has it. */
export const WALL_STRENGTH = {
  earthwork: 10,
  palisade: 25,
  drystone_wall: 45,
  mortared_wall: 70,
  fortress_wall: 100,
};

const TIMBER = { kind: 'logs', base: '#6c4d2f' };
const STONE = { kind: 'stone', base: '#a39d90', mortar: '#7d766a' };

function result(sheet, footprint, shadowPts) {
  return {
    canvas: sheet.canvas,
    anchor: sheet.anchor,
    shadow: shadowPts ? paintShadow(shadowPts, footprint, {}) : null,
    footprint,
    doors: [],
  };
}

/** A box along an axis, centred on the anchor: [x0, y0, x1, y1] for length `l`, thickness `t`. */
function along(axis, l, t) {
  return axis === 'x' ? [-l / 2, -t / 2, l / 2, t / 2] : [-t / 2, -l / 2, t / 2, l / 2];
}

function sheetFor([x0, y0, x1, y1], top) {
  return new Sheet(
    [
      [x0, y0, 0],
      [x1, y1, 0],
      [x0, y1, top],
      [x1, y0, top],
    ],
    8,
  );
}

function shadowOf([x0, y0, x1, y1], h) {
  return [
    [x0, y0, 0],
    [x1, y0, 0],
    [x1, y1, 0],
    [x0, y1, 0],
    [x0, y0, h],
    [x1, y0, h],
    [x1, y1, h],
    [x0, y1, h],
  ];
}

/** Merlons along the top of a box, on its outer half. */
function crenels(sheet, rng, axis, [x0, y0, x1, y1], zt, mat) {
  const l = axis === 'x' ? x1 - x0 : y1 - y0;
  const n = Math.max(2, Math.round(l / 1.6));
  const step = l / n;
  for (let i = 0; i < n; i += 2) {
    const a = (axis === 'x' ? x0 : y0) + i * step + step * 0.15;
    const b = a + step * 0.7;
    const box =
      axis === 'x'
        ? { x0: a, y0, x1: b, y1: y0 + (y1 - y0) * 0.35, zb: zt, zt: zt + 0.8 }
        : { x0, y0: a, x1: x0 + (x1 - x0) * 0.35, y1: b, zb: zt, zt: zt + 0.8 };
    solidBox(sheet, rng, box, mat);
  }
}

/** One 8 m module of a wall of a grade, running along x or y. */
export function paintWall({ id, grade, axis = 'x' }) {
  const look = WALL_LOOKS[grade];
  const rng = rngFor(id);
  const ext = along(axis, WALL_MODULE_M, look.t);
  const top = look.h + (look.crenels ? 0.8 : 0) + 0.6;
  const sheet = sheetFor(ext, top);
  const [x0, y0, x1, y1] = ext;
  solidBox(sheet, rng, { x0, y0, x1, y1, zb: 0, zt: look.h }, look.mat, look.top ?? look.mat);
  if (look.crenels) crenels(sheet, rng, axis, ext, look.h, look.mat);
  const footprint = { minX: x0, minY: y0, maxX: x1, maxY: y1 };
  return result(sheet, footprint, shadowOf(ext, look.h));
}

/** A gatehouse: two piers and a lintel over the way through, of timber or stone. */
export function paintGate({ id, axis = 'x', stone = true }) {
  const rng = rngFor(id);
  const mat = stone ? STONE : TIMBER;
  const pierH = stone ? 6.2 : 5.0;
  const t = stone ? 3.2 : 1.6;
  const ext = along(axis, WALL_MODULE_M, t);
  const sheet = sheetFor(ext, pierH + 1.4);
  const [x0, y0, x1, y1] = ext;
  const pier = 2.2;
  const pierBox = (from, to) =>
    axis === 'x' ? { x0: from, y0, x1: to, y1, zb: 0, zt: pierH } : { x0, y0: from, x1, y1: to, zb: 0, zt: pierH };
  const lo = axis === 'x' ? x0 : y0;
  const hi = axis === 'x' ? x1 : y1;
  solidBox(sheet, rng, pierBox(lo, lo + pier), mat);
  const lintel = pierBox(lo + pier, hi - pier);
  solidBox(sheet, rng, { ...lintel, zb: pierH - 1.6, zt: pierH - 0.4 }, mat);
  solidBox(sheet, rng, pierBox(hi - pier, hi), mat);
  if (stone) crenels(sheet, rng, axis, ext, pierH, mat);
  const footprint = { minX: x0, minY: y0, maxX: x1, maxY: y1 };
  return result(sheet, footprint, shadowOf(ext, pierH));
}

/** A tower standing on the ring: square, crenellated stone or a timber watch tower. */
export function paintTower({ id, stone = true }) {
  const rng = rngFor(id);
  const s = stone ? 4.2 : 3.0;
  const h = stone ? 8.5 : 7.0;
  const ext = [-s / 2, -s / 2, s / 2, s / 2];
  const sheet = sheetFor(ext, h + 1.4);
  const [x0, y0, x1, y1] = ext;
  solidBox(sheet, rng, { x0, y0, x1, y1, zb: 0, zt: h }, stone ? STONE : TIMBER);
  crenels(sheet, rng, 'x', ext, h, stone ? STONE : TIMBER);
  crenels(sheet, rng, 'y', ext, h, stone ? STONE : TIMBER);
  const footprint = { minX: x0, minY: y0, maxX: x1, maxY: y1 };
  return result(sheet, footprint, shadowOf(ext, h));
}
