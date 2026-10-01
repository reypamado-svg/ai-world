// Procedural citizens (PROTOTYPE ARTWORK).
//
// A small set of shared body designs with clothing variations. Identity
// comes from the person ID, never from a unique texture. Two painted facings
// ("front" = walking toward screen bottom-left, "back" = toward top-left);
// the renderer mirrors them for the right-hand directions.

import { KZ } from '../../../world/coords.js';
import { rngFor } from '../../../sim/rng.js';
import { css, jitter, rgbToCss } from './color.js';
import { ART } from './iso.js';

/** People are drawn slightly larger than life for readability. */
export const PERSON_EXAGGERATION = 1.4;
const HEIGHT_PX = 1.72 * KZ * ART * PERSON_EXAGGERATION;
const U = HEIGHT_PX / 7; // one head-height unit

export const FRAME_W = 64;
export const FRAME_H = 104;
export const ENVOY_FRAME_H = 150;
const ANCHOR_X = 32;
const ANCHOR_Y_PAD = 8;

export const ANIMATIONS = {
  walk: { frames: 8, facings: ['front', 'back'] },
  carry_sack: { frames: 8, facings: ['front', 'back'] },
  carry_log: { frames: 8, facings: ['front', 'back'] },
  carry_plank: { frames: 8, facings: ['front', 'back'] },
  hoe: { frames: 6, facings: ['front'] },
  hammer: { frames: 6, facings: ['front'] },
  axe: { frames: 6, facings: ['front'] },
  idle: { frames: 2, facings: ['front', 'back'] },
  talk: { frames: 4, facings: ['front'] },
};

const TUNICS = ['#7b5b3a', '#5e6c3c', '#8a7a58', '#6c4a38', '#4d5d6c', '#9a8662', '#6e5a7a', '#a0703a'];
const LEGS = ['#4a3a2a', '#3c3832', '#5a4a3a', '#6a5a44'];
const SKINS = ['#e3b58e', '#c9936a', '#a26c46', '#7c4c30'];
const HAIRS = ['#2a1a10', '#5a3a1a', '#8a6a3a', '#1a1612', '#a09890', '#7a3a1a'];
const DRESSES = ['#7a4a3a', '#5a6a4a', '#8a6a4a', '#4a5a7a', '#9a7a5a', '#6a3a4a'];

/** Deterministic appearance from an index. */
export function appearance(index) {
  const rng = rngFor(`appearance:${index}`);
  const female = index % 2 === 1;
  return {
    index,
    female,
    skin: SKINS[Math.floor(rng() * SKINS.length)],
    hair: HAIRS[Math.floor(rng() * HAIRS.length)],
    tunic: female ? DRESSES[Math.floor(rng() * DRESSES.length)] : TUNICS[Math.floor(rng() * TUNICS.length)],
    legs: LEGS[Math.floor(rng() * LEGS.length)],
    cap: !female && rng() < 0.5,
    apron: female && rng() < 0.6,
    beard: !female && rng() < 0.4,
  };
}

export const APPEARANCE_COUNT = 10;

function outlineStroke(ctx, w = 1.2) {
  ctx.strokeStyle = 'rgba(26,18,12,0.92)';
  ctx.lineWidth = w;
  ctx.lineJoin = 'round';
  ctx.lineCap = 'round';
  ctx.stroke();
}

function limb(ctx, x0, y0, x1, y1, x2, y2, width, color) {
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  ctx.strokeStyle = 'rgba(26,18,12,0.95)';
  ctx.lineWidth = width + 2.2;
  ctx.beginPath();
  ctx.moveTo(x0, y0);
  ctx.lineTo(x1, y1);
  ctx.lineTo(x2, y2);
  ctx.stroke();
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.stroke();
}

function shadedFill(ctx, x0, x1, color) {
  const g = ctx.createLinearGradient(x0, 0, x1, 0);
  g.addColorStop(0, css(color, 1.22));
  g.addColorStop(0.55, css(color, 1.0));
  g.addColorStop(1, css(color, 0.68));
  ctx.fillStyle = g;
}

