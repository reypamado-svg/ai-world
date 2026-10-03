// Procedural isometric buildings (PROTOTYPE ARTWORK).
//
// A building spec is plain data: footprint, walls, roof, openings. The
// painter returns a sprite canvas, its ground anchor, a separate cast-shadow
// canvas, the ground footprint and door points. The renderer only ever sees
// that contract, so a licensed sprite can replace any one painter later.

import { rngFor } from '../../../sim/rng.js';
import { css, jitter, rgbToCss } from './color.js';
import { Sheet, SHADOW, lightFace, outline, projVec, wallOcclusion } from './iso.js';
import { door, paintMaterial, planks, window_ } from './materials.js';

const N_PX = [1, 0, 0];
const N_PY = [0, 1, 0];

function convexHull(points) {
  const pts = points.map((p) => [p[0], p[1]]).sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const crossZ = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const lower = [];
  for (const p of pts) {
    while (lower.length >= 2 && crossZ(lower[lower.length - 2], lower[lower.length - 1], p) <= 0) lower.pop();
    lower.push(p);
  }
  const upper = [];
  for (const p of pts.slice().reverse()) {
    while (upper.length >= 2 && crossZ(upper[upper.length - 2], upper[upper.length - 1], p) <= 0) upper.pop();
    upper.push(p);
  }
  upper.pop();
  lower.pop();
  return lower.concat(upper);
}

/** Paint a cast shadow for a set of 3D points onto its own canvas. */
export function paintShadow(points3d, footprint, { blur = 5, alpha = 1 } = {}) {
  const ground = points3d.map(([x, y, z]) => [x + z * SHADOW.x, y + z * SHADOW.y, 0]);
  const fp = [
    [footprint.minX, footprint.minY, 0],
    [footprint.maxX, footprint.minY, 0],
    [footprint.maxX, footprint.maxY, 0],
    [footprint.minX, footprint.maxY, 0],
  ];
  const hull = convexHull(ground.concat(fp)).map(([x, y]) => [x, y, 0]);
  const sheet = new Sheet(hull, blur * 3 + 4);
  const ctx = sheet.ctx;
  ctx.filter = `blur(${blur}px)`;
  sheet.path(hull);
  ctx.fillStyle = `rgba(20,24,40,${0.85 * alpha})`;
  ctx.fill();
  // Contact shadow hugging the footprint.
  ctx.filter = `blur(${Math.max(2, blur * 0.6)}px)`;
  const grow = 0.25;
  sheet.path([
    [footprint.minX - grow, footprint.minY - grow, 0],
    [footprint.maxX + grow, footprint.minY - grow, 0],
    [footprint.maxX + grow, footprint.maxY + grow, 0],
    [footprint.minX - grow, footprint.maxY + grow, 0],
  ]);
  ctx.fillStyle = `rgba(10,10,20,${0.6 * alpha})`;
  ctx.fill();
  ctx.filter = 'none';
  return { canvas: sheet.canvas, anchor: sheet.anchor };
}

function wallFace(sheet, rng, face, box, mat, details, opts = {}) {
  const { x0, y0, x1, y1, zb, zt } = box;
  const h = zt - zb;
  let O;
  let U;
  let w;
  let n;
  if (face === '+y') {
    O = [x0, y1, zb];
    U = [1, 0, 0];
    w = x1 - x0;
    n = N_PY;
  } else {
    O = [x1, y1, zb];
    U = [0, -1, 0];
    w = y1 - y0;
    n = N_PX;
  }
  const ctx = sheet.ctx;
  sheet.face(O, U, [0, 0, 1]);
  ctx.save();
  ctx.beginPath();
  ctx.rect(0, 0, w, h);
  ctx.clip();
  paintMaterial(ctx, w, h, rng, mat);
  for (const d of details.filter((d) => d.face === face)) {
    if (d.type === 'door') door(ctx, d.u, d.w, d.h, rng, { wood: d.wood, arch: d.arch });
    else if (d.type === 'window') window_(ctx, d.u, d.v, d.w, d.h, rng, { shutters: d.shutters ?? '#5e6b48' });
    else if (d.type === 'banner') banner(ctx, d.u, h - 0.35, d.w ?? 0.62, d.h ?? 1.25, opts.civColor);
    else if (d.type === 'opening') {
      // Open bay: dark interior seen through the wall.
      const g = ctx.createLinearGradient(0, d.h, 0, 0);
      g.addColorStop(0, '#120c08');
      g.addColorStop(1, '#3a2a1c');
      ctx.fillStyle = g;
      ctx.fillRect(d.u, 0, d.w, d.h);
    }
  }
  wallOcclusion(ctx, w, h, { eave: opts.eave ?? 0.7 });
  lightFace(ctx, w, h, n);
  ctx.restore();
  sheet.reset();
  outline(sheet, [O, add(O, scale(U, w)), add(add(O, scale(U, w)), [0, 0, h]), add(O, [0, 0, h])]);
}

