// Camera over the world container: a centre in world screen pixels (zoom 1)
// and a zoom factor. It never touches simulation or presentation state.

export class Camera {
  constructor({ x = 0, y = 0, zoom = 1, minZoom = 0.3, maxZoom = 2.6, clamp = null } = {}) {
    this.x = x;
    this.y = y;
    this.zoom = zoom;
    this.minZoom = minZoom;
    this.maxZoom = maxZoom;
    this.clampFn = clamp;
  }

  setZoom(z) {
    this.zoom = Math.max(this.minZoom, Math.min(this.maxZoom, z));
  }

  /** Zoom by a factor keeping the world point under screen point (mx, my) fixed. */
  zoomAt(container, screenW, screenH, mx, my, factor) {
    const before = this.screenToWorld(container, mx, my);
    this.setZoom(this.zoom * factor);
    this.x = before.x - (mx - screenW / 2) / this.zoom;
    this.y = before.y - (my - screenH / 2) / this.zoom;
  }

  clamp() {
    if (this.clampFn) this.clampFn(this);
  }

  apply(container, screenW, screenH) {
    container.scale.set(this.zoom);
    container.position.set(screenW / 2 - this.x * this.zoom, screenH / 2 - this.y * this.zoom);
  }

  screenToWorld(container, sx, sy) {
    return { x: (sx - container.position.x) / this.zoom, y: (sy - container.position.y) / this.zoom };
  }

  worldToScreen(container, wx, wy) {
    return { x: wx * this.zoom + container.position.x, y: wy * this.zoom + container.position.y };
  }

  /** Visible rectangle in world screen pixels, with a margin in the same units. */
  viewRect(container, screenW, screenH, margin = 80) {
    return {
      x0: (0 - container.position.x) / this.zoom - margin,
      y0: (0 - container.position.y) / this.zoom - margin,
      x1: (screenW - container.position.x) / this.zoom + margin,
      y1: (screenH - container.position.y) / this.zoom + margin,
    };
  }

  /** Ease toward a target (world screen pixels) over dtMs. */
  followTowards(target, dtMs) {
    const k = 1 - Math.exp(-(dtMs / 1000) * 4);
    this.x += (target.x - this.x) * k;
    this.y += (target.y - this.y) * k;
  }
}