/**
 * Pose for a frame: positions relative to the feet (0, 0), y up negative.
 */
function pose(anim, facing, phase) {
  const fwd = facing === 'front' ? [-0.9, 0.36] : [-0.9, -0.36];
  const walking = anim === 'walk' || anim.startsWith('carry');
  const s = Math.sin(phase * Math.PI * 2);
  const c = Math.cos(phase * Math.PI * 2);
  const stride = walking ? 0.95 * U : 0;
  const bob = walking ? (1 - Math.abs(s)) * 0.16 * U : 0;
  const footA = [fwd[0] * s * stride, fwd[1] * s * stride - Math.max(0, -c) * 0.3 * U * (walking ? 1 : 0)];
  const footB = [-fwd[0] * s * stride, -fwd[1] * s * stride - Math.max(0, c) * 0.3 * U * (walking ? 1 : 0)];
  const p = {
    bob,
    lean: 0,
    footA,
    footB,
    handA: [-0.75 * U - fwd[0] * -s * 0.55 * U, -3.3 * U],
    handB: [0.75 * U + fwd[0] * s * 0.55 * U, -3.3 * U],
    tool: null,
  };
  if (anim === 'hoe') {
    // Raise (0-0.45), strike (0.45-0.6), drag back (0.6-1).
    const t = phase < 0.45 ? phase / 0.45 : phase < 0.6 ? 1 - (phase - 0.45) / 0.15 : ((phase - 0.6) / 0.4) * 0.25;
    const ang = -0.35 + t * 2.1; // radians from pointing down-left
    p.lean = (1 - t) * 0.35 * U;
    const grip = [-0.45 * U - (1 - t) * 0.35 * U, -4.2 * U - t * 1.1 * U];
    p.handA = grip;
    p.handB = [grip[0] + 0.5 * U, grip[1] + 0.35 * U];
    p.tool = { kind: 'hoe', grip, angle: Math.PI * 0.72 - ang, length: 2.8 * U };
  } else if (anim === 'hammer') {
    const t = phase < 0.6 ? phase / 0.6 : 1 - (phase - 0.6) / 0.4;
    const grip = [-0.9 * U + t * 0.4 * U, -3.6 * U - t * 2.0 * U];
    p.handA = grip;
    p.handB = [0.5 * U, -3.4 * U];
    p.lean = (1 - t) * 0.2 * U;
    p.tool = { kind: 'hammer', grip, angle: Math.PI * (0.95 - t * 0.75), length: 1.1 * U };
  } else if (anim === 'axe') {
    const t = phase < 0.55 ? phase / 0.55 : 1 - (phase - 0.55) / 0.45;
    const grip = [-0.6 * U + t * 1.3 * U, -3.5 * U - t * 2.3 * U];
    p.handA = grip;
    p.handB = [grip[0] + 0.35 * U, grip[1] + 0.5 * U];
    p.lean = (1 - t) * 0.3 * U;
    p.tool = { kind: 'axe', grip, angle: Math.PI * (0.92 - t * 0.85), length: 2.0 * U };
  } else if (anim === 'carry_sack') {
    p.handA = [-0.55 * U, -6.0 * U];
  } else if (anim === 'carry_log' || anim === 'carry_plank') {
    p.handA = [-0.4 * U, -5.7 * U];
    p.handB = [0.5 * U, -5.6 * U];
  } else if (anim === 'talk') {
    const t = Math.sin(phase * Math.PI * 2);
    p.handA = [-1.0 * U - t * 0.2 * U, -4.8 * U - t * 0.3 * U];
  } else if (anim === 'idle') {
    p.bob = Math.sin(phase * Math.PI * 2) * 0.05 * U;
  }
  return p;
}

