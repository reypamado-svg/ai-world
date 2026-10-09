// Frame-time bookkeeping for the performance readout.

export class FrameStats {
  constructor(size = 240) {
    this.size = size;
    this.times = [];
  }

  push(ms) {
    this.times.push(ms);
    if (this.times.length > this.size) this.times.shift();
  }

  summary() {
    const sorted = this.times.slice().sort((a, b) => a - b);
    const avg = sorted.reduce((a, b) => a + b, 0) / Math.max(1, sorted.length);
    const p95 = sorted[Math.floor(sorted.length * 0.95)] ?? 0;
    return { updateMsAvg: Number(avg.toFixed(2)), updateMsP95: Number(p95.toFixed(2)) };
  }
}

export function jsHeapBytes() {
  return performance.memory ? performance.memory.usedJSHeapSize : null;
}
