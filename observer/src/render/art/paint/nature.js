// Procedural trees, rocks, fences, crops and small props (PROTOTYPE ARTWORK).

import { K, KZ } from '../../../world/coords.js';
import { rngFor } from '../../../sim/rng.js';
import { css, jitter, mix, rgbToCss } from './color.js';
import { ART, Sheet, projVec } from './iso.js';
import { paintShadow, solidBox } from './buildings.js';

const PX = K * ART; // art px per ground metre along an axis (horizontal part)
const PZ = KZ * ART; // art px per metre of height

function circlePoints(cx, cy, r, z, n = 12) {
  const pts = [];
  for (let i = 0; i < n; i += 1) {
    const a = (i / n) * Math.PI * 2;
    pts.push([cx + Math.cos(a) * r, cy + Math.sin(a) * r, z]);
  }
  return pts;
}

function result(sheet, footprint, shadowPts, opts = {}) {
  return {
    canvas: sheet.canvas,
    anchor: sheet.anchor,
    shadow: shadowPts ? paintShadow(shadowPts, footprint, opts) : null,
    footprint,
    doors: [],
  };
}

export function paintTree({ id, kind = 'oak', size = 1 }) {
  return kind === 'pine' ? paintPine(id, size) : paintOak(id, size);
}

function paintOak(id, size) {
  const rng = rngFor(id);
  const D = (4.2 + rng() * 1.6) * size;
  const trunkH = (2.0 + rng() * 0.8) * size;
  const rx = (D / 2) * PX * 1.18;
  const ry = rx * 0.82;
  const cyOff = -(trunkH + D * 0.42) * PZ;
  const sheet = new Sheet([[0, 0, 0]], 6, [
    [-rx - 8, cyOff - ry - 10],
    [rx + 8, 8],
  ]);
  const ctx = sheet.ctx;
  const ox = sheet.ox;
  const oy = sheet.oy;
  const cx = ox;
  const cy = oy + cyOff;
  // Trunk with root flare and a couple of branches.
  const tw = 0.36 * size * PX;
  const trunkCol = jitter('#5a4030', rng, 0.1);
  const tg = ctx.createLinearGradient(ox - tw, 0, ox + tw, 0);
  tg.addColorStop(0, css(trunkCol, 1.25));
  tg.addColorStop(0.6, css(trunkCol, 0.85));
  tg.addColorStop(1, css(trunkCol, 0.55));
  ctx.fillStyle = tg;
  ctx.beginPath();
  ctx.moveTo(ox - tw * 1.1, oy + 2);
  ctx.quadraticCurveTo(ox - tw * 0.5, oy - 6, ox - tw * 0.45, cy + ry * 0.3);
  ctx.lineTo(ox + tw * 0.45, cy + ry * 0.3);
  ctx.quadraticCurveTo(ox + tw * 0.5, oy - 6, ox + tw * 1.15, oy + 2);
  ctx.closePath();
  ctx.fill();
  ctx.strokeStyle = 'rgba(25,15,8,0.8)';
  ctx.lineWidth = 1.2;
  ctx.stroke();
  ctx.strokeStyle = css(trunkCol, 0.5, 0.7);
  ctx.lineWidth = 1;
  for (let i = 0; i < 5; i += 1) {
    const x = ox + (rng() - 0.5) * tw * 0.8;
    ctx.beginPath();
    ctx.moveTo(x, oy - 2);
    ctx.lineTo(x + (rng() - 0.5) * 3, cy + ry * 0.4);
    ctx.stroke();
  }
  // Canopy clumps.
  const dark = jitter('#2f4a24', rng, 0.08);
  const light = jitter('#7d9a44', rng, 0.08);
  const clumps = [];
  const n = Math.round(24 + D * 3);
  for (let i = 0; i < n; i += 1) {
    const a = rng() * Math.PI * 2;
    const r = Math.sqrt(rng()) * 0.78;
    const dx = Math.cos(a) * r;
    const dy = Math.sin(a) * r;
    clumps.push({
      x: cx + dx * rx,
      y: cy + dy * ry,
      r: rx * (0.2 + rng() * 0.16),
      lit: Math.max(0, Math.min(1, 0.55 - dx * 0.35 - dy * 0.45 + (rng() - 0.5) * 0.25)),
    });
  }
  clumps.sort((a, b) => a.y - b.y);
  // Dark silhouette first: an outline for the whole crown.
  ctx.fillStyle = '#1a2614';
  for (const c of clumps) {
    ctx.beginPath();
    ctx.arc(c.x, c.y, c.r + 1.6, 0, Math.PI * 2);
    ctx.fill();
  }
  for (const c of clumps) {
    const col = mix(dark, light, c.lit);
    const g = ctx.createRadialGradient(c.x - c.r * 0.35, c.y - c.r * 0.4, c.r * 0.1, c.x, c.y, c.r);
    g.addColorStop(0, css(col, 1.25));
    g.addColorStop(0.7, css(col, 0.95));
    g.addColorStop(1, css(col, 0.7));
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(c.x, c.y, c.r, 0, Math.PI * 2);
    ctx.fill();
    // Leafy rim.
    ctx.fillStyle = css(col, 0.9);
    for (let k = 0; k < 7; k += 1) {
      const a = rng() * Math.PI * 2;
      ctx.beginPath();
      ctx.arc(c.x + Math.cos(a) * c.r * 0.92, c.y + Math.sin(a) * c.r * 0.92, c.r * 0.22, 0, Math.PI * 2);
      ctx.fill();
    }
  }
  ctx.save();
  ctx.globalCompositeOperation = 'source-atop';
  for (let i = 0; i < D * 120; i += 1) {
    const x = cx + (rng() - 0.5) * rx * 2;
    const y = cy + (rng() - 0.5) * ry * 2;
    const t = 0.5 - ((x - cx) / rx) * 0.35 - ((y - cy) / ry) * 0.45;
    ctx.fillStyle = t > 0.55 ? 'rgba(200,220,120,0.35)' : t < 0.2 ? 'rgba(10,25,10,0.35)' : 'rgba(90,120,50,0.3)';
    ctx.fillRect(x, y, 1.5 + rng() * 1.5, 1.5 + rng());
  }
  const ug = ctx.createLinearGradient(0, cy, 0, cy + ry * 1.05);
  ug.addColorStop(0, 'rgba(10,20,10,0)');
  ug.addColorStop(1, 'rgba(10,20,10,0.45)');
  ctx.fillStyle = ug;
  ctx.fillRect(cx - rx - 4, cy, rx * 2 + 8, ry + 6);
  ctx.restore();
  const half = (D * 0.7) / 2;
  const footprint = { minX: -half, minY: -half, maxX: half, maxY: half };
  const crown = circlePoints(0, 0, D * 0.42, trunkH + D * 0.45, 16);
  return result(sheet, footprint, crown, { blur: 6, alpha: 0.75 });
}

