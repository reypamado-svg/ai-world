// O3 C4: the chronicle's sentences and places, without a browser.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { describe, labelOf, personOf, visibleEntries, whereText } from '../src/ui/chronicle.js';
import { personLabel, settlementLabel } from '../src/data/naming.js';

const entry = (kind, actor, subject, payload = {}, extra = {}) => ({
  kind,
  actor_id: actor,
  subject_id: subject,
  payload,
  routine: false,
  place: null,
  ...extra,
});

test('events read as plain sentences with observer-assigned names', () => {
  const settlement = 'settlement:0001-0001';
  assert.equal(
    describe(
      entry('wall_section_built', 'civilization:0001', settlement, {
        section: 3,
        grade: 'palisade',
        standing: 4,
        of: 10,
      }),
    ),
    `${settlementLabel(settlement)}: wall section 3 raised to palisade (4 of 10)`,
  );
  assert.equal(
    describe(entry('person_died', 'civilization:0001', 'person:0000000007', { cause: 'old_age' })),
    `${personLabel('person:0000000007')} died (old age)`,
  );
  // A kind with no phrase of its own still reads, with whom it concerns.
  assert.equal(
    describe(entry('siege_lifted', null, 'siege:000010:journey:x')),
    'Siege lifted · siege:000010:journey:x',
  );
  assert.equal(labelOf('tile:3,4'), 'tile 3,4');
  assert.equal(labelOf(null), null);
});

test('places say how they were found', () => {
  assert.equal(whereText(null), 'location not recorded');
  assert.equal(whereText({ q: 3, r: 4, basis: 'recorded' }), 'tile 3,4');
  const capital = whereText({ q: 1, r: 2, basis: 'civilization:0002', as_of: 'that day', capital: true });
  assert.match(capital, /^tile 1,2 · at the capital of .+ realm$/);
  const before = whereText({ q: 1, r: 2, basis: 'person:0000000003', as_of: 'the day before' });
  assert.equal(before, `tile 1,2 · by ${personLabel('person:0000000003')}, the day before`);
});

test('routine events are hidden unless asked for, and people are found to follow', () => {
  const events = [entry('food_consumed', 'c', 's', {}, { routine: true }), entry('house_built', 'c', 'j')];
  assert.deepEqual(
    visibleEntries(events).map((e) => e.kind),
    ['house_built'],
  );
  assert.equal(visibleEntries(events, { routine: true }).length, 2);
  assert.equal(personOf(entry('person_born', 'person:0000000001', 'person:0001-0002')), 'person:0001-0002');
  assert.equal(personOf(entry('person_born', 'person:0000000001', null)), 'person:0000000001');
  assert.equal(personOf(entry('house_built', 'civilization:0001', 'job')), null);
});
