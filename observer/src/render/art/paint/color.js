// Small colour helpers for the procedural painters.

export function hexToRgb(hex) {
  const h = hex.replace('#', '');
  const v = parseInt(h.length === 3 ? h.replace(/./g, '$&$&') : h, 16);
  return [(v >> 16) & 255, (v >> 8) & 255, v & 255];
}

export function rgbToCss([r, g, b], a = 1) {
  const c = (n) => Math.max(0, Math.min(255, Math.round(n)));
  return a >= 1 ? `rgb(${c(r)},${c(g)},${c(b)})` : `rgba(${c(r)},${c(g)},${c(b)},${a})`;
}

export function mix(a, b, t) {
  const x = typeof a === 'string' ? hexToRgb(a) : a;
  const y = typeof b === 'string' ? hexToRgb(b) : b;
  return [x[0] + (y[0] - x[0]) * t, x[1] + (y[1] - x[1]) * t, x[2] + (y[2] - x[2]) * t];
}

/** Multiply brightness; f > 1 lightens. */
export function shade(color, f) {
  const c = typeof color === 'string' ? hexToRgb(color) : color;
  return [c[0] * f, c[1] * f, c[2] * f];
}

export function css(color, f = 1, a = 1) {
  return rgbToCss(shade(color, f), a);
}

/** Random variation of a colour, deterministic from rng. */
export function jitter(color, rng, amount = 0.12) {
  const c = typeof color === 'string' ? hexToRgb(color) : color;
  const f = 1 + (rng() - 0.5) * 2 * amount;
  const warm = (rng() - 0.5) * amount * 40;
  return [c[0] * f + warm, c[1] * f, c[2] * f - warm];
}
