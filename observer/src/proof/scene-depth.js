// Pinned depth-ordering test scene (?scene=depth). Fixed positions, paused
// clock. Each case lists which object must be drawn in front of which.

import { makeSchedule } from '../sim/paths.js';

function pinned(id, x, y, extra = {}) {
  const seg = extra.inside
    ? { type: 'inside', building: extra.inside, at: [x, y], dur: 100, activity: 'Inside (pinned)' }
    : {
        type: 'work',
        at: [x, y],
        face: [x - 1, y + 1],
        anim: 'idle',
        period: 1000,
        dur: 100,
        activity: 'Pinned for depth test',
      };
  return {
    id,
    civ: 0,
    appearance: extra.appearance ?? 0,
    schedule: makeSchedule([seg], 0),
    record: {
      label: id,
      sex: 'male',
      age: 30,
      role: 'Test figure',
      skills: {},
      health: 'Healthy',
      household: 'Not recorded',
      events: [],
    },
  };
}

export function depthScene(assetFootprint) {
  const statics = [];
  const people = [];
  const cases = [];
  const S = 26;
  let n = 0;
  const at = () => {
    const col = n % 4;
    const row = Math.floor(n / 4);
    n += 1;
    return [col * S - 39, row * S - 26];
  };
  const add = (id, asset, x, y, kind = 'building') => statics.push({ id, asset, x, y, kind, label: id });

  let [cx, cy] = at();
  add('d1-house', 'building.house.timber_a', cx, cy);
  people.push(pinned('d1-p', cx - 3.55, cy - 0.6));
  cases.push({ name: 'citizen behind a back corner', front: 'd1-house', back: 'd1-p' });

  [cx, cy] = at();
  add('d2-house', 'building.house.timber_a', cx, cy);
  people.push(pinned('d2-p', cx + 0.6, cy + 3.1));
  cases.push({ name: 'citizen in front of a face', front: 'd2-p', back: 'd2-house' });

  [cx, cy] = at();
  add('d3-house', 'building.house.timber_a', cx, cy);
  people.push(pinned('d3-p', cx, cy + 2.9));
  cases.push({ name: 'citizen on a doorstep', front: 'd3-p', back: 'd3-house' });

  [cx, cy] = at();
  add('d4-house', 'building.house.timber_a', cx, cy);
  people.push(pinned('d4-p', cx, cy + 2.9, { inside: 'd4-house' }));
  cases.push({ name: 'citizen gone through a door is not drawn', hidden: 'd4-p', building: 'd4-house' });

  [cx, cy] = at();
  add('d5-back', 'building.house.timber_a', cx, cy);
  add('d5-front', 'building.house.timber_a', cx + 0.5, cy + 6.6);
  people.push(pinned('d5-p', cx + 0.2, cy + 3.3));
  cases.push({ name: 'citizen between two buildings (front)', front: 'd5-p', back: 'd5-back' });
  cases.push({ name: 'citizen between two buildings (behind)', front: 'd5-front', back: 'd5-p' });

  [cx, cy] = at();
  add('d6-store', 'building.storehouse', cx, cy);
  people.push(pinned('d6-p', cx + 4.6, cy - 3.95));
  cases.push({ name: 'citizen behind a long storehouse (centre depth would fail)', front: 'd6-store', back: 'd6-p' });

  [cx, cy] = at();
  add('d7-main', 'building.workshop.main', cx, cy);
  add('d7-wing', 'building.workshop.wing', cx - 1.75, cy + 4.5);
  people.push(pinned('d7-notch', cx + 1.9, cy + 3.6));
  people.push(pinned('d7-behind', cx - 4.0, cy + 4.6));
  cases.push({
    name: 'L-shape: citizen in the notch is in front of the main part',
    front: 'd7-notch',
    back: 'd7-main',
  });
  cases.push({ name: 'L-shape: citizen behind the wing', front: 'd7-wing', back: 'd7-behind' });

  [cx, cy] = at();
  add('d8-tree', 'nature.oak.2', cx, cy, 'tree');
  add('d8-fence', 'prop.fence.x', cx + 0.3, cy + 4.2, 'fence');
  const tf = assetFootprint('nature.oak.2');
  people.push(pinned('d8-ptree', cx + tf.minX - 0.3, cy - 0.3));
  people.push(pinned('d8-pfence', cx + 0.4, cy + 3.75));
  cases.push({ name: 'tree over a citizen behind it', front: 'd8-tree', back: 'd8-ptree' });
  cases.push({ name: 'fence over a citizen behind it', front: 'd8-fence', back: 'd8-pfence' });

  [cx, cy] = at();
  add('d9-a', 'building.house.timber_a', cx - 6.6, cy);
  add('d9-b', 'building.house.timber_a', cx, cy);
  add('d9-c', 'building.house.timber_a', cx + 6.6, cy);
  cases.push({ name: 'row of buildings (left/middle)', front: 'd9-b', back: 'd9-a' });
  cases.push({ name: 'row of buildings (middle/right)', front: 'd9-c', back: 'd9-b' });

  [cx, cy] = at();
  people.push(pinned('d10-p', cx + 0.3, cy - 1.05));
  const wagon = { id: 'd10-wagon', x: cx, y: cy };
  cases.push({ name: 'wagon over a citizen behind it', front: 'd10-wagon', back: 'd10-p' });

  return {
    bounds: { x0: -56, y0: -46, x1: 56, y1: 46 },
    statics,
    ground: { roads: [], worn: [], fields: [] },
    people,
    caravan: null,
    pinnedWagon: wagon,
    cases,
    focus: { x: 0, y: 0 },
  };
}
