// The crowd: up to 200,000 people from a PeopleFrame, drawn by zoom band
// (Phase 5 S7). PRESENTATION ONLY: positions come from the settlement plans
// and the display clock; who the people are comes from the frame.
//
//  settlement (zoom >= 0.5): the `crowdBudget` people nearest the view's
//      centre as full animated sprites with a civilization accent; the rest
//      in view as still particles; ward houses in view as sprites.
//  local (0.02 - 0.5): ward houses as particles and people as dots, one per
//      8, 16 or 32 m cell; a click lists the people in the cell.
//  regional and atlas (< 0.02): a population badge per settlement.
//
// Nothing is kept per person beyond the frame's columns and the layout's work
// points: sprites and particles are pooled and reused every frame. The
// selected person is always drawn in full (or pinned below the settlement
// band), so anyone can be followed.

import { project, K } from '../world/coords.js';
import { BANDS } from './village-layer.js';
import { CIV_COLORS } from './art/registry.js';
import { personFrameKey } from './art/bake.js';
import { ANIMATIONS } from './art/paint/people.js';
import { BLOCK_M, HOUSES_PER_BLOCK } from '../world/settlement-plan.js';
import { personLabel } from '../data/naming.js';
import { hash2 } from '../sim/rng.js';

const DAY_S = 86400;
const START_S = 8 * 3600; // display time 0 is 08:00 on engine day 0
const HOUSE_ASSETS = ['building.house.log', 'building.house.timber_b'];
const SHEET = 512;
const PAD = 2;
const DOT_PX = 16;
const ICON_ART = 0.25; // ward house particles: an eighth of the 2x painting

function smooth(a, b, x) {
  const t = Math.max(0, Math.min(1, (x - a) / (b - a)));
  return t * t * (3 - 2 * t);
}

function hexNum(css) {
  return parseInt(css.replace('#', ''), 16);
}

/** Display seconds to a time of day (seconds after midnight). */
export function timeOfDay(t) {
  return (((START_S + t) % DAY_S) + DAY_S) % DAY_S;
}

/**
 * One small texture sheet for every particle: still people per civilization,
 * design and facing (accent already tinted), a dot, and ward house icons.
 * Particle containers need a single texture source.
 */
function bakeSheet(PIXI, atlas, designs) {
  const canvas = document.createElement('canvas');
  canvas.width = SHEET;
  canvas.height = SHEET;
  const ctx = canvas.getContext('2d');
  const entries = new Map();
  let x = PAD;
  let y = PAD;
  let rowH = 0;
  const place = (key, w, h, draw, anchor, art) => {
    if (x + w + PAD > SHEET) {
      x = PAD;
      y += rowH + PAD;
      rowH = 0;
    }
    if (y + h + PAD > SHEET) throw new Error('crowd sheet full');
    draw(ctx, x, y);
    entries.set(key, { key, x, y, w, h, anchor, art });
    x += w + PAD;
    rowH = Math.max(rowH, h);
  };
  const pageOf = (e) => atlas.pages[e.pageIndex].canvas;
  const tmp = document.createElement('canvas');
  const tctx = tmp.getContext('2d');
  for (let civ = 0; civ < CIV_COLORS.length; civ += 1) {
    for (const a of designs) {
      for (const facing of ['front', 'back']) {
        const key = personFrameKey(a, 'idle', facing, 0);
        const e = atlas.get(key);
        const m = atlas.get(`${key}#mask`);
        place(
          `still.${civ}.${a}.${facing}`,
          e.w,
          e.h,
          (c, px, py) => {
            c.drawImage(pageOf(e), e.x, e.y, e.w, e.h, px, py, e.w, e.h);
            if (!m) return;
            // The accent mask multiplied by the civilization colour, over the frame.
            tmp.width = m.w;
            tmp.height = m.h;
            tctx.globalCompositeOperation = 'source-over';
            tctx.drawImage(pageOf(m), m.x, m.y, m.w, m.h, 0, 0, m.w, m.h);
            tctx.globalCompositeOperation = 'multiply';
            tctx.fillStyle = CIV_COLORS[civ];
            tctx.fillRect(0, 0, m.w, m.h);
            tctx.globalCompositeOperation = 'destination-in';
            tctx.drawImage(pageOf(m), m.x, m.y, m.w, m.h, 0, 0, m.w, m.h);
            c.drawImage(tmp, px + e.anchor.x - m.anchor.x, py + e.anchor.y - m.anchor.y);
          },
          e.anchor,
          e.art,
        );
      }
    }
  }
  place(
    'dot',
    DOT_PX,
    DOT_PX,
    (c, px, py) => {
      c.fillStyle = '#ffffff';
      c.beginPath();
      c.arc(px + DOT_PX / 2, py + DOT_PX / 2, DOT_PX / 2 - 1, 0, Math.PI * 2);
      c.fill();
    },
    { x: DOT_PX / 2, y: DOT_PX / 2 },
    1,
  );
  for (const asset of HOUSE_ASSETS) {
    const e = atlas.get(asset);
    const k = ICON_ART / e.art;
    const w = Math.max(2, Math.ceil(e.w * k));
    const h = Math.max(2, Math.ceil(e.h * k));
    place(
      `icon.${asset}`,
      w,
      h,
      (c, px, py) => {
        c.imageSmoothingQuality = 'high';
        c.drawImage(pageOf(e), e.x, e.y, e.w, e.h, px, py, e.w * k, e.h * k);
      },
      { x: e.anchor.x * k, y: e.anchor.y * k },
      ICON_ART,
    );
  }
  const source = new PIXI.CanvasSource({ resource: canvas, autoGenerateMipmaps: false, scaleMode: 'linear' });
  for (const e of entries.values()) {
    e.texture = new PIXI.Texture({ source, frame: new PIXI.Rectangle(e.x, e.y, e.w, e.h) });
  }
  return { canvas, source, entries, pixels: ctx.getImageData(0, 0, SHEET, SHEET).data };
}

