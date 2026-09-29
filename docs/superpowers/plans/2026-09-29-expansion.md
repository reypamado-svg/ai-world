# Expansion Plan

**Goal:** Civilizations grow their territory the only way the spec allows: by putting real people somewhere. This is slice 3 of `2026-09-29-territory.md`.

## Internal journeys

Founding a settlement, stationing a garrison and relocating people are *internal journeys*. They reuse the machinery of trade and migration journeys:
- explicit routes over known tiles;
- terrain travel costs;
- packed provisions and foraging;
- delays and hazards;
- permanent death on the road.

Because the travellers stay within their own civilization, no treaty is needed. The sender is also the recipient.

- Travellers depart together from one of their civilization's own settlements or garrisons, and the route starts there.
- Leftover provisions go back into the civilization's common store on arrival. The simulation keeps a single store per civilization; per-settlement stores are left for a later supply slice.

## Orders

1. **`found_settlement`** sends 1–16 settlers to a site.
   - **Validation checks only what the civilization knows.** The site must be:
     - land;
     - at least 3 tiles from every settlement it knows about, its own or seen foreign ones;
     - not a tile it has seen owned by another civilization.
   - **On arrival the engine checks the real world.** If another civilization now owns the site, or any settlement now stands within 3 tiles, the founding **fails**. The settlers walk home and the sender records a private notice.
   - Otherwise a new settlement is recorded, and the settlers become its first residents.
   - Settlements are numbered in order per civilization. They are never deleted: an abandoned settlement stays as history, projecting nothing.
2. **`station_garrison`** sends 1–16 people to hold a land tile that is neither a settlement nor seen as foreign-owned.
   - On arrival they form a **garrison** there, unless another civilization now owns the tile; if it does, they walk home.
   - A garrison with living members at its tile projects influence 25. Like a settlement, it is halved when cut off from its capital. It never anchors its tile.
   - A garrison whose members have all died or left is disbanded.
3. **`relocate_group`** (the order that previously did nothing) moves people to one of their own settlements.
   - They join its residents on arrival.
   - Sending garrison members this way recalls them.
4. **Removed:** `request_survey` and the `settlement_radius` decree. They had no mechanics, and a decreed radius would be exactly the painted border the spec forbids.

## Duties

People on an internal journey or in a garrison are committed in the same way as other travellers:
- they cannot be sent elsewhere at the same time;
- they do not work or teach at home;
- they are not residents of a settlement.

A mother with a birth due cannot set out.

## Supply events

Each settlement's cut-off state is remembered from day to day:
- `route_severed` fires when a settlement or garrison loses its connection to the capital;
- `route_restored` fires when it gets it back.

## Records, events and reports

- **Records.** A civilization gains a list of `garrisons`, alongside its `settlements`.
- **Events:**
  - `settlers_dispatched`, `settlement_founded` and `founding_failed`;
  - `garrison_dispatched`, `garrison_stationed`, `garrison_failed` and `garrison_disbanded`;
  - `relocation_dispatched` and `group_relocated`;
  - `route_severed` and `route_restored`.
- **Reports.** Council reports list the civilization's own settlements and garrisons.

## Validation

- **Focused tests:**
  - founding a settlement, including one that grows its own territory;
  - the spacing and foreign-land rules, applied only to what the civilization knows;
  - a founding that fails on arrival and walks home;
  - garrison influence, supply cut-off and disbanding;
  - relocation and recall;
  - the removed commands are rejected.
- **Stress tests:** the territory matrix runs sovereigns that found colonies, so territories really grow, under the same daily invariants as before. Garrisons, and borders meeting, are covered by the focused and acceptance tests.

## As built

- **Reports gained terrain.** Council reports now include `known_terrain`, the terrain of every tile the civilization has observed.
  - Without it a sovereign could not plan a route that avoids water, which is now impassable.
  - Terrain never changes, so reporting it for observed tiles reveals nothing new.
- **Garrison supply.** Garrisoned people eat from the civilization's common store, standing in for supply, but they do no work at home.
- **Stress runs.** The territory stress matrix now gives two of the four civilizations a sovereign that founds a colony at the nearest open site it knows. In the sampled seeds every expanding civilization founded one, and its territory grew. The test sovereign reuses the same four settlers each time, so its second founding is correctly refused, because those people now live in the colony.
