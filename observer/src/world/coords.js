// One coordinate system for every zoom level.
//
// Ground plane: metres, x and y. Height: metres, z. These metres are a
// PRESENTATION scale only. The engine's authoritative units are axial hex
// tiles and whole days; nothing here is simulation data.
//
// Projection: 2:1 isometric (dimetric). Larger x + y is nearer the viewer.

/** Screen pixels per metre along the ground axes at zoom 1. */
export const K = 16;
/** Screen pixels per metre of height at zoom 1. */
export const KZ = 14;

export function project(x, y, z = 0) {
  return { x: (x - y) * K, y: ((x + y) * K) / 2 - z * KZ };
}

/** Screen point back to the ground plane (z = 0). */
export function unproject(sx, sy) {
  const a = sx / K;
  const b = (2 * sy) / K;
  return { x: (a + b) / 2, y: (b - a) / 2 };
}

/** Screen-space direction for a ground-plane velocity. */
export function screenDirection(vx, vy) {
  return { x: vx - vy, y: (vx + vy) / 2 };
}