function banner(ctx, u, top, w, h, civColor = '#2f5d9a') {
  ctx.fillStyle = '#3a2a1a';
  ctx.fillRect(u - 0.1, top - 0.06, w + 0.2, 0.08);
  ctx.fillStyle = civColor;
  ctx.beginPath();
  ctx.moveTo(u, top);
  ctx.lineTo(u + w, top);
  ctx.lineTo(u + w, top - h);
  ctx.lineTo(u + w / 2, top - h + 0.22);
  ctx.lineTo(u, top - h);
  ctx.closePath();
  ctx.fill();
  ctx.strokeStyle = '#d8b860';
  ctx.lineWidth = 0.04;
  ctx.stroke();
  ctx.fillStyle = '#e8d080';
  ctx.beginPath();
  ctx.arc(u + w / 2, top - h * 0.42, w * 0.2, 0, Math.PI * 2);
  ctx.fill();
  const g = ctx.createLinearGradient(u, 0, u + w, 0);
  g.addColorStop(0, 'rgba(255,255,255,0.12)');
  g.addColorStop(0.5, 'rgba(0,0,0,0)');
  g.addColorStop(1, 'rgba(0,0,0,0.25)');
  ctx.fillStyle = g;
  ctx.fillRect(u, top - h, w, h);
}

function add(a, b) {
  return [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
}

function scale(a, s) {
  return [a[0] * s, a[1] * s, a[2] * s];
}

function len(a) {
  return Math.hypot(a[0], a[1], a[2]);
}

function gableTriangle(sheet, rng, axis, box, rise, mat) {
  const { x0, y0, x1, y1, zb, zt } = box;
  const h = zt - zb;
  const ctx = sheet.ctx;
  let O;
  let U;
  let w;
  let n;
  if (axis === 'x') {
    O = [x1, y1, zb];
    U = [0, -1, 0];
    w = y1 - y0;
    n = N_PX;
  } else {
    O = [x0, y1, zb];
    U = [1, 0, 0];
    w = x1 - x0;
    n = N_PY;
  }
  sheet.face(O, U, [0, 0, 1]);
  ctx.save();
  ctx.beginPath();
  ctx.moveTo(0, h - 0.01);
  ctx.lineTo(w, h - 0.01);
  ctx.lineTo(w / 2, h + rise);
  ctx.closePath();
  ctx.clip();
  ctx.translate(0, h);
  paintMaterial(ctx, w, rise, rng, mat);
  wallOcclusion(ctx, w, rise, { foot: 0.3, eave: 0 });
  lightFace(ctx, w, rise, n);
  ctx.restore();
  sheet.reset();
}

function roofPlane(sheet, rng, O, U, uLen, Vvec, mat, normal) {
  const vLen = len(Vvec);
  const V = scale(Vvec, 1 / vLen);
  const ctx = sheet.ctx;
  sheet.face(O, U, V);
  ctx.save();
  ctx.beginPath();
  ctx.rect(0, 0, uLen, vLen);
  ctx.clip();
  paintMaterial(ctx, uLen, vLen, rng, mat);
  // Soft darkening toward the eave edge for depth.
  const g = ctx.createLinearGradient(0, 0, 0, 0.6);
  g.addColorStop(0, 'rgba(40,25,10,0.3)');
  g.addColorStop(1, 'rgba(40,25,10,0)');
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, uLen, 0.6);
  lightFace(ctx, uLen, vLen, normal);
  ctx.restore();
  sheet.reset();
  outline(sheet, [O, add(O, scale(U, uLen)), add(add(O, scale(U, uLen)), Vvec), add(O, Vvec)], 0.6);
}

