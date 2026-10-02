// Review stills for the world generator v2 preview (G2), and the tile
// interiors at 25 km tiles (O1b).
//   node tests/capture-geography.mjs <outdir>
// Targets are found in the exported engine data: the largest snowfield, a
// hills skirt beside mountains, the biggest river mouth, a desert basin, a
// lake and a forest river.
import { chromium } from 'playwright';
import { mkdir, readFile, readdir } from 'node:fs/promises';
import { serve } from './serve.mjs';
import { hexRadiusOf, sharedCorners } from '../src/world/hex.js';
import { riverPoint } from '../src/world/rivers.js';

const out = process.argv[2] ?? 'captures';
await mkdir(out, { recursive: true });
const data = new URL('../data/terrain/', import.meta.url);

const tiles = new Map();
for (const name of await readdir(new URL('chunks/', data))) {
  const chunk = JSON.parse(await readFile(new URL(`chunks/${name}`, data), 'utf8'));
  for (const row of chunk.tiles) {
    const t = Object.fromEntries(chunk.fields.map((f, i) => [f, row[i]]));
    tiles.set(`${t.q},${t.r}`, t);
  }
}
const hydrology = JSON.parse(await readFile(new URL('hydrology.json', data), 'utf8'));
const around = (t) =>
  [
    [1, 0],
    [1, -1],
    [0, -1],
    [-1, 0],
    [-1, 1],
    [0, 1],
  ]
    .map(([dq, dr]) => tiles.get(`${t.q + dq},${t.r + dr}`))
    .filter(Boolean);
const most = (terrain, score) =>
  [...tiles.values()]
    .filter((t) => t.terrain === terrain)
    .sort((a, b) => score(b) - score(a) || a.q - b.q || a.r - b.r)[0];
const alike = (t) => around(t).filter((n) => n.terrain === t.terrain).length;
const snow = most('snow', alike);
const desert = most('desert', alike);
const hills = most('hills', (t) => around(t).filter((n) => n.terrain === 'mountain').length * 10 + alike(t));
const [aq, ar] = hydrology.edges
  .filter(([, , , , , dq, dr]) => dq !== null && tiles.get(`${dq},${dr}`)?.terrain === 'water')
  .sort((a, b) => b[4] - a[4])[0];
const lake = hydrology.lakes[Math.floor(hydrology.lakes.length / 2)];
const forest = most('forest', (t) => (t.river ? 100 : 0) + alike(t));
console.log({
  snow: [snow.q, snow.r],
  desert: [desert.q, desert.r],
  hills: [hills.q, hills.r],
  mouth: [aq, ar],
  lake,
  forest: [forest.q, forest.r],
});

const server = await serve(0);
const base = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch({
  args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'],
});
const page = await browser.newPage({ viewport: { width: 1600, height: 900 }, deviceScaleFactor: 1 });
page.on('pageerror', (e) => console.log('[pageerror]', e.message));
await page.goto(`${base}/index.html?citizens=400&quality=high`);
await page.waitForFunction(() => window.__observer?.ready || window.__observerError, null, { timeout: 300000 });
await page.evaluate(() => window.__observer.setPaused(true));

// Tile-scale shots give the zoom as screen pixels per tile; close shots as a zoom.
const shots = [
  ['g2-01-world', (o) => o.home()],
  ['g2-02-mountain-range', (o, t) => o.viewHex(t.snow[0], t.snow[1], o.zoomForTilePx(30))],
  ['g2-03-snow-close', (o, t) => o.viewHex(t.snow[0], t.snow[1], o.zoomForTilePx(200))],
  ['g2-04-hills-skirt', (o, t) => o.viewHex(t.hills[0], t.hills[1], o.zoomForTilePx(125))],
  ['g2-05-river-mouth', (o, t) => o.viewHex(t.mouth[0], t.mouth[1], o.zoomForTilePx(75))],
  ['g2-06-river-close', (o, t) => o.viewHex(t.mouth[0], t.mouth[1], o.zoomForTilePx(376))],
  ['g2-07-desert', (o, t) => o.viewHex(t.desert[0], t.desert[1], o.zoomForTilePx(75))],
  ['g2-08-lake', (o, t) => o.viewHex(t.lake[0], t.lake[1], o.zoomForTilePx(125))],
  ['g2-09-forest-river', (o, t) => o.viewHex(t.forest[0], t.forest[1], o.zoomForTilePx(300))],
  ['o1b-g-10-snow-line', (o, t) => o.viewHex(t.snow[0], t.snow[1], 0.01)],
  ['o1b-g-11-hills-relief', (o, t) => o.viewHex(t.hills[0], t.hills[1], 0.01)],
  ['o1b-g-12-dunes', (o, t) => o.viewHex(t.desert[0], t.desert[1], 0.03)],
  ['o1b-g-13-canopy', (o, t) => o.viewHex(t.forest[0], t.forest[1], 0.03)],
  ['o1b-g-14-lake-shore', (o, t) => o.view(t.shore[0], t.shore[1], 0.02)],
  ['o1b-g-15-stream-ford', (o, t) => o.view(t.ford[0], t.ford[1], 0.05)],
];
// A lake shore (the middle of a lake tile's border with land) and a stream ford
// (the middle of a shallow river border), as ground-plane points.
const R = hexRadiusOf(JSON.parse(await readFile(new URL('manifest.json', data), 'utf8')));
const shoreTile = hydrology.lakes
  .map(([q, r]) => tiles.get(`${q},${r}`))
  .find((t) => around(t).some((n) => n.terrain !== 'water'));
const landNeighbour = around(shoreTile).find((n) => n.terrain !== 'water');
const [s1, s2] = sharedCorners(shoreTile.q, shoreTile.r, landNeighbour.q, landNeighbour.r, R);
const stream = hydrology.edges.find(([, , , , flow]) => flow < 4);
const [f1, f2] = sharedCorners(stream[0], stream[1], stream[2], stream[3], R);
const ford = riverPoint({ p1: f1, p2: f2, key: `${stream[0]},${stream[1]},${stream[2]},${stream[3]}` }, 0.5);
const targets = {
  snow: [snow.q, snow.r],
  desert: [desert.q, desert.r],
  hills: [hills.q, hills.r],
  mouth: [aq, ar],
  lake,
  forest: [forest.q, forest.r],
  shore: [(s1.x + s2.x) / 2, (s1.y + s2.y) / 2],
  ford: [ford.x, ford.y],
};
for (const [name, fn] of shots) {
  await page.evaluate(`(${fn.toString()})(window.__observer, ${JSON.stringify(targets)})`);
  await page.evaluate(() => window.__observer.settle());
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${out}/${name}.png` });
  console.log('wrote', name);
}
await browser.close();
server.close();
