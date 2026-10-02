// The ground inside a tile, as a colour field over the plane (PROTOTYPE ARTWORK).
//
// The engine gives each 25 km tile one set of values. The observer paints its
// interior from those values only: meadow or steppe from moisture, canopy
// density from timber, a snow line on mountains from temperature, rock where
// stone is high, and relief whose strength follows the terrain. Noise is in
// world metres, so the field is continuous across texture and tile borders,
// and colours blend across the border between two land tiles (the generator
// keeps neighbouring terrains compatible). Water and land meet at a shore.
// Pure and deterministic: no DOM.

import { hexCentre, planeToHex, planeToUV } from './hex.js';
import { fbm } from '../sim/rng.js';

const SQ3 = Math.sqrt(3);

/** Neighbour offsets, in the order of their direction angle in the hex frame (0, 60, ... degrees). */
const NEIGHBOURS = [
  [1, 0],
  [0, 1],
  [-1, 1],
  [-1, 0],
  [0, -1],
  [1, -1],
];
const NORMALS = NEIGHBOURS.map((_, i) => {
  const a = (i * Math.PI) / 3;
  return [Math.cos(a), Math.sin(a)];
});

/** Share of the radius over which two land tiles blend at their border. */
export const BLEND = 0.15;
/** Share of the radius covered by a beach or shallows where land meets water. */
export const SHORE = 0.04;
/** How far, as a share of the radius, a shoreline wanders off the tile border. */
export const WOBBLE = 0.16;
/** Corners where two shores meet are rounded over this share of the radius. */
const ROUND = 0.05;

