// Ox and wagon for caravans (PROTOTYPE ARTWORK).

import { rngFor } from '../../../sim/rng.js';
import { css } from './color.js';
import { Sheet, projVec } from './iso.js';
import { paintShadow, solidBox } from './buildings.js';

/**
 * Wagon along an axis; `front` is the end with the shafts (+1 or -1).
 * frame rotates the wheel spokes.
 */
export function paintWagon({ id, axis = 'x', front = 1, frame = 0, civColor = '#c9a227' }) {
  const rng = rngFor(`${id}`);
  const L = 3.0;
  const W = 1.5;
  const ex = axis === 'x' ? L / 2 : W / 2;
  const ey = axis === 'x' ? W / 2 : L / 2;
  const shaft = 1.6;
  const pts = [
    [-ex - (axis === 'x' ? shaft : 0) - 0.2, -ey - (axis === 'y' ? shaft : 0) - 0.2, 0],
    [ex + (axis === 'x' ? shaft : 0) + 0.2, ey + (axis === 'y' ? shaft : 0) + 0.2, 0],
    [-ex, ey, 2.9],
    [ex, -ey, 2.9],
  ];
  const sheet = new Sheet(pts, 8);
  const ctx = sheet.ctx;
  const wheelR = 0.48;
  const wheelZ = wheelR;
  const along = axis === 'x' ? [1, 0, 0] : [0, 1, 0];
  const side = axis === 'x' ? [0, 1, 0] : [1, 0, 0];
  const at = (a, s, z) => [along[0] * a + side[0] * s, along[1] * a + side[1] * s, z];
  const wheel = (a, s, near) => {
    const c = at(a, s, wheelZ);
    const n = 20;
    ctx.beginPath();
    for (let i = 0; i <= n; i += 1) {
      const t = (i / n) * Math.PI * 2;
      const p = sheet.P([
        c[0] + along[0] * Math.cos(t) * wheelR,
        c[1] + along[1] * Math.cos(t) * wheelR,
        c[2] + Math.sin(t) * wheelR,
      ]);
      if (i === 0) ctx.moveTo(p[0], p[1]);
      else ctx.lineTo(p[0], p[1]);
    }
    ctx.closePath();
    ctx.fillStyle = near ? '#6a4a2c' : '#3e2a18';
    ctx.fill();
    ctx.strokeStyle = 'rgba(20,12,6,0.95)';
    ctx.lineWidth = 3.2;
    ctx.stroke();
    ctx.strokeStyle = near ? '#4a4a4c' : '#2e2e30';
    ctx.lineWidth = 1.6;
    ctx.stroke();
    const hub = sheet.P(c);
    ctx.strokeStyle = near ? '#8a643a' : '#4a3420';
    ctx.lineWidth = 1.6;
    for (let k = 0; k < 6; k += 1) {
      const t = (k / 6) * Math.PI * 2 + (frame / 4) * (Math.PI / 3) * front;
      const p = sheet.P([
        c[0] + along[0] * Math.cos(t) * wheelR * 0.9,
        c[1] + along[1] * Math.cos(t) * wheelR * 0.9,
        c[2] + Math.sin(t) * wheelR * 0.9,
      ]);
      ctx.beginPath();
      ctx.moveTo(hub[0], hub[1]);
      ctx.lineTo(p[0], p[1]);
      ctx.stroke();
    }
    ctx.fillStyle = '#2a2a2c';
    ctx.beginPath();
    ctx.arc(hub[0], hub[1], 2.6, 0, Math.PI * 2);
    ctx.fill();
  };
  // Far wheels first.
  wheel(-L / 2 + 0.55, -W / 2 - 0.08, false);
  wheel(L / 2 - 0.55, -W / 2 - 0.08, false);
  // Shafts.
  for (const s of [-0.45, 0.45]) {
    const a = sheet.P(at(front * (L / 2 - 0.1), s, 0.8));
    const b = sheet.P(at(front * (L / 2 + shaft), s, 0.75));
    ctx.strokeStyle = 'rgba(20,12,6,0.95)';
    ctx.lineWidth = 4;
    ctx.beginPath();
    ctx.moveTo(a[0], a[1]);
    ctx.lineTo(b[0], b[1]);
    ctx.stroke();
    ctx.strokeStyle = '#8a643a';
    ctx.lineWidth = 2.2;
    ctx.stroke();
  }
  // Bed.
  const box =
    axis === 'x'
      ? { x0: -L / 2, y0: -W / 2, x1: L / 2, y1: W / 2, zb: 0.62, zt: 1.1 }
      : { x0: -W / 2, y0: -L / 2, x1: W / 2, y1: L / 2, zb: 0.62, zt: 1.1 };
  solidBox(
    sheet,
    rng,
    box,
    { kind: 'planks', base: '#8a6440', board: 0.16, vertical: false },
    { kind: 'planks', base: '#6a4a30', board: 0.2 },
  );
  // Cargo.
  const cargo = [
    [-0.8, -0.2, 'crate'],
    [0.1, 0.15, 'sack'],
    [0.75, -0.2, 'crate'],
    [-0.2, -0.35, 'sack'],
  ];
  for (const [a, s, kind] of cargo) {
    const c = at(a, s, 1.1);
    if (kind === 'crate') {
      solidBox(
        sheet,
        rng,
        { x0: c[0] - 0.3, y0: c[1] - 0.3, x1: c[0] + 0.3, y1: c[1] + 0.3, zb: 1.1, zt: 1.6 },
        { kind: 'planks', base: '#a07c50', board: 0.15, vertical: false },
      );
    } else {
      const p = sheet.P(c);
      ctx.fillStyle = '#cbb58c';
      ctx.beginPath();
      ctx.ellipse(p[0], p[1] - 8, 11, 9, 0, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = 'rgba(40,28,14,0.85)';
      ctx.lineWidth = 1;
      ctx.stroke();
    }
  }
  // Pennant pole with the caravan's colours.
  const pb = sheet.P(at(-front * (L / 2 - 0.15), W / 2 - 0.1, 1.1));
  const pt = sheet.P(at(-front * (L / 2 - 0.15), W / 2 - 0.1, 2.8));
  ctx.strokeStyle = '#3a2816';
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(pb[0], pb[1]);
  ctx.lineTo(pt[0], pt[1]);
  ctx.stroke();
  ctx.fillStyle = civColor;
  ctx.beginPath();
  ctx.moveTo(pt[0], pt[1]);
  ctx.lineTo(pt[0] + 18, pt[1] + 5 + Math.sin(frame) * 2);
  ctx.lineTo(pt[0], pt[1] + 12);
  ctx.closePath();
  ctx.fill();
  ctx.strokeStyle = 'rgba(0,0,0,0.5)';
  ctx.lineWidth = 1;
  ctx.stroke();
  // Near wheels last.
  wheel(-L / 2 + 0.55, W / 2 + 0.08, true);
  wheel(L / 2 - 0.55, W / 2 + 0.08, true);
  const footprint = { minX: -ex, minY: -ey, maxX: ex, maxY: ey };
  return {
    canvas: sheet.canvas,
    anchor: sheet.anchor,
    shadow: paintShadow(
      [
        [-ex, -ey, 1.6],
        [ex, ey, 1.6],
        [-ex, ey, 1.6],
        [ex, -ey, 1.6],
      ],
      footprint,
      { blur: 4, alpha: 0.7 },
    ),
    footprint,
    doors: [],
  };
}

/** Ox in one of two painted facings, 8-frame walk. Footprint 2.2 x 0.9 m. */
export function paintOx({ facing = 'front', frame = 0, walking = true }) {
  const len = 2.3;
  const sheet = new Sheet(
    [
      [-1.6, -1.6, 0],
      [1.6, 1.6, 0],
      [-1.6, 1.6, 2.2],
      [1.6, -1.6, 2.2],
    ],
    6,
  );
  const ctx = sheet.ctx;
  const ox = sheet.ox;
  const oy = sheet.oy;
  // Screen direction of travel: front = toward bottom-left (+y), back = toward top-left (-x).
  const dirV = facing === 'front' ? [0, 1, 0] : [-1, 0, 0];
  const d = projVec(dirV);
  const dl = Math.hypot(d[0], d[1]);
  const dx = d[0] / dl;
  const dy = d[1] / dl;
  const pxPerM = dl;
  const bodyZ = 1.0;
  const bz = projVec([0, 0, bodyZ])[1];
  const half = (len / 2) * pxPerM * 0.85;
  const cx = ox;
  const cy = oy + bz;
  const ph = (frame / 8) * Math.PI * 2;
  const leg = (along, sideOff, offset, far) => {
    const hx = cx + dx * along + sideOff;
    const hy = cy + dy * along + 8;
    const swing = walking ? Math.sin(ph + offset) * 6 : 0;
    const fx = hx + dx * swing;
    const fy = oy + dy * along + dy * swing + (far ? -3 : 2);
    ctx.lineCap = 'round';
    ctx.strokeStyle = 'rgba(22,14,8,0.95)';
    ctx.lineWidth = 7;
    ctx.beginPath();
    ctx.moveTo(hx, hy);
    ctx.lineTo(fx, fy);
    ctx.stroke();
    ctx.strokeStyle = far ? '#4e3622' : '#6e4e32';
    ctx.lineWidth = 4.8;
    ctx.stroke();
  };
  const body = '#7a5838';
  const headPos = [cx + dx * (half + 10), cy + dy * (half + 10) + 6];
  const drawHead = () => {
    ctx.fillStyle = css(body, 0.95);
    ctx.beginPath();
    ctx.ellipse(headPos[0], headPos[1], 10, 8, Math.atan2(dy, dx), 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = 'rgba(22,14,8,0.9)';
    ctx.lineWidth = 1.2;
    ctx.stroke();
    ctx.fillStyle = '#c8a888';
    ctx.beginPath();
    ctx.ellipse(headPos[0] + dx * 7, headPos[1] + dy * 7 + 2, 5, 4, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
    ctx.strokeStyle = '#e8dcc0';
    ctx.lineWidth = 2.4;
    ctx.beginPath();
    ctx.moveTo(headPos[0] - 6, headPos[1] - 6);
    ctx.quadraticCurveTo(headPos[0] - 12, headPos[1] - 14, headPos[0] - 6, headPos[1] - 17);
    ctx.moveTo(headPos[0] + 4, headPos[1] - 7);
    ctx.quadraticCurveTo(headPos[0] + 10, headPos[1] - 15, headPos[0] + 4, headPos[1] - 18);
    ctx.stroke();
  };
  if (facing === 'back') drawHead();
  leg(half * 0.6, 5, 0, true);
  leg(-half * 0.6, 5, Math.PI, true);
  // Body.
  const g = ctx.createLinearGradient(cx - half, cy - 16, cx + half, cy + 16);
  g.addColorStop(0, css(body, 1.25));
  g.addColorStop(1, css(body, 0.7));
  ctx.fillStyle = g;
  ctx.beginPath();
  ctx.ellipse(cx, cy, half, 15, Math.atan2(dy, dx), 0, Math.PI * 2);
  ctx.fill();
  ctx.strokeStyle = 'rgba(22,14,8,0.9)';
  ctx.lineWidth = 1.3;
  ctx.stroke();
  // Hump at the shoulders and a yoke.
  ctx.fillStyle = css(body, 1.05);
  ctx.beginPath();
  ctx.ellipse(cx + dx * half * 0.55, cy + dy * half * 0.55 - 7, 11, 9, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.stroke();
  ctx.strokeStyle = '#4a3420';
  ctx.lineWidth = 3.5;
  ctx.beginPath();
  ctx.moveTo(cx + dx * half * 0.8 - 9, cy + dy * half * 0.8 - 10);
  ctx.lineTo(cx + dx * half * 0.8 + 9, cy + dy * half * 0.8 - 6);
  ctx.stroke();
  // Tail.
  ctx.strokeStyle = css(body, 0.6);
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(cx - dx * half, cy - dy * half - 4);
  ctx.quadraticCurveTo(cx - dx * (half + 6), cy - dy * (half + 6) + 6, cx - dx * (half + 4), cy - dy * (half + 4) + 16);
  ctx.stroke();
  leg(half * 0.6, -5, Math.PI, false);
  leg(-half * 0.6, -5, 0, false);
  if (facing === 'front') drawHead();
  const footprint = { minX: -0.45, minY: -1.1, maxX: 0.45, maxY: 1.1 };
  const fpRot = facing === 'front' ? footprint : { minX: -1.1, minY: -0.45, maxX: 1.1, maxY: 0.45 };
  return {
    canvas: sheet.canvas,
    anchor: sheet.anchor,
    shadow: paintShadow(
      [
        [fpRot.minX, fpRot.minY, 1.4],
        [fpRot.maxX, fpRot.maxY, 1.4],
        [fpRot.minX, fpRot.maxY, 1.4],
        [fpRot.maxX, fpRot.minY, 1.4],
      ],
      fpRot,
      { blur: 4, alpha: 0.6 },
    ),
    footprint: fpRot,
    doors: [],
  };
}