function paintPine(id, size) {
  const rng = rngFor(id);
  const H = (6.5 + rng() * 2.5) * size;
  const W = (2.6 + rng() * 0.8) * size;
  const top = -H * PZ;
  const halfW = (W / 2) * PX * 1.3;
  const sheet = new Sheet([[0, 0, 0]], 6, [
    [-halfW - 6, top - 8],
    [halfW + 6, 8],
  ]);
  const ctx = sheet.ctx;
  const ox = sheet.ox;
  const oy = sheet.oy;
  ctx.fillStyle = '#4a3424';
  ctx.fillRect(ox - 3, oy - 1.4 * PZ, 6, 1.4 * PZ + 2);
  const tiers = 6;
  const base = jitter('#2c4630', rng, 0.08);
  for (let i = 0; i < tiers; i += 1) {
    const t0 = i / tiers;
    const yb = oy - (1.1 + t0 * (H - 1.6)) * PZ * 0.95;
    const yt = yb - ((H - 1.1) / tiers) * PZ * 1.9;
    const w = halfW * (1 - t0 * 0.82);
    const col = mix(base, '#4f7448', 0.1 + t0 * 0.25);
    const g = ctx.createLinearGradient(ox - w, 0, ox + w, 0);
    g.addColorStop(0, css(col, 1.35));
    g.addColorStop(0.45, css(col, 1.0));
    g.addColorStop(1, css(col, 0.58));
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.moveTo(ox, Math.max(yt, oy + top));
    const teeth = 7;
    for (let k = 0; k <= teeth; k += 1) {
      const x = ox + w - (2 * w * k) / teeth;
      const y = yb + (k % 2 ? -3 - rng() * 3 : 2 + rng() * 2);
      ctx.lineTo(x, y);
    }
    ctx.closePath();
    ctx.fill();
    ctx.strokeStyle = 'rgba(12,22,14,0.75)';
    ctx.lineWidth = 1.2;
    ctx.stroke();
  }
  const footprint = { minX: -W * 0.45, minY: -W * 0.45, maxX: W * 0.45, maxY: W * 0.45 };
  return result(sheet, footprint, circlePoints(0, 0, W * 0.4, H * 0.55, 10).concat([[0, 0, H]]), {
    blur: 5,
    alpha: 0.7,
  });
}

