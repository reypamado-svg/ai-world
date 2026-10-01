// Detailed painting of one engine tile (PROTOTYPE ARTWORK).
//
// Everything drawn comes from the tile's own engine values: terrain type,
// elevation, timber (tree density), stone (rock outcrops) and the river
// flag. Decoration is seeded per tile, so the same tile always looks the
// same, and nothing implies a resource the tile does not have.

import { project } from '../world/coords.js';
import { hexCentre, hexCorners } from '../world/hex.js';
import { hashString, mulberry32 } from '../sim/rng.js';
import { css, jitter, mix, rgbToCss } from './art/paint/color.js';

const K = 16;

const BASE = {
  0: '#3a6c9a',
  1: '#7d9a46',
  2: '#4f6b34',
  3: '#8a8072',
  4: '#cfb47a',
  5: '#c6ccc2',
};

function hexPath(ctx, q, r, R, grow = 1) {
  const c = hexCentre(q, r, R);
  const pts = hexCorners(q, r, R).map((p) => project(c.x + (p.x - c.x) * grow, c.y + (p.y - c.y) * grow));
  ctx.beginPath();
  pts.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
  ctx.closePath();
}

/** A random ground-plane point inside the tile, at most `frac` of the radius out. */
function pointIn(rng, q, r, R, frac = 0.82) {
  const c = hexCentre(q, r, R);
  const a = rng() * Math.PI * 2;
  const d = Math.sqrt(rng()) * R * frac * 0.86;
  return project(c.x + Math.cos(a) * d, c.y + Math.sin(a) * d);
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
 * Paint tile `t` in world-screen coordinates (zoom 1); the caller has set a
 * transform scaling by `s` (texture px per world-screen px).
 * opts.glyphs: draw trees and rocks (false when sprites provide them).
 */
export function paintHexDetail(ctx, t, R, s, { glyphs = true, riverNeighbours = [] } = {}) {
  const rng = mulberry32(hashString(`tile:${t.q},${t.r}`));
  const px = 1 / s;
  ctx.save();
  hexPath(ctx, t.q, t.r, R, 1.02);
  ctx.clip();
  const c = hexCentre(t.q, t.r, R);
  const cp = project(c.x, c.y);
  const span = R * K * 1.8;
  let base = BASE[t.terrain] ?? '#888';
  if (t.terrain !== 0) base = rgbToCss(mix(base, '#a0a070', (t.elevation / 1000) * 0.15));
  ctx.fillStyle = base;
  ctx.fillRect(cp.x - span, cp.y - span, span * 2, span * 2);
  // Ground mottling.
  for (let i = 0; i < 160; i += 1) {
    const p = pointIn(rng, t.q, t.r, R, 1.05);
    ctx.fillStyle = rng() < 0.5 ? 'rgba(0,0,0,0.06)' : 'rgba(255,250,220,0.06)';
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
      const p = pointIn(rng, t.q, t.r, R, 0.9);
      ctx.beginPath();
      ctx.arc(p.x, p.y, R * K * 0.04, Math.PI * 1.15, Math.PI * 1.85);
      ctx.stroke();
    }
    // Shore: a pale band just inside the tile edge.
    hexPath(ctx, t.q, t.r, R, 1.0);
    ctx.strokeStyle = 'rgba(214,200,150,0.55)';
    ctx.lineWidth = R * K * 0.08;
    ctx.stroke();
  } else if (t.terrain === 3) {
    ctx.strokeStyle = 'rgba(60,50,40,0.25)';
    ctx.lineWidth = 3 * px;
    for (let i = 0; i < 40; i += 1) {
      const p = pointIn(rng, t.q, t.r, R, 0.95);
      ctx.beginPath();
      ctx.moveTo(p.x - R * K * 0.08, p.y);
      ctx.lineTo(p.x + R * K * 0.08, p.y - R * K * 0.02);
      ctx.stroke();
    }
    const n = 3 + Math.floor((t.elevation - 820) / 45);
    const peaks = [];
    for (let i = 0; i < n; i += 1) peaks.push(pointIn(rng, t.q, t.r, R, 0.7));
    peaks.sort((a, b) => a.y - b.y);
    for (const p of peaks) peak(ctx, p, R * K * (0.22 + rng() * 0.12), t.elevation > 900);
  } else if (t.terrain === 4) {
    ctx.strokeStyle = 'rgba(150,110,60,0.3)';
    ctx.lineWidth = 3 * px;
    for (let i = 0; i < 30; i += 1) {
      const p = pointIn(rng, t.q, t.r, R, 0.95);
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
      const p = pointIn(rng, t.q, t.r, R, 1.05);
      const h = (3 + rng() * 4) * px;
      ctx.strokeStyle = tuft[i % 2];
      ctx.beginPath();
      ctx.moveTo(p.x, p.y);
      ctx.lineTo(p.x + (rng() - 0.5) * 2 * px, p.y - h);
      ctx.stroke();
    }
    if (t.terrain === 1) {
      for (let i = 0; i < fine / 40; i += 1) {
        const p = pointIn(rng, t.q, t.r, R, 0.95);
        ctx.fillStyle = ['#f4f0e0', '#f0d860', '#c8a0e0'][i % 3];
        ctx.fillRect(p.x, p.y, 2 * px, 2 * px);
      }
    }
  }
  // River: a ribbon along the tile column, only where the engine flags one.
  if (t.river) {
    const ends = riverNeighbours.length ? riverNeighbours : [0];
    for (const dr of ends) {
      const n = hexCentre(t.q, t.r + (dr || 1), R);
      const target = dr ? project((c.x + n.x) / 2, (c.y + n.y) / 2) : cp;
      for (const [w, col] of [
        [R * K * 0.26, 'rgba(120,100,60,0.55)'],
        [R * K * 0.2, '#4f86b4'],
        [R * K * 0.08, 'rgba(160,200,230,0.45)'],
      ]) {
        ctx.strokeStyle = col;
        ctx.lineWidth = w;
        ctx.lineCap = 'round';
        ctx.beginPath();
        ctx.moveTo(cp.x, cp.y);
        ctx.lineTo(target.x, target.y);
        ctx.stroke();
      }
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
      items.push({ kind: 'tree', p: pointIn(rng, t.q, t.r, R, 0.85), pine: t.terrain === 5 || rng() < 0.3 });
    const rocks = t.terrain !== 0 && t.stone > 600 ? Math.floor((t.stone - 600) / 70) : 0;
    for (let i = 0; i < rocks; i += 1) items.push({ kind: 'rock', p: pointIn(rng, t.q, t.r, R, 0.8) });
    items.sort((a, b) => a.p.y - b.p.y);
    for (const it of items) {
      if (it.kind === 'tree') tree(ctx, it.p, R * K * (0.045 + rng() * 0.02), rng, it.pine);
      else rock(ctx, it.p, R * K * (0.025 + rng() * 0.02), rng);
    }
  }
  ctx.restore();
  hexPath(ctx, t.q, t.r, R, 1.0);
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
