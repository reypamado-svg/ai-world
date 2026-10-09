// Minimap: the whole world from the overview export, plus the camera footprint.
// Click to move the camera.

import { project } from '../world/coords.js';
import { hexCentre, worldScreenBounds } from '../world/hex.js';

const COLORS = ['#3a6e9e', '#86a24f', '#4c7a3a', '#8c8476', '#d2b97f', '#b9bfa8', '#9c9461', '#e9edf0'];

export class Minimap {
  constructor(canvas, { width, height, R, overview, onJump }) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.world = worldScreenBounds(width, height, R);
    const ww = this.world.x1 - this.world.x0;
    const wh = this.world.y1 - this.world.y0;
    this.scale = Math.min(canvas.width / ww, canvas.height / wh);
    this.ox = (canvas.width - ww * this.scale) / 2 - this.world.x0 * this.scale;
    this.oy = (canvas.height - wh * this.scale) / 2 - this.world.y0 * this.scale;
    this.base = document.createElement('canvas');
    this.base.width = canvas.width;
    this.base.height = canvas.height;
    const b = this.base.getContext('2d');
    b.fillStyle = '#12161a';
    b.fillRect(0, 0, canvas.width, canvas.height);
    if (overview) {
      const step = overview.step;
      const cell = Math.max(1.5, R * Math.sqrt(3) * Math.SQRT2 * 16 * step * this.scale);
      for (let oy = 0; oy < overview.height; oy += 1) {
        for (let ox = 0; ox < overview.width; ox += 1) {
          const code = Number(overview.codes[oy * overview.width + ox]);
          const c = hexCentre(ox * step, oy * step, R);
          const p = project(c.x, c.y);
          b.fillStyle = COLORS[code] ?? '#555';
          b.fillRect(p.x * this.scale + this.ox - cell / 2, p.y * this.scale + this.oy - cell / 4, cell, cell / 2);
        }
      }
    } else {
      b.strokeStyle = '#556';
      b.strokeRect(
        this.ox + this.world.x0 * this.scale,
        this.oy + this.world.y0 * this.scale,
        ww * this.scale,
        wh * this.scale,
      );
    }
    this.markers = [];
    this.R = R;
    this.fog = null;
    this.showMarkers = true;
    canvas.addEventListener('click', (e) => {
      const r = canvas.getBoundingClientRect();
      const mx = ((e.clientX - r.left) / r.width) * canvas.width;
      const my = ((e.clientY - r.top) / r.height) * canvas.height;
      onJump({ x: (mx - this.ox) / this.scale, y: (my - this.oy) / this.scale });
    });
  }

  addMarker(worldPoint, color, size = 3.5) {
    this.markers.push({ p: worldPoint, color, size });
  }

  /** In a civilization's perspective (O5): only the tiles it knows show; null shows all. */
  setFog(tiles) {
    if (!tiles) {
      this.fog = null;
      return;
    }
    const fog = document.createElement('canvas');
    fog.width = this.canvas.width;
    fog.height = this.canvas.height;
    const c = fog.getContext('2d');
    c.fillStyle = '#0b0d10';
    c.fillRect(0, 0, fog.width, fog.height);
    const cell = Math.max(2, this.R * Math.sqrt(3) * Math.SQRT2 * this.scale * 1.2);
    c.save();
    c.beginPath();
    for (const [q, r] of tiles) {
      const h = hexCentre(q, r, this.R);
      const p = project(h.x, h.y);
      c.rect(p.x * this.scale + this.ox - cell / 2, p.y * this.scale + this.oy - cell / 4, cell, cell / 2);
    }
    c.clip();
    c.drawImage(this.base, 0, 0);
    c.restore();
    this.fog = fog;
  }

  draw(view) {
    const ctx = this.ctx;
    ctx.drawImage(this.fog ?? this.base, 0, 0);
    for (const m of this.showMarkers ? this.markers : []) {
      ctx.fillStyle = m.color;
      ctx.strokeStyle = '#000';
      ctx.beginPath();
      ctx.arc(m.p.x * this.scale + this.ox, m.p.y * this.scale + this.oy, m.size, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
    }
    const x0 = view.x0 * this.scale + this.ox;
    const y0 = view.y0 * this.scale + this.oy;
    const w = Math.max(3, (view.x1 - view.x0) * this.scale);
    const h = Math.max(3, (view.y1 - view.y0) * this.scale);
    ctx.strokeStyle = '#ffd84a';
    ctx.lineWidth = 1.5;
    ctx.strokeRect(x0, y0, w, h);
  }
}
