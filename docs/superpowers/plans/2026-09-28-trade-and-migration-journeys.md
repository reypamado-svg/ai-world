# Trade Shipments and Migration Journeys Plan

**Goal:** Let ratified trade and migration treaties move real goods and real people over explicit, mortal, fallible routes. Nothing changes hands until the travellers physically arrive.

**Builds on:** Treaty ratification (PR #4, `codex/civilization-layer-slice-b2`).

## Rules

1. A `dispatch_shipment` order needs an active **trade** treaty between the sender and recipient. A `dispatch_migration` order needs an active **migration** treaty. Either party to the treaty may dispatch.
2. Every journey follows an explicit route. The route starts at the sender's home settlement, where every traveller must stand. It ends at the recipient's discovered settlement. It uses adjacent, in-bounds tiles that the sender already knows. Travellers move at most one tile per day.
3. Goods leave the sender's storehouse at dispatch. Cargo is limited to 50 units per carrier, and the goods must still be in store when the order is carried out. If an earlier order in the same council used them up, the shipment is **unfunded**, never departs, and the sender records a private notice.
4. One person holds one duty at a time:
   - Carriers, migrants, ambassadors and explorers are exclusive. Nobody already travelling, and nobody named by an earlier order in the same council, can be sent on another journey.
   - A traveller cannot be named as a worker, teacher or apprentice, and nobody in an active teaching assignment can leave.
   - Carriers and migrants who are away contribute no labour to work orders at home.
   - A mother with a birth due cannot set out, so a child is never born on the road and left behind.
5. Each day, every active journey resolves in a fixed order using named random streams (`day:{d}:logistics:travel:{id}`, and `day:{d}:logistics:hazard-victim:{id}` to choose a hazard's victim):
   - A dead traveller drops out, and death stays permanent. If no traveller is left alive, the party **perishes**, along with any cargo it still carried.
   - On the outbound leg only, a roll below 60/10 000 is a hazard. A shipment's cargo is **lost**, and the carriers turn back empty-handed. A migration loses one migrant, chosen by a seeded draw, who dies with cause `travel hazard`, and the party halts for the day. If that was the last migrant, the party **perishes**.
   - A roll below 900/10 000 is a **delay**, and nobody moves that day.
   - Otherwise every living traveller advances one tile.
6. On reaching the last tile, the journey **arrives**. If the recipient has no living members, the delivery **fails**. Carriers bring the goods home and restore them to the sender's storehouse, and migrants walk home. Otherwise the **receipt** happens:
   - Cargo enters the recipient's inventory. Anything that does not fit is recorded as waste.
   - Migrants move to the recipient's population with their ancestry, skills, and health intact. A pregnancy conceived on the road follows the mother.
7. Carriers walk the route back after delivery, loss, or failure. Return legs can be delayed but meet no hazards. The shipment is **returned** once every living carrier is home. A party that dies out on the way home **perishes**, and any cargo it still carried is gone.
8. Migrants in transit remain members of their origin, eat from its stores, and are subject to its daily mortality. They leave the origin's council roster at departure, and stay off it whatever becomes of them. Only a party that walks home after a failed delivery rejoins it, and a person later sent back by another civilization rejoins it on arrival.
9. Migrants carry their skills. On arrival they join the recipient's practitioner lists, and a capability new to the recipient is recorded as learned by migration. If an emigrant was an origin capability's last practitioner, that capability is forgotten through the existing knowledge-loss rule.

## Records and events

- `WorldState.journeys` holds the authoritative shipment and migration records. They are canonically sorted, serialized, and hashed with the rest of the state.
- `CivilizationState.logistics_notices` is each civilization's private ledger:
  - The sender records its own dispatch, and learns the outcome only when surviving carriers come home.
  - The recipient records only what physically reached it.
  - A migration's origin knows the departure, and no notice or roster change tells it about the arrival.
  - Known limitation: migrants in transit eat from the origin's stores, so the origin's daily food use drops on the arrival day. Closing this needs provisions carried with the party, which is left for a later slice.
- Normalized events:
  - Shipments: `shipment_dispatched`, `shipment_delayed`, `shipment_lost`, `shipment_arrived`, `shipment_received`, `shipment_failed`, `shipment_returned`, `shipment_unfunded`, `shipment_party_perished`.
  - Migrations: `migration_dispatched`, `migration_delayed`, `migrant_lost`, `migration_arrived`, `migrants_received`, `migration_failed`, `migration_returned`, `migration_party_perished`.
  - Each event's actor is the civilization that observes it, or none for events nobody present can report. A death from a travel hazard is a `person_died` event with no actor; its payload names the civilization.

## Supporting fixes

A birth now requires a living mother, so a mother killed on the road, or anywhere else, gives no posthumous birth.

Birth IDs were allocated from a per-civilization counter that starts where the shared founder counter ends. Civilization 1's first child therefore reused civilization 2's first founder ID. Births now get civilization-scoped IDs (`person:<civ>-<seq>`), which keeps person IDs globally unique so migrants can change allegiance without overwriting anyone.

## Validation

- Focused unit tests cover the logistics module. They check that a treaty is required, route and cargo validation, delay, hazard loss, travel death, failed delivery, and a transfer only after arrival.
- An acceptance scenario ratifies trade and migration treaties through ambassadors. It then shows goods and people actually arriving, checks that each report is private, and replays the history to the same hashes.
- A soak matrix runs seeded treaty-and-journey histories and checks the invariants and replay hash every day.

## Future slice: travel provisions

> Implemented in `2026-09-29-travel-provisions.md`. That plan settles the open questions below: packing is automatic, foraging has no depletion, and hunger damage stays permanent for now.

**Why:** migrants in transit currently eat from their origin's stores. The origin's food use therefore drops on the arrival day, which lets it infer an arrival it never observed. Carrying provisions moves that signal to the departure day, which the origin already knows about.

**Proposed rules:**

1. At dispatch, the engine packs provisions from the origin's food store: route length × travellers, plus a margin for delays. For a shipment, it packs for the round trip. An optional order field can request extra.
2. Provisions share the 50-unit load of each traveller with any cargo. Without enough food in store, the order is refused, or the shipment is recorded as unfunded.
3. Travellers stop eating at home from the day they leave. Each day, each living traveller eats 1 unit from the pack.
4. When the pack runs out, the party forages on its current tile, using a seeded roll based on the tile's soil, water and terrain. It gets at most 1 unit per person per day, and tiles are not depleted. Anyone left unfed gains hunger, as people starving at home do.
5. On arrival, migrants' leftover provisions go to the recipient's storehouse. Carriers keep theirs for the way home.
6. New records and events: provisions carried on each journey, `provisions_exhausted`, and `foraged`, each resolved with its own named random stream.

**Costs and open questions:**

- Hunger damage is permanent: nothing ever reduces hunger, and each point adds 0.1% to the daily chance of dying for life. So a long, barren trip permanently shortens travellers' lives. Whether hunger should ever heal is a separate decision affecting the whole world.
- Provisions reduce the goods a caravan can carry, and delays now cost food as well as time.
- Food-poor civilizations will have more journeys refused.
- Foraging without depletion lets a busy route be foraged forever for free. Adding depletion means storing state for every foraged tile.
- Travellers no longer count at home, so the birth check (food on hand per person) and existing seeded histories shift slightly.
- The saved-state format changes again, so histories saved before this slice will fail hash checks. This costs less if it lands before any long-lived world exists.
- The stress tests' conservation check must track food carried in packs.

**Size:** medium. The work sits mostly in `logistics.py`, the food step in `engine.py`, command validation and the tests.
