// A deterministic SYNTHETIC population for the observer (Phase 5 S7).
//
// It stands in for a recorded run until the O2 reader serves real people in
// the same columns. Everyone lives at their civilization's capital, shared out
// equally, five to a house; ages follow a simple pre-modern pyramid and the
// grown take duties in fixed shares. The same total always gives the same
// people.

import { mulberry32, hashString } from '../../sim/rng.js';
import { DUTY, PeopleFrame } from '../population.js';
import { settlementLabel } from '../naming.js';

export const MAX_PEOPLE = 200000;
export const HOUSEHOLD = 5;
const MEAN_AGE = 30;
const GROWN = 16;
const ELDER = 65;

/** Shares of the grown, in this order (sum 100). */
const WORK = [
  ['farmer', 50],
  ['carrier', 10],
  ['builder', 10],
  ['woodcutter', 6],
  ['crafter', 12],
  ['scholar', 4],
  ['guard', 8],
];

/**
 * @param {number} total people (clamped to 0 - MAX_PEOPLE)
 * @param {Array<{ settlement_id: string, tile: [number, number] }>} capitals one per civilization
 */
export function syntheticPopulation(total, capitals) {
  const n = Math.max(0, Math.min(MAX_PEOPLE, Math.floor(total)));
  const settlements = capitals.map((c, civ) => ({
    id: c.settlement_id,
    civ,
    q: c.tile[0],
    r: c.tile[1],
    label: settlementLabel(c.settlement_id),
  }));
  const frame = new PeopleFrame(n, settlements);
  const share = Math.floor(n / settlements.length);
  const rng = mulberry32(hashString(`synthetic-people:${n}:${settlements.length}`));
  let row = 0;
  settlements.forEach((s, si) => {
    const here = share + (si < n - share * settlements.length ? 1 : 0);
    for (let k = 0; k < here; k += 1, row += 1) {
      frame.id[row] = row + 1;
      frame.civ[row] = s.civ;
      frame.q[row] = s.q;
      frame.r[row] = s.r;
      frame.settlement[row] = si;
      const female = rng() < 0.5 ? 1 : 0;
      frame.sex[row] = female;
      const age = Math.min(85, Math.floor(-Math.log(1 - rng()) * MEAN_AGE));
      frame.age[row] = age;
      frame.health[row] = rng() < 0.9 ? 80 + Math.floor(rng() * 21) : 40 + Math.floor(rng() * 40);
      frame.appearance[row] = 2 * Math.floor(rng() * 5) + female;
      frame.house[row] = Math.floor(k / HOUSEHOLD);
      let duty = DUTY.child;
      if (age >= ELDER) duty = DUTY.elder;
      else if (age >= GROWN) {
        let u = rng() * 100;
        for (const [name, w] of WORK) {
          duty = DUTY[name];
          if ((u -= w) < 0) break;
        }
      }
      frame.duty[row] = duty;
    }
  });
  return frame.index();
}