export class CrowdLayer {
  /**
   * @param {{ PIXI: object, atlas: object, population: { frame, plans, layout, origins } }} options
   */
  constructor({ PIXI, atlas, population }) {
    this.PIXI = PIXI;
    this.atlas = atlas;
    const { frame, plans, layout, origins } = population;
    this.frame = frame;
    this.plans = plans;
    this.layout = layout;
    const designs = [...new Set(frame.appearance)].sort((a, b) => a - b);
    this.sheet = bakeSheet(PIXI, atlas, designs);
    this.container = new PIXI.Container();
    this.sites = plans.map((plan, k) => this._site(k, plan, origins[k]));
    this.pin = new PIXI.Graphics();
    this.container.addChild(this.pin);
    this.out = {};
    this.selected = null;
    this.visibleRows = new Int32Array(0);
    this.stat = { visible: 0, visibleFull: 0, simplified: 0, actors: 0, particles: 0, dots: 0 };
    this.lastClick = null;
  }

  _site(k, plan, origin) {
    const { PIXI } = this;
    const offset = project(origin.x, origin.y);
    const root = new PIXI.Container();
    root.position.set(offset.x, offset.y);
    // Ward houses far away: one static particle each, in depth order.
    const far = new PIXI.ParticleContainer({
      texture: this.sheet.entries.get('dot').texture,
      dynamicProperties: { position: false },
    });
    const order = Array.from({ length: plan.houseCount }, (_, h) => h).sort(
      (a, b) => plan.houseX[a] + plan.houseY[a] - (plan.houseX[b] + plan.houseY[b]) || a - b,
    );
    for (const h of order) {
      const icon = this.sheet.entries.get(`icon.${this._houseAsset(k, h)}`);
      const p = project(plan.houseX[h], plan.houseY[h]);
      far.addParticle(
        new PIXI.Particle({
          texture: icon.texture,
          x: p.x,
          y: p.y,
          anchorX: icon.anchor.x / icon.w,
          anchorY: icon.anchor.y / icon.h,
          scaleX: 1 / icon.art,
          scaleY: 1 / icon.art,
        }),
      );
    }
    const dots = new PIXI.ParticleContainer({
      texture: this.sheet.entries.get('dot').texture,
      dynamicProperties: { position: true, vertex: true, color: true },
    });
    const near = new PIXI.Container();
    near.sortableChildren = true;
    const shadows = new PIXI.Container();
    shadows.alpha = 0.42;
    const crowd = new PIXI.ParticleContainer({
      texture: this.sheet.entries.get('dot').texture,
      dynamicProperties: { position: true, vertex: true, uvs: true, color: true },
    });
    const badge = new PIXI.Container();
    const label = new PIXI.Text({
      text: `${this.frame.settlements[k].label} · ${this.frame.residents(k).toLocaleString('en')} people · synthetic`,
      style: { fontFamily: 'system-ui, sans-serif', fontSize: 12, fontWeight: '700', fill: 0xffffff },
    });
    label.position.set(2, -33);
    const g = new PIXI.Graphics();
    g.roundRect(-6, -40, label.width + 16, 34, 6)
      .fill({ color: 0x10151a, alpha: 0.85 })
      .stroke({ width: 1.5, color: hexNum(CIV_COLORS[this.frame.settlements[k].civ]) });
    badge.addChild(g, label);
    root.addChild(far, dots, shadows, near, crowd);
    this.container.addChild(root, badge);
    const reach = plan.fieldRing.r1 + 150;
    return {
      k,
      plan,
      origin,
      offset,
      root,
      far,
      dots,
      near,
      shadows,
      crowd,
      badge,
      reach,
      houseSprites: [],
      actors: [],
      particles: [],
      dotParticles: [],
    };
  }