function drawTool(ctx, ox, oy, tool) {
  if (!tool) return;
  const [gx, gy] = tool.grip;
  const ex = ox + gx + Math.cos(tool.angle) * tool.length;
  const ey = oy + gy + Math.sin(tool.angle) * tool.length;
  const bx = ox + gx - Math.cos(tool.angle) * tool.length * 0.25;
  const by = oy + gy - Math.sin(tool.angle) * tool.length * 0.25;
  ctx.lineCap = 'round';
  ctx.strokeStyle = 'rgba(26,18,12,0.95)';
  ctx.lineWidth = 3.4;
  ctx.beginPath();
  ctx.moveTo(bx, by);
  ctx.lineTo(ex, ey);
  ctx.stroke();
  ctx.strokeStyle = '#9a7448';
  ctx.lineWidth = 1.8;
  ctx.stroke();
  const nx = -Math.sin(tool.angle);
  const ny = Math.cos(tool.angle);
  ctx.fillStyle = '#6a6e74';
  ctx.strokeStyle = 'rgba(20,20,24,0.9)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  if (tool.kind === 'hoe') {
    ctx.moveTo(ex, ey);
    ctx.lineTo(ex + nx * 6 + Math.cos(tool.angle) * 2, ey + ny * 6 + Math.sin(tool.angle) * 2);
    ctx.lineTo(ex + nx * 6 - Math.cos(tool.angle) * 3, ey + ny * 6 - Math.sin(tool.angle) * 3);
    ctx.lineTo(ex - Math.cos(tool.angle) * 2, ey - Math.sin(tool.angle) * 2);
  } else if (tool.kind === 'hammer') {
    ctx.rect(ex - 3.5, ey - 3.5, 7, 7);
  } else {
    ctx.moveTo(ex - Math.cos(tool.angle) * 3, ey - Math.sin(tool.angle) * 3);
    ctx.lineTo(ex + nx * 6, ey + ny * 6);
    ctx.lineTo(ex + nx * 7 + Math.cos(tool.angle) * 5, ey + ny * 7 + Math.sin(tool.angle) * 5);
    ctx.lineTo(ex + Math.cos(tool.angle) * 2, ey + Math.sin(tool.angle) * 2);
  }
  ctx.closePath();
  ctx.fill();
  ctx.stroke();
}

function drawHead(ctx, hx, hy, look, facing) {
  const r = 0.5 * U;
  // Neck.
  ctx.fillStyle = css(look.skin, 0.85);
  ctx.fillRect(hx - 0.16 * U, hy + r * 0.6, 0.32 * U, 0.4 * U);
  const g = ctx.createRadialGradient(hx - r * 0.4, hy - r * 0.4, 1, hx, hy, r * 1.1);
  g.addColorStop(0, css(look.skin, 1.18));
  g.addColorStop(1, css(look.skin, 0.78));
  ctx.fillStyle = g;
  ctx.beginPath();
  ctx.ellipse(hx, hy, r * 0.92, r, 0, 0, Math.PI * 2);
  ctx.fill();
  outlineStroke(ctx, 1.1);
  if (facing === 'front') {
    ctx.fillStyle = '#20140c';
    ctx.fillRect(hx - r * 0.55, hy - r * 0.05, 1.4, 1.4);
    ctx.fillRect(hx - r * 0.05, hy - r * 0.05, 1.4, 1.4);
    if (look.beard) {
      ctx.fillStyle = css(look.hair, 0.95);
      ctx.beginPath();
      ctx.ellipse(hx - r * 0.2, hy + r * 0.55, r * 0.55, r * 0.4, 0, 0, Math.PI);
      ctx.fill();
    }
  }
  // Hair or scarf.
  ctx.beginPath();
  if (look.female) {
    ctx.fillStyle = look.scarf ?? css(look.hair, 1);
    if (facing === 'front')
      ctx.ellipse(hx + r * 0.15, hy - r * 0.2, r * 1.0, r * 0.95, 0, Math.PI * 0.9, Math.PI * 2.15);
    else ctx.ellipse(hx, hy - r * 0.05, r * 1.0, r * 1.05, 0, 0, Math.PI * 2);
    ctx.fill();
    outlineStroke(ctx, 1);
    if (facing === 'back') {
      ctx.fillStyle = look.scarf ?? css(look.hair, 0.9);
      ctx.beginPath();
      ctx.ellipse(hx + r * 0.1, hy + r * 0.9, r * 0.55, r * 0.7, 0, 0, Math.PI * 2);
      ctx.fill();
      outlineStroke(ctx, 1);
    }
  } else if (look.capColor) {
    ctx.fillStyle = look.capColor;
    ctx.ellipse(hx + r * 0.05, hy - r * 0.35, r * 1.02, r * 0.78, 0, Math.PI, Math.PI * 2.02);
    ctx.lineTo(hx - r * 1.1, hy - r * 0.2);
    ctx.fill();
    outlineStroke(ctx, 1);
  } else {
    ctx.fillStyle = css(look.hair, 1);
    if (facing === 'front')
      ctx.ellipse(hx + r * 0.2, hy - r * 0.35, r * 0.95, r * 0.75, 0, Math.PI * 0.95, Math.PI * 2.1);
    else ctx.ellipse(hx, hy - r * 0.15, r * 0.96, r * 0.92, 0, 0, Math.PI * 2);
    ctx.fill();
    outlineStroke(ctx, 1);
  }
}

