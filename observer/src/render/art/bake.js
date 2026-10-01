// Baking: paint assets (or, later, load pack images) into the shared atlas,
// and bake ground tiles for a sample scene.

import { project, unproject } from '../../world/coords.js';
import { CIV_COLORS, checkContract, staticAssetPainters } from './registry.js';
import { ANIMATIONS, dressLook, envoyFrameCanvas, personFrameCanvas } from './paint/people.js';
import { paintOx, paintWagon } from './paint/vehicles.js';
import { GROUND_TILE, buildGroundField, paintGroundTile } from './paint/ground.js';

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
    atlas.add(id, r.canvas, r.anchor);
    if (r.shadow) atlas.add(`${id}#shadow`, r.shadow.canvas, r.shadow.anchor);
    assetInfo.set(id, { footprint: r.footprint, doors: r.doors, category: p.category, height: r.height });
    // Buildings may exceed their wall footprint by the roof overhang (<= 0.6 m a side).
    contract.push(checkContract(id, r, p.category === 'building' ? 1.3 : 0.9));
    count += 1;
    if (count % 6 === 0) await nextFrame();
  }
  return { assetInfo, contract };
}

/** Citizen frames for every (design, civilization) a scene uses, plus vehicles. */
export async function bakeSceneActors(atlas, scene, status = () => {}) {
  const combos = new Set();
  for (const p of scene.people) if (!p.envoy) combos.add(`${p.appearance}:${p.civ}`);
  if (scene.caravan) combos.add(`${scene.caravan.driver.appearance}:${scene.caravan.civ}`);
  for (const combo of combos) {
    const [a, c] = combo.split(':').map(Number);
    status(`Painting citizen design ${a} for civilization ${c + 1}`);
    const look = dressLook(a, CIV_COLORS[c]);
    for (const [anim, spec] of Object.entries(ANIMATIONS)) {
      for (const facing of spec.facings) {
        for (let i = 0; i < spec.frames; i += 1) {
          const f = personFrameCanvas(look, anim, facing, i / spec.frames);
          atlas.add(`person.${a}.${c}.${anim}.${facing}.${i}`, f.canvas, f.anchor);
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
        atlas.add(`envoy.${p.civ}.${facing}.${i}`, f.canvas, f.anchor);
      }
      const f = envoyFrameCanvas(look, facing, -1, CIV_COLORS[p.civ]);
      atlas.add(`envoy.${p.civ}.${facing}.idle`, f.canvas, f.anchor);
    }
  }
  status('Painting ox and wagon');
  for (const facing of ['front', 'back']) {
    for (let i = 0; i < 8; i += 1) {
      const r = paintOx({ facing, frame: i });
      atlas.add(`ox.${facing}.${i}`, r.canvas, r.anchor);
      if (i === 0) atlas.add(`ox.${facing}#shadow`, r.shadow.canvas, r.shadow.anchor);
    }
  }
  for (const axis of ['x', 'y']) {
    for (const front of [1, -1]) {
      for (let i = 0; i < 4; i += 1) {
        const r = paintWagon({ id: 'wagon', axis, front, frame: i, civColor: CIV_COLORS[3] });
        atlas.add(`wagon.${axis}.${front}.${i}`, r.canvas, r.anchor);
        if (i === 0) atlas.add(`wagon.${axis}.${front}#shadow`, r.shadow.canvas, r.shadow.anchor);
      }
    }
  }
  const ps = personShadowCanvas();
  atlas.add('shadow.person', ps.canvas, ps.anchor);
}

/**
 * Bake screen-space ground tiles covering a scene's bounds (local metres).
 * Returns a container of tile sprites positioned in local screen space.
 */
export async function bakeSceneGround(PIXI, scene, status = () => {}) {
  status('Painting the ground');
  await nextFrame();
  const container = new PIXI.Container();
  const corners = [
    project(scene.bounds.x0, scene.bounds.y0),
    project(scene.bounds.x1, scene.bounds.y1),
    project(scene.bounds.x0, scene.bounds.y1),
    project(scene.bounds.x1, scene.bounds.y0),
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
      const canvas = paintGroundTile(field, sx, sy, GROUND_TILE.w, GROUND_TILE.h);
      const source = new PIXI.CanvasSource({ resource: canvas, autoGenerateMipmaps: true, scaleMode: 'linear' });
      const sprite = new PIXI.Sprite(new PIXI.Texture({ source }));
      sprite.position.set(sx, sy);
      container.addChild(sprite);
      bytes += GROUND_TILE.w * GROUND_TILE.h * 4 * 1.34;
      await nextFrame();
    }
  }
  return { container, bytes, field };
}
