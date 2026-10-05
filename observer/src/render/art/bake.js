// Baking: paint assets (or, later, load pack images) into the shared atlas,
// and bake ground tiles for a sample scene.

import { project, unproject } from '../../world/coords.js';
import { CIV_COLORS, checkContract, staticAssetPainters } from './registry.js';
import { ANIMATIONS, dressLook, envoyFrameCanvas, personFrameCanvas } from './paint/people.js';
import { paintOx, paintWagon } from './paint/vehicles.js';
import { GROUND_TILE, buildGroundField, paintGroundTile } from './paint/ground.js';

/** People, vehicles, nature and props are stored at screen size (1x). */
const ONE_X = { art: 1 };

export const nextFrame = () => new Promise((r) => requestAnimationFrame(() => r()));

function personShadowCanvas() {
  const c = document.createElement('canvas');
  c.width = 40;
  c.height = 20;
  const ctx = c.getContext('2d');
  ctx.filter = 'blur(2px)';
  ctx.fillStyle = 'rgba(20,24,40,0.9)';
  ctx.beginPath();
  ctx.ellipse(22, 10, 13, 6, 0, 0, Math.PI * 2);
  ctx.fill();
  return { canvas: c, anchor: { x: 18, y: 10 } };
}

/** Paint every static asset once. Returns per-asset info and contract checks. */
export async function bakeStaticAssets(atlas, civColor, status = () => {}) {
  const assetInfo = new Map();
  const contract = [];
  let count = 0;
  for (const [id, p] of Object.entries(staticAssetPainters(civColor))) {
    status(`Painting ${id}`);
    const r = p.paint();
    // Buildings keep their full 2x art for the settlement band; the rest is stored at 1x, as
    // are painters that ask for it (the town walls' small modules).
    const art = { art: p.art ?? (p.category === 'building' ? 2 : 1) };
    atlas.add(id, r.canvas, r.anchor, {}, art);
    if (r.shadow) atlas.add(`${id}#shadow`, r.shadow.canvas, r.shadow.anchor, {}, art);
    assetInfo.set(id, { footprint: r.footprint, doors: r.doors, category: p.category, height: r.height });
    // Buildings may exceed their wall footprint by the roof overhang (<= 0.6 m a side).
    contract.push(checkContract(id, r, p.category === 'building' ? 1.3 : 0.9));
    count += 1;
    if (count % 6 === 0) await nextFrame();
  }
  return { assetInfo, contract };
}

/**
 * A frame's civilization accents alone: the pixels that change with the accent
 * colour, in the shades they were painted with white accents. Tinted with a
 * civilization's colour and drawn over the frame, they dress it in that colour.
 * Cropped to the accents (on even pixels, so halving keeps it aligned with the
 * frame); returns the canvas and the frame's anchor in its pixels.
 */
function accentMask(white, black, anchor) {
  const W = white.width;
  const H = white.height;
  const w = white.getContext('2d').getImageData(0, 0, W, H);
  const b = black.getContext('2d').getImageData(0, 0, W, H).data;
  const d = w.data;
  let x0 = W;
  let y0 = H;
  let x1 = 0;
  let y1 = 0;
  for (let i = 0; i < d.length; i += 4) {
    const differs = Math.abs(d[i] - b[i]) + Math.abs(d[i + 1] - b[i + 1]) + Math.abs(d[i + 2] - b[i + 2]) > 24;
    if (!differs || d[i + 3] === 0) {
      d[i + 3] = 0;
      continue;
    }
    const x = (i / 4) % W;
    const y = Math.floor(i / 4 / W);
    x0 = Math.min(x0, x);
    y0 = Math.min(y0, y);
    x1 = Math.max(x1, x + 1);
    y1 = Math.max(y1, y + 1);
  }
  if (x1 <= x0) return null;
  x0 -= x0 % 2;
  y0 -= y0 % 2;
  x1 = Math.min(W, x1 + (x1 % 2));
  y1 = Math.min(H, y1 + (y1 % 2));
  const mask = document.createElement('canvas');
  mask.width = x1 - x0;
  mask.height = y1 - y0;
  mask.getContext('2d').putImageData(w, -x0, -y0, x0, y0, x1 - x0, y1 - y0);
  return { canvas: mask, anchor: { x: anchor.x - x0, y: anchor.y - y0 } };
}

/** The atlas key of a citizen frame: one set per design, whatever the civilization. */
export function personFrameKey(appearance, anim, facing, i) {
  return `person.${appearance}.${anim}.${facing}.${i}`;
}

/**
 * Citizen frames for every design a scene uses, plus vehicles. Each frame is
 * painted once with white accents, with a mask of those accents beside it
 * (`#mask`); a civilization's colour is a tint on the mask, so four
 * civilizations cost no more frames than one.
 */
