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
import { planJourney } from '../../sim/travel-plan.js';
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
  // No route leaves the chunk: settle for the nearest checked neighbour, or none.
  for (const [dq, dr] of DIRECTIONS) {
    const [bq, br] = [q + dq, r + dr];
    if (bq >= 0 && br >= 0 && bq < width && br < height && landAt(bq, br) && !deepBetween([q, r], [bq, br]))
      return { dq, dr, steps: 1 };
  }
  return null;
}

/** The SAMPLE village's own size: it was laid out to fill a 64 m hex, whatever the tile size. */
export const VILLAGE_RADIUS_M = 64;

export function observerVillage(footprintOf, R, tile, route = null, travel = null) {
  const scene = villageScene(footprintOf);
  const [q, r] = tile;
  // The village stands at the centre of its 25 km tile (a presentation choice).
  const origin = hexCentre(q, r, R);
  const inradius = (VILLAGE_RADIUS_M * Math.sqrt(3)) / 2;
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
  // boundary), waits a day for a reply and walks back. Positions are local to
  // the village. Across country they keep the engine's pace: each tile takes its
  // entry cost in days, each day 5 hours on foot and then a camp (a visual
  // approximation). With no checked route out (water or deep rivers all round),
  // they stay in the village.
  const [tq, tr] = route ? [q + route.dq * route.steps, r + route.dr * route.steps] : [q, r];
  const id = 'sample-person-0900';
  const roads = [[6, -19.6], [6, -18.6], [10.4, -18.6], [10.4, 0]];
  const local = (cq, cr) => {
    const c = hexCentre(cq, cr, R);
    return [c.x - origin.x, c.y - origin.y];
  };
  let segments;
  let plan = null;
  if (route) {
    // Out along village roads (hall door, plaza, east lane), then cross-country.
    const exit = local(q + route.dq, r + route.dr)[0] > 0 ? [56, 0] : roads[roads.length - 1];
    const path = [...roads, ...(exit === roads[roads.length - 1] ? [] : [exit])];
    const tiles = Array.from({ length: route.steps + 1 }, (_, k) => {
      const [cq, cr] = [q + route.dq * k, r + route.dr * k];
      return { q: cq, r: cr, centre: local(cq, cr) };
    });
    const rules = travel?.rules ?? { day_tenths: 10, entry_cost_tenths: {}, crossing_cost_tenths: {}, stream_flow: 4, deep_flow: 10 };
    const terrainAt = travel?.terrainAt ?? (() => 'grassland');
    const flowBetween = travel?.flowBetween ?? (() => 0);
    const grass = { ...rules, entry_cost_tenths: { grassland: 10, ...rules.entry_cost_tenths } };
    const outbound = planJourney({ start: exit, tiles, terrainAt, flowBetween, rules: grass });
    const back = [...tiles].reverse();
    back[back.length - 1] = { ...back[back.length - 1], centre: exit };
    const inbound = planJourney({ start: tiles[tiles.length - 1].centre, tiles: back, terrainAt, flowBetween, rules: grass });
    const dest = tiles[tiles.length - 1].centre;
    const toExit = { type: 'walk', path, speed: 1.6, anim: 'walk', activity: 'Carrying a message to a neighbouring tile', destination: `Tile (${tq}, ${tr})` };
    const wait = { type: 'work', at: dest, face: [dest[0] - 1, dest[1] + 1], anim: 'idle', period: 2, dur: 86400, activity: 'Waiting a day for a reply' };
    const home = { type: 'walk', path: path.slice().reverse(), speed: 1.6, anim: 'walk', activity: 'Returning to the village', destination: 'Village' };
    const report = { type: 'inside', building: 'b-hall', at: [6, -19.6], dur: 3600, activity: 'Reporting in the community hall' };
    segments = [toExit, ...outbound.segments, wait, ...inbound.segments, home, report];
    // Times along the whole schedule, for the inspector and tests.
    const roadsT = path.reduce((sum, p, i) => (i ? sum + Math.hypot(p[0] - path[i - 1][0], p[1] - path[i - 1][1]) : 0), 0) / 1.6;
    plan = {
      legs: outbound.legs.map((leg) => ({ ...leg, arriveT: roadsT + leg.arriveT })),
      camps: outbound.camps.map((c) => ({ ...c, t0: roadsT + c.t0 })),
      fords: outbound.fords.map((f) => ({ ...f, t0: roadsT + f.t0 })),
      departHomeT: roadsT + outbound.duration + 86400,
    };
  } else {
    const dest = [30, 0];
    segments = [
      { type: 'walk', path: [...roads, dest], speed: 1.6, anim: 'walk', activity: 'Carrying a message within the village', destination: 'Village' },
      { type: 'work', at: dest, face: [dest[0] - 1, dest[1] + 1], anim: 'idle', period: 2, dur: 20, activity: 'Waiting for a reply' },
      { type: 'walk', path: [dest, ...roads.slice().reverse()], speed: 1.6, anim: 'walk', activity: 'Returning to the village', destination: 'Village' },
      { type: 'inside', building: 'b-hall', at: [6, -19.6], dur: 30, activity: 'Reporting in the community hall' },
    ];
  }
  scene.people.push({
    id,
    civ: 0,
    appearance: 4,
    schedule: makeSchedule(segments, 0),
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
  const ground = VILLAGE_RADIUS_M;
  const groundBounds = { x0: -ground, y0: -ground, x1: ground, y1: ground };
  // SAMPLE fields in a ring around the village, about 800 m across (presentation).
  const fieldRing = { x: origin.x, y: origin.y, r0: 60, r1: 400 };
  return {
    scene,
    origin,
    tile,
    alpha,
    groundBounds,
    dropped,
    fieldRing,
    courierId: id,
    courierTile: [tq, tr],
    courierPlan: plan,
  };
}
