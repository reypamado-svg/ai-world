// The SAMPLE village placed on a real engine tile.
//
// The O1a village is placed at the first civilization's day-0 capital tile.
// Its layout, buildings, citizens and routines remain invented SAMPLE data;
// only the tile it sits on comes from the engine. Sample objects that would
// fall outside that tile are dropped, so the village never claims
// neighbouring tiles (for example the lake next door). There is no river on
// the capital tile, so there is no bridge.

import { hexCentre, hexDistance } from '../../world/hex.js';
import { makeSchedule } from '../../sim/paths.js';
import { personLabel } from '../naming.js';
import { villageScene } from '../../proof/scene-village.js';

export function observerVillage(footprintOf, R, tile) {
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

  // A SAMPLE courier who walks out of the village, east across four tiles
  // (and a chunk boundary), and back. Positions are local to the village.
  const far = hexCentre(q + 4, r, R);
  const dest = [far.x - origin.x, far.y - origin.y];
  // Out along village roads (hall door, plaza, east lane, main road), then cross-country.
  const out = [[6, -19.6], [6, -18.6], [10.4, -18.6], [10.4, 0], [56, 0], dest];
  const id = 'sample-person-0900';
  scene.people.push({
    id,
    civ: 0,
    appearance: 4,
    schedule: makeSchedule(
      [
        { type: 'walk', path: out, speed: 1.6, anim: 'walk', activity: 'Carrying a message to a neighbouring tile', destination: `Tile (${q + 4}, ${r})` },
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
