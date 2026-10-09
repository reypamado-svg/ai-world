// River geometry in ground-plane metres (presentation), shared by every painter.
//
// The engine records a river as a border between two tiles with a flow. The
// observer draws it as a channel whose width grows with flow and which wanders
// gently about the border line. The wander is an analytic curve seeded by the
// border, so every zoom level samples the same curve at its own resolution and
// the two tiles on either side agree exactly.

import { sharedCorners } from './hex.js';
import { hashString, mulberry32 } from '../sim/rng.js';

/**
 * Channel width in metres (presentation): streams 40-60 m, fordable rivers
 * 80-140 m, deep rivers 150-400 m. Never narrower downstream.
 */
export function riverWidthM(flow, { streamFlow, deepFlow }) {
  if (flow < streamFlow) return 30 + 10 * flow;
  if (flow < deepFlow) return 80 + 12 * (flow - streamFlow);
  return 150 + 250 * Math.min(1, Math.log2(flow / deepFlow) / Math.log2(11));
}

const shapes = new Map();

/** Amplitude and phase of a border's wander, seeded by its key. */
function shapeOf(key) {
  let shape = shapes.get(key);
  if (!shape) {
    const rng = mulberry32(hashString(`river:${key}`));
    const amp = (0.05 + rng() * 0.05) * (rng() < 0.5 ? -1 : 1);
    shape = { amp, phase: rng() * Math.PI * 2 };
    if (shapes.size > 20000) shapes.clear();
    shapes.set(key, shape);
  }
  return shape;
}

/** Ground-plane point at fraction t (0..1) along a river border. */
export function riverPoint(edge, t) {
  const { p1, p2 } = edge;
  const { amp, phase } = shapeOf(edge.key);
  const dx = p2.x - p1.x;
  const dy = p2.y - p1.y;
  // The normal (-dy, dx) has the border's length, so offsets scale with the tile.
  const off =
    amp * Math.sin(t * Math.PI) +
    0.35 * amp * Math.sin(t * Math.PI * 3) +
    0.012 * Math.sin(t * Math.PI * 7 + phase) * Math.sin(t * Math.PI);
  return { x: p1.x + dx * t - dy * off, y: p1.y + dy * t + dx * off };
}

/** The river as n + 1 ground-plane points from corner to corner. */
export function riverLine(edge, n) {
  const out = [];
  for (let i = 0; i <= n; i += 1) out.push(riverPoint(edge, i / n));
  return out;
}

/**
 * The river borders of one tile, as ground-plane corner pairs. Each border's
 * corners are taken from its first tile as exported, so both sides agree.
 */
export function riverEdgesOf(q, r, rivers, R, streamFlow) {
  return (rivers?.byTile.get(`${q},${r}`) ?? []).map((edge) => {
    const [p1, p2] = sharedCorners(edge.aq, edge.ar, edge.bq, edge.br, R);
    return {
      p1,
      p2,
      flow: edge.flow,
      deep: edge.flow >= rivers.deepFlow,
      widthM: riverWidthM(edge.flow, { streamFlow, deepFlow: rivers.deepFlow }),
      key: `${edge.aq},${edge.ar},${edge.bq},${edge.br}`,
    };
  });
}
