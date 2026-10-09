// Observer-assigned display labels.
//
// The simulation records no names. These labels are derived deterministically
// from persistent IDs for display only and are always shown as
// "observer-assigned". The ID remains the identity. Kept separate from
// rendering so a future simulation feature can supply authoritative names.

import { hashString } from '../sim/rng.js';

const GIVEN_A = ['Ari', 'Bel', 'Cas', 'Dov', 'Eda', 'Fen', 'Gal', 'Hes', 'Ivo', 'Jor', 'Kel', 'Lio', 'Mar', 'Nel', 'Oda', 'Per', 'Ros', 'Sel', 'Tam', 'Ulf', 'Vey', 'Wen', 'Yra', 'Zed'];
const GIVEN_B = ['an', 'en', 'ia', 'or', 'wyn', 'ric', 'ela', 'is', 'ard', 'ine', 'o', 'eth'];
const PLACE_A = ['Ash', 'Brook', 'Cold', 'Elm', 'Fair', 'Green', 'High', 'Mill', 'Oak', 'Red', 'Stone', 'Thorn', 'West', 'Wild'];
const PLACE_B = ['ford', 'field', 'stead', 'holm', 'wick', 'by', 'ton', 'mere', 'dale', 'gate'];

function pick(list, h, shift) {
  return list[(h >>> shift) % list.length];
}

export function personLabel(personId) {
  const h = hashString(`person-label:${personId}`);
  return pick(GIVEN_A, h, 0) + pick(GIVEN_B, h, 7);
}

export function settlementLabel(settlementId) {
  const h = hashString(`settlement-label:${settlementId}`);
  return pick(PLACE_A, h, 0) + pick(PLACE_B, h, 9);
}

export function civilizationLabel(civilizationId) {
  const h = hashString(`civ-label:${civilizationId}`);
  return `${pick(PLACE_A, h, 3)}${pick(PLACE_B, h, 11)} realm`;
}
