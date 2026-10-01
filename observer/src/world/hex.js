// Hex layout for engine tiles, in presentation metres.
//
// Engine tiles are axial (q, r). The observer lays them out as pointy-top
// hexes of radius R metres (a PRESENTATION constant from the export
// manifest) in a hex frame (u, v), rotated 45 degrees onto the ground plane
// so that, after the 2:1 isometric projection, tile rows are horizontal on
// screen. Rotation preserves distances, so metres mean the same everywhere.

import { project } from './coords.js';

const S2 = Math.SQRT2;
const SQ3 = Math.sqrt(3);

export function hexToUV(q, r, R) {
  return { u: R * SQ3 * (q + r / 2), v: 1.5 * R * r };
}

export function uvToPlane(u, v) {
  return { x: (u + v) / S2, y: (v - u) / S2 };
}

export function planeToUV(x, y) {
  return { u: (x - y) / S2, v: (x + y) / S2 };
}

/** Centre of tile (q, r) on the ground plane, in metres. */
export function hexCentre(q, r, R) {
  const { u, v } = hexToUV(q, r, R);
  return uvToPlane(u, v);
}

/** Six corners of tile (q, r) on the ground plane, clockwise on screen. */
export function hexCorners(q, r, R) {
  const c = hexToUV(q, r, R);
  const out = [];
  for (let i = 0; i < 6; i += 1) {
    const a = (Math.PI / 180) * (60 * i - 90);
    out.push(uvToPlane(c.u + R * Math.cos(a), c.v + R * Math.sin(a)));
  }
  return out;
}

function cubeRound(q, r) {
  const s = -q - r;
  let rq = Math.round(q);
  let rr = Math.round(r);
  const rs = Math.round(s);
  const dq = Math.abs(rq - q);
  const dr = Math.abs(rr - r);
  const ds = Math.abs(rs - s);
  if (dq > dr && dq > ds) rq = -rr - rs;
  else if (dr > ds) rr = -rq - rs;
  return { q: rq + 0, r: rr + 0 }; // + 0 turns -0 into 0
}

/** Tile containing a ground-plane point. */
export function planeToHex(x, y, R) {
  const { u, v } = planeToUV(x, y);
  const r = v / (1.5 * R);
  const q = u / (R * SQ3) - r / 2;
  return cubeRound(q, r);
}

export function chunkOf(q, r, chunkTiles) {
  return { cq: Math.floor(q / chunkTiles), cr: Math.floor(r / chunkTiles) };
}

/** Screen-space (zoom 1) bounding box of a set of tiles' hexes. */
export function screenBoundsOfTiles(tiles, R) {
  let x0 = Infinity;
  let y0 = Infinity;
  let x1 = -Infinity;
  let y1 = -Infinity;
  for (const [q, r] of tiles) {
    for (const c of hexCorners(q, r, R)) {
      const p = project(c.x, c.y);
      x0 = Math.min(x0, p.x);
      y0 = Math.min(y0, p.y);
      x1 = Math.max(x1, p.x);
      y1 = Math.max(y1, p.y);
    }
  }
  return { x0, y0, x1, y1 };
}

/** Screen bounds (zoom 1) of a chunk's tiles. Only the four corner tiles matter. */
export function chunkScreenBounds(cq, cr, chunkTiles, R, width, height) {
  const q0 = cq * chunkTiles;
  const r0 = cr * chunkTiles;
  const q1 = Math.min(width, q0 + chunkTiles) - 1;
  const r1 = Math.min(height, r0 + chunkTiles) - 1;
  return screenBoundsOfTiles(
    [
      [q0, r0],
      [q1, r0],
      [q0, r1],
      [q1, r1],
    ],
    R,
  );
}

/** Screen bounds (zoom 1) of a whole width x height map. */
export function worldScreenBounds(width, height, R) {
  return screenBoundsOfTiles(
    [
      [0, 0],
      [width - 1, 0],
      [0, height - 1],
      [width - 1, height - 1],
    ],
    R,
  );
}