function edgeBand(sheet, points, color) {
  sheet.reset();
  sheet.path(points);
  sheet.ctx.fillStyle = color;
  sheet.ctx.fill();
  sheet.ctx.strokeStyle = 'rgba(25,15,8,0.6)';
  sheet.ctx.lineWidth = 1;
  sheet.ctx.stroke();
}

function roofEdgeColor(mat) {
  switch (mat.kind) {
    case 'thatch':
      return '#7a5a32';
    case 'tiles':
      return '#7a3a24';
    default:
      return '#3e3024';
  }
}

/** Gable roof over a box. Returns the 3D points it covers. */
function gableRoof(sheet, rng, box, roof) {
  const { x0, y0, x1, y1, zt } = box;
  const { axis, rise, overhang: o, mat } = roof;
  const th = mat.kind === 'thatch' ? 0.3 : 0.14;
  const edge = roofEdgeColor(mat);
  if (axis === 'x') {
    const hd = (y1 - y0) / 2;
    const yc = (y0 + y1) / 2;
    const tanA = rise / hd;
    const eaveZ = zt - o * tanA;
    const ridgeZ = zt + rise;
    const uLen = x1 - x0 + 2 * o;
    const slopeBack = [0, hd + o, ridgeZ - eaveZ];
    const slopeFront = [0, -(hd + o), ridgeZ - eaveZ];
    const sinA = Math.sin(Math.atan(tanA));
    const cosA = Math.cos(Math.atan(tanA));
    roofPlane(sheet, rng, [x0 - o, y0 - o, eaveZ], [1, 0, 0], uLen, slopeBack, mat, [0, -sinA, cosA]);
    roofPlane(sheet, rng, [x0 - o, y1 + o, eaveZ], [1, 0, 0], uLen, slopeFront, mat, [0, sinA, cosA]);
    const xe = x1 + o;
    edgeBand(
      sheet,
      [
        [xe, y1 + o, eaveZ],
        [xe, yc, ridgeZ],
        [xe, yc, ridgeZ - th],
        [xe, y1 + o, eaveZ - th],
      ],
      css(edge, 0.8),
    );
    edgeBand(
      sheet,
      [
        [xe, y0 - o, eaveZ],
        [xe, yc, ridgeZ],
        [xe, yc, ridgeZ - th],
        [xe, y0 - o, eaveZ - th],
      ],
      css(edge, 0.65),
    );
    edgeBand(
      sheet,
      [
        [x0 - o, y1 + o, eaveZ],
        [xe, y1 + o, eaveZ],
        [xe, y1 + o, eaveZ - th],
        [x0 - o, y1 + o, eaveZ - th],
      ],
      css(edge, 1),
    );
    return [
      [x0 - o, y0 - o, eaveZ - th],
      [xe, y1 + o, eaveZ - th],
      [x0 - o, yc, ridgeZ],
      [xe, yc, ridgeZ],
      [x0 - o, y1 + o, eaveZ - th],
      [xe, y0 - o, eaveZ - th],
    ];
  }
  const hw = (x1 - x0) / 2;
  const xc = (x0 + x1) / 2;
  const tanA = rise / hw;
  const eaveZ = zt - o * tanA;
  const ridgeZ = zt + rise;
  const uLen = y1 - y0 + 2 * o;
  const sinA = Math.sin(Math.atan(tanA));
  const cosA = Math.cos(Math.atan(tanA));
  roofPlane(sheet, rng, [x0 - o, y0 - o, eaveZ], [0, 1, 0], uLen, [hw + o, 0, ridgeZ - eaveZ], mat, [-sinA, 0, cosA]);
  roofPlane(sheet, rng, [x1 + o, y0 - o, eaveZ], [0, 1, 0], uLen, [-(hw + o), 0, ridgeZ - eaveZ], mat, [sinA, 0, cosA]);
  const ye = y1 + o;
  edgeBand(
    sheet,
    [
      [x0 - o, ye, eaveZ],
      [xc, ye, ridgeZ],
      [xc, ye, ridgeZ - th],
      [x0 - o, ye, eaveZ - th],
    ],
    css(edge, 1.05),
  );
  edgeBand(
    sheet,
    [
      [x1 + o, ye, eaveZ],
      [xc, ye, ridgeZ],
      [xc, ye, ridgeZ - th],
      [x1 + o, ye, eaveZ - th],
    ],
    css(edge, 0.85),
  );
  edgeBand(
    sheet,
    [
      [x1 + o, y0 - o, eaveZ],
      [x1 + o, ye, eaveZ],
      [x1 + o, ye, eaveZ - th],
      [x1 + o, y0 - o, eaveZ - th],
    ],
    css(edge, 0.7),
  );
  return [
    [x0 - o, y0 - o, eaveZ - th],
    [x1 + o, ye, eaveZ - th],
    [xc, y0 - o, ridgeZ],
    [xc, ye, ridgeZ],
    [x1 + o, y0 - o, eaveZ - th],
    [x0 - o, ye, eaveZ - th],
  ];
}