export function paintBush({ id }) {
  const rng = rngFor(id);
  const D = 1.2 + rng() * 0.8;
  const rx = (D / 2) * PX * 1.2;
  const ry = rx * 0.75;
  const sheet = new Sheet([[0, 0, 0]], 6, [
    [-rx - 4, -ry * 2 - 6],
    [rx + 4, 6],
  ]);
  const ctx = sheet.ctx;
  const cx = sheet.ox;
  const cy = sheet.oy - ry * 0.85;
  const base = jitter('#3d5a2a', rng, 0.1);
  for (let i = 0; i < 12; i += 1) {
    const dx = (rng() - 0.5) * 1.5;
    const dy = (rng() - 0.5) * 1.1;
    const r = rx * (0.3 + rng() * 0.25);
    const col = mix(base, '#8aa050', Math.max(0, 0.45 - dx * 0.3 - dy * 0.4));
    ctx.fillStyle = '#1c2a14';
    ctx.beginPath();
    ctx.arc(cx + dx * rx * 0.6, cy + dy * ry * 0.6, r + 1.4, 0, Math.PI * 2);
    ctx.fill();
    const g = ctx.createRadialGradient(
      cx + dx * rx * 0.6 - r * 0.3,
      cy + dy * ry * 0.6 - r * 0.3,
      1,
      cx + dx * rx * 0.6,
      cy + dy * ry * 0.6,
      r,
    );
    g.addColorStop(0, css(col, 1.2));
    g.addColorStop(1, css(col, 0.75));
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(cx + dx * rx * 0.6, cy + dy * ry * 0.6, r, 0, Math.PI * 2);
    ctx.fill();
  }
  const h = D * 0.35;
  const footprint = { minX: -h, minY: -h, maxX: h, maxY: h };
  return result(sheet, footprint, circlePoints(0, 0, D * 0.4, D * 0.5, 8), { blur: 3, alpha: 0.6 });
}

