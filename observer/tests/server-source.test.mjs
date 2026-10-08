// O3 review: a live run's source refuses any answer from another history of the run, and the
// token leaves the address once read (no browser). A fake server serves the committed
// export `fixtures/run-small` under the observer server's paths, with an epoch header the
// tests move.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { HistoryChanged, ServerSource } from '../src/data/server-source.js';
import { keepTokenInLocation, takeTokenFromLocation, tokenFromLocation } from '../src/data/auth.js';

const BASE = fileURLToPath(new URL('./fixtures/run-small', import.meta.url));
const file = (path) => readFile(`${BASE}/${path}`);
const pad = (day) => String(day).padStart(6, '0');

/** A fake observer server; `server.epoch` is the history epoch it answers under, and
 * `server.after` may change it once a given path has been answered. */
function fakeServer() {
  const server = { epoch: 0, after: null, asked: [] };
  server.fetch = async (url, init = {}) => {
    const path = String(url);
    server.asked.push(path);
    assert.equal(new Headers(init.headers).get('Authorization'), 'Bearer t');
    const manifest = JSON.parse(await file('manifest.json'));
    const answer = async () => {
      if (path === 'api/status') {
        const ids = JSON.parse(await file('ids.json'));
        return JSON.stringify({
          history_epoch: server.epoch,
          ready: manifest.days.length,
          people: ids.length,
          ...(server.status ?? {}),
        });
      }
      if (path === 'api/run/manifest') return file('manifest.json');
      if (path === 'api/control') return server.control?.(init) ?? null;
      if (path === 'api/run/seal') return JSON.stringify(server.seal ?? { sealed: false });
      if (path === 'api/run/days') return JSON.stringify(manifest.days);
      if (path.startsWith('api/run/ids?from=')) {
        const from = Number(path.split('=')[1]);
        return JSON.stringify(JSON.parse(await file('ids.json')).slice(from));
      }
      if (path.startsWith('api/run/chronicle?day=')) return JSON.stringify({ day: 0, events: [] });
      if (path.startsWith('api/run/terrain/')) return file(`terrain/${path.slice('api/run/terrain/'.length)}`);
      let m = path.match(/^api\/run\/days\/(\d+)\/people$/);
      if (m) return file(`days/d${pad(m[1])}.people.bin.gz`);
      m = path.match(/^api\/run\/days\/(\d+)\/routes$/);
      if (m) return JSON.stringify({ day: Number(m[1]), parties: [] });
      m = path.match(/^api\/run\/days\/(\d+)$/);
      if (m) return file(`days/d${pad(m[1])}.json`);
      return null;
    };
    const body = await answer();
    if (body instanceof Response) return body;
    const headers = { 'X-History-Epoch': String(server.epoch) };
    if (server.after?.path === path) {
      server.epoch = server.after.epoch;
      server.after = null;
    }
    return body === null ? new Response('no', { status: 404 }) : new Response(body, { headers });
  };
  return server;
}

test('a live source reads the run while its history stays the same', async () => {
  const server = fakeServer();
  const src = await ServerSource.open('api', { token: 't', fetch: server.fetch });
  assert.deepEqual(src.days, [0, 1, 2, 3, 4, 5]);
  const { record, frame } = await src.day(0);
  assert.equal(frame.length, record.counts.at_home);
  assert.equal((await src.routes(1)).day, 1);
  assert.deepEqual((await src.chronicle(1)).events, []);
  assert.equal((await src.terrain()).manifest.export_version, 4);
  assert.equal((await src.refresh()).reset, false);
});

test('a live source asks whether the run is sealed, and by whom', async () => {
  const server = fakeServer();
  const src = await ServerSource.open('api', { token: 't', fetch: server.fetch });
  assert.deepEqual(await src.seal(), { sealed: false });
  server.seal = { sealed: true, fingerprint: 'ab'.repeat(32), signature_valid: true, planned_days: 365 };
  assert.equal((await src.seal()).planned_days, 365);
  server.epoch = 1;
  await assert.rejects(src.seal(), HistoryChanged);
});