/** Paint one citizen frame into ctx with feet at (ox, oy). */
export function paintPersonFrame(ctx, ox, oy, look, anim, facing, phase) {
  const p = pose(anim, facing, phase);
  const y = oy - p.bob;
  const lean = p.lean;
  const hipY = y - 3.45 * U;
  const shoulderY = y - 5.55 * U;
  const back = facing === 'back';
  const legColor = look.legs;
  const tunic = look.tunic;
  const farShade = 0.72;

  // Far arm (behind the body).
  const shA = [ox - 0.78 * U - lean, shoulderY + 0.3 * U];
  const shB = [ox + 0.78 * U - lean, shoulderY + 0.3 * U];
  const handB = [ox + p.handB[0] - lean, y + p.handB[1]];
  limb(
    ctx,
    shB[0],
    shB[1],
    (shB[0] + handB[0]) / 2 + 0.2 * U,
    (shB[1] + handB[1]) / 2,
    handB[0],
    handB[1],
    0.42 * U,
    css(tunic, farShade),
  );

  // Legs.
  const knee = (hx, foot) => [(hx + ox + foot[0]) / 2 - 0.12 * U, (hipY + y + foot[1]) / 2];
  const legB = [ox + 0.28 * U, hipY];
  const legA = [ox - 0.28 * U, hipY];
  const kB = knee(legB[0], p.footB);
  const kA = knee(legA[0], p.footA);
  if (!look.female) {
    limb(
      ctx,
      legB[0],
      legB[1],
      kB[0],
      kB[1],
      ox + p.footB[0],
      y + p.footB[1] - 0.15 * U,
      0.42 * U,
      css(legColor, farShade),
    );
    limb(ctx, legA[0], legA[1], kA[0], kA[1], ox + p.footA[0], y + p.footA[1] - 0.15 * U, 0.44 * U, legColor);
  }
  // Feet.
  for (const [f, shadeF] of [
    [p.footB, 0.7],
    [p.footA, 1],
  ]) {
    ctx.fillStyle = css('#3a2618', shadeF);
    ctx.beginPath();
    ctx.ellipse(ox + f[0] - 0.12 * U, y + f[1] - 0.06 * U, 0.3 * U, 0.15 * U, 0, 0, Math.PI * 2);
    ctx.fill();
    outlineStroke(ctx, 0.9);
  }

  // Torso / dress.
  ctx.beginPath();
  if (look.female) {
    const sway = Math.sin(phase * Math.PI * 2) * 0.2 * U;
    ctx.moveTo(ox - 0.8 * U - lean, shoulderY);
    ctx.lineTo(ox + 0.8 * U - lean, shoulderY);
    ctx.lineTo(ox + 1.15 * U + sway, y - 0.35 * U);
    ctx.quadraticCurveTo(ox + sway * 0.5, y - 0.15 * U, ox - 1.2 * U + sway, y - 0.35 * U);
  } else {
    ctx.moveTo(ox - 0.82 * U - lean, shoulderY);
    ctx.lineTo(ox + 0.82 * U - lean, shoulderY);
    ctx.lineTo(ox + 0.95 * U - lean * 0.3, hipY + 0.95 * U);
    ctx.lineTo(ox - 1.0 * U - lean * 0.3, hipY + 0.95 * U);
  }
  ctx.closePath();
  shadedFill(ctx, ox - 1.1 * U, ox + 1.1 * U, tunic);
  ctx.fill();
  outlineStroke(ctx, 1.2);
  if (look.apron && !back) {
    ctx.fillStyle = '#d8ccb0';
    ctx.beginPath();
    ctx.moveTo(ox - 0.55 * U - lean * 0.5, hipY - 0.3 * U);
    ctx.lineTo(ox + 0.45 * U - lean * 0.5, hipY - 0.3 * U);
    ctx.lineTo(ox + 0.6 * U, y - 0.8 * U);
    ctx.lineTo(ox - 0.7 * U, y - 0.8 * U);
    ctx.closePath();
    ctx.fill();
    outlineStroke(ctx, 0.9);
  }
  // Belt / sash in civilization colour (the civ mask).
  ctx.fillStyle = look.sash;
  ctx.fillRect(ox - 0.92 * U - lean * 0.6, hipY - 0.55 * U, 1.84 * U, 0.34 * U);
  ctx.strokeStyle = 'rgba(26,18,12,0.85)';
  ctx.lineWidth = 0.9;
  ctx.strokeRect(ox - 0.92 * U - lean * 0.6, hipY - 0.55 * U, 1.84 * U, 0.34 * U);
  // Collar shading.
  ctx.fillStyle = 'rgba(0,0,0,0.18)';
  ctx.fillRect(ox - 0.5 * U - lean, shoulderY, 1.0 * U, 0.25 * U);

  // Carried goods behind the head.
  if (p && back) drawCarry(ctx, ox - lean, y, phase, facing, look, true);

  // Head.
  drawHead(ctx, ox - 0.05 * U - lean * 1.4, y - 6.4 * U, look, facing);

  // Near arm.
  const handA = [ox + p.handA[0] - lean, y + p.handA[1]];
  limb(
    ctx,
    shA[0],
    shA[1],
    (shA[0] + handA[0]) / 2 - 0.15 * U,
    (shA[1] + handA[1]) / 2 + 0.1 * U,
    handA[0],
    handA[1],
    0.44 * U,
    tunic,
  );
  ctx.fillStyle = look.skin;
  ctx.beginPath();
  ctx.arc(handA[0], handA[1], 0.2 * U, 0, Math.PI * 2);
  ctx.fill();
  outlineStroke(ctx, 0.9);
  drawTool(ctx, ox - lean, y, p.tool);
  if (!back) drawCarry(ctx, ox - lean, y, phase, facing, look, false);
  return p;
}

