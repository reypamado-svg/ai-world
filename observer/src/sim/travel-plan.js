// A traveller's multi-day walk across engine tiles (VISUAL APPROXIMATION).
//
// The engine moves parties tile by tile in whole days: entering a tile costs
// its terrain's entry cost (tenths of a day), and crossing a river along the
// border adds its crossing cost; a deep river cannot be crossed at all. The
// engine records nothing finer, so the observer spreads each day as 5 hours
// of walking and then a camp for the rest of the day, walks at whatever pace
// covers the tile in its cost, and wades a ford at the border. Pure: a
// function of its inputs only.

export const WALK_SECONDS_PER_DAY = 5 * 3600;
export const DAY_SECONDS = 24 * 3600;

const dist = (a, b) => Math.hypot(b[0] - a[0], b[1] - a[1]);
const lerp = (a, b, k) => [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k];

/** How deep a river of this flow is, by the engine's thresholds. */
export function depthOf(flow, rules) {
  if (flow < rules.stream_flow) return 'stream';
  if (flow < rules.deep_flow) return 'river';
  return 'deep';
}

/**
 * Plan a walk from `start` (a local plane point inside the first tile) through
 * the centres of `tiles[1..]`, where tiles[i] = { q, r, centre: [x, y] }.
 *   terrainAt(q, r) -> terrain name; flowBetween(a, b) -> river flow or 0;
 *   rules = manifest.engine.travel.
 * Returns { segments, legs: [{ q, r, arriveT }], camps, fords, duration }.
 * Segment times are relative to the start of the walk.
 */
export function planJourney({
  start,
  tiles,
  terrainAt,
  flowBetween,
  rules,
  walkSecondsPerDay = WALK_SECONDS_PER_DAY,
  daySeconds = DAY_SECONDS,
}) {
  // First the active pieces: walking legs and wading at fords, with their durations.
  const pieces = [];
  const legs = [];
  let from = start;
  for (let i = 1; i < tiles.length; i += 1) {
    const prev = tiles[i - 1];
    const tile = tiles[i];
    const entry = rules.entry_cost_tenths[terrainAt(tile.q, tile.r)];
    if (entry === null || entry === undefined) throw new Error(`tile ${tile.q},${tile.r} cannot be entered on foot`);
    const flow = flowBetween(prev, tile);
    let wade = 0;
    if (flow > 0) {
      const crossing = rules.crossing_cost_tenths[depthOf(flow, rules)];
      if (crossing === null || crossing === undefined)
        throw new Error(`a deep river lies between ${prev.q},${prev.r} and ${tile.q},${tile.r}`);
      wade = (crossing / rules.day_tenths) * walkSecondsPerDay;
    }
    const walkTime = (entry / rules.day_tenths) * walkSecondsPerDay;
    const border = lerp(prev.centre, tile.centre, 0.5);
    const total = dist(from, border) + dist(border, tile.centre);
    const firstHalf = total ? (walkTime * dist(from, border)) / total : 0;
    pieces.push({ kind: 'walk', a: from, b: border, dur: firstHalf });
    if (wade) pieces.push({ kind: 'wade', at: border, dur: wade, depth: depthOf(flow, rules) });
    pieces.push({ kind: 'walk', a: border, b: tile.centre, dur: walkTime - firstHalf, arrive: tile });
    from = tile.centre;
  }
  // Then lay the pieces into days: every day's walking budget is followed by a camp.
  const segments = [];
  const camps = [];
  const fords = [];
  let clock = 0;
  let budget = walkSecondsPerDay;
  let day = 1;
  const camp = (at) => {
    camps.push({ t0: clock, at, day });
    segments.push({
      type: 'work',
      at,
      face: [at[0], at[1] + 1],
      anim: 'idle',
      period: 3,
      dur: daySeconds - walkSecondsPerDay,
      activity: `Camped for the night (day ${day} of the journey)`,
    });
    clock += daySeconds - walkSecondsPerDay;
    day += 1;
    budget = walkSecondsPerDay;
  };
  for (const piece of pieces) {
    let left = piece.dur;
    let a = piece.kind === 'walk' ? piece.a : piece.at;
    while (left > 1e-6) {
      if (budget <= 1e-6) camp(a);
      const take = Math.min(left, budget);
      if (piece.kind === 'walk') {
        const b = lerp(a, piece.b, take / left);
        const length = dist(a, b);
        if (length > 1e-6)
          segments.push({
            type: 'walk',
            path: [a, b],
            speed: length / take,
            anim: 'walk',
            activity: 'Walking to the next tile',
            destination: piece.arrive ? `Tile (${piece.arrive.q}, ${piece.arrive.r})` : null,
          });
        else segments.push({ type: 'work', at: a, anim: 'idle', period: 3, dur: take, activity: 'Resting' });
        a = b;
      } else {
        if (left === piece.dur) fords.push({ t0: clock, at: piece.at, depth: piece.depth });
        segments.push({
          type: 'work',
          at: a,
          face: [a[0] + 1, a[1]],
          anim: 'walk',
          period: 1.2,
          dur: take,
          activity: `Wading the ${piece.depth} (ford)`,
        });
      }
      clock += take;
      budget -= take;
      left -= take;
    }
    if (piece.arrive) legs.push({ q: piece.arrive.q, r: piece.arrive.r, arriveT: clock });
  }
  return { segments, legs, camps, fords, duration: clock };
}
