// AI World observer prototype (O1). One continuous camera over the world:
// engine terrain (seed 21) streamed by chunk, engine day-0 capitals, and a
// SAMPLE village with citizens. Nothing here reads or changes simulation
// state; the display clock only drives presentation routines.

import * as PIXI from '../vendor/pixi/pixi.min.mjs';
import { project, unproject } from './world/coords.js';
import {
  chunkOf,
  chunkScreenBounds,
  hexCentre,
  hexRadiusOf,
  planeToHex,
  worldScreenBounds,
  zoomForTilePx,
} from './world/hex.js';
import { TERRAINS, TerrainSource } from './data/terrain-source.js';
import { SyntheticSource } from './data/synthetic-source.js';
import { chooseCourierRoute, observerVillage } from './data/sample/village.js';
import { TerrainLayer } from './render/terrain-layer.js';
import { PatchLayer } from './render/patch-layer.js';
import { DecorLayer } from './render/decor.js';
import { VillageLayer, bandOf } from './render/village-layer.js';
import { CrowdLayer } from './render/crowd-layer.js';
import { RunOverlays } from './render/run-overlays.js';
import { CapitalMarkers, SiteMarkers } from './render/markers.js';
import { Atlas } from './render/art/atlas.js';
import { CIV_COLORS } from './render/art/registry.js';
import { bakeSceneActors, bakeSceneGround, bakeStaticAssets } from './render/art/bake.js';
import { Camera } from './camera.js';
import { Minimap } from './ui/minimap.js';
import { Inspector } from './ui/inspector.js';
import { FrameStats, jsHeapBytes } from './ui/perf.js';
import { Quality } from './ui/quality.js';
import { budgetsFor } from './ui/budgets.js';
import { scaleCitizens } from './data/sample/citizens.js';
import { syntheticPopulation } from './data/synthetic/people.js';
import { CrowdLayout, plansFor } from './world/settlement-plan.js';
import { VILLAGE_RADIUS_M } from './data/sample/village.js';
import { RunSource } from './data/run-source.js';
import { ServerSource } from './data/server-source.js';
import { tokenFromLocation } from './data/auth.js';
import { ChroniclePanel } from './ui/chronicle.js';
import { APPEARANCE_COUNT } from './render/art/paint/people.js';

const params = new URLSearchParams(location.search);
const SYNTHETIC = params.get('source') === 'synthetic';
const DEBUG = params.has('debug') || SYNTHETIC;
// ?people=N: N synthetic people (up to 200,000) shared among the capitals (S7).
const PEOPLE = Math.max(0, Number(params.get('people') ?? 0) || 0);
// ?run=NAME (or a path with a slash): a recorded run's export, from data/runs/NAME (O2).
const RUN = params.get('run');
// ?run=live: the run `sovereign-world observe` serves, followed as it is saved (O3).
const LIVE = RUN === 'live';
const RUN_BASE = RUN && !LIVE && (RUN.includes('/') ? RUN : `data/runs/${RUN}`);
const LIVE_POLL_MS = 1000;
// ?measure=auto: after loading, tour each band for TOUR_S seconds and show a copyable table (S7).
const AUTO_MEASURE = params.get('measure') === 'auto';
const TOUR_S = Math.max(1, Number(params.get('tourSeconds') ?? 10) || 10);
const TOUR = [
  ['settlement', 1.0],
  ['local', 0.1],
  ['regional', 'tile-3000'],
  ['atlas', 'home'],
];
const $ = (id) => document.getElementById(id);

/** Loading phases with their times since navigation began, for the measurement (S7b). */
const LOAD_PHASES = [];

function setStatus(text) {
  LOAD_PHASES.push({ status: text, atMs: Math.round(performance.now()) });
  const el = $('loading-status');
  if (el) el.textContent = text;
}

/** A recorded day's people, laid out in wards around each settlement. */
function populationOf(loaded, R) {
  const { frame } = loaded;
  const plans = plansFor(frame, { coreRadius: 32 });
  const origins = frame.settlements.map((s) => hexCentre(s.q, s.r, R));
  return { frame, plans, layout: new CrowdLayout(frame, plans), origins, day: loaded.day, record: loaded.record };
}

/** Smallest zoom at which the visible chunk count stays within budget. */
function chunkBudgetZoom(source, screenW, screenH, budget) {
  const [ncq, ncr] = source.manifest.presentation.chunks;
  if (ncq * ncr <= budget) return 0; // the whole world fits the budget
  const ct = source.manifest.presentation.chunk_tiles;
  const R = hexRadiusOf(source.manifest);
  const b = chunkScreenBounds(1, 1, ct, R, source.width, source.height);
  const cw = (b.x1 - b.x0) * 0.6; // chunks overlap in screen space (skewed rows)
  const ch = (b.y1 - b.y0) * 0.6;
  let z = 1e-7;
  while (z < 1 && (screenW / (cw * z) + 2) * (screenH / (ch * z) + 2) > budget) z *= 1.05;
  return z;
}