function roofHeightAt(box, roof, x, y) {
  const { x0, y0, x1, y1, zt } = box;
  if (roof.axis === 'x') {
    const hd = (y1 - y0) / 2;
    return zt + roof.rise * (1 - Math.abs(y - (y0 + y1) / 2) / hd);
  }
  const hw = (x1 - x0) / 2;
  return zt + roof.rise * (1 - Math.abs(x - (x0 + x1) / 2) / hw);
}

/** A plain box with visible +x, +y and top faces. */
export function solidBox(sheet, rng, box, mat, topMat = mat) {
  const { x0, y0, x1, y1, zb, zt } = box;
  wallFace(sheet, rng, '+x', box, mat, [], { eave: 0 });
  wallFace(sheet, rng, '+y', box, mat, [], { eave: 0 });
  const ctx = sheet.ctx;
  sheet.face([x0, y0, zt], [1, 0, 0], [0, 1, 0]);
  ctx.save();
  ctx.beginPath();
  ctx.rect(0, 0, x1 - x0, y1 - y0);
  ctx.clip();
  paintMaterial(ctx, x1 - x0, y1 - y0, rng, topMat);
  lightFace(ctx, x1 - x0, y1 - y0, [0, 0, 1]);
  ctx.restore();
  sheet.reset();
  outline(
    sheet,
    [
      [x0, y0, zt],
      [x1, y0, zt],
      [x1, y1, zt],
      [x0, y1, zt],
    ],
    0.5,
  );
  void zb;
}

function chimney(sheet, rng, box, roof, c) {
  const base = roofHeightAt(box, roof, c.x, c.y) - 0.3;
  solidBox(
    sheet,
    rng,
    { x0: c.x - c.s / 2, y0: c.y - c.s / 2, x1: c.x + c.s / 2, y1: c.y + c.s / 2, zb: base, zt: base + c.h },
    { kind: 'stone', base: '#8a8478' },
    { kind: 'flat', color: '#2a2420' },
  );
}

/**
 * Paint a house-like building.
 * spec: { id, w, d, wallH, plinth?, walls, gable?, roof, details[], chimney?, civColor }
 */
export function paintBuilding(spec) {
  const rng = rngFor(spec.id);
  const x0 = -spec.w / 2;
  const x1 = spec.w / 2;
  const y0 = -spec.d / 2;
  const y1 = spec.d / 2;
  const zb = spec.plinth ? spec.plinth.h : 0;
  const zt = zb + spec.wallH;
  const roof = spec.roof;
  const o = roof.overhang;
  const ridge = zt + roof.rise;
  const extentPts = [
    [x0 - o, y0 - o, 0],
    [x1 + o, y1 + o, 0],
    [x0 - o, y1 + o, 0],
    [x1 + o, y0 - o, 0],
    [x0 - o, y0 - o, ridge + 2],
    [x1 + o, y1 + o, ridge + 2],
    [x0 - o, y1 + o, ridge + 2],
    [x1 + o, y0 - o, ridge + 2],
  ];
  const sheet = new Sheet(extentPts, 8);
  const box = { x0, y0, x1, y1, zb, zt };
  const opts = { civColor: spec.civColor };
  if (spec.plinth) {
    const pb = { x0: x0 - 0.06, y0: y0 - 0.06, x1: x1 + 0.06, y1: y1 + 0.06, zb: 0, zt: zb };
    wallFace(sheet, rng, '+x', pb, spec.plinth.mat, [], { eave: 0 });
    wallFace(sheet, rng, '+y', pb, spec.plinth.mat, [], { eave: 0 });
  }
  const details = spec.details ?? [];
  wallFace(sheet, rng, '+x', box, spec.walls, details, opts);
  wallFace(sheet, rng, '+y', box, spec.walls, details, opts);
  gableTriangle(sheet, rng, roof.axis, box, roof.rise, spec.gable ?? spec.walls);
  const roofPts = gableRoof(sheet, rng, box, roof);
  if (spec.chimney) chimney(sheet, rng, box, roof, spec.chimney);
  if (spec.ridgePennant) pennant(sheet, roof, box, spec.civColor);
  const footprint = { minX: x0, minY: y0, maxX: x1, maxY: y1 };
  const shadowPts = roofPts.concat([
    [x0, y0, zt],
    [x1, y0, zt],
    [x0, y1, zt],
    [x1, y1, zt],
  ]);
  return {
    canvas: sheet.canvas,
    anchor: sheet.anchor,
    shadow: paintShadow(shadowPts, footprint),
    footprint,
    doors: doorPoints(details, box),
    height: ridge,
  };
}

