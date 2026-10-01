// AI World observer prototype (O1). One continuous camera over the world:
// engine terrain (seed 21) streamed by chunk, engine day-0 capitals, and a
// SAMPLE village. Nothing here reads or changes simulation state.

import * as PIXI from '../vendor/pixi/pixi.min.mjs';
import { project, unproject } from './world/coords.js';
import { hexCentre, worldScreenBounds, chunkScreenBounds } from './world/hex.js';
import { TerrainSource } from './data/terrain-source.js';
import { SyntheticSource } from './data/synthetic-source.js';
import { TerrainLayer } from './render/terrain-layer.js';
import { CapitalMarkers } from './render/markers.js';
import { CIV_COLORS } from './render/art/registry.js';
import { Camera } from './camera.js';
import { Minimap } from './ui/minimap.js';
import { FrameStats, jsHeapBytes } from './ui/perf.js';

const params = new URLSearchParams(location.search);
const SYNTHETIC = params.get('source') === 'synthetic';
const DEBUG = params.has('debug') || SYNTHETIC;
const $ = (id) => document.getElementById(id);

function setStatus(text) {
  const el = $('loading-status');
  if (el) el.textContent = text;
}

/** Smallest zoom at which the visible chunk count stays within budget. */
function chunkBudgetZoom(source, screenW, screenH, budget) {
  const [ncq, ncr] = source.manifest.presentation.chunks;
  if (ncq * ncr <= budget) return 0; // the whole world fits the budget
  const ct = source.manifest.presentation.chunk_tiles;
  const R = source.manifest.presentation.hex_radius_m;
  const b = chunkScreenBounds(1, 1, ct, R, source.width, source.height);
  const cw = (b.x1 - b.x0) * 0.6; // chunks overlap in screen space (skewed rows)
  const ch = (b.y1 - b.y0) * 0.6;
  let z = 0.001;
  while (z < 1 && (screenW / (cw * z) + 2) * (screenH / (ch * z) + 2) > budget) z *= 1.05;
  return z;
}

class ObserverApp {
  constructor(pixi, source, extras) {
    this.pixi = pixi;
    this.source = source;
    this.R = source.manifest.presentation.hex_radius_m;
    this.world = new PIXI.Container();
    pixi.stage.addChild(this.world);
    this.terrain = new TerrainLayer({ PIXI, source, ...(extras.terrainOptions ?? {}) });
    this.world.addChild(this.terrain.container);
    this.markers = extras.day0 ? new CapitalMarkers(PIXI, extras.day0, this.R) : null;
    if (this.markers) this.world.addChild(this.markers.container);
    this.bounds = worldScreenBounds(source.width, source.height, this.R);
    const { width, height } = pixi.screen;
    const fit = Math.min(width / (this.bounds.x1 - this.bounds.x0), height / (this.bounds.y1 - this.bounds.y0)) * 0.95;
    const budgetZoom = chunkBudgetZoom(source, width, height, 64);
    this.camera = new Camera({
      x: (this.bounds.x0 + this.bounds.x1) / 2,
      y: (this.bounds.y0 + this.bounds.y1) / 2,
      zoom: Math.max(fit, budgetZoom),
      minZoom: Math.max(Math.min(fit * 0.8, 0.02), budgetZoom),
      maxZoom: 2.6,
      clamp: (cam) => {
        cam.x = Math.max(this.bounds.x0, Math.min(this.bounds.x1, cam.x));
        cam.y = Math.max(this.bounds.y0, Math.min(this.bounds.y1, cam.y));
      },
    });
    this.home = { x: this.camera.x, y: this.camera.y, zoom: this.camera.zoom };
    this.frameStats = new FrameStats();
    this.minimap = new Minimap($('minimap'), {
      width: source.width,
      height: source.height,
      R: this.R,
      overview: extras.overview,
      onJump: (p) => {
        this.camera.x = p.x;
        this.camera.y = p.y;
      },
    });
    if (extras.day0) {
      extras.day0.civilizations.forEach((civ, i) => {
        const c = hexCentre(civ.capital.tile[0], civ.capital.tile[1], this.R);
        this.minimap.addMarker(project(c.x, c.y), CIV_COLORS[i % CIV_COLORS.length]);
      });
    }
  }

  frame(deltaMS) {
    const t0 = performance.now();
    this.camera.clamp();
    const { width, height } = this.pixi.screen;
    this.camera.apply(this.world, width, height);
    const view = this.camera.viewRect(this.world, width, height, 0);
    this.view = view;
    this.terrain.update(view, this.camera.zoom);
    this.markers?.update(this.camera.zoom);
    this.minimap.draw(view);
    this.frameStats.push(performance.now() - t0);
    void deltaMS;
  }