function drawCarry(ctx, ox, y, phase, facing, look, _behind) {
  const anim = look._anim;
  if (anim === 'carry_sack') {
    const g = ctx.createRadialGradient(ox - 0.9 * U, y - 6.4 * U, 1, ox - 0.6 * U, y - 6.0 * U, 1.0 * U);
    g.addColorStop(0, '#e2d0a8');
    g.addColorStop(1, '#a8926a');
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.ellipse(ox - 0.55 * U, y - 6.15 * U, 0.95 * U, 0.62 * U, -0.3, 0, Math.PI * 2);
    ctx.fill();
    outlineStroke(ctx, 1.1);
  } else if (anim === 'carry_log' || anim === 'carry_plank') {
    const log = anim === 'carry_log';
    const dir = facing === 'front' ? 1 : -1;
    const x0 = ox - 1.6 * U;
    const y0 = y - 5.85 * U + dir * 0.45 * U;
    const x1 = ox + 1.7 * U;
    const y1 = y - 5.85 * U - dir * 0.45 * U;
    ctx.lineCap = log ? 'round' : 'butt';
    ctx.strokeStyle = 'rgba(26,18,12,0.95)';
    ctx.lineWidth = (log ? 0.62 : 0.3) * U + 2;
    ctx.beginPath();
    ctx.moveTo(x0, y0);
    ctx.lineTo(x1, y1);
    ctx.stroke();
    ctx.strokeStyle = log ? '#6a4a2c' : '#c8a070';
    ctx.lineWidth = (log ? 0.62 : 0.3) * U;
    ctx.stroke();
    if (log) {
      ctx.fillStyle = '#d0a878';
      ctx.beginPath();
      ctx.ellipse(x0, y0, 0.3 * U, 0.3 * U, 0, 0, Math.PI * 2);
      ctx.fill();
      outlineStroke(ctx, 0.8);
    }
  }
  void phase;
}