class ObserverApp {
  constructor(pixi, source, extras) {
    this.pixi = pixi;
    this.source = source;
    this.R = hexRadiusOf(source.manifest);
    this.world = new PIXI.Container();
    pixi.stage.addChild(this.world);
    this.terrain = new TerrainLayer({ PIXI, source, ...(extras.terrainOptions ?? {}) });
    this.world.addChild(this.terrain.container);
    // Ground patches take over from about 1,700 px per tile, where a tile texture would blur.
    this.patches = new PatchLayer({
      PIXI,
      terrain: this.terrain,
      interior: () => this.terrain.interior(),
      minZoom: zoomForTilePx(1700, this.R),
    });
    this.world.addChild(this.patches.container);
    // Engine worlds take their cache caps from the screen (the synthetic test world keeps its own).
    this.screenBudgets = source instanceof TerrainSource;
    this.applyBudgets();
    this.villageData = extras.village ?? null;
    this.population = null;
    this.atlas = extras.atlas ?? null;
    this.groundBytes = extras.ground?.bytes ?? 0;
    if (extras.village || extras.population) {
      const v = extras.village;
      this.decor = new DecorLayer({ PIXI, atlas: extras.atlas, terrain: this.terrain, clear: [] });
      this.world.addChild(this.decor.container);
      if (v) {
        this.village = new VillageLayer({
          PIXI,
          atlas: extras.atlas,
          assetInfo: extras.assetInfo,
          village: v,
          ground: extras.ground.container,
        });
        this.world.addChild(this.village.container);
      }
      // The crowd (synthetic or recorded people) draws above the SAMPLE village.
      this.crowdHost = new PIXI.Container();
      this.world.addChild(this.crowdHost);
      if (extras.population) this.setPopulation(extras.population);
      else this._applyGround();
    }
    this.runOverlays = extras.run ? new RunOverlays(PIXI, this.R) : null;
    if (this.runOverlays) this.world.addChild(this.runOverlays.container);
    this.markers = extras.day0 ? new CapitalMarkers(PIXI, extras.day0, this.R) : null;
    if (this.markers) this.world.addChild(this.markers.container);
    // Sites: labels from where tiles are about 600 px across.
    this.sites = source.sites?.length ? new SiteMarkers(PIXI, source.sites, this.R, zoomForTilePx(600, this.R)) : null;
    if (this.sites) this.world.addChild(this.sites.container);
    this.bounds = worldScreenBounds(source.width, source.height, this.R);
    const { width, height } = pixi.screen;
    const fit = Math.min(width / (this.bounds.x1 - this.bounds.x0), height / (this.bounds.y1 - this.bounds.y0)) * 0.95;
    // Engine worlds may show every chunk at once (169 at 100 x 100); the synthetic
    // streaming test world keeps the tighter budget it was written for.
    const budgetZoom = chunkBudgetZoom(source, width, height, source instanceof TerrainSource ? 192 : 64);
    this.camera = new Camera({
      x: (this.bounds.x0 + this.bounds.x1) / 2,
      y: (this.bounds.y0 + this.bounds.y1) / 2,
      zoom: Math.max(fit, budgetZoom),
      minZoom: Math.max(fit * 0.8, budgetZoom),
      maxZoom: 2.6,
      clamp: (cam) => {
        cam.x = Math.max(this.bounds.x0, Math.min(this.bounds.x1, cam.x));
        cam.y = Math.max(this.bounds.y0, Math.min(this.bounds.y1, cam.y));
      },
    });
    this.home = { x: this.camera.x, y: this.camera.y, zoom: this.camera.zoom };
    this.frameStats = new FrameStats();
    this.load = { phases: LOAD_PHASES, readyMs: null, firstFrameMs: null };
    this.tourLock = null;
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
    // Sites as small neutral dots, under the capitals.
    for (const site of source.sites ?? []) {
      const c = hexCentre(site.q, site.r, this.R);
      this.minimap.addMarker(project(c.x, c.y), '#d8cfb4', 1.6);
    }
    if (extras.day0) {
      extras.day0.civilizations.forEach((civ, i) => {
        const c = hexCentre(civ.capital.tile[0], civ.capital.tile[1], this.R);
        this.minimap.addMarker(project(c.x, c.y), CIV_COLORS[i % CIV_COLORS.length]);
      });
    }
    if (this.village || this.crowdHost) {
      const r = this.village?.renderer;
      this.inspector = new Inspector($('inspector'), {
        lookup: (id) => r?.byId.get(id) ?? this.crowd?.lookup(id) ?? null,
        occupancy: () => r?.occupancy ?? new Map(),
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

  /**
   * Show a population (synthetic, or a recorded day): its crowd, and the wards and field
   * rings its settlement plans lay out, painted into the ground. Replaces any before it;
   * ground already baked is dropped so the new wards show.
   */
  setPopulation(population) {
    if (this.crowd) {
      this.crowdHost.removeChild(this.crowd.container);
      this.crowd.container.destroy({ children: true });
      this.crowd = null;
    }
    this.population = population;
    if (population) {
      this.crowd = new CrowdLayer({ PIXI, atlas: this.atlas, population });
      this.crowdHost.addChild(this.crowd.container);
    }
    this._applyGround();
  }

  /** Field rings, wards and the trees kept off them, from the village and the population. */
  _applyGround() {
    const pop = this.population;
    const v = this.villageData;
    const rings = pop ? pop.plans.map((plan, k) => ({ ...pop.origins[k], ...plan.fieldRing })) : v ? [v.fieldRing] : [];
    const wards = (pop?.plans ?? []).map((plan, k) => ({
      kind: 'ward',
      ...pop.origins[k],
      r: plan.wardRadius + 8,
      blocks: new Set(plan.blocks.map(([i, j]) => i * 4096 + j)),
    }));
    const before = this.groundKey;
    this.groundKey = JSON.stringify(rings.map((r) => [r.x, r.y, r.r0, r.r1]).concat(wards.map((w) => w.blocks.size)));
    this.terrain.setFeatures([...rings.map((ring) => ({ kind: 'fields', ...ring })), ...wards]);
    if (this.decor) this.decor.clear = rings.map((ring) => ({ x: ring.x, y: ring.y, r: ring.r1 ?? 64 }));
    if (before !== undefined && before !== this.groundKey) {
      this.patches.cache.clear();
      this.terrain.textures.clear();
      this.decor?.reset?.();
    }
  }

  /** Recorded-run mode: its chips, its day's overlays, and the day stepper. */
  showRun(run, population) {
    this.runSource = run;
    $('source-chip').textContent = run.live ? 'LIVE RUN' : 'RECORDED RUN';
    $('source-chip').title = `${run.label}: settlements, houses, people and borders as the engine recorded them.`;
    $('sample-chip').textContent = 'LAYOUT AND MOVEMENT: VISUAL APPROXIMATION';
    $('sample-chip').title =
      'Who lives where, their houses and duties are recorded; where in a settlement they stand and walk is presentation.';
    $('live-chip').textContent = 'Names: observer-assigned';
    $('day-group').hidden = false;
    if (run.live) this.setUpChronicle(run);
    this.showDay(population);
    const step = async (dir) => {
      const days = run.days;
      const at = days.indexOf(this.population.day);
      const next = days[Math.max(0, Math.min(days.length - 1, at + dir))];
      // Stepping back stops following the newest day; stepping onto it follows again.
      if (run.live) this.setFollowLatest(next === run.latest);
      if (next !== this.population.day) await this.loadDay(next);
    };
    $('btn-day-prev').addEventListener('click', () => step(-1));
    $('btn-day-next').addEventListener('click', () => step(1));
    if (run.live) this.followLive(run);
  }

  /** A live run: look for new days every second; follow the newest, or start again when the
   * run's history was cut back or replaced. */
  followLive(run) {
    const button = $('btn-day-follow');
    button.hidden = false;
    button.addEventListener('click', async () => {
      this.setFollowLatest(!this.followLatest);
      if (this.followLatest && run.latest !== this.population.day) await this.loadDay(run.latest);
    });
    this.liveState = { polls: 0, reset: false, error: null };
    // Opened on an older day (?day=N): look at it until asked to follow.
    this.setFollowLatest(this.population.day === run.latest);
    let busy = false;
    const poll = async () => {
      if (busy) return;
      busy = true;
      try {
        const news = await run.refresh();
        this.liveState.polls += 1;
        this.liveState.error = null;
        if (news.reset) {
          this.liveState.reset = true;
          setStatus('The run was cut back or replaced: starting again');
          $('loading').hidden = false;
          clearInterval(this.livePoll);
          location.reload();
          return;
        }
        if (news.added.length && this.followLatest && run.latest !== this.population.day) {
          await this.loadDay(run.latest);
        }
        this.showLiveChip();
      } catch (err) {
        this.liveState.error = String(err.message ?? err);
        this.showLiveChip();
      } finally {
        busy = false;
      }
    };
    this.livePoll = setInterval(poll, LIVE_POLL_MS);
  }

  /** The chronicle panel (live runs: the server places each event on the map). */
  setUpChronicle(run) {
    const panel = $('chronicle');
    this.chronicle = new ChroniclePanel(panel, {
      onGo: (q, r) => {
        this.follow = false;
        const c = hexCentre(q, r, this.R);
        const p = project(c.x, c.y);
        this.camera.x = p.x;
        this.camera.y = p.y;
        this.camera.setZoom(Math.max(this.camera.zoom, 0.6));
      },
      onFollow: (id) => {
        this.select(id);
        this.follow = true;
        this.inspector?.render();
      },
      canFollow: (id) => (this.population?.frame.indexOf(id) ?? -1) >= 0,
    });
    const button = $('btn-chronicle');
    button.hidden = false;
    button.addEventListener('click', () => {
      panel.hidden = !panel.hidden;
    });
    this.chronicleDay = null;
    this.loadChronicle = async (day) => {
      this.chronicleDay = day;
      const record = await run.chronicle(day);
      // A later day may have been asked for meanwhile: show only the newest ask.
      if (this.chronicleDay === day) this.chronicle.setRecord(record);
    };
  }

  setFollowLatest(on) {
    this.followLatest = on;
    const button = $('btn-day-follow');
    if (button) button.setAttribute('aria-pressed', on ? 'true' : 'false');
    this.showLiveChip();
  }

  showLiveChip() {
    const run = this.runSource;
    if (!run?.live) return;
    const chip = $('live-chip');
    const error = this.liveState?.error;
    chip.textContent = error
      ? 'LIVE RUN · server not answering'
      : `LIVE RUN · ${this.followLatest ? 'following' : 'paused'} · ${run.days.length} days`;
    chip.title = error ?? 'Days appear here as the run saves them. Names are observer-assigned.';
  }

  /** Load another exported day; the camera stays where it is. */
  async loadDay(day) {
    const loaded = await this.runSource.day(this.runSource.nearestDay(day));
    this.showDay(populationOf(loaded, this.R));
    const url = new URL(location.href);
    url.searchParams.set('day', String(loaded.day));
    history.replaceState(null, '', url);
    return loaded.day;
  }

  showDay(population) {
    // Someone no longer at a settlement that day (on the road, or dead) cannot stay selected.
    if (this.selected && population.frame.indexOf(this.selected) < 0) this.select(null);
    this.setPopulation(population);
    this.runOverlays.setDay({
      ...population.record,
      settlements: population.record.settlements.map((s, k) => ({
        ...s,
        label: population.frame.settlements[k].label,
      })),
    });
    $('day-label').textContent = `Engine day ${population.day}`;
    this.inspector?.render();
    this.loadChronicle?.(population.day).catch((err) => {
      if (this.liveState) this.liveState.error = String(err.message ?? err);
    });
    if (this.runSource?.routes) {
      const day = population.day;
      this.runSource
        .routes(day)
        .then((routes) => {
          if (this.population.day === day) this.runOverlays.setRoutes(routes);
        })
        .catch((err) => {
          if (this.liveState) this.liveState.error = String(err.message ?? err);
        });
    }
  }

  /** Cache caps for the current screen size; recomputed on resize. */
  applyBudgets() {
    const { width, height } = this.pixi.screen;
    this.budgets = budgetsFor(width, height, this.pixi.renderer.resolution);
    if (!this.screenBudgets) return;
    const b = this.budgets;
    this.terrain.setBudget({ bytes: b.terrainBytes, entries: b.terrainEntries });
    this.patches.setBudget({ bytes: b.patchBytes, entries: b.patchEntries, px: b.patchPx });
  }

  /** Caps, use, peaks and evictions of each GPU cache, for the Measurements panel. */
  caches() {
    const t = this.terrain.stats().gpu;
    const p = this.patches.stats();
    return {
      terrain: {
        maxBytes: t.maxBytes,
        maxEntries: t.maxEntries,
        bytes: Math.round(t.bytes),
        entries: t.entries,
        peakBytes: Math.round(t.peakBytes),
        evictions: t.evictions,
      },
      patches: {
        maxBytes: p.maxBytes,
        maxEntries: p.maxEntries,
        bytes: Math.round(p.bytes),
        entries: p.entries,
        peakBytes: Math.round(p.peakBytes),
        evictions: p.evictions,
        bakeMsAvg: Number(p.bakeMsAvg.toFixed(2)),
      },
      atlas: { bytes: this.atlas ? Math.round(this.atlas.textureBytes() + this.groundBytes) : 0 },
      patchPx: p.px,
      screen: this.budgets,
    };
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

  /** The SAMPLE village, or in a recorded run the first capital. */
  goToVillage(zoom = 1) {
    const origin = this.villageData?.origin ?? this.population?.origins[0];
    if (!origin) return;
    const p = project(origin.x, origin.y);
    this.camera.x = p.x;
    this.camera.y = p.y;
    this.camera.zoom = zoom;
  }

  /** Display time for drawing citizens, stepped at the quality's animation rate. */
  get animT() {
    const hz = this.settings?.animHz ?? 60;
    return Math.floor(this.t * hz) / hz;
  }

  frame(deltaMS, advance = !this.paused) {
    const t0 = performance.now();
    if (advance) this.t += (deltaMS / 1000) * this.speed;
    // During the measurement tour the camera is held: anything that moved it is undone, and noted.
    const lock = this.tourLock;
    if (lock && (this.camera.x !== lock.x || this.camera.y !== lock.y || this.camera.zoom !== lock.zoom)) {
      lock.touched = true;
      Object.assign(this.camera, { x: lock.x, y: lock.y, zoom: lock.zoom });
    }
    if (!lock && this.follow && this.selected) {
      this.village?.renderer.update(this.animT);
      const target = this.village?.worldPosition(this.selected) ?? this.crowd?.worldPosition(this.selected);
      if (target) this.camera.followTowards(target, deltaMS);
    }
    this.camera.clamp();
    const { width, height } = this.pixi.screen;
    this.camera.apply(this.world, width, height);
    const view = this.camera.viewRect(this.world, width, height, 0);
    this.view = view;
    this.terrain.update(view, this.camera.zoom);
    this.patches.bakesPerFrame = this.settings?.patchBakes ?? 2;
    this.patches.update(view, this.camera.zoom);
    this.decor?.update(view, this.camera.zoom);
    const margin = this.camera.viewRect(this.world, width, height, 80);
    this.village?.update(this.animT, margin, this.camera.zoom, {
      selected: this.selected,
      showFootprints: this.showFootprints,
      crowdBudget: this.settings?.crowdBudget ?? Infinity,
    });
    this.crowd?.update(this.animT, margin, this.camera.zoom, {
      selected: this.selected,
      crowdBudget: this.crowdBudgetOverride ?? this.settings?.crowdBudget ?? Infinity,
    });
    this.runOverlays?.update(this.camera.zoom);
    this.markers?.update(this.camera.zoom);
    this.sites?.update(this.camera.zoom);
    this.minimap.draw(view);
    this.frameStats.push(performance.now() - t0);
  }

  stats() {
    const t = this.terrain.stats();
    const p = this.patches.stats();
    const v = { ...(this.village?.counts() ?? {}) };
    if (this.crowd) {
      // R9 counts add the crowd to the SAMPLE village's people.
      const c = this.crowd.counts();
      for (const k of ['worldPopulation', 'indoor', 'outdoor', 'visible', 'visibleFull', 'visibleSimplified'])
        v[k] = (v[k] ?? 0) + c[k];
      v.resident = (v.resident ?? 0) + c.worldPopulation;
      v.crowdDots = c.dots;
    }
    const iv = this.intervalStats.summary();
    return {
      fps: Math.round(this.pixi.ticker.FPS),
      frameMsAvg: iv.updateMsAvg,
      frameMsP95: iv.updateMsP95,
      ...this.frameStats.summary(),
      quality: `${this.quality.mode}${this.quality.mode === 'auto' ? ` (${this.quality.level})` : ''}`,
      zoom: Number(this.camera.zoom.toFixed(4)),
      band: bandOf(this.camera.zoom, this.terrain.hexModeZoom),
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
      patchSize: p.size,
      visiblePatches: p.visible,
      patchBytes: Math.round(p.bytes),
      patchBakeMsAvg: Number(p.bakeMsAvg.toFixed(2)),
      decorSprites: this.decor?.container.visible ? this.decor.sprites : 0,
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
      load: this.load,
      caches: this.caches(),
      tour: this.tourRows ?? null,
      sampleCitizens: this.village ? this.village.renderer.people.length : 0,
      people: this.population ? this.population.frame.length : 0,
      stats: this.stats(),
      note: 'Browser rendering only. Simulation throughput is measured separately.',
    };
  }

  /** The person or building under a canvas point; the crowd is drawn on top, so it is asked first. */
  pickAt(cx, cy) {
    if (!this.village && !this.crowd) return null;
    const w = this.camera.screenToWorld(this.world, cx, cy);
    const hit = this.crowd?.pick(w.x, w.y, this.camera.zoom) ?? this.village?.pick(w.x, w.y, this.camera.zoom) ?? null;
    if (hit) return hit;
    // A recorded run's travellers: the dot on their tile, and the parties there.
    const travellers = this.runOverlays?.pickTraveller(w.x, w.y, this.camera.zoom);
    return travellers ? { travellers } : null;
  }

  /** Select what a pick found: one person or building, or a list of people (a local-band dot). */
  applyPick(hit) {
    this.runOverlays?.showRoutes(hit?.travellers?.parties ?? []);
    if (hit?.travellers) {
      this.selected = null;
      this.follow = false;
      this.inspector?.showParties({ ...hit.travellers, civilizations: this.runSource?.manifest.civilizations });
      return;
    }
    if (hit?.list) {
      this.selected = null;
      this.follow = false;
      this.inspector?.showList(hit.list, `${hit.count} people here (${hit.cell} m cell)`);
      return;
    }
    this.select(hit ? hit.hit.id : null);
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
      if (this.tourLock) {
        drag = null;
        return;
      }
      if (drag && drag.moved <= 4) {
        const rect = canvas.getBoundingClientRect();
        const hit = this.pickAt(e.clientX - rect.left, e.clientY - rect.top);
        this.applyPick(hit);
        $('pick-note').textContent =
          hit && !hit.list && hit.count > 1 ? `${hit.count} here · click again to cycle` : '';
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
          Math.exp(-e.deltaY * 0.0025),
        );
      },
      { passive: false },
    );
    // Zoom spans five decades (a whole continent down to one person): bigger steps.
    $('btn-zoom-in').addEventListener('click', () => this.camera.setZoom(this.camera.zoom * 2));
    $('btn-zoom-out').addEventListener('click', () => this.camera.setZoom(this.camera.zoom / 2));
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
      if (!panel.hidden) this.renderMeasurements();
    });
    this.pixi.renderer.on('resize', () => this.applyBudgets());
    $('btn-copy')?.addEventListener('click', async () => {
      const text = JSON.stringify(this.measurement(), null, 1);
      this.renderMeasurements();
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

  /** Point the camera for a tour stop: the crowd's busiest ward (or the village) at a zoom, or the whole world. */
  tourView(zoom) {
    this.follow = false;
    if (zoom === 'home') {
      Object.assign(this.camera, this.home);
      return;
    }
    const z = zoom === 'tile-3000' ? zoomForTilePx(3000, this.R) : zoom;
    const target = this.crowd ? this.crowd.worldPosition(this.crowd.busiestId()) : null;
    if (target) {
      this.camera.x = target.x;
      this.camera.y = target.y;
      this.camera.zoom = z;
    } else this.goToVillage(z);
  }

  /**
   * The measurement tour: TOUR_S seconds in each band with the clock running,
   * then frame intervals, our own update time, counts and caches per band.
   */
  async tour(seconds = TOUR_S) {
    const rows = [];
    const banner = $('tour-banner');
    const mb = (bytes) => (bytes ? Number((bytes / 1e6).toFixed(1)) : null);
    const snapshot = () => ({
      patchEvictions: this.patches.cache.evictions,
      terrainEvictions: this.terrain.stats().gpu.evictions,
      patchBakes: this.patches.baked,
      heap: jsHeapBytes(),
    });
    this.setPaused(false);
    try {
      for (const [i, [band, zoom]] of TOUR.entries()) {
        this.tourView(zoom);
        this.camera.clamp();
        // Hold this pose for the whole stop (frame() puts the camera back if anything moves it).
        const lock = { x: this.camera.x, y: this.camera.y, zoom: this.camera.zoom, touched: false };
        this.tourLock = lock;
        if (banner) {
          banner.textContent = `Measuring the ${band} band (stop ${i + 1} of ${TOUR.length}, ${seconds} s) — please don't touch the mouse, keyboard or window until the table appears.`;
          banner.hidden = false;
        }
        await new Promise((r) => setTimeout(r, 1500)); // let streaming settle first
        this.frameStats = new FrameStats(4000);
        this.intervalStats = new FrameStats(4000);
        const before = snapshot();
        await new Promise((r) => setTimeout(r, seconds * 1000));
        const after = snapshot();
        const s = this.stats();
        rows.push({
          band,
          // The band actually measured, from the held pose (not the camera, which a late wheel may have moved).
          viewBand: bandOf(lock.zoom, this.terrain.hexModeZoom),
          zoom: Number(lock.zoom.toPrecision(4)),
          touched: lock.touched,
          frameMsAvg: s.frameMsAvg,
          frameMsP95: s.frameMsP95,
          updateMsAvg: s.updateMsAvg,
          updateMsP95: s.updateMsP95,
          visible: s.visible,
          visibleFull: s.visibleFull,
          terrainMB: mb(s.gpuBytes),
          patchMB: mb(s.patchBytes),
          patchEntries: this.patches.cache.map.size,
          patchEvictions: after.patchEvictions - before.patchEvictions,
          terrainEvictions: after.terrainEvictions - before.terrainEvictions,
          patchBakes: after.patchBakes - before.patchBakes,
          atlasMB: mb(s.atlasBytes),
          heapStartMB: mb(before.heap),
          heapMB: mb(s.jsHeapBytes),
        });
      }
    } finally {
      this.tourLock = null;
      if (banner) banner.hidden = true;
    }
    this.tourRows = rows;
    $('measure').hidden = false;
    this.renderMeasurements();
    return rows;
  }

  /** The caps table and the full measurement JSON. */
  renderMeasurements() {
    const m = this.measurement();
    const mb = (v) => (v / 1e6).toFixed(1);
    const secs = (ms) => (ms == null ? '—' : (ms / 1000).toFixed(1));
    const c = m.caches;
    const row = (name, x) =>
      `<tr><td>${name}</td><td>${mb(x.bytes)} / ${mb(x.maxBytes)}</td><td>${x.entries} / ${x.maxEntries}</td><td>${mb(x.peakBytes)}</td><td>${x.evictions}</td></tr>`;
    const table = $('measure-caps');
    if (table) {
      table.innerHTML = `<tr><th>GPU cache</th><th>MB used / cap</th><th>entries / cap</th><th>peak MB</th><th>evictions</th></tr>
        ${row('Terrain', c.terrain)}${row(`Ground patches (${c.patchPx} px, bake ${c.patches.bakeMsAvg} ms)`, c.patches)}
        <tr><td>Art atlas and village ground</td><td>${mb(c.atlas.bytes)}</td><td colspan="3">fixed</td></tr>
        <tr><td colspan="5">Screen ${c.screen.devicePixels.toLocaleString('en')} device pixels: caps × ${c.screen.factor}</td></tr>
        <tr><td colspan="5">Ready in ${secs(m.load.readyMs)} s, first frame at ${secs(m.load.firstFrameMs)} s</td></tr>`;
    }
    const tour = $('measure-tour');
    if (tour && this.tourRows) {
      const cols = [
        'band',
        'zoom',
        'touched',
        'frameMsAvg',
        'frameMsP95',
        'updateMsAvg',
        'updateMsP95',
        'visible',
        'terrainMB',
        'patchMB',
        'patchEvictions',
        'atlasMB',
        'heapStartMB',
        'heapMB',
      ];
      tour.innerHTML = `<tr>${cols.map((k) => `<th>${k}</th>`).join('')}</tr>${this.tourRows
        .map((r) => `<tr>${cols.map((k) => `<td>${r[k] ?? '—'}</td>`).join('')}</tr>`)
        .join('')}`;
    }
    $('measure-json').textContent = JSON.stringify(m, null, 1);
  }

  run() {
    const perf = $('perf');
    const clock = $('clock');
    let hud = 0;
    this.pixi.ticker.add((ticker) => {
      this.load.firstFrameMs ??= Math.round(performance.now());
      this.intervalStats.push(ticker.deltaMS);
      this.quality.observe(ticker.deltaMS);
      this.frame(ticker.deltaMS);
      hud += ticker.deltaMS;
      if (hud > 250) {
        hud = 0;
        const s = this.stats();
        if (clock) {
          const minutes = 8 * 60 + Math.floor(this.t / 60);
          const hhmm = `${String(Math.floor(minutes / 60) % 24).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`;
          clock.textContent = this.runSource
            ? `Engine day ${this.population.day} · display time ${hhmm}`
            : `Engine day 0 · sample time ${hhmm}`;
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
    band: () => bandOf(app.camera.zoom, app.terrain.hexModeZoom),
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
    /** The zoom at which one tile is `px` screen pixels wide. */
    zoomForTilePx: (px) => zoomForTilePx(px, app.R),
    R: () => app.R,
    /** The SAMPLE courier's journey: arrival at each tile, camps and fords, in schedule time. */
    courierPlan: () => app.villageData?.courierPlan ?? null,
    /** Largest local coordinate drawn by the village's Graphics (dots, pin), after a frame. */
    graphicsExtent: () => {
      app.frame(0, false);
      const reach = (g) => {
        if (!g.context?.instructions?.length) return 0;
        const b = g.getLocalBounds();
        return Math.max(Math.abs(b.minX), Math.abs(b.maxX), Math.abs(b.minY), Math.abs(b.maxY));
      };
      const dots = reach(v.dots);
      const pin = reach(v.pin);
      return { dots, pin, max: Math.max(dots, pin) };
    },
    /** How many times citizens' schedules have been recomputed for drawing. */
    animRecomputes: () => app.village?.renderer.recomputes ?? 0,
    /** Run frames until every visible chunk is loaded and baked at the wanted level. */
    settle: async (maxFrames = 600) => {
      for (let f = 0; f < maxFrames; f += 1) {
        app.frame(16, false);
        if (app.terrain.stats().complete && app.patches.complete) return { frames: f + 1, complete: true };
        await new Promise((r) => setTimeout(r, 15));
      }
      return { frames: maxFrames, complete: false };
    },
    /** The art atlas: pages, fill, and pixels by kind, plus the village ground's bytes. */
    atlasStats: () => ({ ...app.atlas.stats(), groundBytes: Math.round(app.groundBytes) }),
    atlasKeys: () => [...app.atlas.entries.keys()],
    /** The population feed (?people=N): totals, bytes and each settlement's plan. */
    populationStats: () => {
      const pop = app.population;
      if (!pop) return null;
      const { frame, plans, layout } = pop;
      return {
        people: frame.length,
        bytes: frame.bytes() + layout.bytes() + plans.reduce((sum, p) => sum + p.bytes(), 0),
        settlements: frame.settlements.map((s, k) => ({
          id: s.id,
          civ: s.civ,
          tile: [s.q, s.r],
          residents: frame.residents(k),
          houses: plans[k].houseCount,
          wardRadius: plans[k].wardRadius,
          fieldRing: plans[k].fieldRing,
        })),
      };
    },
    terrainStats: () => app.terrain.stats(),
    patchStats: () => app.patches.stats(),
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
    activityOf: (id) => v.renderer.activityAt(id, app.t),
    /** Ground-plane distance in metres from (x, y) to the centre of tile (q, r). */
    distanceToHexCentre: (x, y, q, r) => {
      const c = hexCentre(q, r, app.R);
      return Math.hypot(x - c.x, y - c.y);
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
    /** Display seconds per real second. */
    speed: () => app.speed,
    /** Engine sites (ore deposits, quarries, ruins, troves) from the export. */
    sites: () => app.source.sites ?? [],
    pickAt: (cx, cy) => {
      const hit = app.pickAt(cx, cy);
      app.applyPick(hit);
      app.frame(0, false);
      return hit?.hit ? hit.hit.id : null;
    },
    /** A local-band click: the ids listed in the inspector, or null. */
    pickList: (cx, cy) => {
      const hit = app.pickAt(cx, cy);
      app.applyPick(hit);
      return hit?.list ?? null;
    },
    // ---- a recorded run (?run=NAME)
    /** The run's exported days, the day shown, and the engine's and the drawing's counts. */
    runInfo: () => {
      if (!app.runSource) return null;
      const pop = app.population;
      return {
        days: app.runSource.days,
        day: pop.day,
        counts: pop.record.counts,
        residents: pop.record.settlements.map((s) => s.residents),
        drawnResidents: pop.frame.settlements.map((_, k) => pop.frame.residents(k)),
        overlays: app.runOverlays.counts(),
      };
    },
    loadDay: (day) => app.loadDay(day),
    /** The day shown's travellers: [q, r, civilization, count] per tile. */
    runTravellers: () => app.population?.record.travellers ?? [],
    /** Canvas point of the k-th traveller dot of the day shown, or null. */
    travellerPoint: (k = 0) => {
      const t = app.population?.record.travellers?.[k];
      if (!t) return null;
      const c = hexCentre(t[0], t[1], app.R);
      const p = project(c.x, c.y);
      return toCanvas(p.x, p.y);
    },
    /** What the inspector shows (its heading), and the routes drawn. */
    inspectorTitle: () => document.querySelector('#inspector h2')?.textContent ?? null,
    /** Live runs: how many day records were rebuilt from the changes since another day. */
    recordsByChanges: () => app.runSource?.byChanges ?? 0,
    /** The chronicle as shown: its day, and each listed event's kind and buttons. */
    chronicleInfo: () => {
      const panel = document.getElementById('chronicle');
      if (!app.chronicle?.record) return null;
      return {
        day: app.chronicle.record.day,
        total: app.chronicle.record.events.length,
        open: !panel.hidden,
        items: [...panel.querySelectorAll('#chronicle-list li')].map((li) => ({
          kind: li.dataset.kind,
          what: li.querySelector('.what').textContent,
          where: li.querySelector('.where').textContent,
          go: !li.querySelector('button.go').disabled,
          follow: li.querySelector('button.follow')?.dataset.person ?? null,
          canFollow: li.querySelector('button.follow') ? !li.querySelector('button.follow').disabled : false,
        })),
      };
    },
    /** Where the camera looks: its centre in hex-world coordinates, and whom it follows. */
    cameraInfo: () => ({
      x: app.camera.x,
      y: app.camera.y,
      zoom: app.camera.zoom,
      follow: app.follow,
      selected: app.selected,
    }),
    /** The camera position a tile's centre has. */
    hexCamera: (q, r) => {
      const c = hexCentre(q, r, app.R);
      return project(c.x, c.y);
    },
    /** A live run: following or not, polls made, and the last error. */
    liveInfo: () =>
      app.runSource?.live
        ? { following: app.followLatest, days: app.runSource.days, latest: app.runSource.latest, ...app.liveState }
        : null,
    /** Each settlement's town as drawn: designed or plain, its walls, and pieces. */
    townInfo: () =>
      (app.population?.plans ?? []).map((plan, k) => ({
        settlement: app.population.frame.settlements[k].id,
        designed: plan.designed,
        style: plan.design?.style ?? null,
        walls: plan.design ? plan.wallsBuilt() : null,
        planned: plan.planned?.length ?? 0,
        pieces: (plan.pieces ?? []).reduce((n, p) => {
          const kind = p.asset.startsWith('wall.gatehouse')
            ? 'gatehouses'
            : p.asset.startsWith('wall.gate')
              ? 'gates'
              : p.asset.startsWith('wall.tower')
                ? 'towers'
                : p.asset.startsWith('wall.')
                  ? 'walls'
                  : 'places';
          return { ...n, [kind]: (n[kind] ?? 0) + 1 };
        }, {}),
        places: (plan.places ?? []).map((p) => ({ name: p.name, place: p.place, x: p.x, y: p.y })),
        ditch: plan.ditch?.kind ?? null,
        stakes: plan.stakes?.length ?? 0,
        citadel: !!plan.citadel,
        defence: plan.defence ?? null,
        // Where a built wall piece stands, in world metres, to look at it.
        wallAt: (() => {
          const piece = (plan.pieces ?? []).find((p) => p.asset.startsWith('wall.'));
          const o = app.population.origins[k];
          return piece ? { x: o.x + piece.x, y: o.y + piece.y } : null;
        })(),
        resident: app.population.frame.rowsOf(k).length
          ? app.population.frame.idOf(app.population.frame.rowsOf(k)[0])
          : null,
      })),
    // ---- the crowd (?people=N)
    crowdCounts: () => ({ ...app.crowd.counts(), ...app.crowd.stat }),
    crowdBudget: () => app.crowdBudgetOverride ?? app.settings?.crowdBudget ?? Infinity,
    /** Override the crowd's full-sprite budget (null: the quality's). */
    setCrowdBudget: (n) => {
      app.crowdBudgetOverride = n;
      app.frame(0, false);
    },
    /** Where a person is now: world ground-plane metres, indoors or not, walking or not. */
    crowdState: (id) => {
      const i = app.crowd.frame.indexOf(id);
      const s = app.crowd.stateOf(i);
      const o = app.population.origins[app.crowd.frame.settlement[i]];
      return { x: s.x + o.x, y: s.y + o.y, inside: s.inside, moving: s.moving, anim: s.anim, activity: s.activity };
    },
    /** Centre the camera on a person, optionally `offsetPx` screen pixels left of them. */
    viewPerson: (id, zoom, offsetPx = 0) => {
      const p = app.crowd.worldPosition(id);
      app.camera.zoom = zoom;
      app.camera.x = p.x - offsetPx / zoom;
      app.camera.y = p.y;
      app.frame(0, false);
    },
    /** Someone out of doors in the middle of the first capital's wards. */
    crowdBusiest: () => app.crowd.busiestId(),
    /** How a person is drawn now: 'actor', 'particle' or null. */
    crowdDrawnAs: (id) => app.crowd.drawnAs(id),
    /** Canvas point of the largest dot in view (local band). */
    crowdDotPoint: () => {
      let best = null;
      for (const site of app.crowd.sites) {
        if (!site.root.visible || !site.dots.visible) continue;
        for (const q of site.dots.particleChildren) {
          const p = toCanvas(q.x + site.offset.x, q.y + site.offset.y);
          const { width, height } = app.pixi.screen;
          if (p.x < 0 || p.y < 0 || p.x > width || p.y > height) continue;
          if (!best || q.n > best.n) best = { n: q.n, ...p };
        }
      }
      return best;
    },
    personPinPoint: (id) => {
      const p = app.crowd.worldPosition(id);
      return toCanvas(p.x, p.y);
    },
    canvasCentre: () => ({ x: app.pixi.screen.width / 2, y: app.pixi.screen.height / 2 }),
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
      if (app.crowd?.has(id)) return app.crowd.drawnAs(id) ? app.crowd.canvasPoint(id, toCanvas) : null;
      const o = v.renderer.byId.get(id);
      if (!o || o.hidden || !o.sprite.visible) return null;
      const off = v.offset;
      const p = project(o.x, o.y, 1.0);
      return toCanvas(p.x + off.x, p.y + off.y);
    },
    isDrawn: (id) => v.renderer.drawOrder.some((o) => o.id === id),
    releaseHidden: () => {
      app.terrain.releaseHidden();
      app.patches.releaseHidden();
    },
    textureCount: () => ({
      terrain: app.terrain.liveTextures,
      // Freed slots are left as null in the list, so count live entries only.
      pixiManaged: app.pixi.renderer.texture?.managedTextures?.filter(Boolean).length ?? null,
    }),
    measurement: () => app.measurement(),
    /** Run the measurement tour (as ?measure=auto does); resolves to its rows. */
    tour: (seconds) => app.tour(seconds),
    /** Point the camera as the tour does: a zoom, 'tile-3000' or 'home'. */
    viewTour: (zoom) => {
      app.tourView(zoom);
      app.frame(0, false);
    },
    /** The cache caps for this screen (ui/budgets.js) and each cache's use. */
    budgets: () => app.budgets,
    caches: () => app.caches(),
    setQuality: (mode) => app.quality.setMode(mode),
    villageInfo: () =>
      app.villageData
        ? {
            tile: app.villageData.tile,
            origin: app.villageData.origin,
            dropped: app.villageData.dropped,
            courierTile: app.villageData.courierTile,
          }
        : null,
  };
}

/** The SAMPLE courier's route, read from the engine tiles around the capital. */
async function courierRoute(source, tile) {
  const ct = source.manifest.presentation.chunk_tiles;
  const chunks = new Map();
  const tileAt = async (q, r) => {
    const key = `${Math.floor(q / ct)},${Math.floor(r / ct)}`;
    if (!chunks.has(key)) chunks.set(key, await source.load(Math.floor(q / ct), Math.floor(r / ct)));
    const c = chunks.get(key);
    for (let i = 0; i < c.n; i += 1) if (c.q[i] === q && c.r[i] === r) return c.terrain[i];
    return 0;
  };
  const terrain = new Map();
  const [q0, r0] = tile;
  for (let dr = -4; dr <= 4; dr += 1)
    for (let dq = -4; dq <= 4; dq += 1) {
      const [q, r] = [q0 + dq, r0 + dr];
      if (q >= 0 && r >= 0 && q < source.width && r < source.height) terrain.set(`${q},${r}`, await tileAt(q, r));
    }
  const rivers = source.rivers;
  const flowBetween = (a, b) => {
    const [aq, ar] = Array.isArray(a) ? a : [a.q, a.r];
    const [bq, br] = Array.isArray(b) ? b : [b.q, b.r];
    const edge = (rivers.byTile.get(`${aq},${ar}`) ?? []).find(
      (e) => (e.bq === bq && e.br === br) || (e.aq === bq && e.ar === br),
    );
    return edge ? edge.flow : 0;
  };
  const route = chooseCourierRoute(tile, {
    landAt: (q, r) => (terrain.get(`${q},${r}`) ?? 0) !== 0,
    deepBetween: (a, b) => flowBetween(a, b) >= rivers.deepFlow,
    chunkTiles: ct,
    width: source.width,
    height: source.height,
  });
  const travel = {
    rules: source.manifest.engine.travel,
    terrainAt: (q, r) => TERRAINS[terrain.get(`${q},${r}`) ?? 0],
    flowBetween,
  };
  return { route, travel };
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
  } else if (RUN_BASE || LIVE) {
    let run;
    if (LIVE) {
      setStatus('Connecting to the observer server');
      run = await ServerSource.open('api', { token: tokenFromLocation() });
      while (run.latest === null) {
        setStatus('Waiting for the run’s first day');
        await new Promise((resolve) => setTimeout(resolve, LIVE_POLL_MS));
        if ((await run.refresh()).reset) location.reload();
      }
    } else {
      setStatus('Loading the recorded run');
      run = await RunSource.open(RUN_BASE);
    }
    source = await run.terrain();
    extras = { overview: await source.overview(), run, terrainOptions: { gpuBytes: 192e6, gpuEntries: 256 } };
    const atlas = new Atlas(PIXI);
    const { assetInfo } = await bakeStaticAssets(atlas, CIV_COLORS[0], setStatus);
    const designs = Array.from({ length: APPEARANCE_COUNT }, (_, a) => ({ appearance: a }));
    await bakeSceneActors(atlas, { people: designs, caravan: null }, setStatus);
    atlas.finalize();
    const day = run.nearestDay(Number(params.get('day') ?? (LIVE ? run.latest : run.days[0])));
    setStatus(`Loading engine day ${day}`);
    extras.population = populationOf(await run.day(day), hexRadiusOf(source.manifest));
    Object.assign(extras, { atlas, assetInfo });
  } else {
    setStatus('Loading engine terrain manifest');
    source = await TerrainSource.open('data/terrain');
    const day0 = await source.day0();
    extras = { overview: await source.overview(), day0, terrainOptions: { gpuBytes: 192e6, gpuEntries: 256 } };
    const atlas = new Atlas(PIXI);
    const { assetInfo } = await bakeStaticAssets(atlas, CIV_COLORS[0], setStatus);
    const footprintOf = (asset) => assetInfo.get(asset).footprint;
    const tile = day0.civilizations[0].capital.tile;
    const { route, travel } = await courierRoute(source, tile);
    const village = observerVillage(footprintOf, hexRadiusOf(source.manifest), tile, route, travel);
    scaleCitizens(village.scene, Number(params.get('citizens') ?? 400));
    if (PEOPLE) {
      setStatus(`Placing ${PEOPLE.toLocaleString()} people`);
      const capitals = day0.civilizations.map((c) => c.capital);
      const frame = syntheticPopulation(PEOPLE, capitals);
      const plans = plansFor(frame, { coreRadius: VILLAGE_RADIUS_M });
      const origins = capitals.map((c) => hexCentre(c.tile[0], c.tile[1], hexRadiusOf(source.manifest)));
      extras.population = { frame, plans, layout: new CrowdLayout(frame, plans), origins };
    }
    await bakeSceneActors(atlas, village.scene, setStatus);
    atlas.finalize();
    const ground = await bakeSceneGround(PIXI, village.scene, setStatus, {
      alpha: village.alpha,
      bounds: village.groundBounds,
      texelScale: 0.5,
      mipmaps: false,
    });
    Object.assign(extras, { atlas, assetInfo, village, ground });
  }
  $('world-label').textContent = SYNTHETIC
    ? source.label
    : `Engine world · seed ${source.manifest.engine.seed} · ${source.width}×${source.height} tiles`;
  const app = new ObserverApp(pixi, source, extras);
  if (extras.run) app.showRun(extras.run, extras.population);
  const note = $('provenance');
  if (note && extras.village) {
    const [q, r] = extras.village.tile;
    const borders = source.rivers?.byTile.get(`${q},${r}`) ?? [];
    note.textContent += borders.length
      ? ` A river borders the capital tile; no bridge, since none is built before day 1.`
      : ' No river borders the capital tile.';
  }
  app.bindInput();
  app.setPaused(false);
  app.run();
  window.__observer = buildApi(app);
  $('loading').hidden = true;
  app.load.readyMs = Math.round(performance.now());
  if (AUTO_MEASURE) app.tour();
}

main().catch((err) => {
  console.error(err);
  setStatus(`Failed: ${err.message}`);
  window.__observerError = String(err && err.stack ? err.stack : err);
});