  stats() {
    const t = this.terrain.stats();
    return {
      fps: Math.round(this.pixi.ticker.FPS),
      ...this.frameStats.summary(),
      zoom: Number(this.camera.zoom.toFixed(4)),
      textureLevel: t.level,
      visibleChunks: t.visibleChunks,
      loadedChunks: t.loadedChunks,
      gpuTextures: t.gpu.entries,
      gpuBytes: Math.round(t.gpu.bytes),
      jsHeapBytes: jsHeapBytes(),
      inFlight: t.loader.inFlight,
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
        this.camera.x = drag.cx - dx / this.camera.zoom;
        this.camera.y = drag.cy - dy / this.camera.zoom;
      }
    });
    canvas.addEventListener('pointerup', () => (drag = null));
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
    $('btn-zoom-in').addEventListener('click', () => this.camera.setZoom(this.camera.zoom * 1.4));
    $('btn-zoom-out').addEventListener('click', () => this.camera.setZoom(this.camera.zoom / 1.4));
    $('btn-world').addEventListener('click', () => Object.assign(this.camera, this.home));
  }

  run() {
    const perf = $('perf');
    let hud = 0;
    this.pixi.ticker.add((ticker) => {
      this.frame(ticker.deltaMS);
      hud += ticker.deltaMS;
      if (hud > 250) {
        hud = 0;
        const s = this.stats();
        perf.textContent = `${s.fps} fps · update ${s.updateMsAvg} ms (p95 ${s.updateMsP95}) · zoom ${s.zoom} · chunks: ${s.visibleChunks} visible, ${s.loadedChunks} loaded · ${s.gpuTextures} terrain textures (${(s.gpuBytes / 1e6).toFixed(1)} MB)`;
      }
    });
  }
}

function buildApi(app) {
  const canvasToWorld = (cx, cy) => app.camera.screenToWorld(app.world, cx, cy);
  return {
    ready: true,
    source: app.source.label,
    manifest: app.source.manifest,
    /** Centre the camera on a ground-plane point (metres) at a zoom. */
    view: (x, y, zoom) => {
      const p = project(x, y);
      app.camera.x = p.x;
      app.camera.y = p.y;
      app.camera.zoom = zoom;
      app.frame(0);
    },
    viewHex: (q, r, zoom) => {
      const c = hexCentre(q, r, app.R);
      const p = project(c.x, c.y);
      app.camera.x = p.x;
      app.camera.y = p.y;
      app.camera.zoom = zoom;
      app.frame(0);
    },
    camera: () => ({ x: app.camera.x, y: app.camera.y, zoom: app.camera.zoom, minZoom: app.camera.minZoom }),
    frame: () => app.frame(0),
    /** Run frames until every visible chunk is loaded and baked at the wanted level. */
    settle: async (maxFrames = 600) => {
      for (let f = 0; f < maxFrames; f += 1) {
        app.frame(16);
        const s = app.terrain.stats();
        if (s.complete) return { frames: f + 1, complete: true };
        await new Promise((r) => setTimeout(r, 15));
      }
      return { frames: maxFrames, complete: false };
    },
    terrainStats: () => app.terrain.stats(),
    stats: () => app.stats(),
    canvasToPlane: (cx, cy) => {
      const w = canvasToWorld(cx, cy);
      return unproject(w.x, w.y);
    },
    /** Destroy every terrain texture not on screen (used to prove nothing leaks). */
    releaseHidden: () => app.terrain.releaseHidden(),
    textureCount: () => ({
      terrain: app.terrain.liveTextures,
      // Freed slots are left as null in the list, so count live entries only.
      pixiManaged: app.pixi.renderer.texture?.managedTextures?.filter(Boolean).length ?? null,
    }),
  };
}

async function main() {
  const stageEl = $('stage');
  const pixi = new PIXI.Application();
  await pixi.init({
    resizeTo: stageEl,
    background: '#0f1316',
    antialias: true,
    preference: 'webgl',
    resolution: DEBUG ? 1 : Math.min(2, window.devicePixelRatio || 1),
    autoDensity: true,
    preserveDrawingBuffer: DEBUG,
  });
  stageEl.appendChild(pixi.canvas);
  let source;
  let extras = {};
  if (SYNTHETIC) {
    source = new SyntheticSource({
      maxLatency: Number(params.get('latency') ?? 400),
    });
    extras.terrainOptions = {
      gpuBytes: Number(params.get('gpuMB') ?? 96) * 1e6,
      gpuEntries: Number(params.get('gpuEntries') ?? 96),
      cpuBytes: Number(params.get('cpuMB') ?? 24) * 1e6,
    };
    $('source-chip').textContent = 'SYNTHETIC TEST WORLD';
  } else {
    setStatus('Loading engine terrain manifest');
    source = await TerrainSource.open('data/terrain');
    extras = { overview: await source.overview(), day0: await source.day0() };
  }
  $('world-label').textContent = SYNTHETIC
    ? source.label
    : `Engine world · seed ${source.manifest.engine.seed} · ${source.width}×${source.height} tiles`;
  const app = new ObserverApp(pixi, source, extras);
  app.bindInput();
  app.run();
  window.__observer = buildApi(app);
  $('loading').hidden = true;
}

main().catch((err) => {
  console.error(err);
  setStatus(`Failed: ${err.message}`);
  window.__observerError = String(err && err.stack ? err.stack : err);
});
