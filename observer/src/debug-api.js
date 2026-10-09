// window.__proof: the deterministic test and capture API for the proof page.
// It drives display time and the camera only; it cannot alter anything else.

import { project } from './world/coords.js';
import { personAssetContract } from './render/art/registry.js';

export function buildProofApi(app) {
  const r = app.renderer;
  const canvasPoint = (lx, ly) => {
    const o = r.offset;
    return app.camera.worldToScreen(app.world, lx + o.x, ly + o.y);
  };
  const readPixel = (cx, cy) => {
    app.pixi.render();
    const c = document.createElement('canvas');
    c.width = app.pixi.canvas.width;
    c.height = app.pixi.canvas.height;
    const ctx = c.getContext('2d');
    ctx.drawImage(app.pixi.canvas, 0, 0);
    const res = app.pixi.renderer.resolution;
    const d = ctx.getImageData(Math.round(cx * res), Math.round(cy * res), 1, 1).data;
    return [d[0], d[1], d[2]];
  };
  return {
    ready: true,
    scene: app.sceneName,
    cases: app.scene.cases ?? [],
    contract: app.contract,
    personContract: personAssetContract(),
    setPaused: (v) => app.setPaused(v),
    setTime: (v) => {
      app.t = v;
      app.frame(0, false);
    },
    time: () => app.t,
    setFlat: (v) => {
      app.setFlat(v);
      app.frame(0);
    },
    view: (x, y, z) => {
      const p = project(x, y);
      const o = r.offset;
      app.camera.x = p.x + o.x;
      app.camera.y = p.y + o.y;
      app.camera.zoom = z;
      app.frame(0, false);
    },
    camera: () => ({ x: app.camera.x, y: app.camera.y, zoom: app.camera.zoom }),
    order: () => r.drawOrder.map((o) => o.id),
    isDrawn: (id) => r.drawOrder.some((o) => o.id === id),
    worldToCanvas: (x, y, z = 0) => {
      const p = project(x, y, z);
      return canvasPoint(p.x, p.y);
    },
    positionOf: (id) => r.positionAt(id, app.t),
    peopleIds: () => r.people.map((o) => o.id),
    centreOf: (id) => r.centreOf(id),
    select: (id) => {
      app.select(id);
      app.frame(0);
    },
    follow: (v) => {
      app.follow = v;
    },
    selection: () => {
      if (!app.selected) return null;
      const o = r.byId.get(app.selected);
      return {
        id: app.selected,
        inside: o.state?.inside ?? null,
        drawn: r.drawOrder.includes(o),
        highlighted: !!(o.hidden && o.state?.inside && r.byId.get(o.state.inside)?.sprite.tint !== 0xffffff),
      };
    },
    overlapProbe: (aId, bId) => {
      const a = r.byId.get(aId);
      const b = r.byId.get(bId);
      const x0 = Math.max(a.rect.x0, b.rect.x0);
      const x1 = Math.min(a.rect.x1, b.rect.x1);
      const y0 = Math.max(a.rect.y0, b.rect.y0);
      const y1 = Math.min(a.rect.y1, b.rect.y1);
      const others = (x, y) => r.drawOrder.some((o) => o !== a && o !== b && r.hitTest(o, x, y));
      const hits = [];
      for (let y = y0; y < y1; y += 0.5) {
        for (let x = x0; x < x1; x += 0.5) {
          if (!(r.hitTest(a, x, y) && r.hitTest(b, x, y)) || others(x, y)) continue;
          // Require a solid neighbourhood so edge pixels are not used.
          let ok = true;
          for (const [dx, dy] of [
            [-1.5, 0],
            [1.5, 0],
            [0, -1.5],
            [0, 1.5],
          ]) {
            if (!(r.hitTest(a, x + dx, y + dy) && r.hitTest(b, x + dx, y + dy)) || others(x + dx, y + dy)) ok = false;
          }
          if (ok) hits.push([x, y]);
        }
      }
      if (!hits.length) return null;
      const [lx, ly] = hits[Math.floor(hits.length / 2)];
      return { ...canvasPoint(lx, ly), candidates: hits.length };
    },
    probe: (cx, cy) => {
      const [red, green, blue] = readPixel(cx, cy);
      if (blue !== 128) return null;
      const v = (red | (green << 8)) / 4 - 1;
      return r.objects[v]?.id ?? null;
    },
    pixel: readPixel,
    setFootprints: (v) => {
      app.setFootprints(v);
      app.frame(0);
    },
    stats: () => app.stats(),
    frame: () => app.frame(0),
    /** Advance display time and camera easing by dt seconds (deterministic capture). */
    step: (dt) => {
      app.t += dt;
      app.frame(dt * 1000, false);
    },
    setZoom: (z) => {
      app.camera.zoom = z;
      app.frame(0, false);
    },
  };
}
