// SceneRenderer: draws one sample scene (buildings, nature, props, citizens,
// caravan) with depth sorting, picking and highlights.
//
// Scene coordinates are local ground-plane metres. `origin` places the scene
// in world metres; because the projection is linear, that is a single screen
// offset on the root container. Positions come only from the presentation
// schedules (a pure function of person and display time).

import { K, project } from '../world/coords.js';
import { ART } from './art/paint/iso.js';
import { ANIMATIONS } from './art/paint/people.js';
import { CIV_COLORS } from './art/registry.js';
import { personFrameKey } from './art/bake.js';
import { depthSort } from './depth.js';
import { sampleSchedule } from '../sim/paths.js';

function translateFp(fp, x, y) {
  return { minX: fp.minX + x, minY: fp.minY + y, maxX: fp.maxX + x, maxY: fp.maxY + y };
}

/** A civilization's colour as a tint. */
const civTint = (civ) => Number.parseInt(CIV_COLORS[civ % CIV_COLORS.length].slice(1), 16);

function fpDepth(fp) {
  return (fp.minX + fp.maxX) / 2 + (fp.minY + fp.maxY) / 2;
}

export class SceneRenderer {
  constructor({ PIXI, atlas, assetInfo, scene, ground = null, origin = { x: 0, y: 0 }, centreOnly = false }) {
    this.PIXI = PIXI;
    this.atlas = atlas;
    this.assetInfo = assetInfo;
    this.scene = scene;
    this.origin = origin;
    this.centreOnly = centreOnly;
    this.root = new PIXI.Container();
    const o = project(origin.x, origin.y);
    this.root.position.set(o.x, o.y);
    this.groundLayer = ground ?? new PIXI.Container();
    this.shadowLayer = new PIXI.Container();
    this.shadowLayer.alpha = 0.42;
    this.ringLayer = new PIXI.Graphics();
    this.objectLayer = new PIXI.Container();
    this.objectLayer.sortableChildren = true;
    this.overlay = new PIXI.Graphics();
    this.root.addChild(this.groundLayer, this.shadowLayer, this.ringLayer, this.objectLayer, this.overlay);
    this.objects = [];
    this.byId = new Map();
    this.people = [];
    this.caravan = null;
    this.flat = false;
    this.occupancy = new Map();
    this.drawOrder = [];
    this.lastCycles = [];
    this.lastClick = null;
    this._build();
  }

  // ------------------------------------------------------------ construction
  _spriteFor(entry) {
    const s = new this.PIXI.Sprite(entry.texture);
    s.anchor.set(entry.anchor.x / entry.w, entry.anchor.y / entry.h);
    s.scale.set(1 / entry.art);
    return s;
  }

  _setRect(o) {
    const e = o.entry;
    const p = o.sprite.position;
    const ax = o.flip ? e.w - e.anchor.x : e.anchor.x;
    const x0 = p.x - ax / e.art;
    const y0 = p.y - e.anchor.y / e.art;
    o.rect = { x0, y0, x1: x0 + e.w / e.art, y1: y0 + e.h / e.art };
  }

  _add(o) {
    o.index = this.objects.length;
    this.objects.push(o);
    this.byId.set(o.id, o);
    this.objectLayer.addChild(o.sprite);
    if (o.shadow) this.shadowLayer.addChild(o.shadow);
    if (o.kind === 'person' && !o.person.envoy) {
      // The civilization's colour on sash, cap and scarf, drawn just above the person.
      o.accent = new this.PIXI.Sprite();
      o.accent.visible = false;
      o.accent.tint = civTint(o.person.caravan ? this.scene.caravan.civ : o.person.civ);
      this.objectLayer.addChild(o.accent);
    }
    return o;
  }

