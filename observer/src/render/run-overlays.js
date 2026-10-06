// What a recorded run shows above the terrain at map zooms (O2): a marker for every
// settlement, each civilization's border, and travellers counted at their tiles. All of it
// is recorded engine data for the day shown; sizes are kept constant on screen.
//
// Graphics are tessellated in float32, so each one is drawn relative to an anchor near its
// shapes, never in raw world coordinates.

import { project } from '../world/coords.js';
import { hexCentre, sharedCorners } from '../world/hex.js';
import { CIV_COLORS } from './art/registry.js';

const AXIAL = [
  [1, 0],
  [1, -1],
  [0, -1],
  [-1, 0],
  [-1, 1],
  [0, 1],
];
const RANK_LABELS = {
  village: 'village',
  small_town: 'small town',
  town: 'town',
  big_town: 'big town',
  city: 'city',
};

function hexNum(css) {
  return parseInt(css.replace('#', ''), 16);
}

function civColor(civ) {
  return hexNum(CIV_COLORS[civ % CIV_COLORS.length]);
}

export class RunOverlays {
  constructor(PIXI, R) {
    this.PIXI = PIXI;
    this.R = R;
    this.container = new PIXI.Container();
    this.borders = new PIXI.Container();
    this.travellers = new PIXI.Container();
    this.routes = new PIXI.Container();
    this.markers = new PIXI.Container();
    this.container.addChild(this.borders, this.routes, this.travellers, this.markers);
    this.items = [];
    this.drawnZoom = null;
    this.parties = [];
    this.shownRoutes = [];
  }

  /** The day's parties on the road (O3 server: /routes), for picking and route lines. */
  setRoutes(record) {
    this.parties = record && record.day === this.record?.day ? record.parties : [];
    this.showRoutes([]);
  }

  /** Draw these parties' routes, in their civilization's colour, with where they stand. */
  showRoutes(parties) {
    const { PIXI, R } = this;
    for (const child of this.routes.removeChildren()) child.destroy();
    this.shownRoutes = parties;
    for (const party of parties) {
      if (!party.route.length) continue;
      const g = new PIXI.Graphics();
      const points = party.route.map(([q, r]) => {
        const c = hexCentre(q, r, R);
        return project(c.x, c.y);
      });
      const anchor = points[0];
      g.position.set(anchor.x, anchor.y);
      g.moveTo(0, 0);
      for (const p of points.slice(1)) g.lineTo(p.x - anchor.x, p.y - anchor.y);
      g.stroke({ width: 3 / (this.drawnZoom ?? 0.01), color: civColor(party.civilization ?? 0), alpha: 0.9 });
      for (const p of [points[0], points[points.length - 1]]) {
        g.circle(p.x - anchor.x, p.y - anchor.y, 4 / (this.drawnZoom ?? 0.01)).fill({ color: 0xffffff, alpha: 0.9 });
      }
      this.routes.addChild(g);
    }
  }

  /** The day's party with this id, or null (it is not on the road this day). */
  partyById(id) {
    return this.parties.find((party) => party.id === id) ?? null;
  }

  /** Where a party stands: its first traveller's tile, else how far its route had come. */
  static partyTile(party) {
    if (party.tile) return party.tile;
    if (!party.route.length) return null;
    return party.route[Math.min(party.at, party.route.length - 1)];
  }

  /** The traveller dot under a world point, at this zoom: its tile, count and parties. */
  pickTraveller(wx, wy, zoom) {
    if (!this.record || !this.travellers.visible) return null;
    let best = null;
    for (const [q, r, civ, count] of this.record.travellers ?? []) {
      const c = hexCentre(q, r, this.R);
      const p = project(c.x, c.y);
      const reach = (3 + Math.sqrt(count) * 1.5 + 3) / zoom;
      const d = Math.hypot(wx - p.x, wy - p.y);
      if (d <= reach && (!best || d < best.d)) best = { d, q, r, civ, count };
    }
    if (!best) return null;
    const parties = this.parties.filter(
      (party) => party.tile && party.tile[0] === best.q && party.tile[1] === best.r && party.civilization === best.civ,
    );
    return { q: best.q, r: best.r, civ: best.civ, count: best.count, parties };
  }