export function paintRock({ id, size = 1 }) {
  const rng = rngFor(id);
  const R = (0.45 + rng() * 0.5) * size;
  const rx = R * PX * 1.35;
  const hgt = R * (0.6 + rng() * 0.4) * PZ;
  const sheet = new Sheet([[0, 0, 0]], 6, [
    [-rx - 4, -hgt - 8],
    [rx + 4, rx * 0.5 + 4],
  ]);
  const ctx = sheet.ctx;
  const cx = sheet.ox;
  const cy = sheet.oy;
  const pts = [];
  const n = 9;
  for (let i = 0; i < n; i += 1) {
    const a = (i / n) * Math.PI * 2 + rng() * 0.3;
    const r = 0.75 + rng() * 0.3;
    pts.push([
      cx + Math.cos(a) * rx * r,
      cy + Math.sin(a) * rx * 0.48 * r - (Math.sin(a) < 0 ? hgt * (0.6 + rng() * 0.4) : 0),
    ]);
  }
  const base = jitter(rng() < 0.5 ? '#8e8a80' : '#9a8f7c', rng, 0.1);
  ctx.fillStyle = css(base, 0.75);
  ctx.beginPath();
  pts.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
  ctx.closePath();
  ctx.fill();
  // Facets: lit top-left, shaded right.
  const peak = [cx - rx * 0.12, cy - hgt * 0.95];
  for (let i = 0; i < n; i += 1) {
    const a = pts[i];
    const b = pts[(i + 1) % n];
    const mx = (a[0] + b[0]) / 2 - cx;
    const my = (a[1] + b[1]) / 2 - cy;
    const lit = 0.75 - (mx / rx) * 0.45 - (my / rx) * 0.5;
    ctx.fillStyle = css(base, 0.58 + Math.min(1, Math.max(0, lit)) * 0.42);
    ctx.beginPath();
    ctx.moveTo(peak[0], peak[1]);
    ctx.lineTo(a[0], a[1]);
    ctx.lineTo(b[0], b[1]);
    ctx.closePath();
    ctx.fill();
  }
  ctx.strokeStyle = 'rgba(30,26,22,0.8)';
  ctx.lineWidth = 1.2;
  ctx.beginPath();
  pts.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
  ctx.closePath();
  ctx.stroke();
  ctx.strokeStyle = 'rgba(40,34,28,0.5)';
  ctx.lineWidth = 0.8;
  for (let i = 0; i < 3; i += 1) {
    ctx.beginPath();
    ctx.moveTo(peak[0] + (rng() - 0.5) * rx, peak[1] + rng() * hgt * 0.5);
    ctx.lineTo(peak[0] + (rng() - 0.5) * rx, peak[1] + hgt * (0.4 + rng() * 0.5));
    ctx.stroke();
  }
  for (let i = 0; i < 6; i += 1) {
    ctx.fillStyle = 'rgba(150,160,90,0.45)';
    ctx.beginPath();
    ctx.arc(cx + (rng() - 0.6) * rx, cy - hgt * rng() * 0.8, 1 + rng() * 2, 0, Math.PI * 2);
    ctx.fill();
  }
  const h = R * 0.85;
  const footprint = { minX: -h, minY: -h, maxX: h, maxY: h };
  return result(sheet, footprint, circlePoints(0, 0, R * 0.8, R * 0.7, 8), { blur: 3, alpha: 0.6 });
}

/** Split-rail fence segment, 2 m along an axis, centred on its anchor. */
export function paintFence({ id, axis = 'x', length = 2 }) {
  const rng = rngFor(id);
  const hl = length / 2;
  const t = 0.07;
  const fp = axis === 'x' ? { minX: -hl, minY: -t, maxX: hl, maxY: t } : { minX: -t, minY: -hl, maxX: t, maxY: hl };
  const sheet = new Sheet(
    [
      [fp.minX, fp.minY, 0],
      [fp.maxX, fp.maxY, 0],
      [fp.minX, fp.maxY, 1.3],
      [fp.maxX, fp.minY, 1.3],
    ],
    6,
  );
  const wood = { kind: 'planks', base: '#7a5a3a', board: 0.08 };
  const post = (p) =>
    solidBox(sheet, rng, { x0: p[0] - 0.07, y0: p[1] - 0.07, x1: p[0] + 0.07, y1: p[1] + 0.07, zb: 0, zt: 1.15 }, wood);
  const a = axis === 'x' ? [-hl + 0.07, 0] : [0, -hl + 0.07];
  const b = axis === 'x' ? [hl - 0.07, 0] : [0, hl - 0.07];
  post(a);
  for (const z of [0.5, 0.92]) {
    const r =
      axis === 'x'
        ? { x0: -hl, y0: -0.04, x1: hl, y1: 0.04, zb: z, zt: z + 0.09 }
        : { x0: -0.04, y0: -hl, x1: 0.04, y1: hl, zb: z, zt: z + 0.09 };
    solidBox(sheet, rng, r, { kind: 'planks', base: '#8a6844', board: 0.5, vertical: false });
  }
  post(b);
  return result(sheet, fp, null);
}