export async function bakeSceneActors(atlas, scene, status = () => {}) {
  const designs = new Set();
  for (const p of scene.people) if (!p.envoy) designs.add(p.appearance);
  if (scene.caravan) designs.add(scene.caravan.driver.appearance);
  for (const a of [...designs].sort((x, y) => x - y)) {
    status(`Painting citizen design ${a}`);
    const white = dressLook(a, '#ffffff');
    const black = dressLook(a, '#000000');
    for (const [anim, spec] of Object.entries(ANIMATIONS)) {
      for (const facing of spec.facings) {
        for (let i = 0; i < spec.frames; i += 1) {
          const f = personFrameCanvas(white, anim, facing, i / spec.frames);
          const g = personFrameCanvas(black, anim, facing, i / spec.frames);
          const key = personFrameKey(a, anim, facing, i);
          atlas.add(key, f.canvas, f.anchor, {}, ONE_X);
          const mask = accentMask(f.canvas, g.canvas, f.anchor);
          if (mask) atlas.add(`${key}#mask`, mask.canvas, mask.anchor, {}, ONE_X);
        }
      }
    }
    await nextFrame();
  }
  for (const p of scene.people.filter((q) => q.envoy)) {
    const look = dressLook(p.appearance, CIV_COLORS[p.civ]);
    for (const facing of ['front', 'back']) {
      for (let i = 0; i < 8; i += 1) {
        const f = envoyFrameCanvas(look, facing, i / 8, CIV_COLORS[p.civ]);
        atlas.add(`envoy.${p.civ}.${facing}.${i}`, f.canvas, f.anchor, {}, ONE_X);
      }
      const f = envoyFrameCanvas(look, facing, -1, CIV_COLORS[p.civ]);
      atlas.add(`envoy.${p.civ}.${facing}.idle`, f.canvas, f.anchor, {}, ONE_X);
    }
  }
  status('Painting ox and wagon');
  for (const facing of ['front', 'back']) {
    for (let i = 0; i < 8; i += 1) {
      const r = paintOx({ facing, frame: i });
      atlas.add(`ox.${facing}.${i}`, r.canvas, r.anchor, {}, ONE_X);
      if (i === 0) atlas.add(`ox.${facing}#shadow`, r.shadow.canvas, r.shadow.anchor, {}, ONE_X);
    }
  }
  for (const axis of ['x', 'y']) {
    for (const front of [1, -1]) {
      for (let i = 0; i < 4; i += 1) {
        const r = paintWagon({ id: 'wagon', axis, front, frame: i, civColor: CIV_COLORS[3] });
        atlas.add(`wagon.${axis}.${front}.${i}`, r.canvas, r.anchor, {}, ONE_X);
        if (i === 0) atlas.add(`wagon.${axis}.${front}#shadow`, r.shadow.canvas, r.shadow.anchor, {}, ONE_X);
      }
    }
  }
  const ps = personShadowCanvas();
  atlas.add('shadow.person', ps.canvas, ps.anchor, {}, ONE_X);
}

/**
 * Bake screen-space ground tiles covering a scene's bounds (local metres).
 * Returns a container of tile sprites positioned in local screen space.
 */
export async function bakeSceneGround(
  PIXI,
  scene,
  status = () => {},
  { alpha = null, bounds = scene.bounds, texelScale = 1, mipmaps = true } = {},
) {
  status('Painting the ground');
  await nextFrame();
  const container = new PIXI.Container();
  const corners = [
    project(bounds.x0, bounds.y0),
    project(bounds.x1, bounds.y1),
    project(bounds.x0, bounds.y1),
    project(bounds.x1, bounds.y0),
  ];
  const gx0 = Math.floor(Math.min(...corners.map((c) => c.x)) / GROUND_TILE.w) * GROUND_TILE.w;
  const gx1 = Math.ceil(Math.max(...corners.map((c) => c.x)) / GROUND_TILE.w) * GROUND_TILE.w;
  const gy0 = Math.floor(Math.min(...corners.map((c) => c.y)) / GROUND_TILE.h) * GROUND_TILE.h;
  const gy1 = Math.ceil(Math.max(...corners.map((c) => c.y)) / GROUND_TILE.h) * GROUND_TILE.h;
  // The colour field covers every baked tile, not just the scene bounds, so
  // nothing is stretched at the edges.
  const tileCorners = [unproject(gx0, gy0), unproject(gx1, gy0), unproject(gx0, gy1), unproject(gx1, gy1)];
  const field = buildGroundField(scene.ground, {
    x0: Math.floor(Math.min(...tileCorners.map((c) => c.x))) - 1,
    y0: Math.floor(Math.min(...tileCorners.map((c) => c.y))) - 1,
    x1: Math.ceil(Math.max(...tileCorners.map((c) => c.x))) + 1,
    y1: Math.ceil(Math.max(...tileCorners.map((c) => c.y))) + 1,
  });
  let bytes = 0;
  for (let sy = gy0; sy < gy1; sy += GROUND_TILE.h) {
    for (let sx = gx0; sx < gx1; sx += GROUND_TILE.w) {
      let canvas = paintGroundTile(field, sx, sy, GROUND_TILE.w, GROUND_TILE.h, 7, alpha);
      if (texelScale !== 1) {
        // Kept at fewer texels than screen pixels at zoom 1: the ground is soft by nature.
        const small = document.createElement('canvas');
        small.width = Math.round(GROUND_TILE.w * texelScale);
        small.height = Math.round(GROUND_TILE.h * texelScale);
        const ctx = small.getContext('2d');
        ctx.imageSmoothingQuality = 'high';
        ctx.drawImage(canvas, 0, 0, small.width, small.height);
        canvas = small;
      }
      const source = new PIXI.CanvasSource({ resource: canvas, autoGenerateMipmaps: mipmaps, scaleMode: 'linear' });
      const sprite = new PIXI.Sprite(new PIXI.Texture({ source }));
      sprite.position.set(sx, sy);
      sprite.scale.set(1 / texelScale);
      container.addChild(sprite);
      bytes += canvas.width * canvas.height * 4 * (mipmaps ? 4 / 3 : 1);
      await nextFrame();
    }
  }
  return { container, bytes, field };
}
