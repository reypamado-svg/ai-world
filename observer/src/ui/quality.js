// Rendering quality. Auto steps down when frames are slow (p95 frame interval
// over budget) and back up when there is headroom. Quality never touches
// presentation state: it only changes resolution, shadows, how often citizen
// animation is refreshed and how many citizens get full detail.

export const LEVELS = {
  high: { resolution: Math.min(2, window.devicePixelRatio || 1), shadows: true, crowdBudget: 800, animHz: 60 },
  medium: { resolution: 1, shadows: true, crowdBudget: 400, animHz: 30 },
  low: { resolution: 0.75, shadows: false, crowdBudget: 150, animHz: 15 },
};
const ORDER = ['high', 'medium', 'low'];

export class Quality {
  constructor(apply, mode = 'auto') {
    this.apply = apply;
    this.mode = mode;
    this.level = mode === 'auto' ? 'high' : mode;
    this.intervals = [];
    this.lastChange = performance.now();
    this.apply(LEVELS[this.level], this.level);
  }

  setMode(mode) {
    this.mode = mode;
    this._set(mode === 'auto' ? this.level : mode);
  }

  _set(level) {
    if (level === this.level) return;
    this.level = level;
    this.lastChange = performance.now();
    this.intervals = [];
    this.apply(LEVELS[level], level);
  }

  /** Feed one real frame interval (ms). */
  observe(ms) {
    this.intervals.push(ms);
    if (this.intervals.length > 120) this.intervals.shift();
    if (this.mode !== 'auto' || this.intervals.length < 60) return;
    const sorted = this.intervals.slice().sort((a, b) => a - b);
    const p95 = sorted[Math.floor(sorted.length * 0.95)];
    const since = performance.now() - this.lastChange;
    const i = ORDER.indexOf(this.level);
    if (p95 > 40 && since > 3000 && i < ORDER.length - 1) this._set(ORDER[i + 1]);
    else if (p95 < 18 && since > 10000 && i > 0) this._set(ORDER[i - 1]);
  }

  get settings() {
    return LEVELS[this.level];
  }
}