  _houseAsset(k, h) {
    return HOUSE_ASSETS[Math.floor(hash2(h, k, 7) * HOUSE_ASSETS.length)];
  }

  /** Is any of a site's extent inside a world-screen rect? */
  _siteInView(site, view) {
    const r = site.reach * K * 1.42;
    const o = site.offset;
    return o.x + r > view.x0 && o.x - r < view.x1 && o.y + r / 2 > view.y0 && o.y - r / 2 < view.y1;
  }

  /**
   * @param {number} t display seconds
   * @param {{x0,y0,x1,y1}} view world-screen rect at zoom 1
   * @param {number} zoom
   * @param {{ selected?: string|null, crowdBudget?: number }} options
   */
  update(t, view, zoom, { selected = null, crowdBudget = Infinity } = {}) {
    const tod = timeOfDay(t);
    this.tod = tod;
    this.selectedRow = selected ? this.frame.indexOf(selected) : -1;
    const actors = smooth(0.4, 0.6, zoom);
    const local = 1 - actors;
    const st = { visible: 0, visibleFull: 0, simplified: 0, actors: 0, particles: 0, dots: 0 };
    this.band = zoom >= BANDS.settlementMin ? 'settlement' : zoom >= BANDS.localMin ? 'local' : 'regional';
    const cx = (view.x0 + view.x1) / 2;
    const cy = (view.y0 + view.y1) / 2;
    const visible = [];
    for (const site of this.sites) {
      const show = zoom >= BANDS.localMin * 0.8 && this._siteInView(site, view);
      site.root.visible = show;
      site.badge.visible = false;
      if (!show) {
        this._hideSite(site);
        continue;
      }
      const lv = {
        x0: view.x0 - site.offset.x,
        y0: view.y0 - site.offset.y,
        x1: view.x1 - site.offset.x,
        y1: view.y1 - site.offset.y,
      };
      site.far.visible = local > 0.01;
      site.far.alpha = local * smooth(BANDS.localMin * 0.8, BANDS.localMin * 1.5, zoom);
      if (actors > 0.01) {
        this._sampleView(site, lv, visible, st);
      } else {
        this._hideActors(site);
      }
      if (local > 0.01) this._dots(site, lv, zoom, local * site.far.alpha, t, st);
      else site.dots.visible = false;
    }
    // Full sprites for the nearest `crowdBudget`, still particles for the rest.
    if (visible.length) {
      const fx = cx;
      const fy = cy;
      for (const v of visible) v.d = Math.hypot(v.sx - fx, v.sy - fy);
      visible.sort(
        (a, b) => (a.row === this.selectedRow ? -1 : 0) - (b.row === this.selectedRow ? -1 : 0) || a.d - b.d,
      );
    }
    const perSite = new Map(this.sites.map((s) => [s, { actors: 0, particles: 0 }]));
    for (let n = 0; n < visible.length; n += 1) {
      const v = visible[n];
      const used = perSite.get(v.site);
      if (n < crowdBudget || v.row === this.selectedRow) {
        this._actor(v.site, used.actors, v, actors);
        v.kind = 'actor';
        used.actors += 1;
        st.visibleFull += 1;
      } else {
        this._particle(v.site, used.particles, v, actors);
        v.kind = 'particle';
        used.particles += 1;
        st.simplified += 1;
      }
    }
    for (const site of this.sites) {
      const used = perSite.get(site);
      for (let i = used.actors; i < site.actors.length; i += 1) this._hideActor(site.actors[i]);
      site.crowd.particleChildren.length = Math.min(site.crowd.particleChildren.length, used.particles);
      if (used.particles) site.crowd.update();
      st.actors += used.actors;
      st.particles += used.particles;
      // Badges from the regional band out.
      const o = site.offset;
      site.badge.visible = zoom < 0.3;
      site.badge.scale.set(1 / zoom);
      site.badge.position.set(o.x - 120 / zoom, o.y - 20 / zoom);
      site.badge.alpha = 1 - smooth(0.15, 0.3, zoom);
    }
    st.visible = visible.length;
    this.visible = visible;
    this.stat = st;
    this._pin(zoom, actors);
  }