  /** Show one day's record (from the run export). */
  setDay(record) {
    this.record = record;
    this.drawnZoom = null;
    for (const c of [this.borders, this.travellers, this.markers]) {
      for (const child of c.removeChildren()) child.destroy({ children: true });
    }
    this.items = [];
    this.parties = [];
    for (const child of this.routes.removeChildren()) child.destroy();
    this.shownRoutes = [];
    const { PIXI, R } = this;
    for (const s of record.settlements) {
      const c = project(hexCentre(s.q, s.r, R).x, hexCentre(s.q, s.r, R).y);
      const g = new PIXI.Container();
      g.position.set(c.x, c.y);
      const flag = new PIXI.Graphics();
      const h = s.capital ? 34 : 24;
      flag.moveTo(0, 0).lineTo(0, -h).stroke({ width: 2.5, color: 0x2a2014 });
      flag
        .poly([0, -h, s.capital ? 20 : 14, -h + 5, 0, -h + 11])
        .fill(civColor(s.civilization))
        .stroke({ width: 1.2, color: 0x1a1208 });
      flag
        .circle(0, 0, s.capital ? 4 : 3)
        .fill(0xffffff)
        .stroke({ width: 1.5, color: 0x1a1208 });
      const name = new PIXI.Text({
        text: s.label,
        style: {
          fontFamily: 'system-ui, sans-serif',
          fontSize: s.capital ? 14 : 12,
          fontWeight: '700',
          fill: 0xffffff,
          stroke: { color: 0x101418, width: 4 },
        },
      });
      name.position.set(s.capital ? 26 : 18, -h - 6);
      const houses = Object.values(s.houses ?? {}).reduce((a, b) => a + b, 0);
      const sub = new PIXI.Text({
        text: `${s.capital ? 'capital · ' : ''}${RANK_LABELS[s.rank] ?? s.rank} · ${s.residents} people${
          houses ? ` · ${houses} houses` : ''
        } · engine day ${record.day}`,
        style: {
          fontFamily: 'system-ui, sans-serif',
          fontSize: 11,
          fill: 0xe8e0c8,
          stroke: { color: 0x101418, width: 3 },
        },
      });
      sub.position.set(s.capital ? 26 : 18, -h + 12);
      g.addChild(flag, name, sub);
      this.markers.addChild(g);
      this.items.push(g);
    }
  }

  _drawBorders(zoom) {
    const { PIXI, R } = this;
    for (const child of this.borders.removeChildren()) child.destroy();
    const owner = new Map((this.record.owners ?? []).map(([q, r, civ]) => [`${q},${r}`, civ]));
    const byCiv = new Map();
    for (const [q, r, civ] of this.record.owners ?? []) {
      if (!byCiv.has(civ)) byCiv.set(civ, []);
      byCiv.get(civ).push([q, r]);
    }
    for (const [civ, tiles] of byCiv) {
      const g = new PIXI.Graphics();
      const a = hexCentre(tiles[0][0], tiles[0][1], R);
      const anchor = project(a.x, a.y);
      g.position.set(anchor.x, anchor.y);
      for (const [q, r] of tiles) {
        for (const [dq, dr] of AXIAL) {
          if (owner.get(`${q + dq},${r + dr}`) === civ) continue;
          const [p0, p1] = sharedCorners(q, r, q + dq, r + dr, R).map((c) => project(c.x, c.y));
          g.moveTo(p0.x - anchor.x, p0.y - anchor.y).lineTo(p1.x - anchor.x, p1.y - anchor.y);
        }
      }
      g.stroke({ width: 2.5 / zoom, color: civColor(civ), alpha: 0.9 });
      this.borders.addChild(g);
    }
  }

  _drawTravellers(zoom) {
    const { PIXI, R } = this;
    for (const child of this.travellers.removeChildren()) child.destroy();
    for (const [q, r, civ, count] of this.record.travellers ?? []) {
      const c = hexCentre(q, r, R);
      const p = project(c.x, c.y);
      const g = new PIXI.Graphics();
      g.position.set(p.x, p.y);
      g.circle(0, 0, (3 + Math.sqrt(count) * 1.5) / zoom)
        .fill({ color: civColor(civ), alpha: 0.85 })
        .stroke({ width: 1 / zoom, color: 0xffffff });
      this.travellers.addChild(g);
    }
  }

  /** Constant screen sizes: markers scale, borders and dots are redrawn when the zoom moves on. */
  update(zoom) {
    if (!this.record) return;
    const alpha = zoom < 0.25 ? 1 : Math.max(0, 1 - (zoom - 0.25) / 0.25);
    for (const g of this.items) {
      g.scale.set(1 / zoom);
      g.alpha = alpha;
      g.visible = alpha > 0.01;
    }
    if (this.drawnZoom === null || Math.abs(Math.log(zoom / this.drawnZoom)) > 0.2) {
      this._drawBorders(zoom);
      this._drawTravellers(zoom);
      this.drawnZoom = zoom;
      this.showRoutes(this.shownRoutes);
    }
    this.borders.visible = zoom < 0.05;
    this.travellers.alpha = alpha;
    this.travellers.visible = alpha > 0.01;
  }

  counts() {
    const r = this.record;
    return {
      settlements: r?.settlements.length ?? 0,
      travellers: (r?.travellers ?? []).reduce((sum, t) => sum + t[3], 0),
      ownedTiles: r?.owners?.length ?? 0,
      parties: this.parties.length,
      routesShown: this.shownRoutes.length,
    };
  }
}