/** Ambassador: long robe, tall staff with a civilization banner. */
export function paintEnvoyFrame(ctx, ox, oy, look, facing, phase, civColor) {
  const walking = phase >= 0;
  const s = walking ? Math.sin(phase * Math.PI * 2) : 0;
  const y = oy - (1 - Math.abs(s)) * 0.12 * U;
  const shoulderY = y - 5.6 * U;
  // Staff and banner behind on the far side.
  const sx = ox + 1.05 * U;
  ctx.strokeStyle = 'rgba(26,18,12,0.95)';
  ctx.lineWidth = 3.6;
  ctx.beginPath();
  ctx.moveTo(sx, y + 0.2 * U);
  ctx.lineTo(sx, y - 10.2 * U);
  ctx.stroke();
  ctx.strokeStyle = '#8a6a3a';
  ctx.lineWidth = 2;
  ctx.stroke();
  const flutter = Math.sin(phase * Math.PI * 4) * 0.25 * U;
  ctx.fillStyle = civColor;
  ctx.beginPath();
  ctx.moveTo(sx, y - 10.0 * U);
  ctx.lineTo(sx - 2.2 * U, y - 9.8 * U + flutter);
  ctx.lineTo(sx - 1.8 * U, y - 8.9 * U + flutter);
  ctx.lineTo(sx - 2.2 * U, y - 8.0 * U + flutter);
  ctx.lineTo(sx, y - 8.2 * U);
  ctx.closePath();
  ctx.fill();
  outlineStroke(ctx, 1.1);
  ctx.fillStyle = '#e8cc70';
  ctx.beginPath();
  ctx.arc(sx - 0.95 * U, y - 9.05 * U + flutter * 0.6, 0.3 * U, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = '#e8cc70';
  ctx.beginPath();
  ctx.arc(sx, y - 10.3 * U, 0.22 * U, 0, Math.PI * 2);
  ctx.fill();
  // Feet.
  for (const k of [-1, 1]) {
    ctx.fillStyle = '#2a1a10';
    ctx.beginPath();
    ctx.ellipse(ox + k * s * 0.5 * U - 0.1 * U, y - 0.08 * U, 0.3 * U, 0.15 * U, 0, 0, Math.PI * 2);
    ctx.fill();
  }
  // Robe.
  ctx.beginPath();
  ctx.moveTo(ox - 0.85 * U, shoulderY);
  ctx.lineTo(ox + 0.85 * U, shoulderY);
  ctx.lineTo(ox + 1.25 * U + s * 0.15 * U, y - 0.2 * U);
  ctx.lineTo(ox - 1.3 * U + s * 0.15 * U, y - 0.2 * U);
  ctx.closePath();
  shadedFill(ctx, ox - 1.2 * U, ox + 1.2 * U, civColor);
  ctx.fill();
  outlineStroke(ctx, 1.2);
  ctx.strokeStyle = '#e0c060';
  ctx.lineWidth = 1.6;
  ctx.beginPath();
  ctx.moveTo(ox - 0.05 * U, shoulderY + 0.3 * U);
  ctx.lineTo(ox - 0.05 * U + s * 0.1 * U, y - 0.3 * U);
  ctx.moveTo(ox - 1.25 * U + s * 0.15 * U, y - 0.5 * U);
  ctx.lineTo(ox + 1.2 * U + s * 0.15 * U, y - 0.5 * U);
  ctx.stroke();
  // Cloak over the shoulders.
  ctx.fillStyle = css('#3a2a3a', 1);
  ctx.beginPath();
  ctx.moveTo(ox - 0.95 * U, shoulderY - 0.1 * U);
  ctx.lineTo(ox + 0.95 * U, shoulderY - 0.1 * U);
  ctx.lineTo(ox + 1.0 * U, shoulderY + 1.3 * U);
  ctx.lineTo(ox - 1.05 * U, shoulderY + 1.3 * U);
  ctx.closePath();
  ctx.fill();
  outlineStroke(ctx, 1);
  drawHead(ctx, ox - 0.05 * U, y - 6.45 * U, { ...look, capColor: null, female: false }, facing);
  // Tall hat.
  ctx.fillStyle = civColor;
  ctx.beginPath();
  ctx.moveTo(ox - 0.5 * U, y - 6.8 * U);
  ctx.lineTo(ox + 0.42 * U, y - 6.8 * U);
  ctx.lineTo(ox + 0.25 * U, y - 7.7 * U);
  ctx.lineTo(ox - 0.35 * U, y - 7.7 * U);
  ctx.closePath();
  ctx.fill();
  outlineStroke(ctx, 1);
  // Near hand on the staff.
  limb(
    ctx,
    ox + 0.75 * U,
    shoulderY + 0.3 * U,
    ox + 1.0 * U,
    y - 4.6 * U,
    sx,
    y - 4.2 * U,
    0.42 * U,
    css(civColor, 0.9),
  );
}

/** Build the look used for painting, with civilization accents. */
export function dressLook(index, civColor) {
  const look = appearance(index);
  const rng = rngFor(`dress:${index}`);
  return {
    ...look,
    sash: civColor,
    capColor: look.cap ? civColor : null,
    scarf: look.female ? (rng() < 0.5 ? civColor : '#d8ccb0') : null,
  };
}

export function personFrameCanvas(look, anim, facing, phase) {
  const c = document.createElement('canvas');
  c.width = FRAME_W;
  c.height = FRAME_H;
  const ctx = c.getContext('2d');
  paintPersonFrame(ctx, ANCHOR_X, FRAME_H - ANCHOR_Y_PAD, { ...look, _anim: anim }, anim, facing, phase);
  return { canvas: c, anchor: { x: ANCHOR_X, y: FRAME_H - ANCHOR_Y_PAD } };
}

export function envoyFrameCanvas(look, facing, phase, civColor) {
  const c = document.createElement('canvas');
  c.width = FRAME_W;
  c.height = ENVOY_FRAME_H;
  const ctx = c.getContext('2d');
  paintEnvoyFrame(ctx, ANCHOR_X, ENVOY_FRAME_H - ANCHOR_Y_PAD, look, facing, phase, civColor);
  return { canvas: c, anchor: { x: ANCHOR_X, y: ENVOY_FRAME_H - ANCHOR_Y_PAD } };
}

void jitter;
void rgbToCss;