  _build() {
    const { PIXI, atlas, scene } = this;
    for (const s of scene.statics) {
      const entry = atlas.get(s.asset);
      const sprite = this._spriteFor(entry);
      const p = project(s.x, s.y);
      sprite.position.set(p.x, p.y);
      const shadowEntry = atlas.get(`${s.asset}#shadow`);
      let shadow = null;
      if (shadowEntry) {
        shadow = this._spriteFor(shadowEntry);
        shadow.position.set(p.x, p.y);
      }
      const info = this.assetInfo.get(s.asset);
      const fp = translateFp(info.footprint, s.x, s.y);
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
        height: info.height,
      };
      this._setRect(o);
      this._add(o);
    }
    for (const p of scene.people) {
      const sprite = new PIXI.Sprite();
      sprite.scale.set(1 / ART);
      const shadow = this._spriteFor(atlas.get('shadow.person'));
      const o = this._add({
        id: p.id,
        kind: 'person',
        label: p.record.label,
        person: p,
        sprite,
        shadow,
        static: false,
        flip: false,
        entry: null,
        small: true,
      });
      this.people.push(o);
    }
    if (scene.caravan) {
      const c = scene.caravan;
      const mk = (id, kind, label) => {
        const sprite = new PIXI.Sprite();
        sprite.scale.set(1 / ART);
        const shadow = new PIXI.Sprite();
        shadow.scale.set(1 / ART);
        return this._add({ id, kind, label, sprite, shadow, static: false, flip: false, entry: null });
      };
      this.caravan = {
        data: c,
        ox: mk(`${c.id}-ox`, 'ox', 'Ox (caravan)'),
        wagon: mk(`${c.id}-wagon`, 'wagon', 'Caravan wagon'),
        driver: null,
      };
      const dSprite = new PIXI.Sprite();
      dSprite.scale.set(1 / ART);
      this.caravan.driver = this._add({
        id: c.driver.id,
        kind: 'person',
        label: c.driver.record.label,
        person: { ...c.driver, caravan: true },
        sprite: dSprite,
        shadow: this._spriteFor(atlas.get('shadow.person')),
        static: false,
        flip: false,
        entry: null,
        small: true,
      });
    }
    if (scene.pinnedWagon) {
      const w = scene.pinnedWagon;
      const entry = atlas.get('wagon.x.1.0');
      const sprite = this._spriteFor(entry);
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
      this._setRect(o);
      this._add(o);
    }
  }

  // ------------------------------------------------------------ dynamics
  _textureKey(p, s) {
    if (p.envoy)
      return s.moving ? `envoy.${p.civ}.${s.facing}.${Math.floor(s.phase * 8) % 8}` : `envoy.${p.civ}.${s.facing}.idle`;
    const spec = ANIMATIONS[s.anim] ?? ANIMATIONS.idle;
    const facing = spec.facings.includes(s.facing) ? s.facing : spec.facings[0];
    const i = Math.floor(s.phase * spec.frames) % spec.frames;
    return personFrameKey(p.appearance, s.anim, facing, i);
  }

  _place(o, x, y, key, flip, fpHalf) {
    const entry = this.atlas.get(key);
    o.entry = entry;
    o.flip = flip;
    o.x = x;
    o.y = y;
    const p = project(x, y);
    o.sprite.texture = this.flat ? this.atlas.silhouette(entry) : entry.texture;
    o.sprite.anchor.set(entry.anchor.x / entry.w, entry.anchor.y / entry.h);
    o.sprite.scale.set((flip ? -1 : 1) / entry.art, 1 / entry.art);
    o.sprite.position.set(p.x, p.y);
    o.sprite.visible = true;
    if (o.accent) {
      const mask = this.flat ? null : this.atlas.get(`${key}#mask`);
      o.accentOn = !!mask;
      if (mask) {
        o.accent.texture = mask.texture;
        o.accent.anchor.set(mask.anchor.x / mask.w, mask.anchor.y / mask.h);
        o.accent.scale.copyFrom(o.sprite.scale);
        o.accent.position.copyFrom(o.sprite.position);
      }
      o.accent.visible = o.accentOn;
    }
    o.fp = { minX: x - fpHalf[0], minY: y - fpHalf[1], maxX: x + fpHalf[0], maxY: y + fpHalf[1] };
    o.depth = x + y;
    this._setRect(o);
  }

  _hide(o, s) {
    o.sprite.visible = false;
    if (o.accent) o.accent.visible = false;
    if (o.shadow) o.shadow.visible = false;
    o.hidden = true;
    o.state = s;
  }

  _vehicleShadow(o, key, flip) {
    const e = this.atlas.get(key);
    o.shadow.texture = e.texture;
    o.shadow.anchor.set(e.anchor.x / e.w, e.anchor.y / e.h);
    o.shadow.position.copyFrom(o.sprite.position);
    o.shadow.scale.set((flip ? -1 : 1) / e.art, 1 / e.art);
    o.shadow.visible = true;
  }

  /** Place every dynamic object for display time t (local metres). */
  /**
   * Crowd budget: beyond `budget` outdoor citizens, those farthest from
   * `focus` (local metres) are drawn with a still frame. Counts, picking and
   * inspection are unaffected.
   */
  setCrowd(budget, focus) {
    this.crowdBudget = budget;
    this.crowdFocus = focus;
  }

  update(t) {
    // Schedules are a pure function of time, so an update with nothing changed
    // (same time, band and crowd split) is skipped; quality sets how often time
    // advances for drawing.
    const f = this.crowdFocus ?? { x: 0, y: 0 };
    const key = `${t}|${this.flat}|${this.drawActors}|${this.crowdBudget}|${Math.round(f.x / 8)},${Math.round(f.y / 8)}`;
    if (key === this._updateKey) return;
    this._updateKey = key;
    this.recomputes = (this.recomputes ?? 0) + 1;
    this.occupancy.clear();
    const states = this.people.map((o) => sampleSchedule(o.person.schedule, t));
    let simple = null;
    const budget = this.crowdBudget ?? Infinity;
    if (budget < this.people.length) {
      const f = this.crowdFocus ?? { x: 0, y: 0 };
      const outdoor = [];
      states.forEach((s, i) => {
        if (!s.inside) outdoor.push([Math.hypot(s.x - f.x, s.y - f.y), i]);
      });
      if (outdoor.length > budget) {
        outdoor.sort((a, b) => a[0] - b[0]);
        simple = new Set(outdoor.slice(budget).map((e) => e[1]));
      }
    }
    this.simplified = simple ? simple.size : 0;
    for (let i = 0; i < this.people.length; i += 1) {
      const o = this.people[i];
      const s = states[i];
      o.state = s;
      o.simple = !!simple?.has(i);
      if (s.inside) {
        this._hide(o, s);
        if (!this.occupancy.has(s.inside)) this.occupancy.set(s.inside, []);
        this.occupancy.get(s.inside).push(o.id);
        o.x = s.x;
        o.y = s.y;
        continue;
      }
      o.hidden = false;
      if (this.drawActors === false) {
        // Not drawn in this band (people are dots): keep the position, skip the sprite.
        o.x = s.x;
        o.y = s.y;
        o.drawn = false;
        o.sprite.visible = false;
        o.shadow.visible = false;
        if (o.accent) o.accent.visible = false;
        continue;
      }
      o.drawn = true;
      const key = o.simple
        ? this._textureKey(o.person, { ...s, anim: o.person.envoy ? s.anim : 'idle', phase: 0, moving: false })
        : this._textureKey(o.person, s);
      this._place(o, s.x, s.y, key, s.flip, [0.25, 0.25]);
      const p = project(s.x, s.y);
      o.shadow.position.set(p.x, p.y);
      o.shadow.visible = true;
    }
    const c = this.caravan;
    if (!c) return;
    const s = sampleSchedule(c.data.schedule, t);
    if (s.inside) {
      for (const o of [c.ox, c.wagon, c.driver]) this._hide(o, s);
      return;
    }
    const dir = s.flip ? 1 : -1; // along x: flip means moving +x
    if (this.drawActors === false) {
      for (const o of [c.ox, c.wagon]) {
        o.hidden = false;
        o.drawn = false;
        o.sprite.visible = false;
        o.shadow.visible = false;
        o.state = s;
      }
      const d = c.driver;
      d.hidden = false;
      d.drawn = false;
      d.sprite.visible = false;
      d.shadow.visible = false;
      if (d.accent) d.accent.visible = false;
      d.x = s.x - dir * 0.6;
      d.y = s.y - 1.45;
      d.state = { ...s, anim: s.moving ? 'walk' : 'idle' };
      return;
    }
    for (const o of [c.ox, c.wagon, c.driver]) o.drawn = true;
    const step = s.moving ? Math.floor((s.phase * 1.35) / 0.35) : 0;
    this._place(c.ox, s.x, s.y, `ox.${s.facing}.${s.moving ? Math.floor(s.phase * 8) % 8 : 0}`, s.flip, [1.1, 0.45]);
    c.ox.state = s;
    c.ox.hidden = false;
    this._vehicleShadow(c.ox, `ox.${s.facing}#shadow`, s.flip);
    this._place(c.wagon, s.x - dir * 2.75, s.y, `wagon.x.${dir}.${step % 4}`, false, [1.5, 0.75]);
    c.wagon.state = s;
    c.wagon.hidden = false;
    this._vehicleShadow(c.wagon, `wagon.x.${dir}#shadow`, false);
    const d = c.driver;
    const ds = { ...s, anim: s.moving ? 'walk' : 'idle' };
    d.state = { ...ds, activity: s.activity, destination: s.destination };
    d.hidden = false;
    this._place(d, s.x - dir * 0.6, s.y - 1.45, this._textureKey(d.person, ds), s.flip, [0.25, 0.25]);
    const p = project(d.x, d.y);
    d.shadow.position.set(p.x, p.y);
    d.shadow.visible = true;
  }

  /** Pure position of a person at time t, in local metres. */
  positionAt(id, t) {
    const o = this.byId.get(id);
    const sched = o.person.caravan ? this.caravan.data.schedule : o.person.schedule;
    const s = sampleSchedule(sched, t);
    if (o.person.caravan && !s.inside) {
      const dir = s.flip ? 1 : -1;
      return { x: s.x - dir * 0.6, y: s.y - 1.45, inside: null, anim: s.moving ? 'walk' : 'idle', phase: s.phase };
    }
    return { x: s.x, y: s.y, inside: s.inside, anim: s.anim, phase: s.phase };
  }

  /** What a person is doing at time t (presentation schedule). */
  activityAt(id, t) {
    const o = this.byId.get(id);
    const sched = o.person.caravan ? this.caravan.data.schedule : o.person.schedule;
    return sampleSchedule(sched, t).activity ?? null;
  }

  // ------------------------------------------------------------ view
  /** Root offset in world screen pixels (zoom 1). */
  get offset() {
    return this.root.position;
  }

  /** Cull to a local-screen rect, depth sort and apply z order. */
  cullAndSort(view) {
    const visible = [];
    for (const o of this.objects) {
      if (o.hidden || o.drawn === false) continue;
      const r = o.rect;
      const inView = r.x1 > view.x0 && r.x0 < view.x1 && r.y1 > view.y0 && r.y0 < view.y1;
      o.sprite.visible = inView;
      if (o.shadow) o.shadow.visible = inView && !this.flat;
      if (o.accent) o.accent.visible = inView && !this.flat && !!o.accentOn;
      if (inView) visible.push(o);
    }
    const sorted = depthSort(visible, 128, { centreOnly: this.centreOnly });
    this.drawOrder = sorted.order;
    this.lastCycles = sorted.cycles;
    for (let i = 0; i < this.drawOrder.length; i += 1) {
      const o = this.drawOrder[i];
      o.sprite.zIndex = i;
      if (o.accent) o.accent.zIndex = i + 0.5;
    }
    return visible;
  }

  colorOf(o) {
    const v = (o.index + 1) * 4;
    return ((v & 255) << 16) | (((v >> 8) & 255) << 8) | 128;
  }

  /** Tints, selection ring or indoor highlight, and optional footprints. */
  decorate({ selected, zoom, showFootprints, visible }) {
    const pulse = 0.5 + 0.5 * Math.sin(performance.now() / 220);
    for (const o of this.objects) {
      if (this.flat) {
        o.sprite.tint = this.colorOf(o);
        if (o.static) o.sprite.texture = this.atlas.silhouette(o.entry);
      } else {
        o.sprite.tint = 0xffffff;
      }
    }
    const ring = this.ringLayer;
    const overlay = this.overlay;
    ring.clear();
    overlay.clear();
    if (selected && !this.flat) {
      const o = this.byId.get(selected);
      if (o && o.hidden && o.state?.inside && this.byId.has(o.state.inside)) {
        const b = this.byId.get(o.state.inside);
        // Never quite white: at the bottom of the pulse the building stays tinted (it rounded
        // to white for about a tenth of each pulse, and the highlight blinked out).
        const glow = 0.25 + 0.75 * pulse;
        const g = Math.round(255 - glow * 70);
        b.sprite.tint = (255 << 16) | (Math.round(255 - glow * 25) << 8) | g;
        this.drawFootprint(ring, b.fp, 0xffd84a, 3, zoom);
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
        ring
          .ellipse(p.x, p.y, 0.62 * Math.SQRT2 * K, 0.62 * Math.SQRT2 * K * 0.5)
          .stroke({ width: 2.5, color: 0xffd84a });
      } else if (o && o.kind !== 'person') {
        this.drawFootprint(ring, o.fp, 0xffd84a, 3, zoom);
      }
    }
    if (showFootprints) {
      for (const o of visible) {
        this.drawFootprint(
          overlay,
          o.fp,
          o.kind === 'person' ? 0x7fffd4 : o.kind === 'building' ? 0xff7050 : 0xffffff,
          1,
          zoom,
        );
      }
    }
  }

  drawFootprint(g, fp, color, width, zoom) {
    const a = project(fp.minX, fp.minY);
    const b = project(fp.maxX, fp.minY);
    const c = project(fp.maxX, fp.maxY);
    const d = project(fp.minX, fp.maxY);
    g.poly([a.x, a.y, b.x, b.y, c.x, c.y, d.x, d.y]).stroke({ width: width / zoom, color });
  }

  setFlat(on) {
    this.flat = on;
    this._updateKey = null; // sprites must be re-placed with the other textures
    this.groundLayer.visible = !on;
    this.shadowLayer.visible = !on;
    this.overlay.visible = !on;
    this.ringLayer.visible = !on;
    for (const o of this.objects) {
      if (!on && o.static) o.sprite.texture = o.entry.texture;
    }
  }

  // ------------------------------------------------------------ picking
  /** Pixel-accurate hit test at a local-screen point. */
  hitTest(o, wx, wy) {
    if (o.hidden || !o.sprite.visible) return false;
    const r = o.rect;
    if (wx < r.x0 || wx > r.x1 || wy < r.y0 || wy > r.y1) return false;
    const e = o.entry;
    const dx = (wx - o.sprite.position.x) * e.art;
    const lx = (o.flip ? -dx : dx) + e.anchor.x;
    const ly = (wy - o.sprite.position.y) * e.art + e.anchor.y;
    return this.atlas.alphaAt(e, lx, ly) > 48;
  }

  /** Front-to-back pick at a local-screen point; repeated clicks cycle through a crowd. */
  pick(wx, wy, zoom) {
    const hits = [];
    for (let i = this.drawOrder.length - 1; i >= 0; i -= 1) {
      const o = this.drawOrder[i];
      if (
        (o.kind === 'person' || o.kind === 'building' || o.kind === 'wagon' || o.kind === 'ox') &&
        this.hitTest(o, wx, wy)
      )
        hits.push(o);
    }
    // Generous radius for people in crowds: nearest feet within 10 px.
    for (const o of this.people) {
      if (o.hidden || hits.includes(o)) continue;
      const p = project(o.x, o.y, 0.9);
      if (Math.hypot(p.x - wx, p.y - wy) < 10 / Math.max(zoom, 0.5)) hits.push(o);
    }
    if (!hits.length) return null;
    // Citizens come before buildings, so a person partly hidden behind a
    // building is still selectable; clicking again cycles to the building.
    hits.sort((a, b) => (a.kind === 'person' ? 0 : 1) - (b.kind === 'person' ? 0 : 1));
    const last = this.lastClick;
    const sameSpot = last && Math.hypot(last.wx - wx, last.wy - wy) < 4 / zoom;
    const index = sameSpot ? (last.index + 1) % hits.length : 0;
    this.lastClick = { wx, wy, index };
    return { hit: hits[index], count: hits.length };
  }

  centreOf(id) {
    const o = this.byId.get(id);
    if (o.kind === 'person') return { x: o.x, y: o.y };
    return { x: (o.fp.minX + o.fp.maxX) / 2, y: (o.fp.minY + o.fp.maxY) / 2 };
  }

  /** Camera target (local screen, zoom 1) for following a selection. */
  followTarget(id) {
    const o = this.byId.get(id);
    if (o && o.hidden && o.state?.inside && this.byId.has(o.state.inside)) {
      const b = this.byId.get(o.state.inside);
      return project((b.fp.minX + b.fp.maxX) / 2, (b.fp.minY + b.fp.maxY) / 2, 2);
    }
    if (o && !o.hidden) return project(o.x, o.y, 1);
    return null;
  }

  /**
   * Citizen counts, defined separately (R9):
   *  worldPopulation  every person in the scene;
   *  resident         people whose home is this settlement (not visitors);
   *  indoor           inside a building (drawn only as building occupancy);
   *  away             travelling beyond the sample area (not drawn);
   *  outdoor          outside buildings in the loaded area;
   *  visible          outdoor people whose sprite is in the viewport after culling.
   */
  counts() {
    const all = this.caravan ? [...this.people, this.caravan.driver] : this.people;
    const away = all.filter((o) => o.hidden && o.state?.inside === 'away').length;
    const indoor = all.filter((o) => o.hidden && o.state?.inside !== 'away').length;
    return {
      worldPopulation: all.length,
      resident: all.filter((o) => !o.person.envoy && !o.person.caravan).length,
      indoor,
      away,
      outdoor: all.length - indoor - away,
      visible: this.drawOrder.filter((o) => o.kind === 'person').length,
      drawnSprites: this.drawOrder.length,
      depthCycles: this.lastCycles.length,
    };
  }
}
