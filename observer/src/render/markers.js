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
