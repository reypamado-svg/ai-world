// The asset contract.
//
// Every drawable the renderer knows is an asset with:
//   id          stable asset ID (e.g. "building.storehouse")
//   category    building | nature | prop | person | vehicle | shadow
//   kind        static (placed once) or dynamic (moves / animates)
//   footprint   ground box in metres relative to the anchor (depth sorting)
//   anchor      sprite pixel that sits on the ground anchor point
//   size        sprite size in art pixels (2x screen pixels at zoom 1)
//   frames      animations: name -> frame count, facings
//   mask        what the civilization colour is applied to (or null)
//   doors       door points (metres, relative to anchor), outside the footprint
//
// Painters produce assets that satisfy this contract today. A licensed sprite
// can replace any single asset later if it supplies the same fields.

import { K } from '../../world/coords.js';
import { ART } from './paint/iso.js';
import { paintBuilding, paintConstruction, paintWell } from './paint/buildings.js';
import { paintBush, paintCropRow, paintFence, paintProp, paintRock, paintTree } from './paint/nature.js';
import { ANIMATIONS, APPEARANCE_COUNT, ENVOY_FRAME_H, FRAME_H, FRAME_W } from './paint/people.js';
import { WALL_GRADES, paintGate, paintTower, paintWall } from './paint/walls.js';

export const CIV_COLORS = ['#2f5d9a', '#a83a32', '#3f8a3a', '#c9a227'];

const PLASTER = { kind: 'plaster', plaster: '#dccfb0', beam: '#4b3524' };
const PLASTER_WARM = { kind: 'plaster', plaster: '#d6c19a', beam: '#5a3e28', bays: 1.4 };
const PLANKS = { kind: 'planks', base: '#8c6842', board: 0.24 };
const PLANKS_DARK = { kind: 'planks', base: '#6e5236', board: 0.26 };
const STONE = { kind: 'stone', base: '#a39d90', mortar: '#7d766a' };
const STONE_WARM = { kind: 'stone', base: '#b09a7c', mortar: '#857460' };
const LOGS = { kind: 'logs', base: '#7a5634' };
const THATCH = { kind: 'thatch', base: '#b6935a' };
const SHINGLES = { kind: 'shingles', base: '#6d5b49' };
const TILES = { kind: 'tiles', base: '#b4603e' };

