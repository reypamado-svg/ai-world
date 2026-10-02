// The SAMPLE village inside the world camera, with level-of-detail bands:
//  - atlas (tiles under 256 px across): terrain chunks; the village is a badge;
//  - regional (to zoom 0.02): one detailed texture per tile; badge and markers;
//  - local (0.02 - 0.5): buildings as small sprites, people as group dots;
//  - settlement (> 0.5): full sprites and animated citizens.
// The village itself is ~110 m across, so its own crossfades (buildings 0.04 -
// 0.08, people 0.4 - 0.6) sit inside the local band.
// Bands crossfade, and the selected person stays identifiable in every band
// (ring when drawn, pin otherwise).

import { project, unproject } from '../world/coords.js';
import { SceneRenderer } from './scene-renderer.js';
import { CIV_COLORS } from './art/registry.js';

export const BANDS = { localMin: 0.02, settlementMin: 0.5 };

function smooth(a, b, x) {
  const t = Math.max(0, Math.min(1, (x - a) / (b - a)));
  return t * t * (3 - 2 * t);
}

function hexNum(css) {
  return parseInt(css.replace('#', ''), 16);
}

/** The band at a zoom; `atlasMax` is where per-tile textures begin (it depends on tile size). */
export function bandOf(zoom, atlasMax) {
  if (zoom < atlasMax) return 'atlas';
  if (zoom < BANDS.localMin) return 'regional';
  if (zoom < BANDS.settlementMin) return 'local';
  return 'settlement';
}

export class VillageLayer {
  constructor({ PIXI, atlas, assetInfo, village, ground }) {
    this.PIXI = PIXI;
    this.village = village;
    this.renderer = new SceneRenderer({ PIXI, atlas, assetInfo, scene: village.scene, ground, origin: village.origin });
    this.container = new PIXI.Container();
    this.dots = new PIXI.Graphics();
    this.pin = new PIXI.Graphics();
    this.badge = new PIXI.Container();
    const g = new PIXI.Graphics();
    g.roundRect(-6, -40, 216, 34, 6).fill({ color: 0x1b1408, alpha: 0.85 }).stroke({ width: 1.5, color: 0xe9b44c });
    const text = new PIXI.Text({
      text: `SAMPLE village · ${village.scene.people.length} sample citizens`,
      style: { fontFamily: 'system-ui, sans-serif', fontSize: 12, fontWeight: '700', fill: 0xe9b44c },
    });
    text.position.set(2, -33);
    this.badge.addChild(g, text);
    const o = project(village.origin.x, village.origin.y);
    this.badge.position.set(o.x - 100, o.y + 60);
    // Graphics are tessellated in float32: draw them relative to the village origin.
    this.dots.position.set(o.x, o.y);
    this.pin.position.set(o.x, o.y);
    this.container.addChild(this.renderer.root, this.dots, this.pin, this.badge);
  }

  /** R9 counts; in the regional band visible people are drawn as dots (simplified). */
  counts() {
    const r = this.renderer;
    const c = r.counts();
    // Visible: outdoor people inside the view, drawn either as full sprites
    // (settlement band) or simplified (still frames or group dots).
    let visible = 0;
    let full = 0;
    const v = this.lastLocal;
    const shown = r.root.visible && (this.actors > 0.01 || (1 - this.actors) * this.regional > 0.02);
    if (v && shown) {
      const all = r.caravan ? [...r.people, r.caravan.driver] : r.people;
      for (const o of all) {
        if (o.hidden) continue;
        const p = project(o.x, o.y);
        if (p.x < v.x0 || p.x > v.x1 || p.y < v.y0 || p.y > v.y1) continue;
        visible += 1;
        if (this.actors > 0.5 && o.drawn && !o.simple) full += 1;
      }
    }
    return { ...c, visible, visibleFull: full, visibleSimplified: visible - full };
  }

  get offset() {
    return this.renderer.offset;
  }

