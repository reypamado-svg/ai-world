// O2 C4: a recorded run's export read into PeopleFrames (no browser). The committed
// fixture is the export of the engine's format-1 test run (tests/fixtures/format-one).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { RunSource } from '../src/data/run-source.js';

const BASE = fileURLToPath(new URL('./fixtures/run-small', import.meta.url));
const load = async (path) => new Uint8Array(await readFile(path));

test('every exported day fills a frame that matches its record', async () => {
  const run = await RunSource.open(BASE, { load });
  assert.deepEqual(run.days, [0, 1, 2, 3, 4, 5]);
  for (const d of run.days) {
    const { record, frame } = await run.day(d);
    assert.equal(frame.length, record.counts.at_home, `day ${d}`);
    record.settlements.forEach((s, k) => assert.equal(frame.residents(k), s.residents, `day ${d} ${s.id}`));
    const seen = new Set();
    for (let i = 0; i < frame.length; i += 1) {
      const id = frame.idOf(i);
      assert.match(id, /^person:/);
      assert.ok(!seen.has(id), `${id} twice`);
      seen.add(id);
      assert.equal(frame.indexOf(id), i);
      assert.equal(frame.appearance[i] % 2, frame.sex[i]);
    }
    const rec = frame.record(0);
    assert.equal(rec.provenance, 'recorded run');
    assert.equal(rec.id, frame.idOf(0));
  }
});

test('the same person keeps their id and design from day to day', async () => {
  const run = await RunSource.open(BASE, { load });
  const a = (await run.day(0)).frame;
  const b = (await run.day(5)).frame;
  let same = 0;
  for (let i = 0; i < a.length; i += 1) {
    const j = b.indexOf(a.idOf(i));
    if (j < 0) continue;
    same += 1;
    assert.equal(b.appearance[j], a.appearance[i]);
    assert.equal(b.sex[j], a.sex[i]);
  }
  assert.ok(same > 0);
  assert.equal(run.nearestDay(3), 3);
  assert.equal(run.nearestDay(99), 5);
});