test('an answer from another history is refused, not shown', async () => {
  const server = fakeServer();
  const src = await ServerSource.open('api', { token: 't', fetch: server.fetch });
  server.epoch = 1;
  await assert.rejects(src.day(1), HistoryChanged);
  await assert.rejects(src.routes(1), HistoryChanged);
  await assert.rejects(src.chronicle(1), HistoryChanged);
  await assert.rejects(src.terrain(), HistoryChanged);
  assert.equal((await src.refresh()).reset, true);
});

test('a day whose record and people come from two histories is refused', async () => {
  // The run is cut back between the day's record and its people: the people's numbers
  // belong to the new id table, so the day must not be decoded at all.
  const server = fakeServer();
  const src = await ServerSource.open('api', { token: 't', fetch: server.fetch });
  server.after = { path: 'api/run/days/2', epoch: 1 };
  await assert.rejects(src.day(2), HistoryChanged);
  assert.ok(server.asked.includes('api/run/days/2/people'));
});

test('opening a run whose history changes at once is refused', async () => {
  const server = fakeServer();
  server.after = { path: 'api/run/manifest', epoch: 1 };
  await assert.rejects(ServerSource.open('api', { token: 't', fetch: server.fetch }), HistoryChanged);
});

test('the token is taken from the fragment and dropped from the address', () => {
  const calls = [];
  const hist = { state: { kept: true }, replaceState: (state, title, url) => calls.push({ state, url: String(url) }) };
  const loc = { hash: '#token=abc', href: 'http://127.0.0.1:8766/?run=live#token=abc' };
  assert.equal(takeTokenFromLocation(loc, hist), 'abc');
  assert.deepEqual(calls, [{ state: { kept: true }, url: 'http://127.0.0.1:8766/?run=live' }]);
  // No token: nothing read, the address left alone.
  assert.equal(takeTokenFromLocation({ hash: '', href: 'http://x/?run=live' }, hist), null);
  assert.equal(calls.length, 1);
  assert.equal(tokenFromLocation({ hash: '#other=1' }), null);
});

test('a viewing page never asks to steer the runner', async () => {
  const runner = { phase: 'paused', day: 5, last_day: 9, exit_code: null };
  const control = { paused: true, lookahead: 3, shown: 5 };
  const cases = [
    [{ role: 'owner', public: false }, false, true],
    [{ role: 'viewer', public: false }, true, false],
    [{ role: 'owner', public: true }, true, false],
  ];
  for (const [who, viewing, mayControl] of cases) {
    const server = fakeServer();
    server.status = { ...who, runner, control };
    const posted = [];
    server.control = (init) => {
      posted.push(JSON.parse(init.body));
      return JSON.stringify({ runner, control: { ...control, paused: false } });
    };
    const src = await ServerSource.open('api', { token: 't', fetch: server.fetch });
    assert.equal(src.role, who.role);
    assert.equal(src.isPublic, who.public);
    assert.equal(src.viewing, viewing);
    assert.equal(src.mayControl, mayControl);
    const answer = await src.control({ paused: false });
    assert.equal(answer === null, !mayControl, JSON.stringify(who));
    assert.equal(posted.length, mayControl ? 1 : 0);
  }
  // A refusal the page did not expect (the role changed) is no answer, not an error.
  const server = fakeServer();
  server.status = { role: 'owner', public: false, runner, control };
  server.control = () => new Response('{"detail":"viewers may not control the run"}', { status: 403 });
  const src = await ServerSource.open('api', { token: 't', fetch: server.fetch });
  assert.equal(await src.control({ paused: false }), null);
  // No runner: never steered either.
  const plain = await ServerSource.open('api', { token: 't', fetch: fakeServer().fetch });
  assert.equal(plain.role, 'owner');
  assert.equal(plain.mayControl, false);
});

test("a viewer's token is put back in the address, in place", () => {
  const calls = [];
  const hist = { state: null, replaceState: (state, title, url) => calls.push(String(url)) };
  keepTokenInLocation('v'.repeat(40), { href: 'http://x.example/?run=live&day=3' }, hist);
  assert.deepEqual(calls, [`http://x.example/?run=live&day=3#token=${'v'.repeat(40)}`]);
  keepTokenInLocation(null, { href: 'http://x.example/' }, hist);
  assert.equal(calls.length, 1);
});
