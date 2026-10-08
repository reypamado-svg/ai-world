// Compact context inspector for a selected citizen or building.

import { CIV_COLORS } from '../render/art/registry.js';
import { civilizationLabel, personLabel } from '../data/naming.js';

const CIV_NAMES = [
  'civilization:0000000001',
  'civilization:0000000002',
  'civilization:0000000003',
  'civilization:0000000004',
];

const PARTY_KINDS = {
  campaign: 'war party',
  expedition: 'expedition',
  migration: 'migrants',
  settlement: 'settlers',
  shipment: 'shipment',
  haul: 'carriers',
  roadwork: 'road crew',
};

export class Inspector {
  /**
   * @param {HTMLElement} el
   * @param {{ lookup: (id: string) => object, occupancy: () => Map, isFollowing: () => boolean,
   *           onClose: () => void, onFollow: () => void, onSelect: (id: string) => void }} hooks
   */
  constructor(el, hooks) {
    this.el = el;
    this.hooks = hooks;
    this.selected = null;
    el.addEventListener('click', (ev) => {
      const act = ev.target.closest('[data-act]')?.dataset.act;
      const pid = ev.target.closest('[data-person]')?.dataset.person;
      if (act === 'close') hooks.onClose();
      if (act === 'follow') hooks.onFollow();
      const party = ev.target.closest('[data-party]')?.dataset.party;
      if (act === 'follow-party' && party) hooks.onFollowParty?.(party);
      if (pid) {
        ev.preventDefault();
        hooks.onSelect(pid);
      }
    });
  }

  show(id) {
    this.selected = id;
    this.list = null;
    this.render();
  }

  /** A list of people to choose from (a crowd dot's cell). */
  showList(ids, title) {
    this.selected = null;
    this.list = { ids, title };
    const shown = ids.slice(0, 60);
    this.el.hidden = false;
    this.el.innerHTML = `
      <header><div><h2>${title}</h2><span class="tag">choose a person</span></div>
      <button class="x" data-act="close" aria-label="Close">×</button></header>
      <ul class="people">${shown
        .map((id) => {
          const o = this.hooks.lookup(id);
          return `<li><a href="#" data-person="${id}">${o?.label ?? id}</a> <span class="muted">${o?.person.record.role ?? ''}, ${o?.person.record.age ?? ''}</span></li>`;
        })
        .join(
          '',
        )}</ul>${ids.length > shown.length ? `<p class="muted">and ${ids.length - shown.length} more</p>` : ''}`;
  }

  /** Travellers on a tile (a recorded run's dot): the parties there, their routes and people. */
  showParties({ q, r, count, civ, parties, civilizations = [] }) {
    this.selected = null;
    this.list = { parties };
    this.el.hidden = false;
    const kinds = PARTY_KINDS;
    const civName = (k) => (civilizations[k] ? civilizationLabel(civilizations[k]) : `civilization ${k + 1}`);
    const rows = parties.length
      ? parties
          .map(
            (p) => `<li><strong>${kinds[p.kind] ?? p.kind.replaceAll('_', ' ')}</strong> of ${civName(p.civilization)}
              <span class="muted">· ${p.people.length} people · route of ${p.route.length} tiles, at step ${Math.min(p.at + 1, p.route.length)}</span>
              <div class="muted">${p.people
                .slice(0, 12)
                .map((id) => `${personLabel(id)} <code>${id}</code>`)
                .join(', ')}${p.people.length > 12 ? ` and ${p.people.length - 12} more` : ''}</div>
              <div class="muted"><code>${p.id}</code></div>
              <button data-act="follow-party" data-party="${p.id}">Follow party</button></li>`,
          )
          .join('')
      : `<li class="muted">The engine counts them here; which parties they belong to is not loaded for this day.</li>`;
    this.el.innerHTML = `
      <header><div><h2>${count} travelling on tile ${q},${r}</h2><span class="tag">${civName(civ)} · recorded</span></div>
      <button class="x" data-act="close" aria-label="Close">×</button></header>
      <ul class="parties">${rows}</ul>`;
  }