  _hideSite(site) {
    this._hideActors(site);
    site.dots.visible = false;
  }

  _hideActors(site) {
    for (const a of site.actors) this._hideActor(a);
    for (const s of site.houseSprites) s.visible = false;
    site.crowd.particleChildren.length = 0;
  }

  /** People and ward houses inside a local-screen rect, for the settlement band. */
  _sampleView(site, lv, visible, st) {
    const { frame, layout, out } = this;
    const plan = site.plan;
    const rows = frame.rowsOf(site.k);
    // Ground-plane bounds of the view (the projection is a 45-degree turn).
    const a = { x0: lv.x0 / K, x1: lv.x1 / K, y0: (2 * lv.y0) / K, y1: (2 * lv.y1) / K };
    const minS = a.y0 - 4; // x + y
    const maxS = a.y1 + 12;
    const minD = a.x0 - 4; // x - y
    const maxD = a.x1 + 4;
    for (let n = 0; n < rows.length; n += 1) {
      const i = rows[n];
      const h = frame.house[i];
      const hx = plan.houseX[h];
      const hy = plan.houseY[h];
      const wx = layout.workX[i];
      const wy = layout.workY[i];
      // Everyone stays within the box of their home and work, widened by a block.
      const bx0 = Math.min(hx, wx) - BLOCK_M;
      const bx1 = Math.max(hx, wx) + BLOCK_M;
      const by0 = Math.min(hy, wy) - BLOCK_M;
      const by1 = Math.max(hy, wy) + BLOCK_M;
      if (bx1 + by1 < minS || bx0 + by0 > maxS || bx1 - by0 < minD || bx0 - by1 > maxD) {
        if (i !== this.selectedRow) continue;
      }
      layout.sample(i, this.tod, out);
      if (out.inside) continue;
      const s = out.x + out.y;
      const d = out.x - out.y;
      if (s < minS || s > maxS || d < minD || d > maxD) continue;
      visible.push({
        site,
        row: i,
        x: out.x,
        y: out.y,
        sx: site.offset.x + d * K,
        sy: site.offset.y + (s * K) / 2,
        anim: out.anim,
        phase: out.phase,
        facing: out.facing,
        flip: out.flip,
      });
    }
    // Ward houses in view: sprites from the atlas, sorted with the people.
    let used = 0;
    for (let b = 0; b < plan.blocks.length; b += 1) {
      const [bi, bj] = plan.blocks[b];
      const cs = (bi + bj) * BLOCK_M;
      const cd = (bi - bj) * BLOCK_M;
      if (cs + BLOCK_M < minS - 8 || cs - BLOCK_M > maxS + 8 || cd + BLOCK_M < minD - 8 || cd - BLOCK_M > maxD + 8)
        continue;
      for (let h = b * HOUSES_PER_BLOCK; h < Math.min(plan.houseCount, (b + 1) * HOUSES_PER_BLOCK); h += 1) {
        const sprite = this._houseSprite(site, used);
        used += 1;
        const e = this.atlas.get(this._houseAsset(site.k, h));
        const p = project(plan.houseX[h], plan.houseY[h]);
        sprite.texture = e.texture;
        sprite.anchor.set(e.anchor.x / e.w, e.anchor.y / e.h);
        sprite.scale.set(1 / e.art);
        sprite.position.set(p.x, p.y);
        sprite.zIndex = plan.houseX[h] + plan.houseY[h];
        sprite.visible = true;
      }
    }
    for (let n = used; n < site.houseSprites.length; n += 1) site.houseSprites[n].visible = false;
    st.houses = (st.houses ?? 0) + used;
  }

