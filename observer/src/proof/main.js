// Close-zoom art proof (O1a). PROTOTYPE ARTWORK, SAMPLE DATA.
//
// Renders a sample village with the real asset registry, atlas baker and
// depth sorter. Movement is a deterministic visual approximation; nothing
// here reads or changes simulation state.

import * as PIXI from '../../vendor/pixi/pixi.min.mjs';
import { K, project, unproject } from '../world/coords.js';
import { Atlas } from '../render/art/atlas.js';
import { ART } from '../render/art/paint/iso.js';
import { CIV_COLORS, checkContract, personAssetContract, staticAssetPainters } from '../render/art/registry.js';
import { ANIMATIONS, dressLook, envoyFrameCanvas, personFrameCanvas } from '../render/art/paint/people.js';
import { paintOx, paintWagon } from '../render/art/paint/vehicles.js';
import { GROUND_TILE, buildGroundField, paintGroundTile } from '../render/art/paint/ground.js';
import { depthSort } from '../render/depth.js';
import { sampleSchedule } from '../sim/paths.js';
import { civilizationLabel } from '../data/naming.js';
import { villageScene } from './scene-village.js';
import { depthScene } from './scene-depth.js';

const params = new URLSearchParams(location.search);
const SCENE = params.get('scene') === 'depth' ? 'depth' : 'village';
const DEBUG = params.has('debug') || SCENE === 'depth';
const CENTRE_ONLY = params.get('depth') === 'centre';
const CIV_NAMES = [
  'civilization:0000000001',
  'civilization:0000000002',
  'civilization:0000000003',
  'civilization:0000000004',
];

const nextFrame = () => new Promise((r) => requestAnimationFrame(() => r()));

function setStatus(text) {
  const el = document.getElementById('loading-status');
  if (el) el.textContent = text;
}

function hexNum(css) {
  return parseInt(css.replace('#', ''), 16);
}

function translateFp(fp, x, y) {
  return { minX: fp.minX + x, minY: fp.minY + y, maxX: fp.maxX + x, maxY: fp.maxY + y };
}

function fpDepth(fp) {
  return (fp.minX + fp.maxX) / 2 + (fp.minY + fp.maxY) / 2;
}

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