  /** A party being followed from day to day: where it is on its route this day. */
  showPartyFollowed(party, day) {
    this.selected = null;
    this.list = { party };
    this.el.hidden = false;
    const kind = PARTY_KINDS[party.kind] ?? party.kind.replaceAll('_', ' ');
    const step = Math.min(party.at + 1, party.route.length);
    this.el.innerHTML = `
      <header><div><h2>Following ${kind}</h2><span class="tag">recorded · day ${day}</span></div>
      <button class="x" data-act="close" aria-label="Close">×</button></header>
      <p class="following">Day ${day} · step ${step} of ${party.route.length} · ${party.people.length} people</p>
      <p class="muted"><code>${party.id}</code></p>`;
  }

  /** The followed party is no longer on the road on this day. */
  showPartyEnded(party, lastSeen, day) {
    this.list = { party };
    this.el.hidden = false;
    const kind = PARTY_KINDS[party.kind] ?? party.kind.replaceAll('_', ' ');
    this.el.innerHTML = `
      <header><div><h2>The ${kind}'s journey ended</h2><span class="tag">recorded</span></div>
      <button class="x" data-act="close" aria-label="Close">×</button></header>
      <p class="ended">The journey ended before day ${day} (last seen on the road on day ${lastSeen}).</p>
      <p class="muted"><code>${party.id}</code></p>`;
  }

  render() {
    const el = this.el;
    if (!this.selected) {
      if (!this.list) el.hidden = true;
      return;
    }
    const o = this.hooks.lookup(this.selected);
    if (!o) {
      el.hidden = true;
      return;
    }
    el.hidden = false;
    if (o.kind === 'person') {
      const rec = o.person.record;
      const civ = o.person.civ;
      const s = o.state ?? {};
      const insideLabel =
        s.inside === 'away'
          ? 'Beyond the sample area'
          : s.inside
            ? (this.hooks.lookup(s.inside)?.label ?? s.inside)
            : null;
      const skills =
        Object.entries(rec.skills)
          .map(([k, v]) => `${k} ${v}`)
          .join(', ') || 'Not recorded';
      const following = this.hooks.isFollowing();
      el.innerHTML = `
        <header><div><h2>${rec.label}</h2><span class="tag">observer-assigned name · ${rec.provenance ?? 'sample'}</span></div>
        <button class="x" data-act="close" aria-label="Close">×</button></header>
        <dl>
          <dt>ID</dt><dd class="mono">${o.id}</dd>
          <dt>Civilization</dt><dd><span class="swatch"></span>${civilizationLabel(CIV_NAMES[civ])} <span class="tag">${rec.provenance ?? 'sample'}</span></dd>
          <dt>Age</dt><dd>${rec.age} (${rec.sex})</dd>
          <dt>Household</dt><dd class="muted">${rec.household}</dd>
          <dt>Family</dt><dd class="muted">Not recorded in this ${rec.provenance ?? 'sample'}</dd>
          <dt>Occupation</dt><dd>${rec.role}</dd>
          <dt>Skills</dt><dd>${skills}</dd>
          <dt>Health</dt><dd>${rec.health}</dd>
          <dt>Activity</dt><dd>${s.activity ?? '—'} <span class="tag approx">visual approximation</span></dd>
          <dt>Destination</dt><dd>${s.destination ?? (s.moving ? 'Not recorded' : '—')}</dd>
          <dt>Where</dt><dd>${insideLabel ? `Inside: ${insideLabel}` : 'Outdoors'}</dd>
          <dt>Life events</dt><dd>${rec.events.join('<br>')}</dd>
        </dl>
        <footer><button data-act="follow" class="${following ? 'on' : ''}">${following ? 'Following' : 'Follow person'}</button></footer>`;
      // Set through the CSSOM, not a style attribute: the server's CSP refuses inline styles.
      el.querySelector('.swatch').style.background = CIV_COLORS[civ];
    } else {
      const occ = this.hooks.occupancy().get(o.partOf ?? o.id) ?? [];
      el.innerHTML = `
        <header><div><h2>${o.label}</h2><span class="tag">sample building</span></div>
        <button class="x" data-act="close" aria-label="Close">×</button></header>
        <dl>
          <dt>ID</dt><dd class="mono">${o.id}</dd>
          <dt>Asset</dt><dd class="mono">${o.asset ?? o.kind}</dd>
          <dt>Inside now</dt><dd>${occ.length ? occ.map((id) => `<a href="#" data-person="${id}">${this.hooks.lookup(id).label}</a>`).join(', ') : 'Nobody'}</dd>
        </dl>`;
    }
  }
}
