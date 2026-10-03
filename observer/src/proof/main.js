// Close-zoom art proof (O1a). PROTOTYPE ARTWORK, SAMPLE DATA.
//
// Wiring only: bake assets, build the scene renderer, camera and UI, and run
// the frame loop. Movement is a deterministic visual approximation; nothing
// here reads or changes simulation state.

import * as PIXI from '../../vendor/pixi/pixi.min.mjs';
import { project } from '../world/coords.js';
import { Atlas } from '../render/art/atlas.js';
import { CIV_COLORS } from '../render/art/registry.js';
import { bakeSceneActors, bakeSceneGround, bakeStaticAssets } from '../render/art/bake.js';
import { SceneRenderer } from '../render/scene-renderer.js';
import { Camera } from '../camera.js';
import { Inspector } from '../ui/inspector.js';
import { FrameStats, jsHeapBytes } from '../ui/perf.js';
import { buildProofApi } from '../debug-api.js';
import { villageScene } from './scene-village.js';
import { depthScene } from './scene-depth.js';

const params = new URLSearchParams(location.search);
const SCENE = params.get('scene') === 'depth' ? 'depth' : 'village';
const DEBUG = params.has('debug') || SCENE === 'depth';
const CENTRE_ONLY = params.get('depth') === 'centre';

const $ = (id) => document.getElementById(id);

function setStatus(text) {
  const el = $('loading-status');
  if (el) el.textContent = text;
}

class ProofApp {
  constructor(pixi, atlas, scene, renderer, contract, groundBytes) {
    this.pixi = pixi;
    this.atlas = atlas;
    this.scene = scene;
    this.sceneName = SCENE;
    this.renderer = renderer;
    this.contract = contract;
    this.groundBytes = groundBytes;
    this.world = new PIXI.Container();
    this.world.addChild(renderer.root);
    pixi.stage.addChild(this.world);
    this.t = SCENE === 'depth' ? 0 : 30;
    this.paused = SCENE === 'depth';
    this.speed = 1;
    this.selected = null;
    this.follow = false;
    this.showFootprints = false;
    this.frameStats = new FrameStats();
    const focus = project(scene.focus.x, scene.focus.y);
    const b = scene.bounds;
    this.camera = new Camera({
      x: focus.x,
      y: focus.y,
      zoom: SCENE === 'depth' ? 0.42 : 1,
      clamp: (cam) => {
        const lim = project(b.x1, b.y1);
        const limX = project(b.x1, b.y0).x;
        const limTop = project(b.x0, b.y0).y;
        cam.x = Math.max(-limX, Math.min(limX, cam.x));
        cam.y = Math.max(limTop, Math.min(lim.y, cam.y));
      },
    });
    this.inspector = new Inspector($('inspector'), {
      lookup: (id) => renderer.byId.get(id),
      occupancy: () => renderer.occupancy,
      isFollowing: () => this.follow,
      onClose: () => this.select(null),
      onFollow: () => {
        this.follow = !this.follow;
        this.inspector.render();
      },
      onSelect: (id) => this.select(id),
    });
  }

  setPaused(v) {
    this.paused = v;
    $('btn-pause').textContent = v ? '▶ Resume' : '❚❚ Pause';
  }

  select(id) {
    this.selected = id;
    if (!id) this.follow = false;
    this.inspector.show(id);
  }

  setFlat(on) {
    this.renderer.setFlat(on);
    this.pixi.renderer.background.color = on ? 0x000000 : 0x1d2126;
  }

  setFootprints(v) {
    this.showFootprints = v;
    $('chk-footprints').checked = v;
  }

  frame(deltaMS, advance = !this.paused) {
    const t0 = performance.now();
    if (advance) this.t += (deltaMS / 1000) * this.speed;
    const r = this.renderer;
    r.update(this.t);
    if (this.follow && this.selected) {
      const target = r.followTarget(this.selected);
      if (target) this.camera.followTowards({ x: target.x + r.offset.x, y: target.y + r.offset.y }, deltaMS);
    }
    this.camera.clamp();
    const { width, height } = this.pixi.screen;
    this.camera.apply(this.world, width, height);
    const view = this.camera.viewRect(this.world, width, height);
    const local = {
      x0: view.x0 - r.offset.x,
      y0: view.y0 - r.offset.y,
      x1: view.x1 - r.offset.x,
      y1: view.y1 - r.offset.y,
    };
    const visible = r.cullAndSort(local);
    r.decorate({ selected: this.selected, zoom: this.camera.zoom, showFootprints: this.showFootprints, visible });
    this.frameStats.push(performance.now() - t0);
  }

  stats() {
    return {
      fps: Math.round(this.pixi.ticker.FPS),
      ...this.frameStats.summary(),
      ...this.renderer.counts(),
      jsHeapBytes: jsHeapBytes(),
      textureBytes: Math.round(this.atlas.textureBytes() + this.groundBytes),
      atlasPages: this.atlas.pages.length,
      groundTiles: this.renderer.groundLayer.children.length,
    };
  }