/** Building specs: plain data, one per building asset. */
export function buildingSpecs(civColor) {
  return {
    'building.house.timber_a': {
      w: 6,
      d: 5,
      wallH: 2.8,
      walls: PLASTER,
      gable: PLANKS,
      roof: { axis: 'x', rise: 2.5, overhang: 0.45, mat: THATCH },
      details: [
        { type: 'door', face: '+y', u: 2.5, w: 1.0, h: 1.95 },
        { type: 'window', face: '+y', u: 0.8, v: 1.2, w: 0.7, h: 0.65 },
        { type: 'window', face: '+y', u: 4.4, v: 1.2, w: 0.7, h: 0.65 },
        { type: 'window', face: '+x', u: 1.9, v: 1.2, w: 0.7, h: 0.6 },
      ],
    },
    'building.house.timber_b': {
      w: 5,
      d: 6,
      wallH: 2.7,
      walls: PLANKS,
      roof: { axis: 'y', rise: 2.2, overhang: 0.4, mat: SHINGLES },
      details: [
        { type: 'door', face: '+x', u: 2.4, w: 1.0, h: 1.9 },
        { type: 'window', face: '+x', u: 0.8, v: 1.15, w: 0.65, h: 0.6 },
        { type: 'window', face: '+x', u: 4.5, v: 1.15, w: 0.65, h: 0.6 },
        { type: 'window', face: '+y', u: 2.0, v: 1.15, w: 0.8, h: 0.6, shutters: '#7a4a3a' },
      ],
    },
    'building.house.stone_a': {
      w: 6.5,
      d: 5,
      wallH: 2.6,
      walls: STONE,
      gable: STONE,
      roof: { axis: 'x', rise: 2.6, overhang: 0.5, mat: THATCH },
      chimney: { x: 1.8, y: -0.4, s: 0.65, h: 1.5 },
      details: [
        { type: 'door', face: '+y', u: 2.75, w: 1.0, h: 1.9, arch: true },
        { type: 'window', face: '+y', u: 0.9, v: 1.1, w: 0.6, h: 0.6 },
        { type: 'window', face: '+y', u: 5.0, v: 1.1, w: 0.6, h: 0.6 },
        { type: 'window', face: '+x', u: 2.1, v: 1.1, w: 0.6, h: 0.6, shutters: null },
      ],
    },
    'building.house.stone_b': {
      w: 7,
      d: 5,
      wallH: 2.1,
      plinth: { h: 1.0, mat: STONE_WARM },
      walls: PLASTER_WARM,
      gable: PLANKS_DARK,
      roof: { axis: 'x', rise: 2.3, overhang: 0.45, mat: SHINGLES },
      chimney: { x: -2.2, y: 0.5, s: 0.6, h: 1.3 },
      details: [
        { type: 'door', face: '+x', u: 2.0, w: 1.0, h: 1.0 },
        { type: 'window', face: '+y', u: 1.2, v: 0.8, w: 0.7, h: 0.6 },
        { type: 'window', face: '+y', u: 3.2, v: 0.8, w: 0.7, h: 0.6 },
        { type: 'window', face: '+y', u: 5.2, v: 0.8, w: 0.7, h: 0.6 },
      ],
      ridgePennant: false,
    },
    'building.house.log': {
      w: 5,
      d: 5.5,
      wallH: 2.5,
      walls: LOGS,
      gable: PLANKS_DARK,
      roof: { axis: 'y', rise: 2.3, overhang: 0.45, mat: THATCH },
      details: [
        { type: 'door', face: '+x', u: 2.2, w: 0.95, h: 1.85 },
        { type: 'window', face: '+y', u: 1.9, v: 1.1, w: 0.7, h: 0.55, shutters: '#6a5a3a' },
      ],
    },
    'building.storehouse': {
      w: 12,
      d: 7,
      wallH: 3.3,
      plinth: { h: 0.5, mat: STONE },
      walls: PLANKS,
      gable: PLANKS,
      roof: { axis: 'x', rise: 3.0, overhang: 0.55, mat: SHINGLES },
      civColor,
      details: [
        { type: 'door', face: '+y', u: 4.8, w: 2.4, h: 2.6 },
        { type: 'window', face: '+y', u: 1.5, v: 1.6, w: 0.8, h: 0.6, shutters: null },
        { type: 'window', face: '+y', u: 9.6, v: 1.6, w: 0.8, h: 0.6, shutters: null },
        { type: 'banner', face: '+y', u: 3.4, w: 0.7, h: 1.4 },
        { type: 'banner', face: '+y', u: 7.9, w: 0.7, h: 1.4 },
        { type: 'window', face: '+x', u: 3.1, v: 2.0, w: 0.8, h: 0.6, shutters: null },
      ],
      ridgePennant: true,
    },
    'building.hall': {
      w: 12,
      d: 8,
      wallH: 3.6,
      plinth: { h: 0.6, mat: STONE },
      walls: PLASTER,
      gable: PLASTER,
      roof: { axis: 'x', rise: 3.8, overhang: 0.6, mat: TILES },
      civColor,
      details: [
        { type: 'door', face: '+y', u: 5.25, w: 1.5, h: 2.5, arch: true },
        { type: 'window', face: '+y', u: 1.2, v: 1.3, w: 0.8, h: 1.0 },
        { type: 'window', face: '+y', u: 3.2, v: 1.3, w: 0.8, h: 1.0 },
        { type: 'window', face: '+y', u: 8.0, v: 1.3, w: 0.8, h: 1.0 },
        { type: 'window', face: '+y', u: 10.0, v: 1.3, w: 0.8, h: 1.0 },
        { type: 'banner', face: '+y', u: 4.4, w: 0.6, h: 1.5 },
        { type: 'banner', face: '+y', u: 7.0, w: 0.6, h: 1.5 },
        { type: 'window', face: '+x', u: 2.0, v: 1.3, w: 0.8, h: 1.0 },
        { type: 'window', face: '+x', u: 5.2, v: 1.3, w: 0.8, h: 1.0 },
      ],
      ridgePennant: true,
    },
    // Workshop: an L-shaped composite of two box parts that sort separately.
    'building.workshop.main': {
      w: 7,
      d: 5,
      wallH: 2.8,
      walls: STONE_WARM,
      gable: PLANKS_DARK,
      roof: { axis: 'x', rise: 2.4, overhang: 0.4, mat: SHINGLES },
      chimney: { x: 2.4, y: 0.6, s: 0.7, h: 1.6 },
      details: [
        { type: 'door', face: '+y', u: 4.6, w: 1.2, h: 2.0 },
        { type: 'window', face: '+y', u: 1.0, v: 1.2, w: 0.7, h: 0.6 },
        { type: 'window', face: '+x', u: 2.2, v: 1.2, w: 0.7, h: 0.6, shutters: null },
      ],
    },
    // A small shrine: drawn where a council's plan puts it; it does nothing yet.
    'building.shrine': {
      w: 4,
      d: 3.4,
      wallH: 2.6,
      plinth: { h: 0.5, mat: STONE },
      walls: STONE_WARM,
      gable: STONE_WARM,
      roof: { axis: 'x', rise: 2.2, overhang: 0.4, mat: TILES },
      details: [{ type: 'door', face: '+y', u: 1.5, w: 1.0, h: 1.9, arch: true }],
    },
    'building.workshop.wing': {
      w: 3.5,
      d: 4,
      wallH: 2.4,
      walls: PLANKS_DARK,
      gable: PLANKS_DARK,
      roof: { axis: 'y', rise: 1.6, overhang: 0.35, mat: SHINGLES },
      details: [{ type: 'opening', face: '+x', u: 0.4, w: 3.2, h: 2.0 }],
    },
  };
}