  /** view: world-screen rect; returns the renderer's visible objects. */
  update(t, view, zoom, { selected, showFootprints, crowdBudget = Infinity }) {
    const r = this.renderer;
    const off = r.offset;
    const focus = unproject((view.x0 + view.x1) / 2 - off.x, (view.y0 + view.y1) / 2 - off.y);
    r.setCrowd(crowdBudget, focus);
    const regional = smooth(0.04, 0.08, zoom);
    const actors = smooth(0.4, 0.6, zoom);
    this.actors = actors;
    this.regional = regional;
    r.drawActors = actors > 0.01 && regional > 0.01;
    r.update(t);
    r.root.visible = regional > 0.01;
    r.root.alpha = regional;
    for (const o of r.objects) {
      if (o.kind === 'person' || o.kind === 'ox' || o.kind === 'wagon') {
        o.sprite.alpha = actors;
        if (o.shadow) o.shadow.alpha = actors;
      }
    }
    const local = { x0: view.x0 - off.x, y0: view.y0 - off.y, x1: view.x1 - off.x, y1: view.y1 - off.y };
    this.lastLocal = local;
    if (!r.root.visible) r.drawOrder = [];
    const visible = r.root.visible ? r.cullAndSort(local) : [];
    r.decorate({ selected: actors > 0.5 ? selected : null, zoom, showFootprints, visible });
    this._dots(zoom, 1 - actors, regional);
    this._pin(selected, zoom, actors);
    this.badge.scale.set(1 / zoom);
    const o = project(this.village.origin.x, this.village.origin.y);
    this.badge.position.set(o.x - 100 / zoom, o.y + 40 / zoom);
    this.badge.alpha = 1 - smooth(0.15, 0.3, zoom);
    this.badge.visible = this.badge.alpha > 0.01;
    return visible;
  }

  /** Outdoor people as group dots, clustered on an 8 m grid. */
  _dots(zoom, alpha, regional) {
    const g = this.dots;
    g.clear();
    if (alpha * regional < 0.02) return;
    const groups = new Map();
    for (const o of this.renderer.people) {
      if (o.hidden) continue;
      const k = `${Math.round(o.x / 8)},${Math.round(o.y / 8)}`;
      const e = groups.get(k) ?? { x: 0, y: 0, n: 0, civ: o.person.civ };
      e.x += o.x;
      e.y += o.y;
      e.n += 1;
      groups.set(k, e);
    }
    for (const e of groups.values()) {
      const p = project(e.x / e.n, e.y / e.n);
      const rad = (2.5 + Math.sqrt(e.n) * 1.6) / zoom;
      g.circle(p.x, p.y, rad)
        .fill({ color: hexNum(CIV_COLORS[e.civ]), alpha: alpha * regional })
        .stroke({ width: 1 / zoom, color: 0xffffff, alpha: alpha * regional });
    }
  }

  /** A pin above the selected person whenever the full sprite is not shown. */
  _pin(selected, zoom, actors) {
    const g = this.pin;
    g.clear();
    if (!selected || actors > 0.5) return;
    const pos = this.worldPosition(selected);
    if (!pos) return;
    const s = 1 / zoom;
    // Relative to the village origin, where the pin's container sits.
    const x = pos.x - this.pin.position.x;
    const y = pos.y - this.pin.position.y;
    g.poly([x, y, x - 7 * s, y - 14 * s, x + 7 * s, y - 14 * s]).fill(0xffd84a);
    g.circle(x, y - 18 * s, 9 * s)
      .fill(0xffd84a)
      .stroke({ width: 2 * s, color: 0x2a2010 });
    g.circle(x, y - 18 * s, 3.5 * s).fill(0x2a2010);
  }

  /** World-screen position (zoom 1) of a person or object, following indoors. */
  worldPosition(id) {
    const r = this.renderer;
    const target = r.followTarget(id);
    const o = r.byId.get(id);
    let p = target;
    if (!p && o) p = project(o.x, o.y);
    if (!p) return null;
    return { x: p.x + this.offset.x, y: p.y + this.offset.y };
  }

  /** Pick at a world-screen point; regional band picks the nearest outdoor person. */
  pick(wx, wy, zoom) {
    const off = this.offset;
    if (zoom >= 0.4) return this.renderer.pick(wx - off.x, wy - off.y, zoom);
    let best = null;
    for (const o of this.renderer.people) {
      if (o.hidden) continue;
      const p = project(o.x, o.y);
      const d = Math.hypot(p.x + off.x - wx, p.y + off.y - wy) * zoom;
      if (d < 14 && (!best || d < best.d)) best = { d, hit: o };
    }
    return best ? { hit: best.hit, count: 1 } : null;
  }
}
