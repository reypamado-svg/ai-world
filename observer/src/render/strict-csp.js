// PixiJS under a Content-Security-Policy without 'unsafe-eval' (slice H). The observer server
// sends a strict CSP, and Pixi normally builds its uniform, buffer and particle updates with
// `new Function`, which that policy refuses: Pixi then stops with "Current environment does not
// allow unsafe-eval". Pixi ships polyfills that do the same work without eval
// (`pixi.js/unsafe-eval`, vendored unmodified as `vendor/pixi/unsafe-eval.min.js`). It is a
// classic script written against a global `PIXI`, so it is loaded once, before any renderer is
// made, against a plain copy of the module's exports (the classes are the same objects, so its
// patches land on them); the copy stays as the global, since the polyfills read it while
// drawing. It carries its own copy of the particle buffer, so the module's own
// particle buffer is given the same no-eval update here. Where eval is allowed (the static test
// server), nothing is loaded and Pixi is unchanged.

const POLYFILL = new URL('../../vendor/pixi/unsafe-eval.min.js', import.meta.url).href;

/** Whether this page may build functions from strings (false under the server's CSP). */
export function evalAllowed() {
  try {
    return new Function('return true')() === true;
  } catch {
    return false;
  }
}

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = src;
    script.onload = resolve;
    script.onerror = () => reject(new Error(`could not load ${src}`));
    document.head.append(script);
  });
}

/**
 * Make Pixi work under a CSP without 'unsafe-eval'; call before making a renderer.
 * @returns {Promise<boolean>} whether the polyfills were installed
 */
export async function allowStrictCsp(PIXI, allowed = evalAllowed()) {
  if (allowed) return false;
  globalThis.PIXI = { ...PIXI };
  await loadScript(POLYFILL);
  const { generateParticleUpdatePolyfill } = globalThis.PIXI;
  Object.assign(PIXI.ParticleBuffer.prototype, { generateParticleUpdate: generateParticleUpdatePolyfill });
  return true;
}
