// SAMPLE population scaling (?citizens=N) for rendering measurements.
//
// Extra residents are clones of the village's sample routines with new
// stable IDs, shifted in time and a little in space. They prove nothing about
// what the simulation supports; they only load the renderer.

import { makeSchedule } from '../../sim/paths.js';
import { rngFor } from '../../sim/rng.js';
import { personLabel } from '../naming.js';
import { APPEARANCE_COUNT } from '../../render/art/paint/people.js';

export const MAX_SAMPLE_CITIZENS = 5000;

export function scaleCitizens(scene, target) {
  const n = Math.min(MAX_SAMPLE_CITIZENS, Math.max(0, Math.floor(target)));
  const templates = scene.people.filter((p) => !p.envoy && p.record.role !== 'Courier' && p.record.role !== 'Ambassador');
  const rng = rngFor(`sample-citizens:${n}`);
  let serial = 1000;
  while (scene.people.length < n && templates.length) {
    const tpl = templates[serial % templates.length];
    serial += 1;
    const id = `sample-person-${String(serial).padStart(5, '0')}`;
    const a = rng() * Math.PI * 2;
    const r = 0.25 * Math.sqrt(rng());
    const sched = makeSchedule(tpl.schedule.segs, rng() * tpl.schedule.period, [Math.cos(a) * r, Math.sin(a) * r]);
    const age = 16 + Math.floor(rng() * 50);
    const appearance = Math.floor(rng() * APPEARANCE_COUNT);
    scene.people.push({
      id,
      civ: tpl.civ,
      appearance,
      schedule: sched,
      record: {
        ...tpl.record,
        label: personLabel(id),
        sex: appearance % 2 === 1 ? 'female' : 'male',
        age,
        events: [`Born ${age} years before the sample date`, 'Added to load the renderer (sample clone)'],
      },
    });
  }
  return scene;
}
