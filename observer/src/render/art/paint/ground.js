// Ground painting (PROTOTYPE ARTWORK).
//
// Step 1 builds a colour field on the ground plane (metres) from features:
// grass, worn dirt roads with ruts, worn patches, tilled fields. Step 2 bakes
// screen-space tiles from that field and adds upright details (grass tufts,
// flowers, pebbles) that must stay vertical on screen.

import { K, project, unproject } from '../../../world/coords.js';
import { fbm, hash2 } from '../../../sim/rng.js';
import { hexToRgb, mix } from './color.js';

const GRASS_DARK = hexToRgb('#4f6a2c');
const GRASS_LIGHT = hexToRgb('#7f9a42');
const GRASS_DRY = hexToRgb('#a19a5a');
const DIRT_DARK = hexToRgb('#7a6044');
const DIRT_LIGHT = hexToRgb('#a68c64');
const TILLED = hexToRgb('#6b4f35');

function clamp01(v) {
  return v < 0 ? 0 : v > 1 ? 1 : v;
}

function segDistance(px, py, ax, ay, bx, by) {
  const dx = bx - ax;
  const dy = by - ay;
  const l2 = dx * dx + dy * dy || 1;
  const t = clamp01(((px - ax) * dx + (py - ay) * dy) / l2);
  const cx = ax + dx * t;
  const cy = ay + dy * t;
  // Signed lateral offset from the centre line (for ruts).
  const lat = ((px - ax) * dy - (py - ay) * dx) / Math.sqrt(l2);
  return { d: Math.hypot(px - cx, py - cy), lat, t };
}

/**
 * features: { roads: [{a:[x,y], b:[x,y], width, ruts}], worn: [{x,y,r,strength}],
 *             fields: [{x0,y0,x1,y1,rowSpacing}] }
 */
export function buildGroundField(features, bounds, res = 4, seed = 7) {
  const w = Math.ceil((bounds.x1 - bounds.x0) * res) + 1;
  const h = Math.ceil((bounds.y1 - bounds.y0) * res) + 1;
  const color = new Float32Array(w * h * 3);
  const dirt = new Float32Array(w * h);
  const field = new Float32Array(w * h);
  for (let j = 0; j < h; j += 1) {
    const y = bounds.y0 + j / res;
    for (let i = 0; i < w; i += 1) {
      const x = bounds.x0 + i / res;
      const n1 = fbm(x * 0.045, y * 0.045, seed, 3);
      const n2 = fbm(x * 0.2, y * 0.2, seed + 3, 2);
      const dry = clamp01((fbm(x * 0.028 + 40, y * 0.028, seed + 9, 3) - 0.56) * 6);
      let c = mix(GRASS_DARK, GRASS_LIGHT, clamp01(n1 * 1.1 + (n2 - 0.5) * 0.5));
      c = mix(c, GRASS_DRY, dry * 0.55);
      const edge = (fbm(x * 0.4, y * 0.4, seed + 5, 2) - 0.5) * 0.9;
      let m = 0;
      let rut = 1;
      let centreGrass = 0;
      for (const r of features.roads) {
        const s = segDistance(x, y, r.a[0], r.a[1], r.b[0], r.b[1]);
        const half = r.width / 2;
        if (s.d > half + 1.2) continue;
        const k = clamp01((half + edge - s.d) / 0.55 + 0.5);
        if (k > m) m = k;
        if (r.ruts && k > 0.5) {
          for (const off of [-0.8, 0.8]) {
            const q = Math.abs(s.lat - off);
            if (q < 0.22) rut = Math.min(rut, 0.78 + q * 0.6);
          }
          if (Math.abs(s.lat) < 0.28) centreGrass = Math.max(centreGrass, 0.3 * (1 - Math.abs(s.lat) / 0.28));
        }
      }
      for (const p of features.worn) {
        const d = Math.hypot(x - p.x, y - p.y);
        if (d > p.r + 2) continue;
        const k = clamp01((p.r + edge * 1.6 - d) / 1.6) * (p.strength ?? 1);
        if (k > m) m = k;
      }
      let f = 0;
      let tilled = TILLED;
      for (const fl of features.fields) {
        const inside = Math.min(x - fl.x0, fl.x1 - x, y - fl.y0, fl.y1 - y);
        const k = clamp01((inside + edge * 0.5) / 0.5);
        if (k > f) {
          f = k;
          const ph = ((y - (fl.rowOrigin ?? fl.y0)) / fl.rowSpacing) * Math.PI * 2;
          const fur = 0.5 + 0.5 * Math.cos(ph);
          tilled = mix(TILLED, [TILLED[0] * 1.25, TILLED[1] * 1.22, TILLED[2] * 1.15], fur);
        }
      }
      const dn = fbm(x * 0.25, y * 0.25, seed + 11, 2);
      let dc = mix(DIRT_DARK, DIRT_LIGHT, dn);
      dc = [dc[0] * rut, dc[1] * rut, dc[2] * rut];
      dc = mix(dc, c, centreGrass);
      c = mix(c, dc, m);
      c = mix(c, tilled, f);
      const idx = j * w + i;
      color[idx * 3] = c[0];
      color[idx * 3 + 1] = c[1];
      color[idx * 3 + 2] = c[2];
      dirt[idx] = m;
      field[idx] = f;
    }
  }
  return { w, h, res, x0: bounds.x0, y0: bounds.y0, color, dirt, field };
}

