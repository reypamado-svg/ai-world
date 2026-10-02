// Detailed painting of one engine tile (PROTOTYPE ARTWORK).
//
// Everything drawn comes from the tile's own engine values: terrain type,
// elevation, temperature (snow on cold peaks), timber (tree density), stone
// (rock outcrops) and the river borders the engine records. Decoration is
// seeded per tile, so the same tile always looks the same, and nothing implies
// a resource the tile does not have.

import { project } from '../world/coords.js';
import { hexCentre, hexCorners } from '../world/hex.js';
import { riverLine } from '../world/rivers.js';
import { hash2, hashString, mulberry32 } from '../sim/rng.js';
import { css, jitter, mix, rgbToCss } from './art/paint/color.js';

const K = 16;

const BASE = {
  0: '#3a6c9a',
  1: '#7d9a46',
  2: '#4f6b34',
  3: '#8a8072',
  4: '#cfb47a',
  5: '#b9bfa8',
  6: '#958d5c',
  7: '#e6eaee',
};

// Painters take `P(x, y)`: a ground-plane point projected relative to the
// texture's anchor, so canvas coordinates stay small wherever the tile is.

function hexPath(ctx, P, q, r, R, grow = 1) {
  const c = hexCentre(q, r, R);
  const pts = hexCorners(q, r, R).map((p) => P(c.x + (p.x - c.x) * grow, c.y + (p.y - c.y) * grow));
  ctx.beginPath();
  pts.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
  ctx.closePath();
}

/** A random ground-plane point inside the tile, at most `frac` of the radius out. */
function pointIn(P, rng, q, r, R, frac = 0.82) {
  const c = hexCentre(q, r, R);
  const a = rng() * Math.PI * 2;
  const d = Math.sqrt(rng()) * R * frac * 0.86;
  return P(c.x + Math.cos(a) * d, c.y + Math.sin(a) * d);
}

