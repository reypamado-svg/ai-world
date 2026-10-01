// AI World observer prototype (O1). One continuous camera over the world:
// engine terrain (seed 21) streamed by chunk, engine day-0 capitals, and a
// SAMPLE village with citizens. Nothing here reads or changes simulation
// state; the display clock only drives presentation routines.

import * as PIXI from '../vendor/pixi/pixi.min.mjs';
import { project, unproject } from './world/coords.js';
import { hexCentre, worldScreenBounds, chunkScreenBounds, planeToHex, chunkOf } from './world/hex.js';
import { TerrainSource } from './data/terrain-source.js';
import { SyntheticSource } from './data/synthetic-source.js';
import { observerVillage } from './data/sample/village.js';
import { TerrainLayer } from './render/terrain-layer.js';
import { DecorLayer } from './render/decor.js';
import { VillageLayer, bandOf } from './render/village-layer.js';
import { CapitalMarkers } from './render/markers.js';
import { Atlas } from './render/art/atlas.js';
import { CIV_COLORS } from './render/art/registry.js';
import { bakeSceneActors, bakeSceneGround, bakeStaticAssets } from './render/art/bake.js';
import { Camera } from './camera.js';
import { Minimap } from './ui/minimap.js';
import { Inspector } from './ui/inspector.js';
import { FrameStats, jsHeapBytes } from './ui/perf.js';
import { Quality } from './ui/quality.js';
import { scaleCitizens } from './data/sample/citizens.js';

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
    this.villageData = extras.village ?? null;
    if (extras.village) {
      const v = extras.village;
      this.decor = new DecorLayer({
        PIXI,
        atlas: extras.atlas,
        terrain: this.terrain,
        skip: new Set([`${v.tile[0]},${v.tile[1]}`]),
      });
      this.world.addChild(this.decor.container);
      this.village = new VillageLayer({
        PIXI,
        atlas: extras.atlas,
        assetInfo: extras.assetInfo,
        village: v,
        ground: extras.ground.container,
      });
      this.world.addChild(this.village.container);
      this.atlas = extras.atlas;
      this.groundBytes = extras.ground.bytes;
    }
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
    this.intervalStats = new FrameStats();
    this.quality = new Quality(
      (settings) => {
        this.settings = settings;
        pixi.renderer.resize(pixi.screen.width, pixi.screen.height, settings.resolution);
        if (this.village) this.village.renderer.shadowLayer.visible = settings.shadows;
      },
      params.get('quality') ?? 'auto',
    );
    this.t = 30;
    this.paused = false;
    this.speed = 1;
    this.selected = null;
    this.follow = false;
    this.showFootprints = false;
    this.minimap = new Minimap($('minimap'), {
      width: source.width,
      height: source.height,
      R: this.R,
      overview: extras.overview,
      onJump: (p) => {
        this.follow = false;
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
    if (this.village) {
      const r = this.village.renderer;
      this.inspector = new Inspector($('inspector'), {
        lookup: (id) => r.byId.get(id),
        occupancy: () => r.occupancy,
        isFollowing: () => this.follow,
        onClose: () => this.select(null),
        onFollow: () => {
          this.follow = !this.follow;
          this.inspector.render();
        },
        onSelect: (id) => this.select(id),
      });
    }
  }

  setPaused(v) {
    this.paused = v;
    const b = $('btn-pause');
    if (b) b.textContent = v ? '▶ Resume' : '❚❚ Pause';
  }

  select(id) {
    this.selected = id;
    if (!id) this.follow = false;
    this.inspector?.show(id);
  }

  goToVillage(zoom = 1) {
    const v = this.villageData;
    if (!v) return;
    const p = project(v.origin.x, v.origin.y);
    this.camera.x = p.x;
    this.camera.y = p.y;
    this.camera.zoom = zoom;
  }

  frame(deltaMS, advance = !this.paused) {
    const t0 = performance.now();
    if (advance) this.t += (deltaMS / 1000) * this.speed;
    if (this.village && this.follow && this.selected) {
      this.village.renderer.update(this.t);
      const target = this.village.worldPosition(this.selected);
      if (target) this.camera.followTowards(target, deltaMS);
    }
    this.camera.clamp();
    const { width, height } = this.pixi.screen;
    this.camera.apply(this.world, width, height);
    const view = this.camera.viewRect(this.world, width, height, 0);
    this.view = view;
    this.terrain.update(view, this.camera.zoom);
    this.decor?.update(this.camera.zoom);
    if (this.village) {
      const margin = this.camera.viewRect(this.world, width, height, 80);
      this.village.update(this.t, margin, this.camera.zoom, {
        selected: this.selected,
        showFootprints: this.showFootprints,
        crowdBudget: this.settings?.crowdBudget ?? Infinity,
      });
    }
    this.markers?.update(this.camera.zoom);
    this.minimap.draw(view);
    this.frameStats.push(performance.now() - t0);
  }

  stats() {
    const t = this.terrain.stats();
    const v = this.village?.counts() ?? {};
    const iv = this.intervalStats.summary();
    return {
      fps: Math.round(this.pixi.ticker.FPS),
      frameMsAvg: iv.updateMsAvg,
      frameMsP95: iv.updateMsP95,
      ...this.frameStats.summary(),
      quality: `${this.quality.mode}${this.quality.mode === 'auto' ? ` (${this.quality.level})` : ''}`,
      zoom: Number(this.camera.zoom.toFixed(4)),
      band: bandOf(this.camera.zoom),
      ...v,
      textureLevel: t.level,
      hexLevel: t.hexLevel,
      visibleChunks: t.visibleChunks,
      loadedChunks: t.loadedChunks,
      gpuTextures: t.gpu.entries,
      gpuBytes: Math.round(t.gpu.bytes),
      atlasBytes: this.atlas ? Math.round(this.atlas.textureBytes() + this.groundBytes) : 0,
      jsHeapBytes: jsHeapBytes(),
      inFlight: t.loader.inFlight,
    };
  }

  /** Everything needed to compare performance across machines (R9 counts kept separate). */
  measurement() {
    return {
      capturedAt: new Date().toISOString(),
      userAgent: navigator.userAgent,
      screen: {
        width: this.pixi.screen.width,
        height: this.pixi.screen.height,
        devicePixelRatio: window.devicePixelRatio,
      },
      sampleCitizens: this.village ? this.village.renderer.people.length : 0,
      stats: this.stats(),
      note: 'Browser rendering only. Simulation throughput is measured separately.',
    };
  }

  pickAt(cx, cy) {
    if (!this.village) return null;
    const w = this.camera.screenToWorld(this.world, cx, cy);
    return this.village.pick(w.x, w.y, this.camera.zoom);
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
        const hit = this.pickAt(e.clientX - rect.left, e.clientY - rect.top);
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
    $('btn-zoom-in').addEventListener('click', () => this.camera.setZoom(this.camera.zoom * 1.4));
    $('btn-zoom-out').addEventListener('click', () => this.camera.setZoom(this.camera.zoom / 1.4));
    $('btn-world').addEventListener('click', () => {
      this.follow = false;
      Object.assign(this.camera, this.home);
    });
    $('btn-village')?.addEventListener('click', () => {
      this.follow = false;
      this.goToVillage(1);
    });
    $('btn-pause')?.addEventListener('click', () => this.setPaused(!this.paused));
    for (const b of document.querySelectorAll('[data-speed]')) {
      b.addEventListener('click', () => {
        this.speed = Number(b.dataset.speed);
        for (const o of document.querySelectorAll('[data-speed]')) o.classList.toggle('on', o === b);
      });
    }
    $('chk-footprints')?.addEventListener('change', (e) => (this.showFootprints = e.target.checked));
    const q = $('quality');
    if (q) {
      q.value = this.quality.mode;
      q.addEventListener('change', () => this.quality.setMode(q.value));
    }
    $('btn-measure')?.addEventListener('click', () => {
      const panel = $('measure');
      panel.hidden = !panel.hidden;
      if (!panel.hidden) $('measure-json').textContent = JSON.stringify(this.measurement(), null, 1);
    });
    $('btn-copy')?.addEventListener('click', async () => {
      const text = JSON.stringify(this.measurement(), null, 1);
      $('measure-json').textContent = text;
      try {
        await navigator.clipboard.writeText(text);
        $('btn-copy').textContent = 'Copied';
      } catch {
        $('btn-copy').textContent = 'Select the text and copy it';
      }
    });
    setInterval(() => {
      if (this.selected) this.inspector?.render();
    }, 500);
  }

  run() {
    const perf = $('perf');
    const clock = $('clock');
    let hud = 0;
    this.pixi.ticker.add((ticker) => {
      this.intervalStats.push(ticker.deltaMS);
      this.quality.observe(ticker.deltaMS);
      this.frame(ticker.deltaMS);
      hud += ticker.deltaMS;
      if (hud > 250) {
        hud = 0;
        const s = this.stats();
        if (clock) {
          const minutes = 8 * 60 + Math.floor(this.t / 60);
          clock.textContent = `Engine day 0 · sample time ${String(Math.floor(minutes / 60) % 24).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`;
        }
        const people = this.village
          ? ` · people: ${s.worldPopulation} total, ${s.indoor} indoor, ${s.outdoor} outdoor, ${s.visible} visible (${s.visibleFull} full)`
          : '';
        perf.textContent = `${s.fps} fps · update ${s.updateMsAvg} ms (p95 ${s.updateMsP95}) · ${s.band} view, zoom ${s.zoom} · chunks: ${s.visibleChunks} visible, ${s.loadedChunks} loaded · ${s.gpuTextures} terrain textures (${(s.gpuBytes / 1e6).toFixed(1)} MB)${people}`;
      }
    });
  }
}

function buildApi(app) {
  const toCanvas = (wx, wy) => app.camera.worldToScreen(app.world, wx, wy);
  const v = app.village;
  const local = (id) => v.renderer.positionAt(id, app.t);
  return {
    ready: true,
    source: app.source.label,
    manifest: app.source.manifest,
    /** Centre the camera on a ground-plane point (metres) at a zoom. */
    view: (x, y, zoom) => {
      const p = project(x, y);
      app.camera.x = p.x;
      app.camera.y = p.y;
      app.camera.setZoom(zoom);
      app.frame(0, false);
    },
    viewHex: (q, r, zoom) => {
      const c = hexCentre(q, r, app.R);
      const p = project(c.x, c.y);
      app.camera.x = p.x;
      app.camera.y = p.y;
      app.camera.zoom = zoom;
      app.frame(0, false);
    },
    home: () => {
      Object.assign(app.camera, app.home);
      app.frame(0, false);
    },
    viewVillage: (zoom) => {
      app.goToVillage(zoom);
      app.frame(0, false);
    },
    /** Zoom by `factor` about a canvas point, exactly as the mouse wheel does. */
    zoomAt: (cx, cy, factor) => {
      const { width, height } = app.pixi.screen;
      app.camera.zoomAt(app.world, width, height, cx, cy, factor);
      app.frame(0, false);
    },
    camera: () => ({ x: app.camera.x, y: app.camera.y, zoom: app.camera.zoom, minZoom: app.camera.minZoom }),
    /** Set the camera directly (world-screen pixels at zoom 1). */
    setCamera: (x, y, zoom) => {
      app.camera.x = x;
      app.camera.y = y;
      app.camera.zoom = zoom;
      app.frame(0, false);
    },
    villageCamera: () => {
      const p = project(app.villageData.origin.x, app.villageData.origin.y);
      return { x: p.x, y: p.y };
    },
    band: () => bandOf(app.camera.zoom),
    frame: () => app.frame(0, false),
    setPaused: (p) => app.setPaused(p),
    setTime: (t) => {
      app.t = t;
      app.frame(0, false);
    },
    time: () => app.t,
    /** Advance display time and camera easing by dt seconds. */
    step: (dt) => {
      app.t += dt;
      app.frame(dt * 1000, false);
    },
    /** Run frames until every visible chunk is loaded and baked at the wanted level. */
    settle: async (maxFrames = 600) => {
      for (let f = 0; f < maxFrames; f += 1) {
        app.frame(16, false);
        if (app.terrain.stats().complete) return { frames: f + 1, complete: true };
        await new Promise((r) => setTimeout(r, 15));
      }
      return { frames: maxFrames, complete: false };
    },
    terrainStats: () => app.terrain.stats(),
    stats: () => app.stats(),
    canvasToPlane: (cx, cy) => {
      const w = app.camera.screenToWorld(app.world, cx, cy);
      return unproject(w.x, w.y);
    },
    planeToCanvas: (x, y, z = 0) => {
      const p = project(x, y, z);
      return toCanvas(p.x, p.y);
    },
    /** World ground-plane position of a person (village origin applied). */
    positionOf: (id) => {
      const p = local(id);
      return { ...p, x: p.x + app.villageData.origin.x, y: p.y + app.villageData.origin.y };
    },
    chunkOfPerson: (id) => {
      const p = local(id);
      const h = planeToHex(p.x + app.villageData.origin.x, p.y + app.villageData.origin.y, app.R);
      return chunkOf(h.q, h.r, app.source.manifest.presentation.chunk_tiles);
    },
    cameraChunk: () => {
      const g = unproject(app.camera.x, app.camera.y);
      const h = planeToHex(g.x, g.y, app.R);
      return chunkOf(h.q, h.r, app.source.manifest.presentation.chunk_tiles);
    },
    peopleIds: () => (v ? v.renderer.people.map((o) => o.id) : []),
    courierId: () => app.villageData?.courierId,
    pickAt: (cx, cy) => {
      const hit = app.pickAt(cx, cy);
      app.select(hit ? hit.hit.id : null);
      app.frame(0, false);
      return hit ? hit.hit.id : null;
    },
    select: (id) => {
      app.select(id);
      app.frame(0, false);
    },
    follow: (f) => {
      app.follow = f;
    },
    selection: () => (app.selected ? { id: app.selected, following: app.follow } : null),
    /** Canvas point over a drawn person's body (for click tests), or null. */
    personCanvasPoint: (id) => {
      const o = v.renderer.byId.get(id);
      if (!o || o.hidden || !o.sprite.visible) return null;
      const off = v.offset;
      const p = project(o.x, o.y, 1.0);
      return toCanvas(p.x + off.x, p.y + off.y);
    },
    isDrawn: (id) => v.renderer.drawOrder.some((o) => o.id === id),
    releaseHidden: () => app.terrain.releaseHidden(),
    textureCount: () => ({
      terrain: app.terrain.liveTextures,
      // Freed slots are left as null in the list, so count live entries only.
      pixiManaged: app.pixi.renderer.texture?.managedTextures?.filter(Boolean).length ?? null,
    }),
    measurement: () => app.measurement(),
    setQuality: (mode) => app.quality.setMode(mode),
    villageInfo: () =>
      app.villageData
        ? { tile: app.villageData.tile, origin: app.villageData.origin, dropped: app.villageData.dropped }
        : null,
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
    source = new SyntheticSource({ maxLatency: Number(params.get('latency') ?? 400) });
    extras.terrainOptions = {
      gpuBytes: Number(params.get('gpuMB') ?? 96) * 1e6,
      gpuEntries: Number(params.get('gpuEntries') ?? 96),
      cpuBytes: Number(params.get('cpuMB') ?? 24) * 1e6,
    };
    $('source-chip').textContent = 'SYNTHETIC TEST WORLD';
  } else {
    setStatus('Loading engine terrain manifest');
    source = await TerrainSource.open('data/terrain');
    const day0 = await source.day0();
    extras = { overview: await source.overview(), day0, terrainOptions: { gpuBytes: 192e6, gpuEntries: 192 } };
    const atlas = new Atlas(PIXI);
    const { assetInfo } = await bakeStaticAssets(atlas, CIV_COLORS[0], setStatus);
    const footprintOf = (asset) => assetInfo.get(asset).footprint;
    const tile = day0.civilizations[0].capital.tile;
    const village = observerVillage(footprintOf, source.manifest.presentation.hex_radius_m, tile);
    scaleCitizens(village.scene, Number(params.get('citizens') ?? 400));
    await bakeSceneActors(atlas, village.scene, setStatus);
    atlas.finalize();
    const ground = await bakeSceneGround(PIXI, village.scene, setStatus, {
      alpha: village.alpha,
      bounds: village.groundBounds,
    });
    Object.assign(extras, { atlas, assetInfo, village, ground });
  }
  $('world-label').textContent = SYNTHETIC
    ? source.label
    : `Engine world · seed ${source.manifest.engine.seed} · ${source.width}×${source.height} tiles`;
  const app = new ObserverApp(pixi, source, extras);
  app.bindInput();
  app.setPaused(false);
  app.run();
  window.__observer = buildApi(app);
  $('loading').hidden = true;
}

main().catch((err) => {
  console.error(err);
  setStatus(`Failed: ${err.message}`);
  window.__observerError = String(err && err.stack ? err.stack : err);
});