function sampleField(F, x, y, out) {
  let u = (x - F.x0) * F.res;
  let v = (y - F.y0) * F.res;
  u = Math.max(0, Math.min(F.w - 1.001, u));
  v = Math.max(0, Math.min(F.h - 1.001, v));
  const i = Math.floor(u);
  const j = Math.floor(v);
  const fu = u - i;
  const fv = v - j;
  const i00 = (j * F.w + i) * 3;
  const i10 = i00 + 3;
  const i01 = i00 + F.w * 3;
  const i11 = i01 + 3;
  for (let c = 0; c < 3; c += 1) {
    const a = F.color[i00 + c] + (F.color[i10 + c] - F.color[i00 + c]) * fu;
    const b = F.color[i01 + c] + (F.color[i11 + c] - F.color[i01 + c]) * fu;
    out[c] = a + (b - a) * fv;
  }
  const k = j * F.w + i;
  out[3] = F.dirt[k];
  out[4] = F.field[k];
  return out;
}

/** Bake one screen-space tile (zoom-1 pixels) starting at (sx0, sy0). */
export function paintGroundTile(F, sx0, sy0, w, h, seed = 7) {
  const canvas = document.createElement('canvas');
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext('2d');
  const img = ctx.createImageData(w, h);
  const s = [0, 0, 0, 0, 0];
  for (let py = 0; py < h; py += 1) {
    for (let px = 0; px < w; px += 1) {
      const g = unproject(sx0 + px + 0.5, sy0 + py + 0.5);
      sampleField(F, g.x, g.y, s);
      const n = (hash2(sx0 + px, sy0 + py, seed) - 0.5) * 0.12;
      const n2 = (hash2(Math.floor(g.x * 3), Math.floor(g.y * 3), seed + 1) - 0.5) * 0.08;
      const f = 1 + n + n2 * (1 - s[4]);
      const o = (py * w + px) * 4;
      img.data[o] = s[0] * f;
      img.data[o + 1] = s[1] * f;
      img.data[o + 2] = s[2] * f;
      img.data[o + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0);
  // Upright details: iterate a ground grid covering this tile.
  const corners = [unproject(sx0, sy0), unproject(sx0 + w, sy0), unproject(sx0, sy0 + h), unproject(sx0 + w, sy0 + h)];
  const gx0 = Math.floor(Math.min(...corners.map((c) => c.x)) / 0.55) - 1;
  const gx1 = Math.ceil(Math.max(...corners.map((c) => c.x)) / 0.55) + 1;
  const gy0 = Math.floor(Math.min(...corners.map((c) => c.y)) / 0.55) - 1;
  const gy1 = Math.ceil(Math.max(...corners.map((c) => c.y)) / 0.55) + 1;
  for (let j = gy0; j <= gy1; j += 1) {
    for (let i = gx0; i <= gx1; i += 1) {
      const r = hash2(i, j, seed + 21);
      const x = (i + hash2(i, j, seed + 22)) * 0.55;
      const y = (j + hash2(i, j, seed + 23)) * 0.55;
      const p = project(x, y);
      const lx = p.x - sx0;
      const ly = p.y - sy0;
      if (lx < -6 || ly < -8 || lx > w + 6 || ly > h + 2) continue;
      sampleField(F, x, y, s);
      if (s[3] < 0.15 && s[4] < 0.1) {
        if (r < 0.42) tuft(ctx, lx, ly, s, r);
        else if (r < 0.445) flower(ctx, lx, ly, r);
      } else if (s[3] > 0.65 && r < 0.18) {
        pebble(ctx, lx, ly, r);
      } else if (s[4] > 0.6 && r < 0.08) {
        clod(ctx, lx, ly);
      }
    }
  }
  return canvas;
}

function rgb(c, f, a = 1) {
  return `rgba(${Math.round(c[0] * f)},${Math.round(c[1] * f)},${Math.round(c[2] * f)},${a})`;
}

function tuft(ctx, x, y, s, r) {
  const blades = 3 + Math.floor(r * 10);
  for (let b = 0; b < blades; b += 1) {
    const dx = (hash2(b, Math.floor(r * 1e6), 3) - 0.5) * 4;
    const hgt = 2 + hash2(b, Math.floor(r * 1e6), 4) * 4;
    ctx.strokeStyle = b % 2 ? rgb(s, 1.3, 0.85) : rgb(s, 0.7, 0.8);
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x + dx, y);
    ctx.lineTo(x + dx + (hash2(b, 9, 9) - 0.5) * 2, y - hgt);
    ctx.stroke();
  }
}

function flower(ctx, x, y, r) {
  const colors = ['#f4f0e0', '#f0d860', '#c8a0e0', '#e8a0a0'];
  ctx.fillStyle = colors[Math.floor(r * 1000) % colors.length];
  ctx.fillRect(x, y - 2, 1.6, 1.6);
  ctx.fillRect(x + 2, y - 1, 1.4, 1.4);
}

function pebble(ctx, x, y, r) {
  ctx.fillStyle = r < 0.09 ? 'rgba(150,140,125,0.9)' : 'rgba(110,95,75,0.8)';
  ctx.beginPath();
  ctx.ellipse(x, y, 1.3 + r * 4, 0.9 + r * 2, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = 'rgba(220,210,190,0.5)';
  ctx.fillRect(x - 0.6, y - 0.8, 1, 0.6);
}

function clod(ctx, x, y) {
  ctx.fillStyle = 'rgba(70,50,32,0.7)';
  ctx.beginPath();
  ctx.ellipse(x, y, 2, 1.1, 0, 0, Math.PI * 2);
  ctx.fill();
}

export const GROUND_TILE = { w: 1024, h: 512 };
void K;
