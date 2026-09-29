# Terrain-Aware Travel Plan

**Goal:** Travel time depends on terrain, so that travel, territory, supply and war all share one notion of distance. This is slice 1 of `2026-09-29-territory.md`, chosen as Option A there.

## Rules

1. **Entry costs.** Entering a tile costs, in tenths of a day:
   - grassland 10;
   - forest, desert or tundra 15;
   - mountain 30;
   - water impassable.
2. **Settlements start on reachable land.** The world generator used to check for water near a starting site but never that the site itself was land: 33 of 240 generated starts sat on water tiles, which would have made those civilizations unreachable. It now picks starts only on the largest connected landmass, never on water, so every civilization can reach every other on foot. Across 120 sampled worlds this placed no start on water, and no world failed to generate.
3. **Progress.** Every travelling party — expedition, ambassador, shipment or migration — keeps `travel_progress`:
   - Each day it is not delayed or lost, the party gains 10.
   - When its progress covers the next tile's cost, it pays that cost, steps onto the tile, and carries the remainder forward.
   - A party still moves at most one tile a day. Forest therefore averages 1.5 days and a mountain 3.
   - Delays, hazards and losses are rolled exactly as before, and a delayed day earns no progress.
4. **Routes.**
   - Ambassador, shipment and migration routes run over known tiles, so validation refuses any route that enters water.
   - An expedition may head into unknown tiles. Refusing its route because a hidden tile is water would leak what that tile is. So it departs, and if it reaches water it stops there. It observes the water tile and ends as `blocked`, with an `expedition_blocked` event.
   - Validation still refuses an expedition route through water that the civilization already knows about.
5. **Provisions.** Packing now uses real travel days: the sum of the route's entry costs, rounded up to whole days, outbound plus back for a shipment. The delay margin is unchanged.

## Validation

- Unit tests:
  - entry costs;
  - every start on land that all others can reach on foot;
  - progress stepping over grassland, forest and mountain;
  - routes into water refused;
  - an expedition blocked by water;
  - provisions sized from terrain.
- Existing tests keep their timelines. Their shared test world lays grassland along the route between the two civilizations, so each scenario tests its own rules rather than the terrain it happens to cross.
- The normal and stress suites stay green.
