# Travel Provisions Plan

**Goal:** Travellers carry their own food, and forage along the route when it runs out. A civilization's stores then change when a party leaves, which it already knows about, instead of when the party arrives, which it has not observed.

**Builds on:**
- Trade and migration journeys (PR #5).
- Treaty breach and cancellation (PR #6).
- The "Future slice: travel provisions" section of `2026-09-28-trade-and-migration-journeys.md`.

## Rules

1. **Packing.** At dispatch, the engine packs food from the sender's store for every traveller:
   - The days packed are the tiles to travel (the full round trip for a shipment, one way for a migration), plus a delay margin of a quarter of those days, rounded up, and at least 2.
   - An order may ask for `extra_provisions` on top.
2. **Load.** Provisions share each traveller's 50-unit load with any cargo. Validation refuses a journey whose cargo and provisions do not fit, or whose sender lacks the food. That check counts food reserved by earlier orders in the same council, including food shipped as cargo. If the food is gone by the time the order is carried out, the journey is **unfunded**: it never departs, and the sender records a private notice. This now applies to migrations too.
3. **Eating on the road.**
   - From the day they leave until the day they are home or received, travellers do not eat from home stores.
   - Each day that their journey is still under way, each living traveller eats 1 unit from the pack.
   - Arrival and return days are eaten where the party ends up. A received migrant eats at their new home that day, and a returning carrier eats at home.
4. **Foraging.** When the pack cannot feed everyone, each unfed traveller forages on the party's current tile, with its own seeded roll from stream `day:{d}:logistics:forage:{journey_id}`:
   - The chance of success depends on the tile. By terrain: forest 40%, water 35%, grassland 30%, mountain 8%, tundra 10%, desert 5%.
   - Soil adds up to 30% more (soil/1000 × 30%), and a river adds 15%. The best possible tile, a forest with the richest soil and a river, gives 85%.
   - A traveller who finds food eats 1 unit. One who does not gains hunger (+1 nutrition debt), exactly as a person starving at home does.
   - Tiles are never depleted.
5. **What happens to leftovers.**
   - Migrants who are received hand their leftover pack to the recipient's storehouse.
   - Carriers, and refused or failed migrants, bring theirs home to the sender's storehouse.
   - A party that perishes loses its pack.
   - Anything that does not fit in a storehouse is wasted.

## Records and events

- `Journey` gains `provisions_packed` (fixed at dispatch) and `provisions` (what is left), and carriers' capacity counts both.
- New events:
  - `{kind}_provisions_exhausted`: the first day the pack runs out.
  - `{kind}_foraged`: each day that anyone had to forage, with how many found food and how many went hungry.
  - `migration_unfunded`: a migration whose food was gone when the order was carried out.
- Arrival and return notices report leftover provisions handed over or brought home, as part of their `cargo`.

## Deliberate choices

- **Hunger damage heals.** At first this plan kept hunger damage permanent; `2026-09-29-hunger-recovery.md` makes it heal, for everyone, at home and on the road.
- **The engine packs automatically,** so a sovereign cannot under-pack for delays it cannot predict. `extra_provisions` still lets it pack more.
- **No tile depletion,** so saved state does not grow with every foraged tile.

## Validation

- **Focused tests:**
  - How much is packed, and that packing is refused when load or food runs short.
  - Travellers eat from the pack rather than from home, and every person eats exactly once each day.
  - Foraging and hunger once the pack is empty.
  - Leftovers go to the right storehouse.
  - An unfunded migration.
- **Privacy regression test:** the origin's daily food use no longer changes on the day its emigrants arrive.
- **Stress tests:** the seeded journey histories check every day that no pack goes negative or grows, and that goods and people are conserved as before.