/** One 2 m segment of a crop row running along x. */
export function paintCropRow({ id, crop = 'wheat' }) {
  const rng = rngFor(id);
  const fp = { minX: -1, minY: -0.22, maxX: 1, maxY: 0.22 };
  const sheet = new Sheet(
    [
      [-1.1, -0.3, 0],
      [1.1, 0.3, 0],
      [-1.1, 0.3, 1.2],
      [1.1, -0.3, 1.2],
    ],
    6,
  );
  const ctx = sheet.ctx;
  if (crop === 'wheat') {
    const stalks = [];
    for (let i = 0; i < 70; i += 1) stalks.push([-1 + rng() * 2, (rng() - 0.5) * 0.36, 0.75 + rng() * 0.3]);
    stalks.sort((a, b) => a[0] + a[1] - (b[0] + b[1]));
    for (const [x, y, h] of stalks) {
      const lean = (rng() - 0.3) * 0.12;
      const a = sheet.P([x, y, 0]);
      const b = sheet.P([x + lean, y - lean * 0.5, h]);
      const col = mix('#b89a48', '#e0c870', rng());
      ctx.strokeStyle = css(col, 0.8);
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(a[0], a[1]);
      ctx.lineTo(b[0], b[1]);
      ctx.stroke();
      ctx.fillStyle = css(col, 1.05);
      ctx.beginPath();
      ctx.ellipse(b[0], b[1] + 1, 1.3, 3.2, -0.2, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = 'rgba(80,60,20,0.35)';
      ctx.fillRect(b[0], b[1] + 1, 1, 2);
    }
  } else {
    for (let i = 0; i < 4; i += 1) {
      const x = -0.75 + i * 0.5 + (rng() - 0.5) * 0.08;
      const c = sheet.P([x, 0, 0.12]);
      const r = 5.5 + rng() * 1.5;
      const base = jitter('#5d8a3e', rng, 0.1);
      for (let k = 0; k < 7; k += 1) {
        const a = (k / 7) * Math.PI * 2 + rng() * 0.4;
        ctx.fillStyle = css(base, 0.75 + 0.35 * Math.max(0, -Math.cos(a) * 0.6 - Math.sin(a) * 0.6));
        ctx.beginPath();
        ctx.ellipse(c[0] + Math.cos(a) * r * 0.6, c[1] + Math.sin(a) * r * 0.3, r * 0.55, r * 0.32, a, 0, Math.PI * 2);
        ctx.fill();
        ctx.strokeStyle = 'rgba(20,40,15,0.6)';
        ctx.lineWidth = 0.8;
        ctx.stroke();
      }
      ctx.fillStyle = css(base, 1.3);
      ctx.beginPath();
      ctx.arc(c[0] - 1, c[1] - 2, r * 0.38, 0, Math.PI * 2);
      ctx.fill();
    }
  }
  return result(sheet, fp, null);
}

function cylinder(sheet, x, y, r, z0, z1, side, top, staves = 0) {
  const ctx = sheet.ctx;
  const b = sheet.P([x, y, z0]);
  const t = sheet.P([x, y, z1]);
  const rx = Math.abs(projVec([r, -r, 0])[0]) / 2;
  const ry = rx / 2;
  ctx.save();
  ctx.beginPath();
  ctx.ellipse(b[0], b[1], rx, ry, 0, 0, Math.PI);
  ctx.lineTo(t[0] - rx, t[1]);
  ctx.ellipse(t[0], t[1], rx, ry, 0, Math.PI, 0, true);
  ctx.closePath();
  const g = ctx.createLinearGradient(b[0] - rx, 0, b[0] + rx, 0);
  g.addColorStop(0, css(side, 1.15));
  g.addColorStop(0.4, css(side, 1.0));
  g.addColorStop(1, css(side, 0.55));
  ctx.fillStyle = g;
  ctx.fill();
  ctx.clip();
  ctx.strokeStyle = 'rgba(30,18,8,0.5)';
  ctx.lineWidth = 0.8;
  for (let i = 1; i < staves; i += 1) {
    const sx = b[0] - rx + (2 * rx * i) / staves;
    ctx.beginPath();
    ctx.moveTo(sx, t[1] - ry);
    ctx.lineTo(sx, b[1] + ry);
    ctx.stroke();
  }
  ctx.restore();
  ctx.strokeStyle = 'rgba(25,15,8,0.7)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.ellipse(b[0], b[1], rx, ry, 0, 0, Math.PI);
  ctx.lineTo(t[0] - rx, t[1]);
  ctx.moveTo(b[0] + rx, b[1]);
  ctx.lineTo(t[0] + rx, t[1]);
  ctx.stroke();
  ctx.fillStyle = css(top, 1.05);
  ctx.beginPath();
  ctx.ellipse(t[0], t[1], rx, ry, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.stroke();
  return { b, t, rx, ry };
}

export function paintProp({ id, kind }) {
  const rng = rngFor(id);
  const sheet = new Sheet(
    [
      [-1.8, -1.8, 0],
      [1.8, 1.8, 0],
      [-1.8, 1.8, 2.2],
      [1.8, -1.8, 2.2],
    ],
    6,
  );
  const ctx = sheet.ctx;
  let fp = { minX: -0.4, minY: -0.4, maxX: 0.4, maxY: 0.4 };
  let shadowPts = null;
  if (kind === 'barrels') {
    for (const [x, y] of [
      [-0.35, -0.3],
      [0.35, -0.25],
      [0, 0.35],
    ]) {
      const c = cylinder(sheet, x, y, 0.3, 0, 0.85, jitter('#7a5634', rng, 0.08), '#8a6a44', 6);
      ctx.strokeStyle = '#3a3a3a';
      ctx.lineWidth = 1.6;
      for (const f of [0.25, 0.75]) {
        const yy = c.b[1] + (c.t[1] - c.b[1]) * f;
        ctx.beginPath();
        ctx.ellipse(c.b[0], yy, c.rx, c.ry, 0, 0, Math.PI);
        ctx.stroke();
      }
    }
    fp = { minX: -0.7, minY: -0.6, maxX: 0.7, maxY: 0.7 };
    shadowPts = circlePoints(0, 0, 0.7, 0.85, 10);
  } else if (kind === 'crates') {
    solidBox(
      sheet,
      rng,
      { x0: -0.7, y0: -0.6, x1: 0.05, y1: 0.15, zb: 0, zt: 0.75 },
      { kind: 'planks', base: '#9a7448', board: 0.18, vertical: false },
    );
    solidBox(
      sheet,
      rng,
      { x0: 0.1, y0: -0.3, x1: 0.75, y1: 0.35, zb: 0, zt: 0.65 },
      { kind: 'planks', base: '#8a6640', board: 0.16, vertical: false },
    );
    solidBox(
      sheet,
      rng,
      { x0: -0.55, y0: -0.45, x1: 0.0, y1: 0.1, zb: 0.75, zt: 1.25 },
      { kind: 'planks', base: '#a07c50', board: 0.15, vertical: false },
    );
    fp = { minX: -0.7, minY: -0.6, maxX: 0.75, maxY: 0.35 };
    shadowPts = [
      [-0.7, -0.6, 1.25],
      [0.75, 0.35, 0.65],
      [-0.7, 0.15, 1.25],
      [0.75, -0.3, 0.65],
    ];
  } else if (kind === 'sacks') {
    const spots = [
      [-0.4, -0.2, 0],
      [0.25, -0.3, 0],
      [-0.1, 0.3, 0],
      [-0.1, -0.2, 0.45],
    ];
    for (const [x, y, z] of spots) {
      const c = sheet.P([x, y, z]);
      const col = jitter('#c8b28a', rng, 0.08);
      const g = ctx.createRadialGradient(c[0] - 4, c[1] - 14, 2, c[0], c[1] - 8, 16);
      g.addColorStop(0, css(col, 1.15));
      g.addColorStop(1, css(col, 0.7));
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.ellipse(c[0], c[1] - 9, 11, 10, 0, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = 'rgba(40,28,14,0.8)';
      ctx.lineWidth = 1.1;
      ctx.stroke();
      ctx.fillStyle = css(col, 0.8);
      ctx.beginPath();
      ctx.ellipse(c[0] + 1, c[1] - 19, 4, 3, 0, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
    }
    fp = { minX: -0.65, minY: -0.55, maxX: 0.5, maxY: 0.55 };
    shadowPts = circlePoints(0, 0, 0.6, 0.6, 8);
  } else if (kind === 'logpile') {
    const logs = [];
    for (let row = 0; row < 3; row += 1) {
      for (let i = 0; i < 4 - row; i += 1) logs.push([-0.45 + i * 0.3 + row * 0.15, row * 0.26 + 0.14]);
    }
    for (const [y, z] of logs) {
      const a = sheet.P([-1.2, y, z]);
      const b = sheet.P([1.2, y, z]);
      const col = jitter('#6a4a2e', rng, 0.12);
      ctx.strokeStyle = css(col, 0.55);
      ctx.lineWidth = 9.5;
      ctx.lineCap = 'round';
      ctx.beginPath();
      ctx.moveTo(a[0], a[1]);
      ctx.lineTo(b[0], b[1]);
      ctx.stroke();
      ctx.strokeStyle = css(col, 1.0);
      ctx.lineWidth = 7.5;
      ctx.beginPath();
      ctx.moveTo(a[0], a[1] - 1);
      ctx.lineTo(b[0], b[1] - 1);
      ctx.stroke();
      ctx.fillStyle = '#c8a070';
      ctx.beginPath();
      ctx.ellipse(b[0], b[1], 4.6, 4.6, 0, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = 'rgba(90,60,30,0.8)';
      ctx.lineWidth = 0.8;
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(b[0], b[1], 2.2, 0, Math.PI * 2);
      ctx.stroke();
    }
    fp = { minX: -1.25, minY: -0.6, maxX: 1.25, maxY: 0.6 };
    shadowPts = [
      [-1.2, -0.6, 0.8],
      [1.2, 0.6, 0.8],
      [-1.2, 0.6, 0.8],
      [1.2, -0.6, 0.8],
    ];
  } else if (kind === 'planks') {
    for (let i = 0; i < 6; i += 1) {
      const z = i * 0.07;
      const off = (rng() - 0.5) * 0.1;
      solidBox(
        sheet,
        rng,
        { x0: -1.4 + off, y0: -0.45, x1: 1.4 + off, y1: 0.45, zb: z, zt: z + 0.06 },
        { kind: 'planks', base: '#b08858', board: 0.15, vertical: false },
      );
    }
    fp = { minX: -1.5, minY: -0.45, maxX: 1.5, maxY: 0.45 };
    shadowPts = [
      [-1.4, -0.45, 0.45],
      [1.4, 0.45, 0.45],
      [-1.4, 0.45, 0.45],
      [1.4, -0.45, 0.45],
    ];
  } else if (kind === 'stones') {
    for (let i = 0; i < 9; i += 1) {
      const x = (rng() - 0.5) * 1.2;
      const y = (rng() - 0.5) * 0.9;
      const z = i > 5 ? 0.25 : 0;
      solidBox(
        sheet,
        rng,
        { x0: x - 0.2, y0: y - 0.15, x1: x + 0.2, y1: y + 0.15, zb: z, zt: z + 0.25 },
        { kind: 'stone', base: '#a39d90', mortar: '#a39d90' },
      );
    }
    fp = { minX: -0.8, minY: -0.65, maxX: 0.8, maxY: 0.65 };
    shadowPts = circlePoints(0, 0, 0.7, 0.4, 8);
  } else if (kind === 'haystack') {
    const c = sheet.P([0, 0, 0]);
    const w = 1.1 * PX * 1.3;
    const h = 1.6 * PZ;
    ctx.fillStyle = '#b89a52';
    ctx.beginPath();
    ctx.moveTo(c[0] - w, c[1]);
    ctx.quadraticCurveTo(c[0] - w * 0.9, c[1] - h * 1.1, c[0], c[1] - h);
    ctx.quadraticCurveTo(c[0] + w * 0.9, c[1] - h * 1.1, c[0] + w, c[1]);
    ctx.quadraticCurveTo(c[0], c[1] + w * 0.35, c[0] - w, c[1]);
    ctx.fill();
    ctx.save();
    ctx.clip();
    for (let i = 0; i < 500; i += 1) {
      const x = c[0] + (rng() - 0.5) * w * 2;
      const y = c[1] - rng() * h * 1.1;
      ctx.strokeStyle = rng() < 0.5 ? 'rgba(230,205,130,0.6)' : 'rgba(120,90,40,0.5)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, y);
      ctx.lineTo(x + (rng() - 0.5) * 3, y + 4 + rng() * 4);
      ctx.stroke();
    }
    const g = ctx.createLinearGradient(c[0] - w, 0, c[0] + w, 0);
    g.addColorStop(0, 'rgba(255,230,170,0.15)');
    g.addColorStop(1, 'rgba(30,20,40,0.4)');
    ctx.fillStyle = g;
    ctx.fillRect(c[0] - w, c[1] - h * 1.2, w * 2, h * 1.5);
    ctx.restore();
    ctx.strokeStyle = 'rgba(60,40,15,0.8)';
    ctx.lineWidth = 1.2;
    ctx.stroke();
    fp = { minX: -0.85, minY: -0.85, maxX: 0.85, maxY: 0.85 };
    shadowPts = circlePoints(0, 0, 0.9, 1.4, 10);
  } else if (kind === 'anvil') {
    cylinder(sheet, 0, 0, 0.28, 0, 0.55, '#6a4a2e', '#8a6a44', 0);
    const c = sheet.P([0, 0, 0.55]);
    ctx.fillStyle = '#3a3c40';
    ctx.beginPath();
    ctx.moveTo(c[0] - 14, c[1] - 6);
    ctx.lineTo(c[0] + 12, c[1] - 6);
    ctx.lineTo(c[0] + 18, c[1] - 10);
    ctx.lineTo(c[0] + 8, c[1] - 12);
    ctx.lineTo(c[0] - 12, c[1] - 12);
    ctx.closePath();
    ctx.fill();
    ctx.fillStyle = '#5a5e64';
    ctx.fillRect(c[0] - 10, c[1] - 13, 20, 2);
    ctx.fillStyle = '#2a2c30';
    ctx.fillRect(c[0] - 6, c[1] - 6, 10, 6);
    fp = { minX: -0.35, minY: -0.35, maxX: 0.35, maxY: 0.35 };
    shadowPts = circlePoints(0, 0, 0.35, 0.8, 8);
  }
  void rgbToCss;
  return result(sheet, fp, shadowPts, { blur: 3, alpha: 0.65 });
}