function tree(ctx, p, size, rng, pine) {
  if (pine) {
    ctx.fillStyle = '#4a3424';
    ctx.fillRect(p.x - size * 0.08, p.y - size * 0.5, size * 0.16, size * 0.5);
    const dark = jitter('#2c4630', rng, 0.08);
    for (let t = 0; t < 3; t += 1) {
      const yb = p.y - size * (0.3 + t * 0.55);
      const w = size * (0.75 - t * 0.18);
      ctx.fillStyle = css(dark, 1 + t * 0.08);
      ctx.beginPath();
      ctx.moveTo(p.x - w, yb);
      ctx.lineTo(p.x, yb - size * 0.9);
      ctx.lineTo(p.x + w, yb);
      ctx.closePath();
      ctx.fill();
    }
    return;
  }
  ctx.fillStyle = 'rgba(20,24,30,0.3)';
  ctx.beginPath();
  ctx.ellipse(p.x + size * 0.5, p.y, size * 0.9, size * 0.35, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = '#4a3424';
  ctx.fillRect(p.x - size * 0.09, p.y - size * 0.9, size * 0.18, size * 0.9);
  const col = jitter('#3d6630', rng, 0.1);
  ctx.fillStyle = css(col, 0.7);
  ctx.beginPath();
  ctx.ellipse(p.x, p.y - size * 1.25, size * 0.85, size * 0.72, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = css(col, 1.05);
  ctx.beginPath();
  ctx.ellipse(p.x - size * 0.18, p.y - size * 1.38, size * 0.6, size * 0.5, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = css(col, 1.35);
  ctx.beginPath();
  ctx.ellipse(p.x - size * 0.32, p.y - size * 1.5, size * 0.28, size * 0.22, 0, 0, Math.PI * 2);
  ctx.fill();
}

function rock(ctx, p, size, rng) {
  const base = jitter('#8e8a80', rng, 0.12);
  ctx.fillStyle = css(base, 0.7);
  ctx.beginPath();
  ctx.ellipse(p.x, p.y - size * 0.25, size, size * 0.6, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = css(base, 1.15);
  ctx.beginPath();
  ctx.ellipse(p.x - size * 0.25, p.y - size * 0.45, size * 0.6, size * 0.35, 0, 0, Math.PI * 2);
  ctx.fill();
}

function peak(ctx, p, size, snow) {
  ctx.fillStyle = '#a0988a';
  ctx.beginPath();
  ctx.moveTo(p.x - size, p.y);
  ctx.lineTo(p.x - size * 0.1, p.y - size * 1.15);
  ctx.lineTo(p.x + size * 0.15, p.y);
  ctx.closePath();
  ctx.fill();
  ctx.fillStyle = '#6c6558';
  ctx.beginPath();
  ctx.moveTo(p.x - size * 0.1, p.y - size * 1.15);
  ctx.lineTo(p.x + size, p.y);
  ctx.lineTo(p.x + size * 0.15, p.y);
  ctx.closePath();
  ctx.fill();
  if (snow) {
    ctx.fillStyle = '#f0f2f4';
    ctx.beginPath();
    ctx.moveTo(p.x - size * 0.42, p.y - size * 0.7);
    ctx.lineTo(p.x - size * 0.1, p.y - size * 1.15);
    ctx.lineTo(p.x + size * 0.32, p.y - size * 0.72);
    ctx.lineTo(p.x - size * 0.05, p.y - size * 0.82);
    ctx.closePath();
    ctx.fill();
  }
}

const patterns = new Map();

/** Tileable value noise on a period-P lattice. */
function periodicNoise(x, y, P, seed) {
  const ix = Math.floor(x);
  const iy = Math.floor(y);
  const fx = x - ix;
  const fy = y - iy;
  const sx = fx * fx * (3 - 2 * fx);
  const sy = fy * fy * (3 - 2 * fy);
  const h = (a, b) => hash2(((a % P) + P) % P, ((b % P) + P) % P, seed);
  const a = h(ix, iy);
  const b = h(ix + 1, iy);
  const c = h(ix, iy + 1);
  const d = h(ix + 1, iy + 1);
  return a + (b - a) * sx + (c - a) * sy + (a - b - c + d) * sx * sy;
}

/**
 * A 256 px tileable ground texture per terrain, in the same style as the
 * village ground (mottled colour plus upright blades). Used for close views.
 */
function groundPattern(terrain) {
  if (patterns.has(terrain)) return patterns.get(terrain);
  const N = 256;
  const c = document.createElement('canvas');
  c.width = N;
  c.height = N;
  const ctx = c.getContext('2d');
  const img = ctx.createImageData(N, N);
  const palettes = {
    0: ['#2f5f8c', '#4a80ae'],
    1: ['#4f6a2c', '#86a24a'],
    2: ['#3a5228', '#5f7a3a'],
    3: ['#7a7166', '#a0978a'],
    4: ['#bfa36c', '#dcc48e'],
    5: ['#a7ad98', '#cfd4c4'],
    6: ['#7f7850', '#aaa274'],
    7: ['#d3dae2', '#f6f8fa'],
  };
  const [lo, hi] = palettes[terrain] ?? palettes[1];
  const a = [parseInt(lo.slice(1, 3), 16), parseInt(lo.slice(3, 5), 16), parseInt(lo.slice(5, 7), 16)];
  const b = [parseInt(hi.slice(1, 3), 16), parseInt(hi.slice(3, 5), 16), parseInt(hi.slice(5, 7), 16)];
  for (let y = 0; y < N; y += 1) {
    for (let x = 0; x < N; x += 1) {
      const n =
        periodicNoise(x / 32, y / 32, 8, terrain) * 0.55 +
        periodicNoise(x / 8, y / 8, 32, terrain + 7) * 0.3 +
        hash2(x, y, terrain + 13) * 0.15;
      const o = (y * N + x) * 4;
      for (let k = 0; k < 3; k += 1) img.data[o + k] = a[k] + (b[k] - a[k]) * n;
      img.data[o + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0);
  const rng = mulberry32(hashString(`pattern:${terrain}`));
  if (terrain === 1 || terrain === 2 || terrain === 5) {
    for (let i = 0; i < 900; i += 1) {
      const x = rng() * N;
      const y = rng() * N;
      ctx.strokeStyle = i % 2 ? 'rgba(200,230,140,0.45)' : 'rgba(30,50,20,0.4)';
      ctx.lineWidth = 1;
      for (const dx of [0, N, -N]) {
        for (const dy of [0, N, -N]) {
          if (x + dx < -4 || x + dx > N + 4 || y + dy < -8 || y + dy > N + 2) continue;
          ctx.beginPath();
          ctx.moveTo(x + dx, y + dy);
          ctx.lineTo(x + dx + (rng() - 0.5) * 2, y + dy - 3 - rng() * 4);
          ctx.stroke();
        }
      }
    }
  }
  patterns.set(terrain, c);
  return c;
}

/**
 * Paint tile `t` in world-screen units (zoom 1) relative to `origin`, a
 * ground-plane point (the tile centre by default): the caller's transform
 * scales by `s` (texture px per world-screen px) and places project(origin).
 * opts.glyphs: draw trees and rocks (false when sprites provide them).
 * opts.seed: decoration seed (defaults to the tile's own).
 */
export function paintHexDetail(ctx, t, R, s, { glyphs = true, riverEdges = [], origin = null, seed = null } = {}) {
  const rng = mulberry32(hashString(seed ?? `tile:${t.q},${t.r}`));
  const o = origin ?? hexCentre(t.q, t.r, R);
  const P = (x, y) => project(x - o.x, y - o.y);
  const px = 1 / s;
  ctx.save();
  hexPath(ctx, P, t.q, t.r, R, 1.02);
  ctx.clip();
  const c = hexCentre(t.q, t.r, R);
  const cp = P(c.x, c.y);
  const span = R * K * 1.8;
  let base = BASE[t.terrain] ?? '#888';
  if (t.terrain === 0 && t.lake) base = '#3f7f98';
  else if (t.terrain !== 0 && t.terrain !== 7) base = rgbToCss(mix(base, '#a0a070', (t.elevation / 1000) * 0.15));
  ctx.fillStyle = base;
  ctx.fillRect(cp.x - span, cp.y - span, span * 2, span * 2);
  // Close views: a fine tiling ground texture instead of coarse mottling.
  if (s >= 0.8) {
    const pattern = ctx.createPattern(groundPattern(t.terrain), 'repeat');
    // 256 texture px of pattern span 256 / s world px (16 m at s = 1), in phase
    // with the world origin so neighbouring tiles line up.
    const period = 256 / s;
    const a = project(o.x, o.y);
    pattern.setTransform(new DOMMatrix().translate(-(a.x % period), -(a.y % period)).scale(1 / s));
    ctx.save();
    ctx.globalAlpha = t.terrain === 0 ? 0.55 : 0.9;
    ctx.fillStyle = pattern;
    ctx.fillRect(cp.x - span, cp.y - span, span * 2, span * 2);
    ctx.restore();
  }
  // Ground mottling.
  for (let i = 0; i < (s >= 0.8 ? 40 : 160); i += 1) {
    const p = pointIn(P, rng, t.q, t.r, R, 1.05);
    ctx.fillStyle = rng() < 0.5 ? 'rgba(0,0,0,0.05)' : 'rgba(255,250,220,0.04)';
    ctx.beginPath();
    ctx.ellipse(p.x, p.y, R * K * (0.05 + rng() * 0.1), R * K * (0.025 + rng() * 0.05), 0, 0, Math.PI * 2);
    ctx.fill();
  }
  const fine = Math.min(1600, Math.round(2400 * s));
  if (t.terrain === 0) {
    const g = ctx.createRadialGradient(cp.x, cp.y, 0, cp.x, cp.y, R * K * 1.1);
    g.addColorStop(0, 'rgba(10,30,60,0.35)');
    g.addColorStop(1, 'rgba(255,255,255,0.0)');
    ctx.fillStyle = g;
    ctx.fillRect(cp.x - span, cp.y - span, span * 2, span * 2);
    ctx.strokeStyle = 'rgba(220,235,255,0.25)';
    ctx.lineWidth = 2 * px;
    for (let i = 0; i < fine / 6; i += 1) {
      const p = pointIn(P, rng, t.q, t.r, R, 0.9);
      ctx.beginPath();
      ctx.arc(p.x, p.y, R * K * 0.04, Math.PI * 1.15, Math.PI * 1.85);
      ctx.stroke();
    }
    // Shore: a pale band just inside the tile edge.
    hexPath(ctx, P, t.q, t.r, R, 1.0);
    ctx.strokeStyle = 'rgba(214,200,150,0.55)';
    ctx.lineWidth = R * K * 0.08;
    ctx.stroke();
  } else if (t.terrain === 6) {
    // Hills: rolling rises, light on the north-west slope, shaded to the south-east.
    const rises = 7 + Math.floor(t.elevation / 120);
    const hills = [];
    for (let i = 0; i < rises; i += 1) hills.push(pointIn(P, rng, t.q, t.r, R, 0.85));
    hills.sort((a, b) => a.y - b.y);
    for (const p of hills) {
      const w = R * K * (0.12 + rng() * 0.08);
      ctx.fillStyle = 'rgba(70,64,36,0.35)';
      ctx.beginPath();
      ctx.ellipse(p.x + w * 0.15, p.y, w, w * 0.45, 0, Math.PI, 0);
      ctx.fill();
      ctx.fillStyle = 'rgba(200,192,140,0.35)';
      ctx.beginPath();
      ctx.ellipse(p.x - w * 0.2, p.y - w * 0.08, w * 0.6, w * 0.3, 0, Math.PI, 0);
      ctx.fill();
    }
  } else if (t.terrain === 7) {
    // Snowfield: blue-grey wind ridges and a few dark rock peaks breaking through.
    ctx.strokeStyle = 'rgba(120,140,170,0.25)';
    ctx.lineWidth = 3 * px;
    for (let i = 0; i < 50; i += 1) {
      const p = pointIn(P, rng, t.q, t.r, R, 0.95);
      ctx.beginPath();
      ctx.arc(p.x, p.y, R * K * 0.08, Math.PI * 1.1, Math.PI * 1.7);
      ctx.stroke();
    }
    const peaks = [];
    for (let i = 0; i < 4; i += 1) peaks.push(pointIn(P, rng, t.q, t.r, R, 0.65));
    peaks.sort((a, b) => a.y - b.y);
    for (const p of peaks) peak(ctx, p, R * K * (0.24 + rng() * 0.12), true);
  } else if (t.terrain === 3) {
    ctx.strokeStyle = 'rgba(60,50,40,0.25)';
    ctx.lineWidth = 3 * px;
    for (let i = 0; i < 40; i += 1) {
      const p = pointIn(P, rng, t.q, t.r, R, 0.95);
      ctx.beginPath();
      ctx.moveTo(p.x - R * K * 0.08, p.y);
      ctx.lineTo(p.x + R * K * 0.08, p.y - R * K * 0.02);
      ctx.stroke();
    }
    const n = 3 + Math.max(0, Math.floor((t.elevation - 800) / 45));
    const peaks = [];
    for (let i = 0; i < n; i += 1) peaks.push(pointIn(P, rng, t.q, t.r, R, 0.7));
    peaks.sort((a, b) => a.y - b.y);
    const snowy = t.temperature !== undefined ? t.temperature < 220 : t.elevation > 900;
    for (const p of peaks) peak(ctx, p, R * K * (0.22 + rng() * 0.12), snowy);
  } else if (t.terrain === 4) {
    ctx.strokeStyle = 'rgba(150,110,60,0.3)';
    ctx.lineWidth = 3 * px;
    for (let i = 0; i < 30; i += 1) {
      const p = pointIn(P, rng, t.q, t.r, R, 0.95);
      ctx.beginPath();
      ctx.arc(p.x, p.y + R * K * 0.1, R * K * 0.12, Math.PI * 1.2, Math.PI * 1.8);
      ctx.stroke();
    }
  } else {
    // Grass, forest floor, tundra: short upright strokes.
    const tuft =
      t.terrain === 5
        ? ['rgba(140,150,140,0.5)', 'rgba(230,235,230,0.5)']
        : ['rgba(40,70,25,0.45)', 'rgba(170,200,100,0.45)'];
    ctx.lineWidth = 1.5 * px;
    for (let i = 0; i < fine; i += 1) {
      const p = pointIn(P, rng, t.q, t.r, R, 1.05);
      const h = (3 + rng() * 4) * px;
      ctx.strokeStyle = tuft[i % 2];
      ctx.beginPath();
      ctx.moveTo(p.x, p.y);
      ctx.lineTo(p.x + (rng() - 0.5) * 2 * px, p.y - h);
      ctx.stroke();
    }
    if (t.terrain === 1) {
      for (let i = 0; i < fine / 40; i += 1) {
        const p = pointIn(P, rng, t.q, t.r, R, 0.95);
        ctx.fillStyle = ['#f4f0e0', '#f0d860', '#c8a0e0'][i % 3];
        ctx.fillRect(p.x, p.y, 2 * px, 2 * px);
      }
    }
  }
  // Rivers: along the tile's borders where the engine records one, meandering
  // the same way from both sides so neighbouring tiles join up.
  for (const edge of riverEdges) {
    const line = riverLine(edge, 24).map((p) => P(p.x, p.y));
    // Channel width in metres, never thinner than about 1.5 texture pixels.
    const width = Math.max(edge.widthM * K * 1.1, 1.5 * px);
    for (const [w, col] of [
      [width * 2.2, 'rgba(120,100,60,0.35)'],
      [width, edge.deep ? '#2f6b9e' : '#4f8fbf'],
      [width * 0.35, 'rgba(170,205,235,0.45)'],
    ]) {
      ctx.strokeStyle = col;
      ctx.lineWidth = w;
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      ctx.beginPath();
      line.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
      ctx.stroke();
    }
    if (!edge.deep) {
      // Ford (presentation): a pale gravel bar where the channel is crossed on foot.
      const f = line[12];
      ctx.fillStyle = 'rgba(214,200,160,0.85)';
      ctx.beginPath();
      ctx.ellipse(f.x, f.y, width * 1.4, width * 0.5, 0, 0, Math.PI * 2);
      ctx.fill();
    }
  }
  if (glyphs) {
    const items = [];
    const trees =
      t.terrain === 2
        ? 10 + Math.floor(t.timber / 25)
        : t.terrain === 1 && t.timber > 600
          ? Math.floor((t.timber - 600) / 50)
          : t.terrain === 5 && t.timber > 700
            ? 3
            : 0;
    for (let i = 0; i < trees; i += 1)
      items.push({ kind: 'tree', p: pointIn(P, rng, t.q, t.r, R, 0.85), pine: t.terrain === 5 || rng() < 0.3 });
    const rocks = t.terrain !== 0 && t.stone > 600 ? Math.floor((t.stone - 600) / 70) : 0;
    for (let i = 0; i < rocks; i += 1) items.push({ kind: 'rock', p: pointIn(P, rng, t.q, t.r, R, 0.8) });
    items.sort((a, b) => a.p.y - b.p.y);
    for (const it of items) {
      if (it.kind === 'tree') tree(ctx, it.p, R * K * (0.045 + rng() * 0.02), rng, it.pine);
      else rock(ctx, it.p, R * K * (0.025 + rng() * 0.02), rng);
    }
  }
  ctx.restore();
  hexPath(ctx, P, t.q, t.r, R, 1.0);
  ctx.strokeStyle = 'rgba(20,24,20,0.12)';
  ctx.lineWidth = 1.5 * px;
  ctx.stroke();
}

/** Decoration plan for a tile, shared with the settlement-band sprite layer. */
export function tileDecor(t, R) {
  const rng = mulberry32(hashString(`decor:${t.q},${t.r}`));
  const out = [];
  const trees =
    t.terrain === 2
      ? 6 + Math.floor(t.timber / 60)
      : t.terrain === 1 && t.timber > 600
        ? Math.floor((t.timber - 600) / 80)
        : 0;
  const c = hexCentre(t.q, t.r, R);
  const place = (frac) => {
    const a = rng() * Math.PI * 2;
    const d = Math.sqrt(rng()) * R * frac * 0.86;
    return { x: c.x + Math.cos(a) * d, y: c.y + Math.sin(a) * d };
  };
  for (let i = 0; i < trees; i += 1)
    out.push({ kind: rng() < 0.3 ? 'pine' : 'oak', variant: Math.floor(rng() * 6), ...place(0.85) });
  const rocks = t.terrain !== 0 && t.stone > 600 ? Math.floor((t.stone - 600) / 90) : 0;
  for (let i = 0; i < rocks; i += 1) out.push({ kind: 'rock', variant: Math.floor(rng() * 4), ...place(0.8) });
  return out;
}