function pennant(sheet, roof, box, civColor) {
  const { x0, y0, x1, y1, zt } = box;
  const top = zt + roof.rise;
  const base =
    roof.axis === 'x' ? [x1 + roof.overhang * 0.5, (y0 + y1) / 2, top] : [(x0 + x1) / 2, y1 + roof.overhang * 0.5, top];
  const ctx = sheet.ctx;
  const [bx, by] = sheet.P(base);
  const [tx, ty] = sheet.P([base[0], base[1], top + 1.9]);
  ctx.strokeStyle = '#3a2816';
  ctx.lineWidth = 2.4;
  ctx.beginPath();
  ctx.moveTo(bx, by);
  ctx.lineTo(tx, ty);
  ctx.stroke();
  ctx.fillStyle = civColor;
  ctx.beginPath();
  ctx.moveTo(tx, ty + 1);
  ctx.quadraticCurveTo(tx + 14, ty + 4, tx + 26, ty + 9);
  ctx.lineTo(tx, ty + 17);
  ctx.closePath();
  ctx.fill();
  ctx.strokeStyle = 'rgba(0,0,0,0.4)';
  ctx.lineWidth = 1;
  ctx.stroke();
}

function doorPoints(details, box) {
  const pts = [];
  for (const d of details) {
    if (d.type !== 'door') continue;
    const mid = d.u + d.w / 2;
    if (d.face === '+y') pts.push({ x: box.x0 + mid, y: box.y1 + 0.4, face: '+y' });
    else if (d.face === '+x') pts.push({ x: box.x1 + 0.4, y: box.y1 - mid, face: '+x' });
  }
  return pts;
}

