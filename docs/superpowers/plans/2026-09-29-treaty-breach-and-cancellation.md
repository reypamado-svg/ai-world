# Treaty Breach and Cancellation Plan

**Goal:** Let an agreement end, lawfully or not, with the same delivery evidence that brought it into force. Nothing that has already happened is erased: an ended treaty remains a historical record.

**Builds on:**
- Treaty ratification (PR #4).
- Trade and migration journeys (PR #5).

## Two ways a treaty ends

1. **Cancellation (lawful, with notice).**
   - Either party may send a `cancel_treaty` order: an ambassador message carrying a cancellation of a treaty that is currently in force.
   - The treaty stays in force for both parties while the notice travels. It ends only when the notice physically reaches the other party. That mirrors activation, which needs the acceptance to reach the proposer.
   - A lost or delayed notice ends nothing. A party cannot have two cancellation notices for the same treaty travelling at once.
2. **Breach (repudiation without notice).**
   - Either party may issue a `repudiate_treaty` order. It needs no ambassador, and the treaty ends immediately, recorded as **breached** by that party.
   - The injured party is not told. It learns of the breach only by observing its effects (see *Consequences*), or if the repudiating party later sends a message.
   - Later slices can call the same breach rule from other acts. For example, attacking a peace partner will breach the peace treaty.

## Consequences

- **No new journeys.** Once a treaty has ended, neither party can dispatch new shipments or migrations under it. Command validation refuses them.
- **Turned away at the border.**
  - A journey that set out while the treaty was in force, but arrives after it ended, is **refused** at the border. Goods are not received and migrants do not change allegiance.
  - Carriers bring their cargo home, and it is restored to the sender's storehouse. Refused migrants walk home and rejoin their origin's roster.
  - This applies in both directions, including to journeys sent by the breaching party itself.
- **Private knowledge.**
  - The recipient records a private notice that it turned a party away.
  - The sender learns of the refusal only when its travellers come home; the returned notice reports outcome `refused`.
  - Every logistics notice now names its treaty, so a sovereign can connect a refusal to the agreement that ended.
- **History is kept.**
  - Ended treaties stay in `active_treaties`, marked with the day they ended, how they ended (`cancelled` or `breached`), and which party ended them.
  - Journeys that referred to them remain valid history.

## Records and events

- `ActiveTreaty` gains `ended_day`, `end_kind` (`cancelled` or `breached`) and `ended_by`. A treaty is *in force* while `ended_day` is empty.
- `DiplomaticMessage` gains `cancellation_of`. A message carries at most one of: an offer, an acceptance, or a cancellation.
- `JourneyOutcome` gains `refused`. Carriers may still be carrying their cargo while it is `refused`, as they may while it is `failed`.
- New events:

  | Event | Actor | Payload |
  |---|---|---|
  | `treaty_cancelled` | the party that received the notice | the party that cancelled |
  | `treaty_breached` | the breaching party | the injured party |
  | `shipment_refused` / `migration_refused` | the recipient that turned the party away | — |

- Command errors:
  - `unknown_treaty`: the treaty is not in force, or this civilization is not a party to it.
  - `cancellation_pending`: a notice for that treaty is already travelling.

## Deliberately out of scope

- **No lasting penalties.** Trust, reputation or grievance from a breach will belong to foreign relations or war. This slice records the breach so those rules can read it later.
- **No new treaty terms.** There are no tribute schedules or quotas yet, so there is nothing to breach by under-delivery. Breach is repudiation only.
- **No notice periods.** Cancellation takes effect on delivery.

## Validation

- **Focused tests:**
  - A cancellation ends the treaty on the day it is delivered, not before.
  - A lost notice ends nothing.
  - Repudiation ends the treaty immediately.
  - Dispatch is refused after a treaty ends.
  - Journeys that arrive after the end are refused, their cargo comes home, and refused migrants rejoin their roster.
  - Notices stay private, and invalid cancellations are rejected.
- **Acceptance scenario:** ratify a trade treaty through ambassadors, deliver a shipment, cancel through an ambassador, and show a later shipment being turned away. Replay the whole history to the same hashes.
- **Stress tests:** the seeded journey matrix also cancels or repudiates treaties partway through. It checks, every day, that goods and people are conserved, and re-simulates each seed to identical hashes.
