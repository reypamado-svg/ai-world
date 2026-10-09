// What one civilization knows, drawn over the map (O5): fog over the tiles it has never seen,
// a veil over those it has not seen lately (darker the longer ago), and glyphs for the foreign
// settlements, ruins, sites, roads, bridges, tolls and garrisons its council knows of, each
// labelled with the day it was last seen. Everything comes from the perspective record, which
// is built from the council report alone.
//
// Fog and veils are fills on the ground, drawn once per day (anchored per chunk, since
// Graphics are tessellated in float32); glyphs keep a constant size on screen.

import { project } from '../world/coords.js';
import { chunkOf, hexCentre, hexCorners, sharedCorners } from '../world/hex.js';
import { civilizationLabel } from '../data/naming.js';
import { CIV_COLORS } from './art/registry.js';

const FOG = 0x0b0d10;
/** Unknown tiles are covered completely: nothing of the ground, trees or rivers there shows. */
export const FOG_ALPHA = 1;
/** Fog hexes are drawn this fraction of R larger, so antialiased seams between neighbouring
 * fog tiles cannot show the ground. */
const FOG_BLEED = 0.03;
const VEIL = 0x8a8f96;
const UNDATED_ALPHA = 0.3;
const SITE_STYLE = {
  ore_deposit: { label: 'ore deposit', color: 0x5a4a6e },
  quarry: { label: 'quarry', color: 0x9a9286 },
  ancient_ruin: { label: 'ancient ruin', color: 0xb08a5a },
  trove: { label: 'trove', color: 0xe9b44c },
};

/** A fog tile's corners, pushed FOG_BLEED of R out from its centre on the ground plane:
 * opaque neighbours overlap, so no antialiased seam shows the ground. Veils stay exact
 * (overlapping veils would darken twice). */
export function fogCorners(q, r, R) {
  const c = hexCentre(q, r, R);
  return hexCorners(q, r, R).map((p) => ({
    x: c.x + (p.x - c.x) * (1 + FOG_BLEED),
    y: c.y + (p.y - c.y) * (1 + FOG_BLEED),
  }));
}

/** How strongly a known tile is veiled: not at all when seen today, more the older the
 * sighting (0.12 a day ago to 0.45 a year or more ago); a fixed veil when undated. */
export function veilAlpha(asOf, day) {
  if (asOf === null || asOf === undefined) return UNDATED_ALPHA;
  const age = day - asOf;
  if (age <= 0) return 0;
  return 0.12 + (0.33 * Math.min(age, 365)) / 365;
}

function civColor(index) {
  return parseInt(CIV_COLORS[index % CIV_COLORS.length].replace('#', ''), 16);
}

export class PerspectiveLayer {
  /**
   * @param {object} PIXI
   * @param {number} R tile radius
   * @param {{ width: number, height: number, chunkTiles: number, labelZoom: number }} world
   */
  constructor(PIXI, R, { width, height, chunkTiles, labelZoom }) {
    this.PIXI = PIXI;
    this.R = R;
    this.width = width;
    this.height = height;
    this.chunkTiles = chunkTiles;
    this.labelZoom = labelZoom;
    this.container = new PIXI.Container();
    this.ground = new PIXI.Container();
    this.glyphs = new PIXI.Container();
    this.container.addChild(this.ground, this.glyphs);
    this.items = [];
    this.stat = null;
    this.container.visible = false;
  }