/** Open-sided timber frame under construction. */
export function paintConstruction(spec) {
  const rng = rngFor(spec.id);
  const x0 = -spec.w / 2;
  const x1 = spec.w / 2;
  const y0 = -spec.d / 2;
  const y1 = spec.d / 2;
  const h = spec.wallH;
  const sheet = new Sheet(
    [
      [x0 - 0.5, y0 - 0.5, 0],
      [x1 + 0.5, y1 + 0.5, 0],
      [x0 - 0.5, y1 + 0.5, h + 2.6],
      [x1 + 0.5, y0 - 0.5, h + 2.6],
    ],
    8,
  );
  const wood = { kind: 'planks', base: '#a07a4c', board: 0.2 };
  const post = (x, y, z0, z1) =>
    solidBox(sheet, rng, { x0: x - 0.09, y0: y - 0.09, x1: x + 0.09, y1: y + 0.09, zb: z0, zt: z1 }, wood);
  // Floor deck.
  solidBox(
    sheet,
    rng,
    { x0, y0, x1, y1, zb: 0, zt: 0.25 },
    { kind: 'stone', base: '#8f897c' },
    { kind: 'planks', base: '#b28a58', board: 0.22, vertical: false },
  );
  // Back walls partly planked, seen from inside: draw as faces at x0 and y0.
  const ctx = sheet.ctx;
  const inner = (O, U, w, hh) => {
    sheet.face(O, U, [0, 0, 1]);
    ctx.save();
    ctx.beginPath();
    ctx.rect(0, 0, w, hh);
    ctx.clip();
    planks(ctx, w, hh, rng, { base: '#9c7448', board: 0.22, vertical: false });
    lightFace(ctx, w, hh, [0, 0.2, 0.1]);
    ctx.fillStyle = 'rgba(20,15,10,0.25)';
    ctx.fillRect(0, 0, w, hh);
    ctx.restore();
    sheet.reset();
  };
  inner([x0 + 0.1, y0 + 0.1, 0.25], [1, 0, 0], x1 - x0 - 0.2, 1.5);
  inner([x0 + 0.1, y1 - 0.1, 0.25], [0, -1, 0], y1 - y0 - 0.2, 1.1);
  // Posts and plates in back-to-front order.
  const xs = [x0 + 0.1, 0, x1 - 0.1];
  const ys = [y0 + 0.1, 0, y1 - 0.1];
  for (const y of ys) for (const x of xs) if (y === ys[0] || x === xs[0]) post(x, y, 0.25, h + 0.25);
  solidBox(sheet, rng, { x0, y0: y0, x1, y1: y0 + 0.2, zb: h + 0.25, zt: h + 0.45 }, wood);
  solidBox(sheet, rng, { x0, y0, x1: x0 + 0.2, y1, zb: h + 0.25, zt: h + 0.45 }, wood);
  // Rafters rising toward a ridge along x.
  for (const x of [x0 + 0.1, 0, x1 - 0.1]) {
    const a = sheet.P([x, y0 + 0.1, h + 0.45]);
    const b = sheet.P([x, 0, h + 2.2]);
    const c = sheet.P([x, y1 - 0.1, h + 0.45]);
    ctx.strokeStyle = '#8a643a';
    ctx.lineWidth = 4;
    ctx.beginPath();
    ctx.moveTo(a[0], a[1]);
    ctx.lineTo(b[0], b[1]);
    ctx.lineTo(c[0], c[1]);
    ctx.stroke();
    ctx.strokeStyle = 'rgba(30,18,8,0.6)';
    ctx.lineWidth = 1;
    ctx.stroke();
  }
  for (const y of ys) for (const x of xs) if (!(y === ys[0] || x === xs[0])) post(x, y, 0.25, h + 0.25);
  solidBox(sheet, rng, { x0, y0: y1 - 0.2, x1, y1, zb: h + 0.25, zt: h + 0.45 }, wood);
  solidBox(sheet, rng, { x0: x1 - 0.2, y0, x1, y1, zb: h + 0.25, zt: h + 0.45 }, wood);
  // Ridge beam.
  const r0 = sheet.P([x0 + 0.1, 0, h + 2.2]);
  const r1 = sheet.P([x1 - 0.1, 0, h + 2.2]);
  ctx.strokeStyle = '#7a5630';
  ctx.lineWidth = 4.5;
  ctx.beginPath();
  ctx.moveTo(r0[0], r0[1]);
  ctx.lineTo(r1[0], r1[1]);
  ctx.stroke();
  // Diagonal braces on the front frame.
  ctx.strokeStyle = '#9a7246';
  ctx.lineWidth = 3;
  for (const [a, b] of [
    [
      [x0 + 0.1, y1 - 0.1, 0.4],
      [0, y1 - 0.1, h],
    ],
    [
      [x1 - 0.1, y1 - 0.1, 0.4],
      [x1 - 0.1, 0, h],
    ],
  ]) {
    const pa = sheet.P(a);
    const pb = sheet.P(b);
    ctx.beginPath();
    ctx.moveTo(pa[0], pa[1]);
    ctx.lineTo(pb[0], pb[1]);
    ctx.stroke();
  }
  const footprint = { minX: x0, minY: y0, maxX: x1, maxY: y1 };
  return {
    canvas: sheet.canvas,
    anchor: sheet.anchor,
    shadow: paintShadow(
      [
        [x0, y0, h],
        [x1, y1, h],
        [x0, y1, h],
        [x1, y0, h],
      ],
      footprint,
      { alpha: 0.45 },
    ),
    footprint,
    doors: [{ x: 0, y: y1 + 0.4, face: '+y' }],
    height: h + 2.2,
  };
}

