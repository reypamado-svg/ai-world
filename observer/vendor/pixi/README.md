# Vendored PixiJS

- Package: `pixi.js` 8.21.0 from the npm registry (MIT, see `LICENSE`).
- File: `dist/pixi.min.mjs`, copied unmodified.
- SHA-256: `2f69e90f5b1980e6ea244bacfb3fa1eb457e16ddcb0a602b694642658956f2ef`

Vendored so the observer has no build step and no CDN dependency. To update,
bump `pixi.js` in `observer/package.json`, run `npm install`, copy
`node_modules/pixi.js/dist/pixi.min.mjs` here and update this hash.
