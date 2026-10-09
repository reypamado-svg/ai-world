// Isometric drawing surface for procedural sprites.
//
// A Sheet is one sprite canvas. Geometry is described in ground-plane metres
// (x, y) and height metres (z) relative to the sprite's ground anchor. Every
// planar face of a box or roof projects to a parallelogram, so a face can be
// painted in its own flat (u, v) metre coordinates through one affine
// transform. That is what lets planks, stones and thatch sit on the walls.

import { K, KZ } from '../../../world/coords.js';

/** Art pixels per screen pixel at zoom 1. Sprites are painted at 2x. */
export const ART = 2;

/** Direction towards the sun (back-left, high). Lights +y faces, shades +x. */
export const SUN = normalize([-0.6, 0.45, 0.66]);

/** Ground offset of a shadow per metre of height (presentation choice). */
export const SHADOW = { x: 0.55, y: -0.3 };

export function normalize(v) {
  const l = Math.hypot(v[0], v[1], v[2]) || 1;
  return [v[0] / l, v[1] / l, v[2] / l];
}

export function dot(a, b) {
  return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}

export function cross(a, b) {
  return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
}

/** Projected art-pixel offset of a 3D vector. */
export function projVec(v) {
  return [(v[0] - v[1]) * K * ART, ((v[0] + v[1]) * K * 0.5 - v[2] * KZ) * ART];
}

/** Brightness of a face with outward normal n, in [0.6, 1]. */
export function brightness(n) {
  return 0.6 + 0.4 * Math.max(0, dot(normalize(n), SUN));
}

export class Sheet {
  /**
   * @param {number[][]} points3d every 3D point the sprite will cover
   * @param {number} pad extra art pixels around the bounds
   * @param {number[][]} extra2d extra art-pixel offsets (relative to anchor) to include
   */
  constructor(points3d, pad = 6, extra2d = []) {
    let minX = Infinity;
    let minY = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    const grow = (x, y) => {
      minX = Math.min(minX, x);
      minY = Math.min(minY, y);
      maxX = Math.max(maxX, x);
      maxY = Math.max(maxY, y);
    };
    for (const p of points3d) {
      const [x, y] = projVec(p);
      grow(x, y);
    }
    for (const [x, y] of extra2d) grow(x, y);
    this.ox = Math.ceil(-minX + pad);
    this.oy = Math.ceil(-minY + pad);
    this.canvas = document.createElement('canvas');
    this.canvas.width = Math.ceil(maxX - minX + pad * 2);
    this.canvas.height = Math.ceil(maxY - minY + pad * 2);
    this.ctx = this.canvas.getContext('2d');
  }

  /** Art-pixel canvas position of a 3D point. */
  P(p) {
    const [x, y] = projVec(p);
    return [x + this.ox, y + this.oy];
  }

  /** Map local (u, v) metres onto the plane O + u*U + v*V. */
  face(O, U, V) {
    const o = this.P(O);
    const pu = projVec(U);
    const pv = projVec(V);
    this.ctx.setTransform(pu[0], pu[1], pv[0], pv[1], o[0], o[1]);
  }

  /** Back to canvas pixels. */
  reset() {
    this.ctx.setTransform(1, 0, 0, 1, 0, 0);
  }

  /** Polygon through 3D points, in canvas pixels. */
  path(points) {
    const ctx = this.ctx;
    ctx.beginPath();
    points.forEach((p, i) => {
      const [x, y] = this.P(p);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.closePath();
  }

  /** Anchor (projected origin) in canvas pixels. */
  get anchor() {
    return { x: this.ox, y: this.oy };
  }
}

/** Darken or warm a face after painting, according to its normal. */
export function lightFace(ctx, w, h, normal) {
  const b = brightness(normal);
  if (b < 1) {
    ctx.fillStyle = `rgba(28,22,38,${(1 - b) * 0.95})`;
    ctx.fillRect(0, 0, w, h);
  }
  const warm = Math.max(0, dot(normalize(normal), SUN));
  if (warm > 0.2) {
    ctx.fillStyle = `rgba(255,214,150,${warm * 0.1})`;
    ctx.fillRect(0, 0, w, h);
  }
}

/** Contact darkening at the foot of a wall and under the eaves. */
export function wallOcclusion(ctx, w, h, { foot = 0.8, eave = 0.7 } = {}) {
  let g = ctx.createLinearGradient(0, 0, 0, foot);
  g.addColorStop(0, 'rgba(30,20,12,0.45)');
  g.addColorStop(1, 'rgba(30,20,12,0)');
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, w, foot);
  if (eave > 0) {
    g = ctx.createLinearGradient(0, h, 0, h - eave);
    g.addColorStop(0, 'rgba(15,10,10,0.5)');
    g.addColorStop(1, 'rgba(15,10,10,0)');
    ctx.fillStyle = g;
    ctx.fillRect(0, h - eave, w, eave);
  }
}

/** Thin dark edge around a face, drawn in canvas pixels. */
export function outline(sheet, points, alpha = 0.55, width = 1.2) {
  sheet.reset();
  sheet.path(points);
  sheet.ctx.strokeStyle = `rgba(30,20,14,${alpha})`;
  sheet.ctx.lineWidth = width;
  sheet.ctx.lineJoin = 'round';
  sheet.ctx.stroke();
}
