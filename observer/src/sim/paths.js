// Presentation paths: a VISUAL APPROXIMATION of movement.
//
// The engine records whole tiles and whole days. Street-level movement shown
// here is a deterministic presentation derived from a schedule. It is a pure
// function of (person, display time): the camera, zoom, selection and
// following never change it, and it never changes anything authoritative.

import { screenDirection } from '../world/coords.js';

const key = (p) => `${Math.round(p[0] * 100)},${Math.round(p[1] * 100)}`;

export class WalkGraph {
  constructor() {
    this.nodes = new Map();
  }

  _node(p) {
    const k = key(p);
    if (!this.nodes.has(k)) this.nodes.set(k, { p: [p[0], p[1]], edges: [] });
    return this.nodes.get(k);
  }

  addPolyline(points) {
    for (let i = 0; i + 1 < points.length; i += 1) {
      const a = this._node(points[i]);
      const b = this._node(points[i + 1]);
      const d = Math.hypot(a.p[0] - b.p[0], a.p[1] - b.p[1]);
      a.edges.push([b, d]);
      b.edges.push([a, d]);
    }
  }

  /** Shortest path between two graph points (Dijkstra; graphs are small). */
  route(from, to) {
    const start = this.nodes.get(key(from));
    const goal = this.nodes.get(key(to));
    if (!start || !goal) throw new Error(`route endpoints not on the walk graph: ${key(from)} -> ${key(to)}`);
    const dist = new Map([[start, 0]]);
    const prev = new Map();
    const open = new Set([start]);
    while (open.size) {
      let best = null;
      for (const n of open) if (!best || dist.get(n) < dist.get(best)) best = n;
      open.delete(best);
      if (best === goal) break;
      for (const [m, d] of best.edges) {
        const nd = dist.get(best) + d;
        if (nd < (dist.get(m) ?? Infinity)) {
          dist.set(m, nd);
          prev.set(m, best);
          open.add(m);
        }
      }
    }
    const out = [];
    for (let n = goal; n; n = prev.get(n)) out.push(n.p);
    return out.reverse();
  }
}

/** Shift interior vertices sideways by `o` metres (mitred); ends stay put. */
export function offsetPolyline(points, o) {
  if (!o || points.length < 3) return points.map((p) => [p[0], p[1]]);
  const out = [[points[0][0], points[0][1]]];
  for (let i = 1; i < points.length - 1; i += 1) {
    const a = points[i - 1];
    const b = points[i];
    const c = points[i + 1];
    const n1 = normal(a, b);
    const n2 = normal(b, c);
    const dot = n1[0] * n2[0] + n1[1] * n2[1];
    const k = o / Math.max(0.3, 1 + dot);
    out.push([b[0] + (n1[0] + n2[0]) * k, b[1] + (n1[1] + n2[1]) * k]);
  }
  out.push([points[points.length - 1][0], points[points.length - 1][1]]);
  return out;
}

function normal(a, b) {
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  const l = Math.hypot(dx, dy) || 1;
  return [-dy / l, dx / l];
}

export function pathLength(points) {
  let s = 0;
  for (let i = 0; i + 1 < points.length; i += 1)
    s += Math.hypot(points[i + 1][0] - points[i][0], points[i + 1][1] - points[i][1]);
  return s;
}

function pointAlong(points, d) {
  for (let i = 0; i + 1 < points.length; i += 1) {
    const a = points[i];
    const b = points[i + 1];
    const l = Math.hypot(b[0] - a[0], b[1] - a[1]);
    if (d <= l || i + 2 === points.length) {
      const t = l ? Math.min(1, d / l) : 0;
      return {
        x: a[0] + (b[0] - a[0]) * t,
        y: a[1] + (b[1] - a[1]) * t,
        vx: (b[0] - a[0]) / (l || 1),
        vy: (b[1] - a[1]) / (l || 1),
      };
    }
    d -= l;
  }
  const p = points[points.length - 1];
  return { x: p[0], y: p[1], vx: 0, vy: 1 };
}

/** Facing for a ground direction: painted facing plus horizontal mirror. */
export function facingFor(vx, vy) {
  const s = screenDirection(vx, vy);
  return { facing: s.y >= 0 ? 'front' : 'back', flip: s.x > 0 };
}

/**
 * Schedule segments:
 *   { type: 'walk', path, speed, anim, activity, destination }
 *   { type: 'work', at, face: [x,y], anim, period, dur, activity }
 *   { type: 'inside', building, at, dur, activity }
 * Returns a cyclic schedule; sample() is pure in t.
 */
export function makeSchedule(segments, offset = 0) {
  let t = 0;
  const segs = segments.map((s) => {
    const dur = s.type === 'walk' ? pathLength(s.path) / s.speed : s.dur;
    const out = { ...s, t0: t, dur };
    t += dur;
    return out;
  });
  return { segs, period: t, offset };
}

export const STRIDE_M = 1.35;

export function sampleSchedule(sched, t) {
  const local = (((t + sched.offset) % sched.period) + sched.period) % sched.period;
  let seg = sched.segs[sched.segs.length - 1];
  for (const s of sched.segs) {
    if (local < s.t0 + s.dur) {
      seg = s;
      break;
    }
  }
  const u = local - seg.t0;
  if (seg.type === 'walk') {
    const d = u * seg.speed;
    const p = pointAlong(seg.path, d);
    const f = facingFor(p.vx, p.vy);
    return {
      x: p.x,
      y: p.y,
      inside: null,
      anim: seg.anim ?? 'walk',
      phase: (d / STRIDE_M) % 1,
      facing: f.facing,
      flip: f.flip,
      activity: seg.activity,
      destination: seg.destination ?? null,
      moving: true,
    };
  }
  if (seg.type === 'work') {
    const dx = seg.face ? seg.face[0] - seg.at[0] : 0;
    const dy = seg.face ? seg.face[1] - seg.at[1] : 1;
    const f = facingFor(dx, dy);
    let x = seg.at[0];
    let y = seg.at[1];
    if (seg.drift) {
      const k = u / seg.dur;
      x += seg.drift[0] * k;
      y += seg.drift[1] * k;
    }
    return {
      x,
      y,
      inside: null,
      anim: seg.anim,
      phase: (u / (seg.period ?? 1.2)) % 1,
      facing: seg.anim === 'idle' || seg.anim === 'talk' ? f.facing : 'front',
      flip: f.flip,
      activity: seg.activity,
      destination: null,
      moving: false,
    };
  }
  return {
    x: seg.at[0],
    y: seg.at[1],
    inside: seg.building,
    anim: 'idle',
    phase: 0,
    facing: 'front',
    flip: false,
    activity: seg.activity,
    destination: null,
    moving: false,
  };
}
