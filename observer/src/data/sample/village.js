// The SAMPLE village placed on a real engine tile.
//
// The O1a village is placed at the first civilization's day-0 capital tile.
// Its layout, buildings, citizens and routines remain invented SAMPLE data;
// only the tile it sits on comes from the engine. Sample objects that would
// fall outside that tile are dropped, so the village never claims
// neighbouring tiles. The SAMPLE courier's route is chosen from engine data:
// land all the way, never across a deep river, into a neighbouring chunk.

import { hexCentre, hexDistance } from '../../world/hex.js';
import { makeSchedule } from '../../sim/paths.js';
import { personLabel } from '../naming.js';
import { villageScene } from '../../proof/scene-village.js';

const DIRECTIONS = [
  [1, 0],
  [1, -1],
  [0, -1],
  [-1, 0],
  [-1, 1],
  [0, 1],
];

/**
 * A straight courier route from the capital: the first hex direction, and the
 * fewest steps (2 to 4), that stays on land, never crosses a deep river and
 * ends in another chunk. `landAt(q, r)` and `deepBetween(a, b)` read engine data.
 */
export function chooseCourierRoute(tile, { landAt, deepBetween, chunkTiles, width, height }) {
  const [q, r] = tile;
  const chunk = (cq, cr) => `${Math.floor(cq / chunkTiles)},${Math.floor(cr / chunkTiles)}`;
  for (let steps = 2; steps <= 4; steps += 1) {
    for (const [dq, dr] of DIRECTIONS) {
      let ok = true;
      for (let k = 1; k <= steps && ok; k += 1) {
        const [aq, ar, bq, br] = [q + dq * (k - 1), r + dr * (k - 1), q + dq * k, r + dr * k];
        ok = bq >= 0 && br >= 0 && bq < width && br < height && landAt(bq, br) && !deepBetween([aq, ar], [bq, br]);
      }
      if (ok && chunk(q + dq * steps, r + dr * steps) !== chunk(q, r)) return { dq, dr, steps };
    }
  }
  return { dq: 1, dr: 0, steps: 2 };
}

export function observerVillage(footprintOf, R, tile, route = { dq: 1, dr: 0, steps: 4 }) {
  const scene = villageScene(footprintOf);
  const [q, r] = tile;
  const origin = hexCentre(q, r, R);
  const inradius = (R * Math.sqrt(3)) / 2;
  const inside = (x, y, margin) => hexDistance(x, y) <= inradius - margin;
  const before = scene.statics.length;
  scene.statics = scene.statics.filter((s) => {
    const fp = footprintOf(s.asset);
    return [
      [fp.minX, fp.minY],
      [fp.maxX, fp.minY],
      [fp.minX, fp.maxY],
      [fp.maxX, fp.maxY],
    ].every(([dx, dy]) => inside(s.x + dx, s.y + dy, 1.5));
  });
  const dropped = before - scene.statics.length;

  // A SAMPLE courier who walks out of the village a few tiles (across a chunk
  // boundary) and back. Positions are local to the village.
  const [tq, tr] = [q + route.dq * route.steps, r + route.dr * route.steps];
  const far = hexCentre(tq, tr, R);
  const dest = [far.x - origin.x, far.y - origin.y];
  // Out along village roads (hall door, plaza, east lane), then cross-country.
  const out = [[6, -19.6], [6, -18.6], [10.4, -18.6], [10.4, 0], ...(dest[0] > 0 ? [[56, 0]] : []), dest];
  const id = 'sample-person-0900';
  scene.people.push({
    id,
    civ: 0,
    appearance: 4,
    schedule: makeSchedule(
      [
        { type: 'walk', path: out, speed: 1.6, anim: 'walk', activity: 'Carrying a message to a neighbouring tile', destination: `Tile (${tq}, ${tr})` },
        { type: 'work', at: dest, face: [dest[0] - 1, dest[1] + 1], anim: 'idle', period: 2, dur: 20, activity: 'Waiting for a reply' },
        { type: 'walk', path: out.slice().reverse(), speed: 1.6, anim: 'walk', activity: 'Returning to the village', destination: 'Village' },
        { type: 'inside', building: 'b-hall', at: [6, -19.6], dur: 30, activity: 'Reporting in the community hall' },
      ],
      0,
    ),
    record: {
      label: personLabel(id),
      sex: 'female',
      age: 27,
      role: 'Courier',
      skills: { travel: 64 },
      health: 'Healthy',
      household: 'Not recorded',
      events: ['Carries messages between tiles (sample)'],
    },
  });

  const alpha = (x, y) => Math.max(0, Math.min(1, (inradius - hexDistance(x, y)) / 6));
  const groundBounds = { x0: -R, y0: -R, x1: R, y1: R };
  return { scene, origin, tile, alpha, groundBounds, dropped, courierId: id };
}