  _houseSprite(site, n) {
    if (!site.houseSprites[n]) {
      const s = new this.PIXI.Sprite();
      site.houseSprites[n] = s;
      site.near.addChild(s);
    }
    return site.houseSprites[n];
  }

  _actor(site, n, v, alpha) {
    const { PIXI } = this;
    if (!site.actors[n]) {
      const a = { sprite: new PIXI.Sprite(), accent: new PIXI.Sprite(), shadow: new PIXI.Sprite(), row: -1 };
      site.near.addChild(a.sprite, a.accent);
      site.shadows.addChild(a.shadow);
      site.actors[n] = a;
    }
    const a = site.actors[n];
    const f = this.frame;
    const spec = ANIMATIONS[v.anim] ?? ANIMATIONS.idle;
    const facing = spec.facings.includes(v.facing) ? v.facing : spec.facings[0];
    const key = personFrameKey(f.appearance[v.row], v.anim, facing, Math.floor(v.phase * spec.frames) % spec.frames);
    const e = this.atlas.get(key);
    const p = project(v.x, v.y);
    const z = v.x + v.y + 0.01;
    a.row = v.row;
    a.entry = e;
    a.flip = v.flip;
    a.sprite.texture = e.texture;
    a.sprite.anchor.set(e.anchor.x / e.w, e.anchor.y / e.h);
    a.sprite.scale.set((v.flip ? -1 : 1) / e.art, 1 / e.art);
    a.sprite.position.set(p.x, p.y);
    a.sprite.zIndex = z;
    a.sprite.alpha = alpha;
    a.sprite.visible = true;
    const m = this.atlas.get(`${key}#mask`);
    a.accent.visible = !!m;
    if (m) {
      a.accent.texture = m.texture;
      a.accent.anchor.set(m.anchor.x / m.w, m.anchor.y / m.h);
      a.accent.scale.copyFrom(a.sprite.scale);
      a.accent.position.copyFrom(a.sprite.position);
      a.accent.tint = hexNum(CIV_COLORS[f.civ[v.row]]);
      a.accent.zIndex = z + 0.001;
      a.accent.alpha = alpha;
    }
    const s = this.atlas.get('shadow.person');
    a.shadow.texture = s.texture;
    a.shadow.anchor.set(s.anchor.x / s.w, s.anchor.y / s.h);
    a.shadow.scale.set(1 / s.art);
    a.shadow.position.set(p.x, p.y);
    a.shadow.alpha = alpha;
    a.shadow.visible = true;
  }

  _hideActor(a) {
    a.sprite.visible = false;
    a.accent.visible = false;
    a.shadow.visible = false;
    a.row = -1;
  }

  _particle(site, n, v, alpha) {
    if (!site.particles[n])
      site.particles[n] = new this.PIXI.Particle({ texture: this.sheet.entries.get('dot').texture });
    const q = site.particles[n];
    const f = this.frame;
    const e = this.sheet.entries.get(`still.${f.civ[v.row]}.${f.appearance[v.row]}.${v.facing}`);
    const p = project(v.x, v.y);
    q.texture = e.texture;
    q.x = p.x;
    q.y = p.y;
    q.anchorX = e.anchor.x / e.w;
    q.anchorY = e.anchor.y / e.h;
    q.scaleX = (v.flip ? -1 : 1) / e.art;
    q.scaleY = 1 / e.art;
    q.alpha = alpha;
    q.row = v.row;
    q.entry = e;
    q.flip = v.flip;
    site.crowd.particleChildren[n] = q;
  }

