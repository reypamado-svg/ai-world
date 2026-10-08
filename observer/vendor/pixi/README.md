# Vendored PixiJS

- Package: `pixi.js` 8.21.0 from the npm registry (MIT, see `LICENSE`).
- File: `dist/pixi.min.mjs`, copied unmodified.
- SHA-256: `2f69e90f5b1980e6ea244bacfb3fa1eb457e16ddcb0a602b694642658956f2ef`
- File: `dist/packages/unsafe-eval.min.js` (Pixi's own `pixi.js/unsafe-eval` polyfills), copied
  unmodified as `unsafe-eval.min.js`. SHA-256:
  `95b16558c615575f08d6b9e2b518b0edc4d446673b3307d967e2e59fab0464cd`. The page loads it only
  where eval is refused (the observer server's Content-Security-Policy); see
  `src/render/strict-csp.js`.

Vendored so the observer has no build step and no CDN dependency. To update,
bump `pixi.js` in `observer/package.json`, run `npm install`, copy
`node_modules/pixi.js/dist/pixi.min.mjs` and
`node_modules/pixi.js/dist/packages/unsafe-eval.min.js` here and update these hashes.
