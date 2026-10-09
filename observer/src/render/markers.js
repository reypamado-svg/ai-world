// Atlas markers for engine day-0 capitals, kept a constant size on screen.
// Every marker says where its data comes from ("engine day 0").

import { project } from '../world/coords.js';
import { hexCentre } from '../world/hex.js';
import { settlementLabel } from '../data/naming.js';
import { CIV_COLORS } from './art/registry.js';

function hexNum(css) {
  return parseInt(css.replace('#', ''), 16);
}

export class CapitalMarkers {
  constructor(PIXI, day0, R) {
    this.container = new PIXI.Container();
    this.items = [];
    day0.civilizations.forEach((civ, i) => {
      const [q, r] = civ.capital.tile;
      const c = hexCentre(q, r, R);
      const p = project(c.x, c.y);
      const g = new PIXI.Container();
      g.position.set(p.x, p.y);
      const color = hexNum(CIV_COLORS[i % CIV_COLORS.length]);
      const flag = new PIXI.Graphics();
      flag.moveTo(0, 0).lineTo(0, -34).stroke({ width: 2.5, color: 0x2a2014 });
      flag.poly([0, -34, 20, -29, 0, -23]).fill(color).stroke({ width: 1.2, color: 0x1a1208 });
      flag.circle(0, 0, 4).fill(0xffffff).stroke({ width: 1.5, color: 0x1a1208 });
      g.addChild(flag);
      const name = new PIXI.Text({
        text: `${settlementLabel(civ.capital.settlement_id)}`,
        style: {
          fontFamily: 'system-ui, sans-serif',
          fontSize: 14,
          fontWeight: '700',
          fill: 0xffffff,
          stroke: { color: 0x101418, width: 4 },
        },
      });
      name.position.set(26, -40);
      const sub = new PIXI.Text({
        text: `capital · ${civ.founders} founders · engine day 0`,
        style: {
          fontFamily: 'system-ui, sans-serif',
          fontSize: 11,
          fill: 0xe8e0c8,
          stroke: { color: 0x101418, width: 3 },
        },
      });
      sub.position.set(26, -22);
      g.addChild(name, sub);
      this.container.addChild(g);
      this.items.push({ g, civ, index: i, world: p });
    });
  }

  /** Keep markers a constant screen size; fade them out at close zoom. */
  update(zoom) {
    const alpha = zoom < 0.25 ? 1 : Math.max(0, 1 - (zoom - 0.25) / 0.25);
    for (const it of this.items) {
      it.g.scale.set(1 / zoom);
      it.g.alpha = alpha;
      it.g.visible = alpha > 0.01;
    }
  }
}

/** How each kind of site is marked and named (engine site kinds, day 0). */
const SITE_STYLE = {
  ore_deposit: { label: 'ore deposit', color: 0x5a4a6e },
  quarry: { label: 'quarry', color: 0x9a9286 },
  ancient_ruin: { label: 'ancient ruin', color: 0xb08a5a },
  trove: { label: 'trove', color: 0xe9b44c },
};

/**
 * Ore deposits, quarries, ancient ruins and troves placed when the world was made.
 * A small glyph per site at a constant screen size; its label shows once tiles are
 * large enough on screen to tell sites apart.
 */
export class SiteMarkers {
  constructor(PIXI, sites, R, labelZoom) {
    this.container = new PIXI.Container();
    this.items = [];
    this.labelZoom = labelZoom;
    for (const site of sites) {
      const style = SITE_STYLE[site.kind] ?? { label: site.kind, color: 0xffffff };
      const c = hexCentre(site.q, site.r, R);
      const p = project(c.x, c.y);
      const g = new PIXI.Container();
      g.position.set(p.x, p.y);
      const glyph = new PIXI.Graphics();
      if (site.kind === 'ore_deposit') glyph.poly([0, -7, 6, 0, 0, 7, -6, 0]);
      else if (site.kind === 'quarry') glyph.rect(-6, -5, 12, 10);
      else if (site.kind === 'ancient_ruin') glyph.rect(-6, -2, 4, 8).rect(2, -6, 4, 12);
      else glyph.circle(0, 0, 5.5);
      glyph.fill(style.color).stroke({ width: 1.5, color: 0x101418 });
      const label = new PIXI.Text({
        text: `${style.label} · engine day 0`,
        style: {
          fontFamily: 'system-ui, sans-serif',
          fontSize: 11,
          fill: 0xf2ead2,
          stroke: { color: 0x101418, width: 3 },
        },
      });
      label.position.set(10, -8);
      g.addChild(glyph, label);
      this.container.addChild(g);
      this.items.push({ g, label, site });
    }
  }

  /** Constant screen size; labels from `labelZoom`; hidden at close zoom like the capitals. */
  update(zoom) {
    const alpha = zoom < 0.25 ? 1 : Math.max(0, 1 - (zoom - 0.25) / 0.25);
    for (const it of this.items) {
      it.g.scale.set(1 / zoom);
      it.g.alpha = alpha;
      it.g.visible = alpha > 0.01;
      it.label.visible = zoom >= this.labelZoom;
    }
  }
}