  /** Outdoor people as dots per cell (8, 16 or 32 m by zoom), refreshed each display second. */
  _dots(site, lv, zoom, alpha, t, st) {
    const cell = zoom >= 0.2 ? 8 : zoom >= 0.08 ? 16 : 32;
    const key = `${Math.floor(t)}|${cell}`;
    if (site.dotKey !== key) {
      site.dotKey = key;
      const { frame, layout, out } = this;
      const rows = frame.rowsOf(site.k);
      const cells = new Map();
      let outdoor = 0;
      for (let n = 0; n < rows.length; n += 1) {
        const i = rows[n];
        layout.sample(i, this.tod, out);
        if (out.inside) continue;
        outdoor += 1;
        const ck = Math.floor(out.x / cell) * 65536 + Math.floor(out.y / cell);
        let c = cells.get(ck);
        if (!c) {
          c = { x: 0, y: 0, n: 0 };
          cells.set(ck, c);
        }
        c.x += out.x;
        c.y += out.y;
        c.n += 1;
      }
      site.cells = cells;
      site.cellSize = cell;
      site.outdoor = outdoor;
      const dot = this.sheet.entries.get('dot');
      const tint = hexNum(CIV_COLORS[frame.settlements[site.k].civ]);
      let n = 0;
      for (const c of cells.values()) {
        if (!site.dotParticles[n]) site.dotParticles[n] = new this.PIXI.Particle({ texture: dot.texture });
        const q = site.dotParticles[n];
        const p = project(c.x / c.n, c.y / c.n);
        q.x = p.x;
        q.y = p.y;
        q.anchorX = 0.5;
        q.anchorY = 0.5;
        q.tint = tint;
        q.n = c.n;
        site.dots.particleChildren[n] = q;
        n += 1;
      }
      site.dots.particleChildren.length = n;
    }
    // Dot size is in screen pixels, so it follows the zoom every frame.
    for (const q of site.dots.particleChildren) {
      const r = (2.5 + Math.sqrt(q.n) * 1.6) / zoom;
      q.scaleX = (2 * r) / DOT_PX;
      q.scaleY = q.scaleX;
      q.alpha = alpha;
    }
    site.dots.update();
    site.dots.visible = alpha > 0.02;
    st.dots += site.dots.particleChildren.length;
  }

  /** A pin above the selected person whenever the full sprite is not shown. */
  _pin(zoom, actors) {
    const g = this.pin;
    g.clear();
    const i = this.selectedRow;
    if (i < 0 || actors > 0.5) return;
    const pos = this.worldPosition(this.frame.idOf(i));
    const s = 1 / zoom;
    // Drawn about the person: Graphics are tessellated in float32, far from the world origin.
    g.position.set(pos.x, pos.y);
    const x = 0;
    const y = 0;
    g.poly([x, y, x - 7 * s, y - 14 * s, x + 7 * s, y - 14 * s]).fill(0xffd84a);
    g.circle(x, y - 18 * s, 9 * s)
      .fill(0xffd84a)
      .stroke({ width: 2 * s, color: 0x2a2010 });
    g.circle(x, y - 18 * s, 3.5 * s).fill(0x2a2010);
  }

  // ------------------------------------------------------------ people
  /** 'actor', 'particle' or null: how a person was drawn in the last update. */
  drawnAs(id) {
    const i = this.frame.indexOf(id);
    return this.visible?.find((v) => v.row === i)?.kind ?? null;
  }

  /** Canvas point over a drawn person's body, through `toCanvas` (world-screen to canvas). */
  canvasPoint(id, toCanvas) {
    const i = this.frame.indexOf(id);
    const v = this.visible?.find((w) => w.row === i);
    if (!v) return null;
    const p = project(v.x, v.y, 0.9);
    return toCanvas(p.x + v.site.offset.x, p.y + v.site.offset.y);
  }

  has(id) {
    return this.frame.indexOf(id) >= 0;
  }

  /** Where a person is now: settlement-local metres and state. */
  stateOf(i) {
    return this.layout.sample(i, this.tod ?? timeOfDay(0), {});
  }

  /** World-screen position (zoom 1) of a person, at home or outdoors. */
  worldPosition(id) {
    const i = this.frame.indexOf(id);
    if (i < 0) return null;
    const s = this.stateOf(i);
    const site = this.sites[this.frame.settlement[i]];
    const p = project(s.x, s.y, s.inside ? 2 : 1);
    return { x: p.x + site.offset.x, y: p.y + site.offset.y };
  }

