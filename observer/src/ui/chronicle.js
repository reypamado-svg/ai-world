// The chronicle panel (O3): the shown day's events in plain sentences, each with where the
// record puts it. Go moves the camera there; Follow selects and follows the person an event
// names, when they are at a settlement that day. Routine bookkeeping (food eaten, orders
// accepted, single tiles changing hands) is hidden unless asked for.

import { civilizationLabel, personLabel, settlementLabel } from '../data/naming.js';

/** A display label for an engine id; the id itself when it is of no known kind. */
export function labelOf(id) {
  if (!id) return null;
  if (id.startsWith('person:')) return personLabel(id);
  if (id.startsWith('settlement:')) return settlementLabel(id);
  if (id.startsWith('civilization:')) return civilizationLabel(id);
  if (id.startsWith('tile:')) return `tile ${id.slice('tile:'.length)}`;
  return id;
}

const PHRASES = {
  person_born: (e) => `${labelOf(e.subject_id) ?? 'A child'} was born to ${labelOf(e.actor_id)}`,
  person_died: (e) =>
    `${labelOf(e.subject_id) ?? 'Someone'} died${e.payload?.cause ? ` (${String(e.payload.cause).replaceAll('_', ' ')})` : ''}`,
  battle_won: (e) => `${labelOf(e.actor_id)} won a battle`,
  settlement_ceded: (e) => `${labelOf(e.payload?.settlement ?? e.subject_id)} was ceded`,
  peace_made: (e) => `${labelOf(e.actor_id)} made peace`,
  house_built: (e) =>
    `${labelOf(e.payload?.settlement)}: ${e.payload?.count ?? 1} ${e.payload?.grade ?? 'house'} built`,
  wall_section_built: (e) =>
    `${labelOf(e.subject_id)}: wall section ${e.payload?.section} raised to ${e.payload?.grade} (${e.payload?.standing} of ${e.payload?.of})`,
  settlement_planned: (e) => `${labelOf(e.subject_id)} was laid out as a ${e.payload?.style} town`,
  defence_set: (e) => `${labelOf(e.subject_id)} set its defence: ${e.payload?.posture}`,
};

function sentenceCase(kind) {
  const words = kind.replaceAll('_', ' ');
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** One event as a plain sentence. */
export function describe(entry) {
  const phrase = PHRASES[entry.kind];
  if (phrase) return phrase(entry);
  const who = labelOf(entry.actor_id);
  const what = labelOf(entry.subject_id);
  return [sentenceCase(entry.kind), who && `· ${who}`, what && what !== who && `· ${what}`].filter(Boolean).join(' ');
}

/** Where the record puts an event, in words. */
export function whereText(place) {
  if (!place) return 'location not recorded';
  const tile = `tile ${place.q},${place.r}`;
  if (place.basis === 'recorded') return tile;
  const via = place.capital ? `at the capital of ${labelOf(place.basis)}` : `by ${labelOf(place.basis)}`;
  return `${tile} · ${via}${place.as_of === 'the day before' ? ', the day before' : ''}`;
}

/** The person an event names, if any (subject first). */
export function personOf(entry) {
  for (const id of [entry.subject_id, entry.actor_id]) if (id?.startsWith('person:')) return id;
  return null;
}

/** The entries to show: all, or all but the routine ones. */
export function visibleEntries(entries, { routine = false } = {}) {
  return routine ? entries : entries.filter((entry) => !entry.routine);
}

export class ChroniclePanel {
  /**
   * @param {HTMLElement} root the panel
   * @param {{ onGo: (q: number, r: number) => void, onFollow: (id: string) => void,
   *   canFollow: (id: string) => boolean }} hooks
   */
  constructor(root, hooks) {
    this.root = root;
    this.list = root.querySelector('#chronicle-list');
    this.routineBox = root.querySelector('#chk-routine');
    this.title = root.querySelector('#chronicle-day');
    this.hooks = hooks;
    this.record = null;
    this.routineBox?.addEventListener('change', () => this.render());
    root.querySelector('#btn-chronicle-close')?.addEventListener('click', () => (root.hidden = true));
  }

  setRecord(record) {
    this.record = record;
    this.render();
  }

  render() {
    const doc = this.root.ownerDocument;
    this.list.replaceChildren();
    if (!this.record) return;
    const shown = visibleEntries(this.record.events, { routine: this.routineBox?.checked });
    const hidden = this.record.events.length - shown.length;
    if (this.title) {
      this.title.textContent = `Engine day ${this.record.day} · ${shown.length} events${hidden ? ` (${hidden} routine hidden)` : ''}`;
    }
    for (const entry of shown) {
      const item = doc.createElement('li');
      item.dataset.kind = entry.kind;
      const text = doc.createElement('div');
      text.className = 'what';
      text.textContent = describe(entry);
      const where = doc.createElement('div');
      where.className = 'where';
      where.textContent = whereText(entry.place);
      item.append(text, where);
      const actions = doc.createElement('div');
      actions.className = 'actions';
      const go = doc.createElement('button');
      go.textContent = 'Go';
      go.className = 'go';
      go.disabled = !entry.place;
      go.addEventListener('click', () => entry.place && this.hooks.onGo(entry.place.q, entry.place.r));
      actions.append(go);
      const person = personOf(entry);
      if (person) {
        const follow = doc.createElement('button');
        follow.textContent = 'Follow';
        follow.className = 'follow';
        follow.dataset.person = person;
        follow.disabled = !this.hooks.canFollow(person);
        if (follow.disabled) follow.title = 'Not at a settlement on this day';
        follow.addEventListener('click', () => this.hooks.onFollow(person));
        actions.append(follow);
      }
      item.append(actions);
      this.list.append(item);
    }
  }
}