/** Stone well with a small roof. */
export function paintWell(spec) {
  const rng = rngFor(spec.id);
  const r = 0.85;
  const sheet = new Sheet(
    [
      [-1.4, -1.4, 0],
      [1.4, 1.4, 0],
      [-1.4, 1.4, 3.4],
      [1.4, -1.4, 3.4],
    ],
    8,
  );
  const ctx = sheet.ctx;
  const ring = (z) => {
    const c = sheet.P([0, 0, z]);
    const rx = projVec([r, -r, 0])[0] / 2;
    return { cx: c[0], cy: c[1], rx: Math.abs(rx) * 1.0, ry: Math.abs(rx) * 0.5 };
  };
  const b = ring(0);
  const t = ring(0.85);
  // Posts behind.
  const postAt = (x, y) =>
    solidBox(
      sheet,
      rng,
      { x0: x - 0.08, y0: y - 0.08, x1: x + 0.08, y1: y + 0.08, zb: 0.7, zt: 2.6 },
      { kind: 'planks', base: '#6a4a2c' },
    );
  postAt(-0.75, -0.75);
  // Side wall.
  ctx.save();
  ctx.beginPath();
  ctx.ellipse(b.cx, b.cy, b.rx, b.ry, 0, 0, Math.PI);
  ctx.lineTo(t.cx - t.rx, t.cy);
  ctx.ellipse(t.cx, t.cy, t.rx, t.ry, 0, Math.PI, 0, true);
  ctx.closePath();
  ctx.clip();
  ctx.fillStyle = '#8c867a';
  ctx.fillRect(b.cx - b.rx, t.cy - t.ry, b.rx * 2, b.cy - t.cy + b.ry * 2);
  for (let row = 0; row < 4; row += 1) {
    for (let i = 0; i < 9; i += 1) {
      const a0 = (i + (row % 2) * 0.5) / 9;
      const x = b.cx - b.rx + a0 * b.rx * 2;
      const y = t.cy + row * ((b.cy - t.cy) / 4) + Math.sin(a0 * Math.PI) * b.ry;
      ctx.fillStyle = rgbToCss(jitter('#a49e90', rng, 0.15));
      ctx.beginPath();
      ctx.roundRect(x + 1, y + 1, b.rx * 0.2, (b.cy - t.cy) / 4 - 1.5, 2);
      ctx.fill();
    }
  }
  const g = ctx.createLinearGradient(b.cx - b.rx, 0, b.cx + b.rx, 0);
  g.addColorStop(0, 'rgba(255,230,190,0.15)');
  g.addColorStop(0.6, 'rgba(0,0,0,0.1)');
  g.addColorStop(1, 'rgba(10,10,30,0.45)');
  ctx.fillStyle = g;
  ctx.fillRect(b.cx - b.rx, t.cy - t.ry, b.rx * 2, b.cy - t.cy + b.ry * 2);
  ctx.restore();
  // Top rim and dark water.
  ctx.fillStyle = '#b0aa9c';
  ctx.beginPath();
  ctx.ellipse(t.cx, t.cy, t.rx, t.ry, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = '#1c2630';
  ctx.beginPath();
  ctx.ellipse(t.cx, t.cy, t.rx * 0.74, t.ry * 0.74, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.strokeStyle = 'rgba(30,20,14,0.6)';
  ctx.lineWidth = 1.2;
  ctx.stroke();
  postAt(0.75, 0.75);
  postAt(-0.75, 0.75);
  postAt(0.75, -0.75);
  // Little roof.
  const roofBox = { x0: -1.0, y0: -1.0, x1: 1.0, y1: 1.0, zb: 2.6, zt: 2.6 };
  gableRoof(sheet, rng, roofBox, { axis: 'x', rise: 0.75, overhang: 0.15, mat: { kind: 'shingles', base: '#6a5444' } });
  // Bucket.
  const bk = sheet.P([0, 0.2, 1.6]);
  ctx.strokeStyle = '#3a2a1a';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(bk[0], bk[1] - 18);
  ctx.lineTo(bk[0], bk[1]);
  ctx.stroke();
  ctx.fillStyle = '#6a4a2a';
  ctx.fillRect(bk[0] - 4, bk[1], 8, 8);
  const footprint = { minX: -1.05, minY: -1.05, maxX: 1.05, maxY: 1.05 };
  return {
    canvas: sheet.canvas,
    anchor: sheet.anchor,
    shadow: paintShadow(
      [
        [-1, -1, 3.2],
        [1, 1, 3.2],
        [-1, 1, 3.2],
        [1, -1, 3.2],
      ],
      footprint,
      { alpha: 0.7 },
    ),
    footprint,
    doors: [],
    height: 3.4,
  };
}