  /** The person for the inspector, made on demand. */
  lookup(id) {
    const i = this.frame.indexOf(id);
    if (i < 0) return null;
    const s = this.stateOf(i);
    const record = this.frame.record(i);
    return {
      id,
      kind: 'person',
      label: record.label,
      person: { civ: this.frame.civ[i], record },
      state: { ...s, inside: s.inside ? (s.activity === 'At home' ? record.household : 'workplace') : null },
    };
  }

  /** Canvas-free hit test of a particle or actor at a local-screen point. */
  _hit(entry, pixels, width, px, py, flip, art, lx, ly) {
    const dx = (lx - px) * art;
    const ex = (flip ? -dx : dx) + entry.anchor.x;
    const ey = (ly - py) * art + entry.anchor.y;
    if (ex < 0 || ey < 0 || ex >= entry.w || ey >= entry.h) return false;
    return pixels[((entry.y + Math.floor(ey)) * width + entry.x + Math.floor(ex)) * 4 + 3] > 48;
  }

  /**
   * Pick at a world-screen point. Settlement band: people under the point,
   * pixel-accurate, front first; repeated clicks cycle. Local band: everyone
   * in the dot's cell, as a list.
   */
  pick(wx, wy, zoom) {
    if (zoom >= 0.4) {
      const hits = [];
      for (const site of this.sites) {
        if (!site.root.visible) continue;
        const lx = wx - site.offset.x;
        const ly = wy - site.offset.y;
        for (const a of site.actors) {
          if (a.row < 0 || !a.sprite.visible) continue;
          const e = a.entry;
          const page = this.atlas.pages[e.pageIndex];
          if (!page.pixels) this.atlas.alphaAt(e, 0, 0);
          if (this._hit(e, page.pixels, page.canvas.width, a.sprite.x, a.sprite.y, a.flip, e.art, lx, ly))
            hits.push({ row: a.row, z: a.sprite.zIndex });
        }
        for (const q of site.crowd.particleChildren) {
          if (this._hit(q.entry, this.sheet.pixels, SHEET, q.x, q.y, q.flip, q.entry.art, lx, ly))
            hits.push({ row: q.row, z: -1 });
        }
      }
      if (!hits.length) return null;
      hits.sort((a, b) => b.z - a.z);
      const last = this.lastClick;
      const same = last && Math.hypot(last.wx - wx, last.wy - wy) < 4 / zoom;
      const index = same ? (last.index + 1) % hits.length : 0;
      this.lastClick = { wx, wy, index };
      const id = this.frame.idOf(hits[index].row);
      return { hit: this.lookup(id), count: hits.length };
    }
    if (zoom < BANDS.localMin) return null;
    // The nearest dot within 14 px; its cell's people, in id order.
    let best = null;
    for (const site of this.sites) {
      if (!site.root.visible || !site.cells) continue;
      for (const [ck, c] of site.cells) {
        const p = project(c.x / c.n, c.y / c.n);
        const d = Math.hypot(p.x + site.offset.x - wx, p.y + site.offset.y - wy) * zoom;
        if (d < 14 && (!best || d < best.d)) best = { d, site, ck };
      }
    }
    if (!best) return null;
    const { site, ck } = best;
    const cell = site.cellSize;
    const rows = this.frame.rowsOf(site.k);
    const ids = [];
    for (let n = 0; n < rows.length; n += 1) {
      const s = this.layout.sample(rows[n], this.tod, this.out);
      if (s.inside) continue;
      if (Math.floor(s.x / cell) * 65536 + Math.floor(s.y / cell) === ck) ids.push(this.frame.idOf(rows[n]));
    }
    return ids.length ? { list: ids, count: ids.length, cell } : null;
  }

  /** R9 counts for the crowd (people at home or at indoor work are indoor). */
  counts() {
    const tod = this.tod ?? timeOfDay(0);
    if (this._indoorAt !== tod) {
      let indoor = 0;
      const out = {};
      for (let i = 0; i < this.frame.length; i += 1) if (this.layout.sample(i, tod, out).inside) indoor += 1;
      this._indoorAt = tod;
      this._indoor = indoor;
    }
    const indoor = this._indoor;
    return {
      worldPopulation: this.frame.length,
      indoor,
      outdoor: this.frame.length - indoor,
      visible: this.stat.visible,
      visibleFull: this.stat.visibleFull,
      visibleSimplified: this.stat.simplified,
      dots: this.stat.dots,
    };
  }

  labelOf(id) {
    return personLabel(id);
  }
}
