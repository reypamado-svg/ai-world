// Procedural surface materials, painted in face-local metres.
//
// Every function paints the rectangle u in [0, w], v in [0, h] where v points
// UP the face (or up the roof slope). The caller has already set the face
// transform on the context.

import { css, jitter, mix, rgbToCss } from './color.js';

const grainCache = new Map();

/** Fine speckle pattern for subtle surface grain. */
function grain(ctx, seed) {
  if (!grainCache.has(seed)) {
    const c = document.createElement('canvas');
    c.width = 96;
    c.height = 96;
    const g = c.getContext('2d');
    const img = g.createImageData(96, 96);
    let s = seed * 9301 + 49297;
    for (let i = 0; i < img.data.length; i += 4) {
      s = (s * 9301 + 49297) % 233280;
      const r = s / 233280;
      const v = r < 0.5 ? 0 : 255;
      img.data[i] = v;
      img.data[i + 1] = v;
      img.data[i + 2] = v;
      img.data[i + 3] = Math.abs(r - 0.5) * 120;
    }
    g.putImageData(img, 0, 0);
    grainCache.set(seed, c);
  }
  const pattern = ctx.createPattern(grainCache.get(seed), 'repeat');
  pattern.setTransform(new DOMMatrix().scale(1 / 48));
  return pattern;
}

function applyGrain(ctx, w, h, alpha = 0.35, seed = 1) {
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.fillStyle = grain(ctx, seed);
  ctx.fillRect(0, 0, w, h);
  ctx.restore();
}