const rgb = (hex) => [parseInt(hex.slice(1, 3), 16), parseInt(hex.slice(3, 5), 16), parseInt(hex.slice(5, 7), 16)];
const mix = (a, b, t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
const scale = (a, f) => [a[0] * f, a[1] * f, a[2] * f];
const clamp01 = (t) => Math.max(0, Math.min(1, t));
const smooth = (t) => t * t * (3 - 2 * t);

const C = {
  ocean: rgb('#2a5a86'),
  oceanDeep: rgb('#1d4670'),
  lake: rgb('#3f7f98'),
  shallows: rgb('#5f9fb2'),
  beach: rgb('#d6c896'),
  steppe: rgb('#a5a05a'),
  meadow: rgb('#6f9a3e'),
  floor: rgb('#617d3c'),
  canopy: rgb('#2c4f24'),
  rock: rgb('#7d756a'),
  rockHigh: rgb('#a39b8e'),
  snow: rgb('#eef1f4'),
  snowShade: rgb('#c9d3df'),
  hills: rgb('#958d5c'),
  sand: rgb('#d6bd84'),
  sandDark: rgb('#bf9f66'),
  stone: rgb('#9a9286'),
  tundra: rgb('#b3b9a2'),
  frost: rgb('#d9ddd2'),
  fieldA: rgb('#c9b25a'),
  fieldB: rgb('#8fa548'),
  fieldC: rgb('#a5843f'),
  hedge: rgb('#3f5a2a'),
};

/** Snow line for a mountain at this temperature (attribute units, 0..1000). */
export function snowLine(temperature) {
  return 800 + 1.2 * (temperature - 150);
}

/**
 * How much of a pattern with wavelength `lambda` metres to keep when the field
 * is sampled every `res` metres: all of it at four samples per wavelength,
 * none below two (it would alias into noise).
 */
function keep(lambda, res) {
  return res > 0 ? clamp01((lambda / res - 2) / 2) : 1;
}

/** Fade a pattern value in 0..1 toward its middle. */
const faded = (p, k) => 0.5 + (p - 0.5) * k;

/** Relief: a smooth height field in world metres, about 0..1, without octaves finer than `res`. */
function relief(x, y, res) {
  let octaves = 4;
  while (octaves > 1 && 1200 / 2.03 ** (octaves - 1) < 2 * res) octaves -= 1;
  return fbm(x / 1200, y / 1200, 101, octaves);
}

/** Light from the top of the screen (the -x, -y direction): > 1 lit, < 1 shaded. */
function hillshade(x, y, strength, res) {
  const d = Math.max(60, res / 2);
  const slope = relief(x - d, y - d, res) - relief(x + d, y + d, res);
  // The same gradient whatever the step: the difference grows with the distance sampled.
  return 1 + slope * 2 * strength * (60 / d);
}

/** Colour of a terrain at a plane point, from its tile's values only; `res` is the sample spacing in metres. */
function terrainColour(t, x, y, lake, res) {
  const fine = faded(fbm(x / 300, y / 300, 7, 3), keep(300, res));
  switch (t.terrain) {
    case 0: {
      const base = lake ? C.lake : mix(C.ocean, C.oceanDeep, fine * 0.6);
      return scale(base, 0.94 + fine * 0.12);
    }
    case 1: {
      const wet = clamp01((t.moisture - 300) / 200);
      const base = mix(C.steppe, C.meadow, wet);
      return scale(base, (0.9 + fine * 0.2) * hillshade(x, y, 0.15, res));
    }
    case 2: {
      const density = Math.min(1, 0.5 + t.timber / 2000);
      const blobs = faded(fbm(x / 180, y / 180, 31, 3), keep(180, res));
      const clumps = smooth(clamp01((blobs - 0.5 + density * 0.5) * 2.2));
      return scale(mix(C.floor, C.canopy, clumps * density + (1 - density) * 0.2), hillshade(x, y, 0.15, res));
    }
    case 3: {
      const ridge = (fbm(x / 2500, y / 2500, 53, 3) - 0.5) * 2;
      const high = clamp01((t.elevation - 800) / 200);
      let base = mix(C.rock, C.rockHigh, high * 0.6 + fine * 0.4);
      const above = t.elevation + 240 * ridge - snowLine(t.temperature);
      if (above > 0) base = mix(base, C.snow, smooth(clamp01(above / 60)));
      return scale(base, hillshade(x, y, 1.0, res));
    }
    case 4: {
      const warp = fbm(x / 900, y / 900, 71, 2) * 3;
      const dune = faded(0.5 + 0.5 * Math.sin(((x * 0.8 + y * 0.6) / 300) * Math.PI * 2 + warp), keep(300, res));
      let base = mix(C.sand, C.sandDark, dune * 0.5);
      if (t.stone > 600) {
        const rock = smooth(clamp01((faded(fbm(x / 500, y / 500, 73, 3), keep(500, res)) - 0.58) * 12));
        base = mix(base, C.stone, rock * 0.7);
      }
      return scale(base, hillshade(x, y, 0.15, res));
    }
    case 5: {
      const frost = smooth(clamp01((faded(fbm(x / 120, y / 120, 83, 3), keep(120, res)) - 0.45) * 3));
      return scale(mix(C.tundra, C.frost, frost * 0.7), hillshade(x, y, 0.15, res));
    }
    case 6:
      return scale(mix(C.hills, C.stone, t.stone > 600 ? fine * 0.4 : 0), hillshade(x, y, 0.6, res));
    case 7: {
      const wind = faded(0.5 + 0.5 * Math.sin(((x * 0.6 - y * 0.8) / 140) * Math.PI * 2 + fine * 4), keep(140, res));
      return scale(mix(C.snow, C.snowShade, wind * 0.35), hillshade(x, y, 0.8, res));
    }
    default:
      return [128, 128, 128];
  }
}

/** The SAMPLE field ring (presentation): strips in sectors between r0 and r1, with hedgerows. */
function fieldColour(f, x, y, under) {
  const dx = x - f.x;
  const dy = y - f.y;
  const d = Math.hypot(dx, dy);
  if (d < f.r0 || d > f.r1) return null;
  // 14 sectors and 85 m rings make fields of a few hectares.
  const sectorF = ((Math.atan2(dy, dx) + Math.PI) / (Math.PI * 2)) * 14;
  const sector = Math.floor(sectorF);
  const ringF = (d - f.r0) / 85;
  const ring = Math.floor(ringF);
  const cell = (sector * 7 + ring * 3) % 3;
  // Hedgerows about 5 m wide along the field edges.
  const across = Math.min(sectorF - sector, 1 - (sectorF - sector)) * ((d * Math.PI * 2) / 14);
  const along = Math.min(ringF - ring, 1 - (ringF - ring)) * 85;
  if (across < 2.5 || along < 2.5) return mix(under, C.hedge, smooth(clamp01((f.r1 - d) / 40)));
  const crop = [C.fieldA, C.fieldB, C.fieldC][cell];
  const rows = 0.93 + 0.07 * Math.sin((cell ? dx : dy) / 2.5);
  // Fade the outer edge into the ground.
  const fade = smooth(clamp01((f.r1 - d) / 40));
  return mix(under, scale(crop, rows), fade);
}

/**
 * Build a sampler over the plane.
 *   tileAt(q, r) -> { terrain, elevation, moisture, temperature, timber, stone } or null (off the map: ocean);
 *   lakes: Set of "q,r" water tiles that are lakes; features: [{ kind: 'fields', x, y, r0, r1 }].
 */
export function makeInterior({ R, tileAt, lakes = new Set(), features = [] }) {
  const inradius = (R * SQ3) / 2;
  const blend = BLEND * R;
  const shore = SHORE * R;
  const wobble = WOBBLE * R;
  const reach = Math.max(blend, wobble + shore);
  const colourOf = (t, q, r, x, y, res) => terrainColour(t, x, y, t.terrain === 0 && lakes.has(`${q},${r}`), res);
  const at = (q, r) => tileAt(q, r) ?? { terrain: 0 };

  /** Ground colour [r, g, b] at a plane point; `res` is the sample spacing in metres (0: full detail). */
  /**
   * Where a point lies relative to the shore: its tile, the distances to the
   * tile's six borders, and the nearest water/land border it may have crossed.
   */
  function locate(x, y) {
    const h = planeToHex(x, y, R);
    const t = tileAt(h.q, h.r);
    if (!t) return { h, t };
    const c = hexCentre(h.q, h.r, R);
    const { u, v } = planeToUV(x - c.x, y - c.y);
    const ownWater = t.terrain === 0;
    // Where water meets land the shoreline wanders off the tile border by up to
    // WOBBLE of the radius. `wob` > 0 moves water into the land tile; the same
    // value is seen from both tiles, so the shore is continuous.
    const wob = (fbm(x / 7000, y / 7000, 211, 3) - 0.5) * 2 * wobble;
    const d = NORMALS.map(([nu, nv]) => inradius - (u * nu + v * nv));
    // The nearest water/land border: { i, e } with e the signed distance to the shoreline.
    let across = null;
    let second = Infinity;
    for (let i = 0; i < 6; i += 1) {
      if (d[i] >= reach) continue;
      const n = at(h.q + NEIGHBOURS[i][0], h.r + NEIGHBOURS[i][1]);
      if ((n.terrain === 0) === ownWater) continue;
      const e = ownWater ? d[i] + wob : d[i] - wob;
      if (!across || e < across.e) {
        second = across ? across.e : second;
        across = { i, e };
      } else second = Math.min(second, e);
    }
    // Where two shores meet at a corner, round it off (a smooth minimum).
    if (across && second < Infinity) {
      const k = ROUND * R;
      across.e = -k * Math.log(Math.exp(-across.e / k) + Math.exp(-second / k));
    }
    return { h, t, d, ownWater, across };
  }

  /** Whether a plane point is water (lake, sea or off the map), shorelines included. */
  function waterAt(x, y) {
    const { t, ownWater, across } = locate(x, y);
    if (!t) return true;
    return across && across.e < 0 ? !ownWater : ownWater;
  }

  function sample(x, y, res = 0) {
    const { h, t, d, ownWater, across } = locate(x, y);
    if (!t) return terrainColour({ terrain: 0 }, x, y, false, res);
    let colour;
    let water = ownWater;
    if (across && across.e < 0) {
      // Past the shoreline: this point belongs to the neighbour's surface.
      const [dq, dr] = NEIGHBOURS[across.i];
      colour = colourOf(at(h.q + dq, h.r + dr), h.q + dq, h.r + dr, x, y, res);
      water = !ownWater;
    } else {
      // Blend with land (or water) neighbours alike. Weights: 1 for the own tile,
      // rising to 1 for a neighbour at the border, so a border mixes half and half
      // and a corner a third each, from either side.
      let acc = colourOf(t, h.q, h.r, x, y, res);
      let sum = 1;
      for (let i = 0; i < 6; i += 1) {
        if (d[i] >= blend) continue;
        const [dq, dr] = NEIGHBOURS[i];
        const n = at(h.q + dq, h.r + dr);
        if ((n.terrain === 0) !== ownWater) continue;
        const w = smooth(1 - d[i] / blend);
        const other = colourOf(n, h.q + dq, h.r + dr, x, y, res);
        acc = [acc[0] + other[0] * w, acc[1] + other[1] * w, acc[2] + other[2] * w];
        sum += w;
      }
      colour = scale(acc, 1 / sum);
    }
    // A beach on the land side of the shoreline, shallows on the water side.
    if (across && Math.abs(across.e) < shore) {
      const k = smooth(1 - Math.abs(across.e) / shore);
      colour = mix(colour, water ? C.shallows : C.beach, k * 0.85);
    }
    for (const f of features) {
      if (f.kind !== 'fields') continue;
      const field = fieldColour(f, x, y, colour);
      if (field) colour = field;
    }
    return colour;
  }

  return { sample, waterAt };
}

/** A tile's flat colour for the map (its interior at the centre, without relief). */
export function tileBaseColor(t, lake = false) {
  const base = {
    0: lake ? C.lake : C.ocean,
    1: mix(C.steppe, C.meadow, clamp01((t.moisture - 300) / 200)),
    2: mix(C.floor, C.canopy, Math.min(1, 0.5 + t.timber / 2000) * 0.75),
    3: mix(C.rock, C.rockHigh, clamp01((t.elevation - 800) / 200) * 0.6),
    4: C.sand,
    5: C.tundra,
    6: C.hills,
    7: C.snow,
  }[t.terrain] ?? [128, 128, 128];
  if (t.terrain === 3 && t.elevation - snowLine(t.temperature) > 0) return mix(base, C.snow, 0.6);
  return base;
}
