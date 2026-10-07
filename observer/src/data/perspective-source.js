// One civilization's view of the world, as its council knows it (O5).
//
// The server (or an export made with `--perspectives`) gives, for a day and a civilization,
// a record built from that civilization's council report alone. This turns it into what the
// page draws: its own settlements in the export's row shape, a PeopleFrame of its people as
// the council counts them (the report's notable people named, the rest counted but not
// identified), and the overlay record (borders it knows, its own parties on the road). The
// foreign settlements, ruins, sites, roads and bridges it knows of are drawn by the
// perspective layer straight from the record.

import { PeopleFrame } from './population.js';
import { civilizationLabel, settlementLabel } from './naming.js';
import { hashString } from '../sim/rng.js';

export const COUNCIL_DAYS = 30;

/** The day of the last regular council on or before `day`: councils sit on day 0 and every
 * `interval` days after (30 for worlds made before the interval could be chosen). */
export function councilDay(day, interval = COUNCIL_DAYS) {
  return day - (day % interval);
}

/** Which day's perspective to show for `day`: the day itself, or with asOf 'council' its last
 * council day when the export holds that day. `councilMissing` says it does not, so the shown
 * day stands in (a static export made with a stride may skip council days). */
export function perspectiveDayFor(day, days, asOf, interval = COUNCIL_DAYS) {
  if (asOf !== 'council') return { day, councilMissing: false };
  const council = councilDay(day, interval);
  return days.includes(council) ? { day: council, councilMissing: false } : { day, councilMissing: true };
}

/** A PeopleFrame whose rows are the council's count of its people: the named ones carry what
 * the report says of them; the others say they are counted, not identified. */
class CouncilFrame extends PeopleFrame {
  constructor(n, settlements, options, day) {
    super(n, settlements, options);
    this.day = day;
    this.named = new Array(n).fill(null);
  }

  record(i) {
    const rec = super.record(i);
    const named = this.named[i];
    const s = this.settlements[this.settlement[i]];
    if (named) {
      rec.skills = named.skills;
      rec.events = [
        `Named in its council's report for day ${this.day}${named.duty_label ? ` (duty ${named.duty_label})` : ''}`,
      ];
    } else {
      rec.label = 'Counted, not named';
      rec.events = [
        `One of ${s.residents} people the council counts at ${s.label}; not identified in its report, so age, sex and duty here are placeholders`,
      ];
    }
    return rec;
  }
}

/**
 * A perspective record as the page draws it.
 * @param {object} perspective the server's (or export's) perspective record
 * @param {string[]} civilizations the run's civilization ids, in manifest order
 * @returns {{ day: number, record: object, frame: PeopleFrame, perspective: object }}
 */
export function perspectiveDay(perspective, civilizations) {
  const civIndex = new Map(civilizations.map((id, k) => [id, k]));
  const civ = (id) => civIndex.get(id);
  const rows = perspective.settlements.map((s) => ({
    ...s,
    civilization: civ(s.civilization),
  }));
  const settlements = rows.map((s) => ({
    id: s.id,
    civ: s.civilization,
    q: s.q,
    r: s.r,
    label: settlementLabel(s.id),
    capital: s.capital,
    rank: s.rank,
    houses: s.houses,
    slots: s.slots,
    residents: s.residents,
    houseJobs: s.house_jobs,
    institutions: s.institutions,
    plan: s.plan ?? null,
    walls: s.walls ?? null,
    defence: s.defence ?? null,
  }));
  const named = new Map();
  for (const person of perspective.people.notable) {
    if (person.settlement === null) continue;
    if (!named.has(person.settlement)) named.set(person.settlement, []);
    named.get(person.settlement).push(person);
  }
  const sizes = settlements.map((s) => Math.max(s.residents, named.get(s.id)?.length ?? 0));
  const total = sizes.reduce((sum, n) => sum + n, 0);
  const names = [];
  const frame = new CouncilFrame(
    total,
    settlements,
    {
      names,
      provenance: 'council report',
      note: `What the council of ${civilizationLabel(perspective.civilization)} knows on day ${perspective.day}`,
    },
    perspective.day,
  );
  let row = 0;
  settlements.forEach((s, k) => {
    const people = named.get(s.id) ?? [];
    for (let j = 0; j < sizes[k]; j += 1) {
      const person = people[j] ?? null;
      const id = person ? person.id : `council:${s.id}:${j}`;
      frame.id[row] = names.length;
      names.push(id);
      frame.civ[row] = s.civ;
      frame.q[row] = s.q;
      frame.r[row] = s.r;
      frame.settlement[row] = k;
      frame.sex[row] = person ? person.sex : j % 2;
      frame.age[row] = person ? Math.min(255, person.age) : 30;
      frame.health[row] = person ? person.health : 100;
      frame.duty[row] = person ? person.duty : 2; // a farmer, for someone not named
      frame.appearance[row] = 2 * (hashString(id) % 5) + frame.sex[row];
      frame.house[row] = Math.floor(j / 5);
      frame.named[row] = person;
      row += 1;
    }
  });
  frame.index();
  const record = {
    day: perspective.day,
    counts: perspective.counts,
    settlements: rows,
    travellers: perspective.travellers.map(([q, r, id, n]) => [q, r, civ(id), n]),
    owners: perspective.owners.filter(([, , id]) => civIndex.has(id)).map(([q, r, id]) => [q, r, civ(id)]),
  };
  return { day: perspective.day, record, frame, perspective };
}

/** One council news item as a plain sentence, with observer-assigned names. */
export function describeNews(item) {
  const realm = (id) => civilizationLabel(id);
  switch (item.kind) {
    case 'battle':
      return `${item.won ? 'Won' : 'Lost'} a battle against ${realm(item.enemy)}: ${item.own_fighters} of ours fought, ${item.own_dead} died, ${item.own_wounded} wounded${item.own_captured ? `, ${item.own_captured} taken` : ''}; about ${item.enemy_fighters_estimate} of theirs, about ${item.enemy_losses_estimate} lost.`;
    case 'notice':
      return `Word on a ${item.notice.replaceAll('_', ' ')} with ${realm(item.counterpart)}${item.people ? ` (${item.people} people)` : ''}${item.outcome ? `: ${item.outcome.replaceAll('_', ' ')}` : ''}.`;
    case 'caught_spy':
      return `Caught a spy sent by ${realm(item.sender)} at ${settlementLabel(item.settlement)}.`;
    case 'spy_report':
      return `${item.by_courier ? 'A courier' : 'Our spies'} brought word of a settlement of ${realm(item.civilization)} as seen on day ${item.seen_day}: about ${item.residents} people, ${item.fighters} able to fight${item.wall_grade ? `, ${item.wall_grade} walls` : ''}${item.towers ? `, ${item.towers} towers` : ''}.`;
    case 'message':
      return `A message from ${realm(item.sender)}: “${item.text}”`;
    case 'treaty':
      return `A ${item.treaty} treaty with ${realm(item.counterparty)} came into force.`;
    case 'treaty_ended':
      return `The ${item.treaty} treaty with ${realm(item.counterparty)} ended${item.end ? ` (${item.end.replaceAll('_', ' ')})` : ''}.`;
    case 'crisis':
      return item.text;
    default:
      return item.kind;
  }
}
