// A run served live by `sovereign-world observe` (O3).
//
// The server answers with the same bytes the static run export holds, so ServerSource is a
// RunSource that asks the server instead of reading files: the manifest, the id table, each
// day's record and people, and the run's terrain, all with the token. `refresh` asks what
// is new: more days (and the ids they brought), or a new history when the run was cut back
// or replaced, after which the page starts again. Every answer is checked against the
// history epoch the source was opened under: a payload from another history (whose person
// numbers mean other people) is refused with `HistoryChanged`, never shown.

import { RunSource } from './run-source.js';
import { TerrainSource } from './terrain-source.js';
import { authorisedFetch } from './auth.js';

const EPOCH = 'X-History-Epoch';

/** Rebuild a day's record from another day's and the changes between them (the server's
 * `changes.apply_changes`, in JS). */
export function applyChanges(before, changes) {
  if (before.day !== changes.from) throw new Error('these changes start from another day');
  const rows = new Map(before.settlements.map((row) => [row.id, row]));
  for (const row of changes.settlements) rows.set(row.id, row);
  const owners = new Map(before.owners.map(([q, r, owner]) => [`${q},${r}`, [q, r, owner]]));
  for (const [q, r] of changes.owners.unset) owners.delete(`${q},${r}`);
  for (const [q, r, owner] of changes.owners.set) owners.set(`${q},${r}`, [q, r, owner]);
  const sorted = [...owners.values()].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  return {
    counts: changes.counts,
    day: changes.day,
    owners: sorted,
    settlements: changes.order.map((id) => rows.get(id)),
    state_hash: changes.state_hash,
    travellers: changes.travellers,
  };
}

/** An answer came from another history of the run than the one being shown. */
export class HistoryChanged extends Error {
  constructor() {
    super('the run was cut back or replaced');
    this.name = 'HistoryChanged';
  }
}

export class ServerSource extends RunSource {
  /**
   * @param {string} api the server's API root, e.g. 'api'
   * @param {{ token: string, fetch?: typeof fetch }} options
   */
  static async open(api, { token, fetch: get } = {}) {
    const authed = authorisedFetch(token, get);
    const base = `${api}/run`;
    const res = await authed(`${base}/manifest`);
    if (!res.ok) throw new Error(`${base}/manifest: HTTP ${res.status}`);
    const manifest = await res.json();
    if (manifest.kind !== 'recorded run') throw new Error('the server is not serving a recorded run');
    // Day records and people files are read through the checked loader.
    const source = new ServerSource(base, manifest, [], (url) => source._bytes(url));
    source.api = api;
    source.authed = authed;
    source.epoch = Number(res.headers.get(EPOCH) ?? manifest.history_epoch);
    source.label = `live run ${manifest.run_id.slice(0, 8)}`;
    source.live = true;
    if ((await source.refresh()).reset) throw new HistoryChanged();
    return source;
  }

  _path(kind, day) {
    return kind === 'people' ? `days/${day}/people` : `days/${day}`;
  }

  /** A day's record: from the last one read and the changes since, when there is one. */
  async record(day) {
    const held = this.held;
    let record;
    if (held && held.day !== day) {
      record = applyChanges(held, await this._json(`${this.base}/days/${day}/changes?from=${held.day}`));
      this.byChanges = (this.byChanges ?? 0) + 1;
    } else {
      record = await super.record(day);
    }
    this.held = record;
    return record;
  }

  /** The server serves every civilization's perspective (O5). */
  get hasPerspectives() {
    return true;
  }

  /** What civilization number `civ` knows on a day (O5), checked against the history. */
  async perspective(day, civ) {
    return this._json(`${this.base}/days/${day}/perspective/${civ}`);
  }

  /** Whether the run is sealed, and by whom (sealed trial). */
  async seal() {
    return this._json(`${this.base}/seal`);
  }

  /** The day's parties on the road, with their routes. */
  async routes(day) {
    return this._json(`${this.base}/days/${day}/routes`);
  }

  /** The run's own terrain, asked of the server with the token. */
  terrain() {
    return TerrainSource.open(`${this.base}/terrain`, { fetch: (url, init) => this._checked(url, init) });
  }

  /** A fetch with the token whose answer must belong to the history being shown. An answer
   * without the header (a refusal or an error) is left to the caller's status check. */
  async _checked(url, init) {
    const res = await this.authed(url, init);
    const seen = res.headers.get(EPOCH);
    if (seen !== null && Number(seen) !== this.epoch) throw new HistoryChanged();
    return res;
  }

  async _bytes(url) {
    const res = await this._checked(url);
    if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
    return new Uint8Array(await res.arrayBuffer());
  }

  async _json(path) {
    const res = await this._checked(path);
    if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
    return res.json();
  }

  /** The server's status: days saved and ready, the history epoch, the people seen, and the
   * runner and its control when the observer started one. */
  async status() {
    this.lastStatus = await this._json(`${this.api}/status`);
    return this.lastStatus;
  }

  /** Whether the observer started a runner for this run (O4). */
  get hasRunner() {
    return Boolean(this.lastStatus?.runner);
  }

  /**
   * Ask the runner to play or pause, change its lookahead, or say which day is shown.
   * @param {{ paused?: boolean, lookahead?: number, shown?: number }} body
   * @returns {Promise<object|null>} the runner and control state, or null without a runner
   */
  control(body) {
    // One at a time: the server may handle two posts in flight in either order, and the last
    // day asked must be the last one the runner is gated on.
    const next = (this._controlling ?? Promise.resolve()).catch(() => {}).then(() => this._control(body));
    this._controlling = next;
    return next;
  }

  async _control(body) {
    const res = await this._checked(`${this.api}/control`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (res.status === 409) return null;
    if (!res.ok) throw new Error(`control: HTTP ${res.status}`);
    const state = await res.json();
    if (this.lastStatus) Object.assign(this.lastStatus, state);
    return state;
  }

  /**
   * What is new since the last look.
   * @returns {Promise<{ reset: boolean, added: number[], status: object }>} `reset` when the
   *   run's history changed: the page must start again.
   */
  async refresh() {
    try {
      const status = await this.status();
      if (status.history_epoch !== this.epoch) return { reset: true, added: [], status };
      if (status.ready === this.days.length && status.people === this.ids.length) {
        return { reset: false, added: [], status };
      }
      const days = await this._json(`${this.base}/days`);
      // Ids only grow while the history stays the same: ask for the ones not yet held.
      const ids = await this._json(`${this.base}/ids?from=${this.ids.length}`);
      this.ids.push(...ids);
      const known = new Set(this.days);
      const added = days.filter((day) => !known.has(day));
      this.days = days;
      this.manifest.days = days;
      return { reset: false, added, status };
    } catch (err) {
      if (err instanceof HistoryChanged) return { reset: true, added: [], status: null };
      throw err;
    }
  }

  /** The newest ready day, or null before the first is ready. */
  get latest() {
    return this.days.length ? this.days[this.days.length - 1] : null;
  }

  /** A saved day's events, each with its place on the map (or none). */
  async chronicle(day) {
    return this._json(`${this.base}/chronicle?day=${day}`);
  }
}