export function planks(ctx, w, h, rng, { base = '#8a6440', board = 0.24, vertical = true } = {}) {
  const along = vertical ? w : h;
  const across = vertical ? h : w;
  for (let s = 0; s < along; s += board) {
    const col = jitter(base, rng, 0.14);
    ctx.fillStyle = rgbToCss(col);
    if (vertical) ctx.fillRect(s, 0, board, across);
    else ctx.fillRect(0, s, across, board);
    ctx.strokeStyle = css(col, 0.72, 0.5);
    ctx.lineWidth = 0.012;
    for (let i = 0; i < 4; i += 1) {
      const o = s + board * (0.15 + 0.7 * rng());
      ctx.beginPath();
      let wobble = 0;
      for (let t = 0; t <= across; t += 0.25) {
        wobble += (rng() - 0.5) * 0.012;
        if (vertical) ctx.lineTo(o + wobble, t);
        else ctx.lineTo(t, o + wobble);
      }
      ctx.stroke();
    }
    if (rng() < 0.35) {
      ctx.fillStyle = css(col, 0.55, 0.8);
      ctx.beginPath();
      const ku = s + board * (0.3 + 0.4 * rng());
      const kv = across * rng();
      if (vertical) ctx.ellipse(ku, kv, 0.025, 0.04, 0, 0, Math.PI * 2);
      else ctx.ellipse(kv, ku, 0.04, 0.025, 0, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.fillStyle = 'rgba(25,15,8,0.75)';
    if (vertical) ctx.fillRect(s, 0, 0.022, across);
    else ctx.fillRect(0, s, across, 0.022);
  }
  applyGrain(ctx, w, h, 0.25, 3);
  const g = ctx.createLinearGradient(0, 0, 0, 0.5);
  g.addColorStop(0, 'rgba(60,40,20,0.45)');
  g.addColorStop(1, 'rgba(60,40,20,0)');
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, w, 0.5);
}

export function stoneWall(ctx, w, h, rng, { base = '#9d978b', mortar = '#7d766a' } = {}) {
  ctx.fillStyle = mortar;
  ctx.fillRect(0, 0, w, h);
  let v = 0;
  let row = 0;
  while (v < h) {
    const rowH = 0.2 + rng() * 0.13;
    let u = row % 2 === 0 ? -rng() * 0.2 : -0.15 - rng() * 0.2;
    while (u < w) {
      const sw = 0.28 + rng() * 0.38;
      const col = jitter(mix(base, rng() < 0.3 ? '#a08a6a' : base, 0.5), rng, 0.16);
      const x = u + 0.018;
      const y = v + 0.018;
      const sw2 = sw - 0.036;
      const sh = rowH - 0.036;
      ctx.fillStyle = rgbToCss(col);
      ctx.beginPath();
      ctx.roundRect(x, y, sw2, sh, 0.05);
      ctx.fill();
      // Lit top edge (v up), shaded lower edge.
      ctx.fillStyle = css(col, 1.22, 0.85);
      ctx.fillRect(x + 0.02, y + sh - 0.035, sw2 - 0.04, 0.03);
      ctx.fillStyle = css(col, 0.62, 0.85);
      ctx.fillRect(x + 0.02, y, sw2 - 0.04, 0.03);
      if (rng() < 0.4) {
        ctx.fillStyle = 'rgba(110,120,70,0.35)';
        ctx.beginPath();
        ctx.ellipse(x + sw2 * rng(), y + sh * rng(), 0.05, 0.03, 0, 0, Math.PI * 2);
        ctx.fill();
      }
      u += sw;
    }
    v += rowH;
    row += 1;
  }
  applyGrain(ctx, w, h, 0.4, 5);
}

export function logs(ctx, w, h, rng, { base = '#7a5634', dia = 0.27 } = {}) {
  ctx.fillStyle = '#b8a888';
  ctx.fillRect(0, 0, w, h);
  for (let v = 0; v < h; v += dia) {
    const col = jitter(base, rng, 0.1);
    const g = ctx.createLinearGradient(0, v, 0, v + dia);
    g.addColorStop(0, css(col, 0.55));
    g.addColorStop(0.55, css(col, 1.05));
    g.addColorStop(0.8, css(col, 1.2));
    g.addColorStop(1, css(col, 0.8));
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.roundRect(-0.1, v + 0.015, w + 0.2, dia - 0.03, dia * 0.45);
    ctx.fill();
    ctx.strokeStyle = css(col, 0.6, 0.6);
    ctx.lineWidth = 0.012;
    for (let i = 0; i < w * 6; i += 1) {
      const u = rng() * w;
      const y = v + dia * (0.2 + 0.6 * rng());
      ctx.beginPath();
      ctx.moveTo(u, y);
      ctx.lineTo(u + 0.15 + rng() * 0.3, y + (rng() - 0.5) * 0.02);
      ctx.stroke();
    }
  }
  applyGrain(ctx, w, h, 0.3, 7);
}

export function plasterFrame(ctx, w, h, rng, { plaster = '#d9ccad', beam = '#4b3524', bays = 1.6 } = {}) {
  ctx.fillStyle = plaster;
  ctx.fillRect(0, 0, w, h);
  for (let i = 0; i < w * h * 3; i += 1) {
    ctx.fillStyle = rng() < 0.5 ? 'rgba(150,130,100,0.13)' : 'rgba(255,250,235,0.18)';
    ctx.beginPath();
    ctx.ellipse(rng() * w, rng() * h, 0.1 + rng() * 0.25, 0.06 + rng() * 0.15, rng() * 3, 0, Math.PI * 2);
    ctx.fill();
  }
  applyGrain(ctx, w, h, 0.35, 11);
  const beamW = 0.17;
  const drawBeam = (x0, y0, x1, y1) => {
    const col = jitter(beam, rng, 0.12);
    ctx.strokeStyle = rgbToCss(col);
    ctx.lineWidth = beamW;
    ctx.lineCap = 'butt';
    ctx.beginPath();
    ctx.moveTo(x0, y0);
    ctx.lineTo(x1, y1);
    ctx.stroke();
    ctx.strokeStyle = css(col, 1.35, 0.5);
    ctx.lineWidth = 0.025;
    ctx.stroke();
  };
  drawBeam(0, beamW / 2, w, beamW / 2);
  drawBeam(0, h - beamW / 2, w, h - beamW / 2);
  drawBeam(0, h * 0.52, w, h * 0.52);
  const n = Math.max(1, Math.round(w / bays));
  for (let i = 0; i <= n; i += 1) {
    const u = Math.min(w - beamW / 2, Math.max(beamW / 2, (w * i) / n));
    drawBeam(u, 0, u, h);
    if (i < n && i % 2 === 0) {
      const u2 = (w * (i + 1)) / n;
      drawBeam(u + 0.05, beamW, u2 - 0.05, h * 0.52 - 0.05);
    }
  }
}

/** Shared roof base for straw, shingles and tiles. Rows run along u. */
export function thatch(ctx, w, h, rng, { base = '#b6935a' } = {}) {
  ctx.fillStyle = base;
  ctx.fillRect(0, -0.12, w, h + 0.12);
  const buckets = [
    ['#d7b97c', []],
    ['#a98552', []],
    ['#7c5d35', []],
    ['#c4a46a', []],
  ];
  const count = Math.round(w * h * 520);
  for (let i = 0; i < count; i += 1) {
    const u = rng() * w;
    const v = -0.12 + rng() * (h + 0.1);
    const len = 0.12 + rng() * 0.28;
    buckets[Math.floor(rng() * buckets.length)][1].push(u, v, (rng() - 0.5) * 0.05, len);
  }
  ctx.lineWidth = 0.022;
  ctx.lineCap = 'round';
  for (const [color, strokes] of buckets) {
    ctx.strokeStyle = color;
    ctx.beginPath();
    for (let i = 0; i < strokes.length; i += 4) {
      ctx.moveTo(strokes[i], strokes[i + 1]);
      ctx.lineTo(strokes[i] + strokes[i + 2], strokes[i + 1] + strokes[i + 3]);
    }
    ctx.stroke();
  }
  // Courses: each row's lower edge throws a soft shadow on the row below.
  for (let v = 0.36; v < h; v += 0.36) {
    const g = ctx.createLinearGradient(0, v, 0, v - 0.14);
    g.addColorStop(0, 'rgba(60,40,20,0.35)');
    g.addColorStop(1, 'rgba(60,40,20,0)');
    ctx.fillStyle = g;
    ctx.fillRect(0, v - 0.14, w, 0.14);
  }
  // Ridge cap.
  ctx.fillStyle = 'rgba(95,70,40,0.85)';
  ctx.fillRect(0, h - 0.22, w, 0.22);
  ctx.strokeStyle = 'rgba(60,42,22,0.7)';
  ctx.lineWidth = 0.02;
  for (let u = 0; u < w; u += 0.09) {
    ctx.beginPath();
    ctx.moveTo(u, h - 0.22);
    ctx.lineTo(u + 0.05, h);
    ctx.stroke();
  }
}

export function shingles(ctx, w, h, rng, { base = '#6d5b49' } = {}) {
  ctx.fillStyle = css(base, 0.5);
  ctx.fillRect(0, 0, w, h);
  const rowH = 0.2;
  let row = 0;
  for (let v = h - rowH; v > -rowH; v -= rowH) {
    let u = row % 2 ? -0.1 : 0;
    while (u < w) {
      const sw = 0.16 + rng() * 0.14;
      const col = jitter(base, rng, 0.16);
      const g = ctx.createLinearGradient(0, v, 0, v + rowH * 1.4);
      g.addColorStop(0, css(col, 0.95));
      g.addColorStop(1, css(col, 1.15));
      ctx.fillStyle = g;
      ctx.fillRect(u + 0.01, v - 0.02, sw - 0.02, rowH * 1.45);
      ctx.fillStyle = 'rgba(20,14,8,0.6)';
      ctx.fillRect(u, v - 0.02, 0.012, rowH * 1.45);
      u += sw;
    }
    ctx.fillStyle = 'rgba(15,10,6,0.45)';
    ctx.fillRect(0, v - 0.02, w, 0.035);
    row += 1;
  }
  applyGrain(ctx, w, h, 0.3, 13);
  ctx.fillStyle = css(base, 0.62);
  ctx.fillRect(0, h - 0.14, w, 0.14);
}

export function clayTiles(ctx, w, h, rng, { base = '#b4603e' } = {}) {
  ctx.fillStyle = css(base, 0.45);
  ctx.fillRect(0, 0, w, h);
  const colW = 0.21;
  const rowH = 0.3;
  for (let u = 0; u < w; u += colW) {
    const col = jitter(base, rng, 0.1);
    const g = ctx.createLinearGradient(u, 0, u + colW, 0);
    g.addColorStop(0, css(col, 0.68));
    g.addColorStop(0.35, css(col, 1.18));
    g.addColorStop(0.7, css(col, 0.98));
    g.addColorStop(1, css(col, 0.6));
    ctx.fillStyle = g;
    ctx.fillRect(u + 0.01, 0, colW - 0.02, h);
  }
  for (let v = 0; v < h; v += rowH) {
    for (let u = 0; u < w; u += colW) {
      ctx.fillStyle = 'rgba(50,20,10,0.38)';
      ctx.beginPath();
      ctx.ellipse(u + colW / 2, v + 0.01, colW / 2, 0.04, 0, 0, Math.PI);
      ctx.fill();
      if (rng() < 0.15) {
        ctx.fillStyle = 'rgba(90,100,60,0.3)';
        ctx.fillRect(u + 0.03, v + 0.05, colW * 0.6, 0.12);
      }
    }
  }
  applyGrain(ctx, w, h, 0.3, 17);
  ctx.fillStyle = css(base, 0.7);
  ctx.fillRect(0, h - 0.16, w, 0.16);
}

export function paintMaterial(ctx, w, h, rng, mat) {
  switch (mat.kind) {
    case 'planks':
      return planks(ctx, w, h, rng, mat);
    case 'stone':
      return stoneWall(ctx, w, h, rng, mat);
    case 'logs':
      return logs(ctx, w, h, rng, mat);
    case 'plaster':
      return plasterFrame(ctx, w, h, rng, mat);
    case 'thatch':
      return thatch(ctx, w, h, rng, mat);
    case 'shingles':
      return shingles(ctx, w, h, rng, mat);
    case 'tiles':
      return clayTiles(ctx, w, h, rng, mat);
    default:
      ctx.fillStyle = mat.color ?? '#888';
      ctx.fillRect(0, 0, w, h);
      return undefined;
  }
}

/** A planked door in a wall, local metres; u is the left edge. */
export function door(ctx, u, w, h, rng, { wood = '#5b3d24', arch = false } = {}) {
  ctx.fillStyle = '#2a1c12';
  ctx.fillRect(u - 0.08, 0, w + 0.16, h + 0.1);
  const inner = (x, y, ww, hh) => {
    ctx.beginPath();
    if (arch) {
      ctx.moveTo(x, y);
      ctx.lineTo(x, y + hh - ww / 2);
      ctx.ellipse(x + ww / 2, y + hh - ww / 2, ww / 2, ww / 2.4, 0, Math.PI, 0, true);
      ctx.lineTo(x + ww, y);
    } else {
      ctx.rect(x, y, ww, hh);
    }
    ctx.closePath();
  };
  ctx.save();
  inner(u, 0, w, h);
  ctx.clip();
  planks(ctx, w + u + 0.5, h + 0.2, rng, { base: wood, board: 0.16 });
  ctx.fillStyle = 'rgba(0,0,0,0.25)';
  ctx.fillRect(u, h - 0.12, w, 0.12);
  ctx.restore();
  ctx.fillStyle = '#2b2b2b';
  ctx.fillRect(u + 0.05, h * 0.25, w * 0.55, 0.05);
  ctx.fillRect(u + 0.05, h * 0.72, w * 0.55, 0.05);
  ctx.beginPath();
  ctx.arc(u + w * 0.8, h * 0.48, 0.04, 0, Math.PI * 2);
  ctx.fill();
}

/** A shuttered window; (u, v) is the lower-left corner. */
export function window_(ctx, u, v, w, h, rng, { frame = '#4a3322', shutters = '#5e6b48', glow = false } = {}) {
  ctx.fillStyle = frame;
  ctx.fillRect(u - 0.07, v - 0.07, w + 0.14, h + 0.14);
  const g = ctx.createLinearGradient(0, v + h, 0, v);
  g.addColorStop(0, '#120c08');
  g.addColorStop(1, glow ? '#5a3a18' : '#2a2018');
  ctx.fillStyle = g;
  ctx.fillRect(u, v, w, h);
  ctx.fillStyle = frame;
  ctx.fillRect(u + w / 2 - 0.025, v, 0.05, h);
  ctx.fillRect(u, v + h / 2 - 0.025, w, 0.05);
  if (shutters) {
    const col = jitter(shutters, rng, 0.08);
    ctx.fillStyle = rgbToCss(col);
    ctx.fillRect(u - 0.07 - w * 0.42, v - 0.04, w * 0.4, h + 0.08);
    ctx.fillRect(u + w + 0.07, v - 0.04, w * 0.4, h + 0.08);
    ctx.fillStyle = 'rgba(0,0,0,0.3)';
    for (const x of [u - 0.07 - w * 0.42, u + w + 0.07]) {
      for (let s = 0.08; s < w * 0.4; s += 0.1) ctx.fillRect(x + s, v - 0.04, 0.012, h + 0.08);
    }
  }
  // Sill.
  ctx.fillStyle = css(frame, 1.2);
  ctx.fillRect(u - 0.12, v - 0.12, w + 0.24, 0.07);
}