/** All static assets used by the art proof, keyed by asset id. */
export function staticAssetPainters(civColor) {
  const specs = buildingSpecs(civColor);
  const painters = {};
  for (const [id, spec] of Object.entries(specs)) {
    painters[id] = { category: 'building', paint: () => paintBuilding({ id, civColor, ...spec }) };
  }
  painters['building.construction'] = {
    category: 'building',
    paint: () => paintConstruction({ id: 'building.construction', w: 6, d: 5, wallH: 2.6 }),
  };
  painters['building.well'] = { category: 'building', paint: () => paintWell({ id: 'building.well' }) };
  // Town walls (rules version 3): a module per grade and axis, gatehouses and towers.
  for (const axis of ['x', 'y']) {
    for (const grade of WALL_GRADES) {
      painters[`wall.${grade}.${axis}`] = {
        category: 'building',
        paint: () => paintWall({ id: `wall.${grade}.${axis}`, grade, axis }),
      };
    }
    for (const stone of [false, true]) {
      const id = `wall.gate.${stone ? 'stone' : 'timber'}.${axis}`;
      painters[id] = { category: 'building', paint: () => paintGate({ id, axis, stone }) };
    }
  }
  for (const stone of [false, true]) {
    const id = `wall.tower.${stone ? 'stone' : 'timber'}`;
    painters[id] = { category: 'building', paint: () => paintTower({ id, stone }) };
  }
  for (let i = 0; i < 6; i += 1) {
    painters[`nature.oak.${i}`] = {
      category: 'nature',
      paint: () => paintTree({ id: `oak${i}`, kind: 'oak', size: 0.9 + (i % 3) * 0.12 }),
    };
    painters[`nature.pine.${i}`] = {
      category: 'nature',
      paint: () => paintTree({ id: `pine${i}`, kind: 'pine', size: 0.9 + (i % 3) * 0.1 }),
    };
  }
  for (let i = 0; i < 4; i += 1) {
    painters[`nature.bush.${i}`] = { category: 'nature', paint: () => paintBush({ id: `bush${i}` }) };
    painters[`nature.rock.${i}`] = {
      category: 'nature',
      paint: () => paintRock({ id: `rock${i}`, size: 0.8 + i * 0.25 }),
    };
  }
  painters['prop.fence.x'] = { category: 'prop', paint: () => paintFence({ id: 'fx', axis: 'x' }) };
  painters['prop.fence.y'] = { category: 'prop', paint: () => paintFence({ id: 'fy', axis: 'y' }) };
  for (let i = 0; i < 3; i += 1) {
    painters[`crop.wheat.${i}`] = { category: 'nature', paint: () => paintCropRow({ id: `wheat${i}`, crop: 'wheat' }) };
    painters[`crop.cabbage.${i}`] = {
      category: 'nature',
      paint: () => paintCropRow({ id: `cab${i}`, crop: 'cabbage' }),
    };
  }
  for (const kind of ['barrels', 'crates', 'sacks', 'logpile', 'planks', 'stones', 'haystack', 'anvil']) {
    painters[`prop.${kind}`] = { category: 'prop', paint: () => paintProp({ id: `prop-${kind}`, kind }) };
  }
  return painters;
}

/** Contract for the shared citizen designs. */
export function personAssetContract() {
  return {
    id: 'person.villager',
    category: 'person',
    kind: 'dynamic',
    designs: APPEARANCE_COUNT,
    size: { w: FRAME_W, h: FRAME_H },
    anchor: { x: FRAME_W / 2, y: FRAME_H - 8 },
    footprint: { minX: -0.25, minY: -0.25, maxX: 0.25, maxY: 0.25 },
    frames: ANIMATIONS,
    mask: 'clothing (sash, cap or scarf)',
    envoy: { size: { w: FRAME_W, h: ENVOY_FRAME_H }, mask: 'robe, hat and banner' },
  };
}

/**
 * Check the depth rule: a sprite's opaque horizontal extent must not exceed
 * its projected footprint width by more than a tolerance (in screen pixels).
 */
export function checkContract(id, painted, toleranceM = 0.75) {
  const { canvas, anchor, footprint: fp } = painted;
  const ctx = canvas.getContext('2d');
  const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
  let minX = Infinity;
  let maxX = -Infinity;
  for (let y = 0; y < canvas.height; y += 1) {
    for (let x = 0; x < canvas.width; x += 1) {
      if (data[(y * canvas.width + x) * 4 + 3] > 64) {
        if (x < minX) minX = x;
        if (x > maxX) maxX = x;
      }
    }
  }
  const left = (minX - anchor.x) / ART;
  const right = (maxX - anchor.x) / ART;
  const fpLeft = (fp.minX - fp.maxY) * K;
  const fpRight = (fp.maxX - fp.minY) * K;
  const tol = toleranceM * K;
  const ok = left >= fpLeft - tol && right <= fpRight + tol;
  return { id, ok, spriteLeft: left, spriteRight: right, footprintLeft: fpLeft, footprintRight: fpRight };
}
