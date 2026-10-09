// Slice H: a public observer (`sovereign-world observe --public`) watched through a shared link.
// The page runs under the server's strict Content-Security-Policy (Pixi through its own no-eval
// polyfills), a viewer's link is viewing only and keeps its token for a reload, the owner's own
// window on a public observer is viewing only too, nobody may steer, and a client asking too
// often is held back and then served again.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { cp, mkdtemp, readFile, readdir, rm } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const repo = fileURLToPath(new URL('../..', import.meta.url));
const cli = join(repo, '.venv', 'bin', 'sovereign-world');
const fixture = join(repo, 'tests', 'fixtures', 'format-one');
const OWNER = 'owner-browser-token-0123456789abcdef';
const VIEWER = 'viewer-browser-token-0123456789abcdef';
const DAYS = 5;

let browser;
let observer;
let dir;
let port;
let logs = '';
let pristine;

function freePort() {
  return new Promise((resolve) => {
    const probe = createServer().listen(0, '127.0.0.1', () => {
      const { port: free } = probe.address();
      probe.close(() => resolve(free));
    });
  });
}

async function api(path, { token = VIEWER, method = 'GET', body, headers = {} } = {}) {
  return fetch(`http://127.0.0.1:${port}${path}`, {
    method,
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json', ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

async function until(check, ms, what) {
  const end = Date.now() + ms;
  for (;;) {
    const value = await check();
    if (value) return value;
    if (Date.now() > end) throw new Error(`timed out waiting for ${what}\n${logs}`);
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
}

async function files(root) {
  const out = {};
  for (const name of (await readdir(root)).sort()) out[name] = (await readFile(join(root, name))).toString('base64');
  return out;
}

before(async () => {
  dir = await mkdtemp(join(tmpdir(), 'viewer-'));
  await cp(fixture, join(dir, 'run'), { recursive: true });
  pristine = await files(join(dir, 'run'));
  port = await freePort();
  observer = spawn(cli, ['observe', join(dir, 'run'), '--port', String(port), '--public'], {
    env: { ...process.env, SOVEREIGN_WORLD_OBSERVER_TOKEN: OWNER, SOVEREIGN_WORLD_VIEWER_TOKEN: VIEWER },
  });
  observer.stdout.on('data', (d) => (logs += d));
  observer.stderr.on('data', (d) => (logs += d));
  await until(
    async () => {
      try {
        const res = await api('/api/status');
        return res.ok && (await res.json()).ready === DAYS + 1;
      } catch {
        return false;
      }
    },
    60000,
    'the public observer to walk the run',
  );
  browser = await chromium.launch({
    args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'],
  });
});

after(async () => {
  await browser?.close();
  if (observer && observer.exitCode === null && observer.signalCode === null) {
    const exited = new Promise((resolve) => observer.once('exit', resolve));
    observer.kill();
    await exited;
  }
  if (dir) {
    assert.deepEqual(await files(join(dir, 'run')), pristine, 'the run was written to');
    await rm(dir, { recursive: true, force: true });
  }
});

/** A page that records its errors and every CSP violation. */
async function watched() {
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.addInitScript(() => {
    window.__violations = [];
    document.addEventListener('securitypolicyviolation', (e) =>
      window.__violations.push({ directive: e.violatedDirective, blocked: e.blockedURI }),
    );
  });
  return { page, errors };
}

async function ready(page) {
  await page.waitForFunction(() => window.__observer?.ready || window.__observerError, null, { timeout: 600000 });
  const failed = await page.evaluate(() => window.__observerError ?? null);
  assert.equal(failed, null, `${failed}\n${logs}`);
}

/** Only the eval probes (Pixi's, and the page's own check) may be refused: nothing else. */
async function onlyEvalProbes(page) {
  const violations = await page.evaluate(() => window.__violations);
  for (const v of violations) assert.equal(v.blocked, 'eval', JSON.stringify(v));
  return violations;
}

test('a shared link is viewing only, keeps its token for a reload, and steps a day', async () => {
  const { page, errors } = await watched();
  try {
    const answer = await page.goto(`http://127.0.0.1:${port}/?run=live#token=${VIEWER}`);
    assert.match(answer.headers()['content-security-policy'], /script-src 'self'/);
    await ready(page);
    assert.equal(await page.locator('#role-chip').textContent(), 'VIEWING ONLY · shared');
    assert.equal(await page.locator('#role-chip').isVisible(), true);
    assert.equal(await page.locator('#lookahead-group').isVisible(), false);
    assert.equal(await page.evaluate(() => location.hash), `#token=${VIEWER}`);
    // It opens on the newest day, following it.
    assert.equal(await page.evaluate(() => window.__observer.runInfo().day), DAYS);
    const live = await page.locator('#live-chip').textContent();
    assert.match(live, /following newest|playing/);
    await page.click('#btn-day-prev');
    await page.waitForFunction((d) => window.__observer.runInfo().day === d, DAYS - 1);
    // The crowd drawn as still particles and as dots: Pixi's particle updates, which it would
    // otherwise build with eval, run through the no-eval polyfill.
    const drawn = await page.evaluate(async () => {
      const o = window.__observer;
      const frames = () => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
      o.setTime(1800);
      o.setCrowdBudget(1);
      const id = o.crowdBusiest();
      o.viewPerson(id, 1.0);
      await frames();
      const near = o.crowdCounts();
      o.viewPerson(id, 0.2);
      await frames();
      const far = o.crowdCounts();
      o.setCrowdBudget(null);
      return { simplified: near.visibleSimplified, dots: far.dots };
    });
    assert.ok(drawn.simplified > 0, JSON.stringify(drawn));
    assert.ok(drawn.dots > 0, JSON.stringify(drawn));
    await onlyEvalProbes(page);
    // A reload (or a bookmark) still opens: the link is the viewer's credential.
    await page.reload();
    await ready(page);
    assert.equal(await page.evaluate(() => location.hash), `#token=${VIEWER}`);
    assert.equal(await page.locator('#role-chip').textContent(), 'VIEWING ONLY · shared');
    await onlyEvalProbes(page);
    assert.deepEqual(errors, []);
  } finally {
    await page.close();
  }
});

test("the owner's own window on a shared observer is viewing only and drops its token", async () => {
  const { page, errors } = await watched();
  try {
    await page.goto(`http://127.0.0.1:${port}/?run=live#token=${OWNER}`);
    await ready(page);
    assert.equal(await page.locator('#role-chip').textContent(), 'VIEWING ONLY · shared');
    assert.equal(await page.evaluate(() => location.hash), '');
    await onlyEvalProbes(page);
    assert.deepEqual(errors, []);
  } finally {
    await page.close();
  }
});

test('nobody may steer a public observer, and its answers carry the security headers', async () => {
  for (const token of [VIEWER, OWNER]) {
    const posted = await api('/api/control', { token, method: 'POST', body: { paused: false } });
    assert.equal(posted.status, 403);
    assert.match((await posted.json()).detail, /viewing only/);
    assert.equal((await api('/api/control', { token })).status, 403);
  }
  const status = await (await api('/api/status')).json();
  assert.equal(status.role, 'viewer');
  assert.equal(status.public, true);
  const page = await api('/');
  assert.equal(page.status, 200);
  assert.equal(page.headers.get('x-frame-options'), 'DENY');
  assert.equal(page.headers.get('x-content-type-options'), 'nosniff');
  assert.equal(page.headers.get('referrer-policy'), 'no-referrer');
  assert.equal(page.headers.get('server'), null);
  // The owner's token through the tunnel is refused like a wrong one.
  const outside = await api('/api/status', { token: OWNER, headers: { 'Cf-Ray': '8a1b2c3d4e5f-DXB' } });
  assert.equal(outside.status, 401);
  assert.equal((await api('/node_modules/playwright/package.json')).status, 404);
});

test('a client asking too often is held back, then served again', async () => {
  // Through the tunnel, each visitor is told apart by the address the tunnel forwards.
  const visitor = { 'X-Forwarded-For': '203.0.113.9' };
  let refused = null;
  for (let i = 0; i < 800 && !refused; i += 50) {
    const answers = await Promise.all(Array.from({ length: 50 }, () => api('/api/run/days', { headers: visitor })));
    refused = answers.find((res) => res.status === 429) ?? null;
    for (const res of answers) await res.arrayBuffer();
  }
  assert.ok(refused, 'never held back');
  const wait = Number(refused.headers.get('retry-after'));
  assert.ok(wait >= 1 && wait <= 5, String(wait));
  // Another visitor is not held back by the first.
  assert.equal((await api('/api/status', { headers: { 'X-Forwarded-For': '203.0.113.10' } })).status, 200);
  await new Promise((resolve) => setTimeout(resolve, wait * 1000 + 200));
  assert.equal((await api('/api/run/days', { headers: visitor })).status, 200);
});
