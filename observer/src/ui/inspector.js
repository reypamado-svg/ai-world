// Compact context inspector for a selected citizen or building.

import { CIV_COLORS } from '../render/art/registry.js';
import { civilizationLabel } from '../data/naming.js';

const CIV_NAMES = [
  'civilization:0000000001',
  'civilization:0000000002',
  'civilization:0000000003',
  'civilization:0000000004',
];

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
          <dt>Civilization</dt><dd><span class="swatch" style="background:${CIV_COLORS[civ]}"></span>${civilizationLabel(CIV_NAMES[civ])} <span class="tag">${rec.provenance ?? 'sample'}</span></dd>
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
