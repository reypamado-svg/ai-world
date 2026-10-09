// Depth ordering for isometric sprites.
//
// Every drawable has a ground-plane footprint (axis-aligned box in metres).
// For two boxes that do not intersect, one is separated from the other on at
// least one ground axis, and the one with the larger coordinate on that axis
// is nearer the viewer. The asset contract (sprite width no wider than its
// projected footprint, plus tolerance) keeps the two axes from disagreeing
// for sprites that actually overlap on screen.
//
// Order: a topological sort over pairs whose screen rectangles overlap,
// breaking ties by footprint-centre depth (x + y) and then by id.

/** +1 if a is in front of b, -1 if behind, 0 if undetermined. */
export function relation(a, b) {
  const af = a.fp.minX >= b.fp.maxX || a.fp.minY >= b.fp.maxY;
  const bf = b.fp.minX >= a.fp.maxX || b.fp.minY >= a.fp.maxY;
  if (af && !bf) return 1;
  if (bf && !af) return -1;
  return 0;
}

function overlaps(a, b) {
  return a.rect.x0 < b.rect.x1 && b.rect.x0 < a.rect.x1 && a.rect.y0 < b.rect.y1 && b.rect.y0 < a.rect.y1;
}

function compareBase(a, b) {
  return a.depth - b.depth || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
}

class MinHeap {
  constructor() {
    this.a = [];
  }
  push(v) {
    const a = this.a;
    a.push(v);
    let i = a.length - 1;
    while (i > 0) {
      const p = (i - 1) >> 1;
      if (a[p] <= a[i]) break;
      [a[p], a[i]] = [a[i], a[p]];
      i = p;
    }
  }
  pop() {
    const a = this.a;
    const top = a[0];
    const last = a.pop();
    if (a.length) {
      a[0] = last;
      let i = 0;
      for (;;) {
        const l = i * 2 + 1;
        const r = l + 1;
        let m = i;
        if (l < a.length && a[l] < a[m]) m = l;
        if (r < a.length && a[r] < a[m]) m = r;
        if (m === i) break;
        [a[m], a[i]] = [a[i], a[m]];
        i = m;
      }
    }
    return top;
  }
  get size() {
    return this.a.length;
  }
}

/**
 * Sort items back to front. Items need: id, fp {minX,minY,maxX,maxY},
 * rect {x0,y0,x1,y1} (screen), depth. Returns { order, cycles }.
 * centreOnly is the naive centre-depth order, kept only as a negative control
 * for the visual tests.
 */
export function depthSort(items, cell = 128, { centreOnly = false } = {}) {
  const base = items.slice().sort(compareBase);
  const n = base.length;
  const grid = new Map();
  for (let i = 0; i < n; i += 1) {
    const r = base[i].rect;
    const cx0 = Math.floor(r.x0 / cell);
    const cx1 = Math.floor(r.x1 / cell);
    const cy0 = Math.floor(r.y0 / cell);
    const cy1 = Math.floor(r.y1 / cell);
    for (let cy = cy0; cy <= cy1; cy += 1) {
      for (let cx = cx0; cx <= cx1; cx += 1) {
        const k = cx * 73856093 + cy * 19349663;
        let b = grid.get(k);
        if (!b) grid.set(k, (b = []));
        b.push(i);
      }
    }
  }
  const after = Array.from({ length: n }, () => []);
  const indeg = new Int32Array(n);
  const seen = new Set();
  for (const bucket of grid.values()) {
    for (let p = 0; p < bucket.length; p += 1) {
      for (let q = p + 1; q < bucket.length; q += 1) {
        const i = bucket[p];
        const j = bucket[q];
        const lo = i < j ? i : j;
        const hi = i < j ? j : i;
        const a = base[lo];
        const b = base[hi];
        // Two citizens: centre depth already orders them; skipping the pair
        // (before any bookkeeping) keeps crowds cheap to sort.
        if (centreOnly || (a.small && b.small)) continue;
        const key = lo * n + hi;
        if (seen.has(key)) continue;
        seen.add(key);
        if (!overlaps(a, b)) continue;
        const rel = relation(a, b);
        if (rel === 1) {
          after[hi].push(lo); // b must be drawn before a
          indeg[lo] += 1;
        } else if (rel === -1) {
          after[lo].push(hi);
          indeg[hi] += 1;
        }
      }
    }
  }
  const heap = new MinHeap();
  for (let i = 0; i < n; i += 1) if (indeg[i] === 0) heap.push(i);
  const order = [];
  const done = new Uint8Array(n);
  while (heap.size) {
    const i = heap.pop();
    done[i] = 1;
    order.push(base[i]);
    for (const j of after[i]) {
      indeg[j] -= 1;
      if (indeg[j] === 0) heap.push(j);
    }
  }
  const cycles = [];
  if (order.length < n) {
    for (let i = 0; i < n; i += 1) {
      if (!done[i]) {
        order.push(base[i]);
        cycles.push(base[i].id);
      }
    }
  }
  return { order, cycles };
}