  bindInput() {
    const canvas = this.pixi.canvas;
    let drag = null;
    canvas.addEventListener('pointerdown', (e) => {
      drag = { x: e.clientX, y: e.clientY, cx: this.camera.x, cy: this.camera.y, moved: 0 };
      canvas.setPointerCapture(e.pointerId);
    });
    canvas.addEventListener('pointermove', (e) => {
      if (!drag) return;
      const dx = e.clientX - drag.x;
      const dy = e.clientY - drag.y;
      drag.moved = Math.max(drag.moved, Math.hypot(dx, dy));
      if (drag.moved > 4) {
        this.follow = false;
        this.camera.x = drag.cx - dx / this.camera.zoom;
        this.camera.y = drag.cy - dy / this.camera.zoom;
      }
    });
    canvas.addEventListener('pointerup', (e) => {
      if (drag && drag.moved <= 4) {
        const rect = canvas.getBoundingClientRect();
        const w = this.camera.screenToWorld(this.world, e.clientX - rect.left, e.clientY - rect.top);
        const off = this.renderer.offset;
        const hit = this.renderer.pick(w.x - off.x, w.y - off.y, this.camera.zoom);
        this.select(hit ? hit.hit.id : null);
        $('pick-note').textContent = hit && hit.count > 1 ? `${hit.count} here · click again to cycle` : '';
      }
      drag = null;
    });
    canvas.addEventListener(
      'wheel',
      (e) => {
        e.preventDefault();
        const rect = canvas.getBoundingClientRect();
        const { width, height } = this.pixi.screen;
        this.camera.zoomAt(
          this.world,
          width,
          height,
          e.clientX - rect.left,
          e.clientY - rect.top,
          Math.exp(-e.deltaY * 0.0015),
        );
      },
      { passive: false },
    );
    $('btn-pause').addEventListener('click', () => this.setPaused(!this.paused));
    for (const b of document.querySelectorAll('[data-speed]')) {
      b.addEventListener('click', () => {
        this.speed = Number(b.dataset.speed);
        for (const o of document.querySelectorAll('[data-speed]')) o.classList.toggle('on', o === b);
      });
    }
    $('btn-zoom-in').addEventListener('click', () => this.camera.setZoom(this.camera.zoom * 1.25));
    $('btn-zoom-out').addEventListener('click', () => this.camera.setZoom(this.camera.zoom / 1.25));
    $('btn-reset').addEventListener('click', () => {
      this.camera.zoom = SCENE === 'depth' ? 0.42 : 1;
      const f = project(this.scene.focus.x, this.scene.focus.y);
      this.camera.x = f.x;
      this.camera.y = f.y;
      this.follow = false;
    });
    $('chk-footprints').addEventListener('change', (e) => (this.showFootprints = e.target.checked));
    $('chk-flat').addEventListener('change', (e) => this.setFlat(e.target.checked));
    this.setPaused(this.paused);
    setInterval(() => {
      if (this.selected) this.inspector.render();
    }, 500);
  }

  run() {
    const clock = $('clock');
    const perf = $('perf');
    let hudTimer = 0;
    this.pixi.ticker.add((ticker) => {
      this.frame(ticker.deltaMS);
      hudTimer += ticker.deltaMS;
      if (hudTimer > 250) {
        hudTimer = 0;
        const t = this.t;
        const minutes = 8 * 60 + Math.floor(t / 60);
        clock.textContent = `Sample day 1 · ${String(Math.floor(minutes / 60) % 24).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}:${String(Math.floor(t) % 60).padStart(2, '0')}`;
        const s = this.stats();
        perf.textContent = `${s.fps} fps · update ${s.updateMsAvg} ms (p95 ${s.updateMsP95}) · people: ${s.worldPopulation} total, ${s.outdoor} outdoor, ${s.visible} visible, ${s.indoor} indoors · ${s.drawnSprites} sprites`;
      }
    });
  }
}

async function main() {
  const stageEl = $('stage');
  const pixi = new PIXI.Application();
  await pixi.init({
    resizeTo: stageEl,
    background: '#1d2126',
    antialias: !DEBUG,
    preference: 'webgl',
    resolution: DEBUG ? 1 : Math.min(2, window.devicePixelRatio || 1),
    autoDensity: true,
    preserveDrawingBuffer: DEBUG,
  });
  stageEl.appendChild(pixi.canvas);

  const atlas = new Atlas(PIXI);
  const { assetInfo, contract } = await bakeStaticAssets(atlas, CIV_COLORS[0], setStatus);
  const footprintOf = (asset) => assetInfo.get(asset).footprint;
  const scene = SCENE === 'depth' ? depthScene(footprintOf) : villageScene(footprintOf);
  await bakeSceneActors(atlas, scene, setStatus);
  atlas.finalize();
  const ground = await bakeSceneGround(PIXI, scene, setStatus);
  const renderer = new SceneRenderer({
    PIXI,
    atlas,
    assetInfo,
    scene,
    ground: ground.container,
    centreOnly: CENTRE_ONLY,
  });

  const app = new ProofApp(pixi, atlas, scene, renderer, contract, ground.bytes);
  app.bindInput();
  app.run();
  window.__proof = buildProofApi(app);
  $('loading').hidden = true;
  if (SCENE === 'depth') app.setFootprints(true);
}

main().catch((err) => {
  console.error(err);
  setStatus(`Failed: ${err.message}`);
  window.__proofError = String(err && err.stack ? err.stack : err);
});