async function main() {
  const stageEl = document.getElementById('stage');
  const app = new PIXI.Application();
  await app.init({
    resizeTo: stageEl,
    background: '#1d2126',
    antialias: !DEBUG,
    preference: 'webgl',
    resolution: DEBUG ? 1 : Math.min(2, window.devicePixelRatio || 1),
    autoDensity: true,
    preserveDrawingBuffer: DEBUG,
  });
  stageEl.appendChild(app.canvas);

  // ---------------------------------------------------------------- assets
  const atlas = new Atlas(PIXI);
  const assetInfo = new Map();
  const contract = [];
  const village = CIV_COLORS[0];
  const painters = staticAssetPainters(village);
  let count = 0;
  for (const [id, p] of Object.entries(painters)) {
    setStatus(`Painting ${id}`);
    const r = p.paint();
    atlas.add(id, r.canvas, r.anchor);
    if (r.shadow) atlas.add(`${id}#shadow`, r.shadow.canvas, r.shadow.anchor);
    assetInfo.set(id, { footprint: r.footprint, doors: r.doors, category: p.category, height: r.height });
    // Buildings may exceed their wall footprint by the roof overhang (<= 0.6 m a side).
    contract.push(checkContract(id, r, p.category === 'building' ? 1.3 : 0.9));
    count += 1;
    if (count % 6 === 0) await nextFrame();
  }
  const footprintOf = (asset) => assetInfo.get(asset).footprint;
  const scene = SCENE === 'depth' ? depthScene(footprintOf) : villageScene(footprintOf);

  // Citizen frames for every (design, civilization) actually used.
  const combos = new Set();
  for (const p of scene.people) if (!p.envoy) combos.add(`${p.appearance}:${p.civ}`);
  if (scene.caravan) combos.add(`${scene.caravan.driver.appearance}:${scene.caravan.civ}`);
  for (const combo of combos) {
    const [a, c] = combo.split(':').map(Number);
    setStatus(`Painting citizen design ${a} for civilization ${c + 1}`);
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
  setStatus('Painting ox and wagon');
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
  atlas.finalize();

  // ---------------------------------------------------------------- ground
  setStatus('Painting the ground');
  await nextFrame();
  const groundLayer = new PIXI.Container();
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
  let groundBytes = 0;
  for (let sy = gy0; sy < gy1; sy += GROUND_TILE.h) {
    for (let sx = gx0; sx < gx1; sx += GROUND_TILE.w) {
      const canvas = paintGroundTile(field, sx, sy, GROUND_TILE.w, GROUND_TILE.h);
      const source = new PIXI.CanvasSource({ resource: canvas, autoGenerateMipmaps: true, scaleMode: 'linear' });
      const sprite = new PIXI.Sprite(new PIXI.Texture({ source }));
      sprite.position.set(sx, sy);
      groundLayer.addChild(sprite);
      groundBytes += GROUND_TILE.w * GROUND_TILE.h * 4 * 1.34;
      await nextFrame();
    }
  }

  // ---------------------------------------------------------------- layers
  const world = new PIXI.Container();
  const shadowLayer = new PIXI.Container();
  shadowLayer.alpha = 0.42;
  const ringLayer = new PIXI.Graphics();
  const objectLayer = new PIXI.Container();
  objectLayer.sortableChildren = true;
  const overlay = new PIXI.Graphics();
  world.addChild(groundLayer, shadowLayer, ringLayer, objectLayer, overlay);
  app.stage.addChild(world);

  const objects = [];
  const byId = new Map();
  let flat = false;

  function spriteFor(entry) {
    const s = new PIXI.Sprite(entry.texture);
    s.anchor.set(entry.anchor.x / entry.w, entry.anchor.y / entry.h);
    s.scale.set(1 / ART);
    return s;
  }

  function setRect(o) {
    const e = o.entry;
    const p = o.sprite.position;
    const ax = o.flip ? e.w - e.anchor.x : e.anchor.x;
    const x0 = p.x - ax / ART;
    const y0 = p.y - e.anchor.y / ART;
    o.rect = { x0, y0, x1: x0 + e.w / ART, y1: y0 + e.h / ART };
  }

  function addObject(o) {
    o.index = objects.length;
    objects.push(o);
    byId.set(o.id, o);
    objectLayer.addChild(o.sprite);
    if (o.shadow) shadowLayer.addChild(o.shadow);
    return o;
  }

  for (const s of scene.statics) {
    const entry = atlas.get(s.asset);
    const sprite = spriteFor(entry);
    const p = project(s.x, s.y);
    sprite.position.set(p.x, p.y);
    const shadowEntry = atlas.get(`${s.asset}#shadow`);
    let shadow = null;
    if (shadowEntry) {
      shadow = spriteFor(shadowEntry);
      shadow.position.set(p.x, p.y);
    }
    const fp = translateFp(assetInfo.get(s.asset).footprint, s.x, s.y);
    const o = {
      id: s.id,
      kind: s.kind,
      label: s.label,
      asset: s.asset,
      x: s.x,
      y: s.y,
      entry,
      sprite,
      shadow,
      fp,
      depth: fpDepth(fp),
      flip: false,
      static: true,
      partOf: s.partOf,
      height: assetInfo.get(s.asset).height,
    };
    setRect(o);
    addObject(o);
  }

  const people = [];
  for (const p of scene.people) {
    const sprite = new PIXI.Sprite();
    sprite.scale.set(1 / ART);
    const shadow = spriteFor(atlas.get('shadow.person'));
    const o = addObject({
      id: p.id,
      kind: 'person',
      label: p.record.label,
      person: p,
      sprite,
      shadow,
      static: false,
      flip: false,
      entry: null,
    });
    people.push(o);
  }

  let caravan = null;
  if (scene.caravan) {
    const c = scene.caravan;
    const mk = (id, kind, label) => {
      const sprite = new PIXI.Sprite();
      sprite.scale.set(1 / ART);
      const shadow = new PIXI.Sprite();
      shadow.scale.set(1 / ART);
      return addObject({ id, kind, label, sprite, shadow, static: false, flip: false, entry: null });
    };
    caravan = {
      data: c,
      ox: mk(`${c.id}-ox`, 'ox', 'Ox (caravan)'),
      wagon: mk(`${c.id}-wagon`, 'wagon', 'Caravan wagon'),
      driver: null,
    };
    const dShadow = spriteFor(atlas.get('shadow.person'));
    const dSprite = new PIXI.Sprite();
    dSprite.scale.set(1 / ART);
    caravan.driver = addObject({
      id: c.driver.id,
      kind: 'person',
      label: c.driver.record.label,
      person: { ...c.driver, caravan: true },
      sprite: dSprite,
      shadow: dShadow,
      static: false,
      flip: false,
      entry: null,
    });
  }
  if (scene.pinnedWagon) {
    const w = scene.pinnedWagon;
    const entry = atlas.get('wagon.x.1.0');
    const sprite = spriteFor(entry);
    const p = project(w.x, w.y);
    sprite.position.set(p.x, p.y);
    const fp = { minX: w.x - 1.5, minY: w.y - 0.75, maxX: w.x + 1.5, maxY: w.y + 0.75 };
    const o = {
      id: w.id,
      kind: 'wagon',
      label: 'Wagon',
      entry,
      sprite,
      shadow: null,
      fp,
      depth: fpDepth(fp),
      flip: false,
      static: true,
    };
    setRect(o);
    addObject(o);
  }

  // ---------------------------------------------------------------- state
  let t = SCENE === 'depth' ? 0 : 30;
  let paused = SCENE === 'depth';
  let speed = 1;
  let zoom = SCENE === 'depth' ? 0.42 : 1;
  const cam = project(scene.focus.x, scene.focus.y);
  let selected = null;
  let follow = false;
  let showFootprints = false;
  const occupancy = new Map();
  let drawOrder = [];
  let lastCycles = [];
  const frameTimes = [];

  function personTextureKey(p, s) {
    if (p.envoy)
      return s.moving ? `envoy.${p.civ}.${s.facing}.${Math.floor(s.phase * 8) % 8}` : `envoy.${p.civ}.${s.facing}.idle`;
    const spec = ANIMATIONS[s.anim] ?? ANIMATIONS.idle;
    const facing = spec.facings.includes(s.facing) ? s.facing : spec.facings[0];
    const i = Math.floor(s.phase * spec.frames) % spec.frames;
    return `person.${p.appearance}.${p.civ}.${s.anim}.${facing}.${i}`;
  }

  function placeDynamic(o, x, y, key, flip, fpHalf) {
    const entry = atlas.get(key);
    o.entry = entry;
    o.flip = flip;
    o.x = x;
    o.y = y;
    const p = project(x, y);
    o.sprite.texture = flat ? atlas.silhouette(entry) : entry.texture;
    o.sprite.anchor.set(entry.anchor.x / entry.w, entry.anchor.y / entry.h);
    o.sprite.scale.set((flip ? -1 : 1) / ART, 1 / ART);
    o.sprite.position.set(p.x, p.y);
    o.sprite.visible = true;
    o.fp = { minX: x - fpHalf[0], minY: y - fpHalf[1], maxX: x + fpHalf[0], maxY: y + fpHalf[1] };
    o.depth = x + y;
    setRect(o);
  }

  function updateDynamics() {
    occupancy.clear();
    for (const o of people) {
      const s = sampleSchedule(o.person.schedule, t);
      o.state = s;
      if (s.inside) {
        o.sprite.visible = false;
        o.shadow.visible = false;
        o.hidden = true;
        if (!occupancy.has(s.inside)) occupancy.set(s.inside, []);
        occupancy.get(s.inside).push(o.id);
        o.x = s.x;
        o.y = s.y;
        continue;
      }
      o.hidden = false;
      placeDynamic(o, s.x, s.y, personTextureKey(o.person, s), s.flip, [0.25, 0.25]);
      const p = project(s.x, s.y);
      o.shadow.position.set(p.x, p.y);
      o.shadow.visible = true;
    }
    if (caravan) {
      const s = sampleSchedule(caravan.data.schedule, t);
      const parts = [caravan.ox, caravan.wagon, caravan.driver];
      if (s.inside) {
        for (const o of parts) {
          o.sprite.visible = false;
          o.shadow.visible = false;
          o.hidden = true;
          o.state = s;
        }
      } else {
        const dir = s.flip ? 1 : -1; // along x: flip means moving +x
        const step = s.moving ? Math.floor((s.phase * 1.35) / 0.35) : 0;
        const oxKey = `ox.${s.facing}.${s.moving ? Math.floor(s.phase * 8) % 8 : 0}`;
        placeDynamic(caravan.ox, s.x, s.y, oxKey, s.flip, [1.1, 0.45]);
        caravan.ox.state = s;
        caravan.ox.hidden = false;
        const ws = atlas.get(`ox.${s.facing}#shadow`);
        caravan.ox.shadow.texture = ws.texture;
        caravan.ox.shadow.anchor.set(ws.anchor.x / ws.w, ws.anchor.y / ws.h);
        caravan.ox.shadow.position.copyFrom(caravan.ox.sprite.position);
        caravan.ox.shadow.scale.set((s.flip ? -1 : 1) / ART, 1 / ART);
        caravan.ox.shadow.visible = true;
        const wx = s.x - dir * 2.75;
        const front = dir;
        placeDynamic(caravan.wagon, wx, s.y, `wagon.x.${front}.${step % 4}`, false, [1.5, 0.75]);
        caravan.wagon.state = s;
        caravan.wagon.hidden = false;
        const wsh = atlas.get(`wagon.x.${front}#shadow`);
        caravan.wagon.shadow.texture = wsh.texture;
        caravan.wagon.shadow.anchor.set(wsh.anchor.x / wsh.w, wsh.anchor.y / wsh.h);
        caravan.wagon.shadow.position.copyFrom(caravan.wagon.sprite.position);
        caravan.wagon.shadow.visible = true;
        const d = caravan.driver;
        const ds = { ...s, anim: s.moving ? 'walk' : 'idle' };
        d.state = { ...ds, activity: s.activity, destination: s.destination };
        d.hidden = false;
        placeDynamic(d, s.x - dir * 0.6, s.y - 1.45, personTextureKey(d.person, ds), s.flip, [0.25, 0.25]);
        const p = project(d.x, d.y);
        d.shadow.position.set(p.x, p.y);
        d.shadow.visible = true;
      }
    }
  }

  function viewRect(margin = 80) {
    const w = app.screen.width;
    const h = app.screen.height;
    return {
      x0: (0 - world.position.x) / zoom - margin,
      y0: (0 - world.position.y) / zoom - margin,
      x1: (w - world.position.x) / zoom + margin,
      y1: (h - world.position.y) / zoom + margin,
    };
  }

  function applyCamera() {
    world.scale.set(zoom);
    world.position.set(app.screen.width / 2 - cam.x * zoom, app.screen.height / 2 - cam.y * zoom);
  }

  function clampCamera() {
    const lim = project(scene.bounds.x1, scene.bounds.y1);
    const limX = project(scene.bounds.x1, scene.bounds.y0).x;
    const limTop = project(scene.bounds.x0, scene.bounds.y0).y;
    cam.x = Math.max(-limX, Math.min(limX, cam.x));
    cam.y = Math.max(limTop, Math.min(lim.y, cam.y));
  }

  function colorOf(o) {
    const v = (o.index + 1) * 4;
    return ((v & 255) << 16) | (((v >> 8) & 255) << 8) | 128;
  }

  function frame(deltaMS, advance = !paused) {
    const t0 = performance.now();
    if (advance) t += (deltaMS / 1000) * speed;
    updateDynamics();
    if (follow && selected) {
      const o = byId.get(selected);
      let target = null;
      if (o && o.hidden && o.state?.inside && byId.has(o.state.inside)) {
        const b = byId.get(o.state.inside);
        target = project((b.fp.minX + b.fp.maxX) / 2, (b.fp.minY + b.fp.maxY) / 2, 2);
      } else if (o && !o.hidden) {
        target = project(o.x, o.y, 1);
      }
      if (target) {
        const k = 1 - Math.exp(-(deltaMS / 1000) * 4);
        cam.x += (target.x - cam.x) * k;
        cam.y += (target.y - cam.y) * k;
      }
    }
    clampCamera();
    applyCamera();
    const view = viewRect();
    const visible = [];
    for (const o of objects) {
      if (o.hidden) continue;
      const r = o.rect;
      const inView = r.x1 > view.x0 && r.x0 < view.x1 && r.y1 > view.y0 && r.y0 < view.y1;
      o.sprite.visible = inView;
      if (o.shadow) o.shadow.visible = inView && !flat;
      if (inView) visible.push(o);
    }
    const sorted = depthSort(visible, 128, { centreOnly: CENTRE_ONLY });
    drawOrder = sorted.order;
    lastCycles = sorted.cycles;
    for (let i = 0; i < drawOrder.length; i += 1) drawOrder[i].sprite.zIndex = i;
    // Highlights.
    const pulse = 0.5 + 0.5 * Math.sin(performance.now() / 220);
    for (const o of objects) {
      if (flat) {
        o.sprite.tint = colorOf(o);
        if (o.static) o.sprite.texture = atlas.silhouette(o.entry);
      } else {
        o.sprite.tint = 0xffffff;
      }
    }
    ringLayer.clear();
    overlay.clear();
    if (selected && !flat) {
      const o = byId.get(selected);
      if (o && o.hidden && o.state?.inside && byId.has(o.state.inside)) {
        const b = byId.get(o.state.inside);
        const g = Math.round(255 - pulse * 70);
        b.sprite.tint = (255 << 16) | (Math.round(255 - pulse * 25) << 8) | g;
        drawFootprint(ringLayer, b.fp, 0xffd84a, 3);
        // Marker above the roof: the person is inside this building.
        const top = project((b.fp.minX + b.fp.maxX) / 2, (b.fp.minY + b.fp.maxY) / 2, (b.height ?? 5) + 1.2);
        const bob = Math.sin(performance.now() / 300) * 3;
        const r = 9 / Math.max(zoom, 0.6);
        overlay
          .circle(top.x, top.y - r * 2.4 + bob, r)
          .fill(0xffd84a)
          .stroke({ width: 2 / zoom, color: 0x2a2010 });
        overlay
          .poly([
            top.x - r * 0.6,
            top.y - r * 1.7 + bob,
            top.x + r * 0.6,
            top.y - r * 1.7 + bob,
            top.x,
            top.y - r * 0.4 + bob,
          ])
          .fill(0xffd84a);
        overlay.circle(top.x, top.y - r * 2.65 + bob, r * 0.32).fill(0x2a2010);
        overlay.ellipse(top.x, top.y - r * 2.0 + bob, r * 0.5, r * 0.3).fill(0x2a2010);
      } else if (o && !o.hidden) {
        const p = project(o.x, o.y);
        ringLayer
          .ellipse(p.x, p.y, 0.62 * Math.SQRT2 * K, 0.62 * Math.SQRT2 * K * 0.5)
          .stroke({ width: 2.5, color: 0xffd84a });
      } else if (o && o.kind !== 'person') {
        drawFootprint(ringLayer, o.fp, 0xffd84a, 3);
      }
    }
    if (showFootprints) {
      for (const o of visible) {
        drawFootprint(overlay, o.fp, o.kind === 'person' ? 0x7fffd4 : o.kind === 'building' ? 0xff7050 : 0xffffff, 1);
      }
    }
    frameTimes.push(performance.now() - t0);
    if (frameTimes.length > 240) frameTimes.shift();
  }

  function drawFootprint(g, fp, color, width) {
    const a = project(fp.minX, fp.minY);
    const b = project(fp.maxX, fp.minY);
    const c = project(fp.maxX, fp.maxY);
    const d = project(fp.minX, fp.maxY);
    g.poly([a.x, a.y, b.x, b.y, c.x, c.y, d.x, d.y]).stroke({ width: width / zoom, color });
  }

  // ---------------------------------------------------------------- picking
  function hitTest(o, wx, wy) {
    if (o.hidden || !o.sprite.visible) return false;
    const r = o.rect;
    if (wx < r.x0 || wx > r.x1 || wy < r.y0 || wy > r.y1) return false;
    const e = o.entry;
    const dx = (wx - o.sprite.position.x) * ART;
    const lx = (o.flip ? -dx : dx) + e.anchor.x;
    const ly = (wy - o.sprite.position.y) * ART + e.anchor.y;
    return atlas.alphaAt(e, lx, ly) > 48;
  }

  let lastClick = null;
  function pick(wx, wy) {
    const hits = [];
    for (let i = drawOrder.length - 1; i >= 0; i -= 1) {
      const o = drawOrder[i];
      if ((o.kind === 'person' || o.kind === 'building' || o.kind === 'wagon' || o.kind === 'ox') && hitTest(o, wx, wy))
        hits.push(o);
    }
    // Generous radius for people in crowds: nearest feet within 10 px.
    for (const o of people) {
      if (o.hidden || hits.includes(o)) continue;
      const p = project(o.x, o.y, 0.9);
      if (Math.hypot(p.x - wx, p.y - wy) < 10 / Math.max(zoom, 0.5)) hits.push(o);
    }
    if (!hits.length) return null;
    const sameSpot = lastClick && Math.hypot(lastClick.wx - wx, lastClick.wy - wy) < 4 / zoom;
    const index = sameSpot ? (lastClick.index + 1) % hits.length : 0;
    lastClick = { wx, wy, index };
    return { hit: hits[index], count: hits.length };
  }

  // ---------------------------------------------------------------- UI
  const inspector = document.getElementById('inspector');
  function select(id) {
    selected = id;
    if (!id) follow = false;
    renderInspector();
  }

  function renderInspector() {
    if (!selected) {
      inspector.hidden = true;
      return;
    }
    const o = byId.get(selected);
    inspector.hidden = false;
    if (o.kind === 'person') {
      const rec = o.person.record;
      const civ = o.person.civ;
      const s = o.state ?? {};
      const insideLabel =
        s.inside === 'away' ? 'Beyond the sample area' : s.inside ? (byId.get(s.inside)?.label ?? s.inside) : null;
      const skills =
        Object.entries(rec.skills)
          .map(([k, v]) => `${k} ${v}`)
          .join(', ') || 'Not recorded';
      inspector.innerHTML = `
        <header><div><h2>${rec.label}</h2><span class="tag">observer-assigned name · sample</span></div>
        <button class="x" data-act="close" aria-label="Close">×</button></header>
        <dl>
          <dt>ID</dt><dd class="mono">${o.id}</dd>
          <dt>Civilization</dt><dd><span class="swatch" style="background:${CIV_COLORS[civ]}"></span>${civilizationLabel(CIV_NAMES[civ])} <span class="tag">sample</span></dd>
          <dt>Age</dt><dd>${rec.age} (${rec.sex})</dd>
          <dt>Household</dt><dd class="muted">${rec.household}</dd>
          <dt>Family</dt><dd class="muted">Not recorded in this sample</dd>
          <dt>Occupation</dt><dd>${rec.role}</dd>
          <dt>Skills</dt><dd>${skills}</dd>
          <dt>Health</dt><dd>${rec.health}</dd>
          <dt>Activity</dt><dd>${s.activity ?? '—'} <span class="tag approx">visual approximation</span></dd>
          <dt>Destination</dt><dd>${s.destination ?? (s.moving ? 'Not recorded' : '—')}</dd>
          <dt>Where</dt><dd>${insideLabel ? `Inside: ${insideLabel}` : 'Outdoors'}</dd>
          <dt>Life events</dt><dd>${rec.events.join('<br>')}</dd>
        </dl>
        <footer><button data-act="follow" class="${follow ? 'on' : ''}">${follow ? 'Following' : 'Follow person'}</button></footer>`;
    } else {
      const occ = occupancy.get(o.partOf ?? o.id) ?? [];
      inspector.innerHTML = `
        <header><div><h2>${o.label}</h2><span class="tag">sample building</span></div>
        <button class="x" data-act="close" aria-label="Close">×</button></header>
        <dl>
          <dt>ID</dt><dd class="mono">${o.id}</dd>
          <dt>Asset</dt><dd class="mono">${o.asset ?? o.kind}</dd>
          <dt>Inside now</dt><dd>${occ.length ? occ.map((id) => `<a href="#" data-person="${id}">${byId.get(id).label}</a>`).join(', ') : 'Nobody'}</dd>
        </dl>`;
    }
  }

  inspector.addEventListener('click', (ev) => {
    const act = ev.target.closest('[data-act]')?.dataset.act;
    const pid = ev.target.closest('[data-person]')?.dataset.person;
    if (act === 'close') select(null);
    if (act === 'follow') {
      follow = !follow;
      renderInspector();
    }
    if (pid) {
      ev.preventDefault();
      select(pid);
    }
  });
  setInterval(() => {
    if (selected) renderInspector();
  }, 500);

  // Camera input.
  let drag = null;
  app.canvas.addEventListener('pointerdown', (e) => {
    drag = { x: e.clientX, y: e.clientY, cx: cam.x, cy: cam.y, moved: 0 };
    app.canvas.setPointerCapture(e.pointerId);
  });
  app.canvas.addEventListener('pointermove', (e) => {
    if (!drag) return;
    const dx = e.clientX - drag.x;
    const dy = e.clientY - drag.y;
    drag.moved = Math.max(drag.moved, Math.hypot(dx, dy));
    if (drag.moved > 4) {
      follow = false;
      cam.x = drag.cx - dx / zoom;
      cam.y = drag.cy - dy / zoom;
    }
  });
  app.canvas.addEventListener('pointerup', (e) => {
    if (drag && drag.moved <= 4) {
      const rect = app.canvas.getBoundingClientRect();
      const wx = (e.clientX - rect.left - world.position.x) / zoom;
      const wy = (e.clientY - rect.top - world.position.y) / zoom;
      const r = pick(wx, wy);
      select(r ? r.hit.id : null);
      document.getElementById('pick-note').textContent =
        r && r.count > 1 ? `${r.count} here · click again to cycle` : '';
    }
    drag = null;
  });
  app.canvas.addEventListener(
    'wheel',
    (e) => {
      e.preventDefault();
      const rect = app.canvas.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const my = e.clientY - rect.top;
      const before = { x: (mx - world.position.x) / zoom, y: (my - world.position.y) / zoom };
      zoom = Math.max(0.3, Math.min(2.6, zoom * Math.exp(-e.deltaY * 0.0015)));
      cam.x = before.x - (mx - app.screen.width / 2) / zoom;
      cam.y = before.y - (my - app.screen.height / 2) / zoom;
    },
    { passive: false },
  );

  const $ = (id) => document.getElementById(id);
  $('btn-pause').addEventListener('click', () => {
    paused = !paused;
    $('btn-pause').textContent = paused ? '▶ Resume' : '❚❚ Pause';
  });
  for (const b of document.querySelectorAll('[data-speed]')) {
    b.addEventListener('click', () => {
      speed = Number(b.dataset.speed);
      for (const o of document.querySelectorAll('[data-speed]')) o.classList.toggle('on', o === b);
    });
  }
  $('btn-zoom-in').addEventListener('click', () => (zoom = Math.min(2.6, zoom * 1.25)));
  $('btn-zoom-out').addEventListener('click', () => (zoom = Math.max(0.3, zoom / 1.25)));
  $('btn-reset').addEventListener('click', () => {
    zoom = SCENE === 'depth' ? 0.42 : 1;
    const f = project(scene.focus.x, scene.focus.y);
    cam.x = f.x;
    cam.y = f.y;
    follow = false;
  });
  $('chk-footprints').addEventListener('change', (e) => (showFootprints = e.target.checked));
  $('chk-flat').addEventListener('change', (e) => setFlat(e.target.checked));
  $('btn-pause').textContent = paused ? '▶ Resume' : '❚❚ Pause';

  function setFlat(on) {
    flat = on;
    groundLayer.visible = !on;
    shadowLayer.visible = !on;
    overlay.visible = !on;
    ringLayer.visible = !on;
    app.renderer.background.color = on ? 0x000000 : 0x1d2126;
    for (const o of objects) {
      if (!on && o.static) o.sprite.texture = o.entry.texture;
    }
  }

  function stats() {
    const sorted = frameTimes.slice().sort((a, b) => a - b);
    const avg = sorted.reduce((a, b) => a + b, 0) / Math.max(1, sorted.length);
    const p95 = sorted[Math.floor(sorted.length * 0.95)] ?? 0;
    const outdoor = people.filter((o) => !o.hidden).length + (caravan && !caravan.driver.hidden ? 0 : 0);
    const visiblePeople = drawOrder.filter((o) => o.kind === 'person').length;
    const mem = performance.memory ? performance.memory.usedJSHeapSize : null;
    return {
      fps: Math.round(app.ticker.FPS),
      updateMsAvg: Number(avg.toFixed(2)),
      updateMsP95: Number(p95.toFixed(2)),
      worldPopulation: people.length,
      resident: people.length,
      indoor: people.filter((o) => o.hidden).length,
      outdoor,
      visible: visiblePeople,
      drawnSprites: drawOrder.length,
      jsHeapBytes: mem,
      textureBytes: Math.round(atlas.textureBytes() + groundBytes),
      atlasPages: atlas.pages.length,
      groundTiles: groundLayer.children.length,
      depthCycles: lastCycles.length,
    };
  }

  const clock = $('clock');
  const perf = $('perf');
  let hudTimer = 0;
  app.ticker.add((ticker) => {
    frame(ticker.deltaMS);
    hudTimer += ticker.deltaMS;
    if (hudTimer > 250) {
      hudTimer = 0;
      const minutes = 8 * 60 + Math.floor(t / 60);
      clock.textContent = `Sample day 1 · ${String(Math.floor(minutes / 60) % 24).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}:${String(Math.floor(t) % 60).padStart(2, '0')}`;
      const s = stats();
      perf.textContent = `${s.fps} fps · update ${s.updateMsAvg} ms (p95 ${s.updateMsP95}) · people: ${s.worldPopulation} total, ${s.outdoor} outdoor, ${s.visible} visible, ${s.indoor} indoors · ${s.drawnSprites} sprites`;
    }
  });

  // ---------------------------------------------------------------- test API
  function worldToCanvas(x, y, z = 0) {
    const p = project(x, y, z);
    return { x: p.x * zoom + world.position.x, y: p.y * zoom + world.position.y };
  }

  function readPixel(cx, cy) {
    app.render();
    const c = document.createElement('canvas');
    c.width = app.canvas.width;
    c.height = app.canvas.height;
    const ctx = c.getContext('2d');
    ctx.drawImage(app.canvas, 0, 0);
    const res = app.renderer.resolution;
    const d = ctx.getImageData(Math.round(cx * res), Math.round(cy * res), 1, 1).data;
    return [d[0], d[1], d[2]];
  }

  window.__proof = {
    ready: true,
    scene: SCENE,
    cases: scene.cases ?? [],
    contract,
    personContract: personAssetContract(),
    setPaused: (v) => {
      paused = v;
    },
    setTime: (v) => {
      t = v;
      frame(0, false);
    },
    time: () => t,
    setFlat: (v) => {
      setFlat(v);
      frame(0);
    },
    view: (x, y, z) => {
      const p = project(x, y);
      cam.x = p.x;
      cam.y = p.y;
      zoom = z;
      frame(0, false);
    },
    camera: () => ({ x: cam.x, y: cam.y, zoom }),
    order: () => drawOrder.map((o) => o.id),
    isDrawn: (id) => drawOrder.some((o) => o.id === id),
    worldToCanvas,
    positionOf: (id) => {
      const o = byId.get(id);
      const s = sampleSchedule(o.person.schedule, t);
      return { x: s.x, y: s.y, inside: s.inside, anim: s.anim, phase: s.phase };
    },
    peopleIds: () => people.map((o) => o.id),
    centreOf: (id) => {
      const o = byId.get(id);
      if (o.kind === 'person') return { x: o.x, y: o.y };
      return { x: (o.fp.minX + o.fp.maxX) / 2, y: (o.fp.minY + o.fp.maxY) / 2 };
    },
    select: (id) => {
      select(id);
      frame(0);
    },
    follow: (v) => {
      follow = v;
    },
    selection: () => {
      if (!selected) return null;
      const o = byId.get(selected);
      return {
        id: selected,
        inside: o.state?.inside ?? null,
        drawn: drawOrder.includes(o),
        highlighted: !!(o.hidden && o.state?.inside && byId.get(o.state.inside)?.sprite.tint !== 0xffffff),
      };
    },
    overlapProbe: (aId, bId) => {
      const a = byId.get(aId);
      const b = byId.get(bId);
      const x0 = Math.max(a.rect.x0, b.rect.x0);
      const x1 = Math.min(a.rect.x1, b.rect.x1);
      const y0 = Math.max(a.rect.y0, b.rect.y0);
      const y1 = Math.min(a.rect.y1, b.rect.y1);
      const hits = [];
      for (let y = y0; y < y1; y += 0.5) {
        for (let x = x0; x < x1; x += 0.5) {
          if (hitTest(a, x, y) && hitTest(b, x, y) && !drawOrder.some((o) => o !== a && o !== b && hitTest(o, x, y))) {
            // Require a solid neighbourhood so edge pixels are not used.
            let ok = true;
            for (const [dx, dy] of [
              [-1.5, 0],
              [1.5, 0],
              [0, -1.5],
              [0, 1.5],
            ]) {
              if (!(hitTest(a, x + dx, y + dy) && hitTest(b, x + dx, y + dy))) ok = false;
              else if (drawOrder.some((o) => o !== a && o !== b && hitTest(o, x + dx, y + dy))) ok = false;
            }
            if (ok) hits.push([x, y]);
          }
        }
      }
      if (!hits.length) return null;
      const [wx, wy] = hits[Math.floor(hits.length / 2)];
      return { x: wx * zoom + world.position.x, y: wy * zoom + world.position.y, candidates: hits.length };
    },
    probe: (cx, cy) => {
      const [r, g, b] = readPixel(cx, cy);
      if (b !== 128) return null;
      const v = (r | (g << 8)) / 4 - 1;
      return objects[v]?.id ?? null;
    },
    pixel: readPixel,
    setFootprints: (v) => {
      showFootprints = v;
      document.getElementById('chk-footprints').checked = v;
      frame(0);
    },
    stats,
    frame: () => frame(0),
    /** Advance display time and camera easing by dt seconds (deterministic capture). */
    step: (dt) => {
      t += dt;
      frame(dt * 1000, false);
    },
    setZoom: (z) => {
      zoom = z;
      frame(0, false);
    },
  };

  document.getElementById('loading').hidden = true;
  if (SCENE === 'depth') {
    $('chk-footprints').checked = true;
    showFootprints = true;
  }
}

main().catch((err) => {
  console.error(err);
  setStatus(`Failed: ${err.message}`);
  window.__proofError = String(err && err.stack ? err.stack : err);
});
