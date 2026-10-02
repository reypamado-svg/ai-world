// Detailed painting of one engine tile (PROTOTYPE ARTWORK).
//
// Everything drawn comes from the tile's own engine values: terrain type,
// elevation, temperature (snow on cold peaks), timber (tree density), stone
// (rock outcrops) and the river borders the engine records. Decoration is
// seeded per tile, so the same tile always looks the same, and nothing implies
// a resource the tile does not have.

import { project, unproject } from '../world/coords.js';
import { hexCentre, hexCorners } from '../world/hex.js';
import { riverLine } from '../world/rivers.js';
import { snowLine } from '../world/interior.js';
import { hashString, mulberry32 } from '../sim/rng.js';
import { css, jitter } from './art/paint/color.js';

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

/**
 * Fill the canvas with the interior colour field: sampled on a coarse lattice
 * of canvas pixels and scaled up smoothly (the field varies over hundreds of
 * metres; a texel here is tens of metres).
 */
function paintField(ctx, field, o) {
  const m = ctx.getTransform();
  const W = ctx.canvas.width;
  const H = ctx.canvas.height;
  const block = Math.max(4, Math.ceil(W / 64));
  // Lattice spacing in ground metres (a canvas pixel is 1 / (scale * K * sqrt 2) metres along the ground).
  const res = block / (m.a * K * Math.SQRT2);
  const nx = Math.ceil(W / block) + 2;
  const ny = Math.ceil(H / block) + 2;
  const lattice = document.createElement('canvas');
  lattice.width = nx;
  lattice.height = ny;
  const lctx = lattice.getContext('2d');
  const img = lctx.createImageData(nx, ny);
  for (let b = 0; b < ny; b += 1) {
    for (let a = 0; a < nx; a += 1) {
      // Canvas pixel -> world-screen units relative to the anchor -> ground plane.
      const g = unproject((a * block - m.e) / m.a, (b * block - m.f) / m.d);
      const c = field.sample(o.x + g.x, o.y + g.y, res);
      const k = (b * nx + a) * 4;
      img.data[k] = c[0];
      img.data[k + 1] = c[1];
      img.data[k + 2] = c[2];
      img.data[k + 3] = 255;
    }
  }
  lctx.putImageData(img, 0, 0);
  ctx.save();
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = 'high';
  ctx.drawImage(lattice, 0, 0, nx, ny, -block / 2, -block / 2, nx * block, ny * block);
  ctx.restore();
}

/**
 * Paint tile `t` in world-screen units (zoom 1) relative to `origin`, a
 * ground-plane point (the tile centre by default): the caller's transform
 * scales by `s` (texture px per world-screen px) and places project(origin).
 * opts.interior: the ground colour field (world/interior.js); without it, a flat colour.
 * opts.glyphs: draw trees and rocks (false when sprites provide them).
 * opts.seed: decoration seed (defaults to the tile's own).
 * opts.outline: which of the six outline segments to draw (see landOutline).
 */
export function paintHexDetail(
  ctx,
  t,
  R,
  s,
  { glyphs = true, riverEdges = [], origin = null, seed = null, interior = null, outline = Array(6).fill(true) } = {},
) {
  const rng = mulberry32(hashString(seed ?? `tile:${t.q},${t.r}`));
  const o = origin ?? hexCentre(t.q, t.r, R);
  const P = (x, y) => project(x - o.x, y - o.y);
  const px = 1 / s;
  ctx.save();
  hexPath(ctx, P, t.q, t.r, R, 1.02);
  ctx.clip();
  if (interior) paintField(ctx, interior, o);
  else {
    // No field available: the terrain's flat colour.
    const c = P(hexCentre(t.q, t.r, R).x, hexCentre(t.q, t.r, R).y);
    const span = R * K * 1.8;
    ctx.fillStyle = t.terrain === 0 && t.lake ? '#3f7f98' : (BASE[t.terrain] ?? '#888');
    ctx.fillRect(c.x - span, c.y - span, span * 2, span * 2);
  }
  // Map symbols on top of the ground: rises on hills, peaks on mountains and snowfields.
  if (t.terrain === 6) {
    const rises = 7 + Math.floor(t.elevation / 120);
    const hills = [];
    for (let i = 0; i < rises; i += 1) hills.push(pointIn(P, rng, t.q, t.r, R, 0.85));
    hills.sort((a, b) => a.y - b.y);
    for (const p of hills) {
      const w = R * K * (0.12 + rng() * 0.08);
      ctx.fillStyle = 'rgba(70,64,36,0.3)';
      ctx.beginPath();
      ctx.ellipse(p.x + w * 0.15, p.y, w, w * 0.45, 0, Math.PI, 0);
      ctx.fill();
      ctx.fillStyle = 'rgba(200,192,140,0.3)';
      ctx.beginPath();
      ctx.ellipse(p.x - w * 0.2, p.y - w * 0.08, w * 0.6, w * 0.3, 0, Math.PI, 0);
      ctx.fill();
    }
  } else if (t.terrain === 3 || t.terrain === 7) {
    const n = t.terrain === 7 ? 4 : 3 + Math.max(0, Math.floor((t.elevation - 800) / 45));
    const peaks = [];
    for (let i = 0; i < n; i += 1) peaks.push(pointIn(P, rng, t.q, t.r, R, t.terrain === 7 ? 0.65 : 0.7));
    peaks.sort((a, b) => a.y - b.y);
    const snowy = t.terrain === 7 || t.elevation > snowLine(t.temperature);
    for (const p of peaks) peak(ctx, p, R * K * (0.22 + rng() * 0.12), snowy);
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
  // Tile outline, faint, only between land tiles (a shore is its own outline).
  const corners = hexCorners(t.q, t.r, R).map((p) => P(p.x, p.y));
  ctx.strokeStyle = 'rgba(20,24,20,0.1)';
  ctx.lineWidth = 1.5 * px;
  ctx.beginPath();
  for (let i = 0; i < 6; i += 1) {
    if (!outline[i]) continue;
    ctx.moveTo(corners[i].x, corners[i].y);
    ctx.lineTo(corners[(i + 1) % 6].x, corners[(i + 1) % 6].y);
  }
  ctx.stroke();
}

/**
 * Which of a tile's six outline segments (corner i to corner i + 1) lie
 * between two land tiles. `landAt(q, r)` is true for land.
 */
export function landOutline(q, r, landAt) {
  // Segment i faces the neighbour in the direction 60 i - 60 degrees in the hex frame.
  const towards = [
    [1, -1],
    [1, 0],
    [0, 1],
    [-1, 1],
    [-1, 0],
    [0, -1],
  ];
  return towards.map(([dq, dr]) => landAt(q, r) && landAt(q + dq, r + dr));
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