  /** Draw one perspective (or clear with null). `shownDay` is the day the page shows, against
   * which "last seen" ages are reckoned; `civilizations` the run's ids in manifest order. */
  setDay(perspective, { shownDay, civilizations } = {}) {
    for (const c of [this.ground, this.glyphs])
      for (const child of c.removeChildren()) child.destroy({ children: true });
    this.items = [];
    this.stat = null;
    this.container.visible = Boolean(perspective);
    if (!perspective) return;
    const { PIXI, R } = this;
    const civIndex = new Map(civilizations.map((id, k) => [id, k]));
    const dates = new Map(perspective.tile_dates.map(([q, r, asOf]) => [`${q},${r}`, asOf]));
    const known = new Set(perspective.known_tiles.map(([q, r]) => `${q},${r}`));
    for (const key of dates.keys()) known.add(key);
    // Fog and veils, grouped by chunk so each Graphics stays near its anchor.
    const groups = new Map();
    let fogTiles = 0;
    let staleTiles = 0;
    for (let r = 0; r < this.height; r += 1) {
      for (let q = 0; q < this.width; q += 1) {
        const key = `${q},${r}`;
        let alpha;
        if (!known.has(key)) {
          alpha = FOG_ALPHA;
          fogTiles += 1;
        } else {
          alpha = veilAlpha(dates.get(key), shownDay);
          if (alpha === 0) continue;
          staleTiles += 1;
        }
        const { cq, cr } = chunkOf(q, r, this.chunkTiles);
        const id = `${cq},${cr}`;
        if (!groups.has(id)) groups.set(id, []);
        groups.get(id).push([q, r, alpha, !known.has(key)]);
      }
    }
    for (const tiles of groups.values()) {
      const g = new PIXI.Graphics();
      const a = hexCentre(tiles[0][0], tiles[0][1], R);
      const anchor = project(a.x, a.y);
      g.position.set(anchor.x, anchor.y);
      for (const [q, r, alpha, fog] of tiles) {
        const points = (fog ? fogCorners(q, r, R) : hexCorners(q, r, R)).flatMap((c) => {
          const p = project(c.x, c.y);
          return [p.x - anchor.x, p.y - anchor.y];
        });
        g.poly(points).fill({ color: fog ? FOG : VEIL, alpha });
      }
      this.ground.addChild(g);
    }
    const day = shownDay;
    const ago = (asOf) => (asOf === day ? 'seen today' : `last seen day ${asOf}`);
    for (const s of perspective.foreign_settlements) {
      const civ = civIndex.get(s.civilization) ?? 0;
      this._glyph(s.q, s.r, `settlement of ${civilizationLabel(s.civilization)} · ${ago(s.last_seen_day)}`, (g) => {
        g.moveTo(0, 0).lineTo(0, -24).stroke({ width: 2.5, color: 0x2a2014 });
        g.poly([0, -24, 14, -19, 0, -13]).stroke({ width: 2.5, color: civColor(civ) });
        g.circle(0, 0, 3).fill(0xffffff).stroke({ width: 1.5, color: 0x1a1208 });
      });
    }
    for (const ruin of perspective.ruins) {
      this._glyph(
        ruin.q,
        ruin.r,
        `ruin of ${civilizationLabel(ruin.former_civilization)} · ${ago(ruin.as_of_day)}`,
        (g) => g.rect(-6, -2, 4, 8).rect(2, -6, 4, 12).fill(0x6e6a63).stroke({ width: 1.5, color: 0x101418 }),
      );
    }
    for (const site of perspective.sites) {
      const style = SITE_STYLE[site.kind] ?? { label: site.kind, color: 0xffffff };
      const when = site.as_of_day > 0 ? ago(site.as_of_day) : 'as made';
      this._glyph(site.q, site.r, `${style.label} · ${site.remaining} of ${site.richness} left · ${when}`, (g) => {
        if (site.kind === 'ore_deposit') g.poly([0, -7, 6, 0, 0, 7, -6, 0]);
        else if (site.kind === 'quarry') g.rect(-6, -5, 12, 10);
        else if (site.kind === 'ancient_ruin') g.rect(-6, -2, 4, 8).rect(2, -6, 4, 12);
        else g.circle(0, 0, 5.5);
        g.fill(style.color).stroke({ width: 1.5, color: 0x101418 });
      });
    }
    for (const road of perspective.roads) {
      this._glyph(road.q, road.r, `${road.grade} road · ${ago(road.as_of_day)}`, (g) =>
        g.rect(-4, -1.5, 8, 3).fill(0xb89a6a).stroke({ width: 1, color: 0x101418 }),
      );
    }
    for (const [aq, ar, bq, br] of perspective.bridges) {
      const [p0, p1] = sharedCorners(aq, ar, bq, br, R).map((c) => project(c.x, c.y));
      const mid = { x: (p0.x + p1.x) / 2, y: (p0.y + p1.y) / 2 };
      this._glyphAt(mid, 'bridge', (g) => g.rect(-6, -2, 12, 4).fill(0x8a6a44).stroke({ width: 1, color: 0x101418 }));
    }
    for (const toll of perspective.tolls) {
      const civ = civIndex.get(toll.owner) ?? 0;
      this._glyph(toll.q, toll.r, `toll post of ${civilizationLabel(toll.owner)} · ${ago(toll.as_of_day)}`, (g) =>
        g.poly([0, -7, 6, 5, -6, 5]).fill(civColor(civ)).stroke({ width: 1.5, color: 0x101418 }),
      );
    }
    for (const garrison of perspective.garrisons) {
      this._glyph(garrison.q, garrison.r, `garrison · ${garrison.members} people`, (g) =>
        g.circle(0, 0, 5).fill(0x3d4a5c).stroke({ width: 1.5, color: 0xffffff }),
      );
    }
    this.stat = {
      known: known.size,
      fogTiles,
      staleTiles,
      foreign: perspective.foreign_settlements.length,
      ruins: perspective.ruins.length,
      sites: perspective.sites.length,
      roads: perspective.roads.length,
      bridges: perspective.bridges.length,
      tolls: perspective.tolls.length,
      garrisons: perspective.garrisons.length,
    };
  }

  _glyph(q, r, text, paint) {
    const c = hexCentre(q, r, this.R);
    this._glyphAt(project(c.x, c.y), text, paint);
  }

  _glyphAt(p, text, paint) {
    const { PIXI } = this;
    const g = new PIXI.Container();
    g.position.set(p.x, p.y);
    const glyph = new PIXI.Graphics();
    paint(glyph);
    const label = new PIXI.Text({
      text,
      style: {
        fontFamily: 'system-ui, sans-serif',
        fontSize: 11,
        fill: 0xf2ead2,
        stroke: { color: 0x101418, width: 3 },
      },
    });
    label.position.set(10, -8);
    g.addChild(glyph, label);
    this.glyphs.addChild(g);
    this.items.push({ g, label });
  }

  /** Constant screen size for glyphs, labels from `labelZoom`, hidden at close zoom. */
  update(zoom) {
    if (!this.container.visible) return;
    const alpha = zoom < 0.25 ? 1 : Math.max(0, 1 - (zoom - 0.25) / 0.25);
    for (const it of this.items) {
      it.g.scale.set(1 / zoom);
      it.g.alpha = alpha;
      it.g.visible = alpha > 0.01;
      it.label.visible = zoom >= this.labelZoom;
    }
  }

  counts() {
    return this.stat;
  }
}
