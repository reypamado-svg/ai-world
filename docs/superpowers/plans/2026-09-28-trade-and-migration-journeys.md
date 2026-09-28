# Trade Shipments and Migration Journeys Plan

**Goal:** Let ratified trade and migration treaties move real goods and real people over explicit, mortal, fallible routes. Nothing changes hands until the travellers physically arrive.

**Builds on:** Treaty ratification (PR #4, `codex/civilization-layer-slice-b2`).

## Rules

1. A `dispatch_shipment` order needs an active **trade** treaty between the sender and recipient. A `dispatch_migration` order needs an active **migration** treaty. Either party to the treaty may dispatch.
2. Every journey follows an explicit route. The route starts at the sender's home settlement, where every traveller must stand. It ends at the recipient's discovered settlement. It uses adjacent, in-bounds tiles that the sender already knows. Travellers move at most one tile per day.
3. Goods leave the sender's storehouse at dispatch. Cargo is limited to 50 units per living carrier. Carriers, migrants, ambassadors, and explorers are exclusive: one person cannot be on two journeys at once.
4. Each day, every active journey resolves in a fixed order using named random streams (`day:{d}:logistics:{id}`):
   - A dead traveller drops out, and death stays permanent. If no traveller is left alive, a shipment is **lost** and a migration **fails**.
   - A roll below 60/10 000 is a hazard. A shipment's cargo is **lost**, and the carriers turn back empty-handed. A migration loses one migrant, chosen by a seeded draw, who dies with cause `travel hazard`.
   - A roll below 900/10 000 is a **delay**, and nobody moves that day.
   - Otherwise every living traveller advances one tile.
5. On reaching the last tile, the journey **arrives**. If the recipient has no living members, the delivery **fails**. Carriers bring the goods home and restore them to the sender's storehouse, and migrants walk home. Otherwise the **receipt** happens:
   - Cargo enters the recipient's inventory. Anything that does not fit is recorded as waste.
   - Migrants move to the recipient's population with their ancestry, skills, and health intact. A pregnancy follows the mother.
6. Carriers walk the route back after delivery, loss, or failure. The shipment is **returned** once every living carrier is home.

## Records and events

- `WorldState.shipments` and `WorldState.migrations` hold the authoritative journey records. They are canonically sorted, serialized, and hashed with the rest of the state.
- `CivilizationState.logistics_notices` is each civilization's private ledger:
  - The sender records its own dispatch, and learns the outcome only when surviving carriers come home.
  - The recipient records only what physically reached it.
  - A migration's origin knows the departure. It never learns the arrival.
- Normalized events:
  - Shipments: `shipment_dispatched`, `shipment_delayed`, `shipment_lost`, `shipment_arrived`, `shipment_received`, `shipment_failed`, `shipment_returned`, `shipment_unfunded`.
  - Migrations: `migration_dispatched`, `migration_delayed`, `migrant_lost`, `migration_arrived`, `migrants_received`, `migration_failed`, `migration_returned`.
  - Each event's actor is the civilization that observes it, or none for events nobody present can report.

## Supporting fix

Birth IDs were allocated from a per-civilization counter that starts where the shared founder counter ends. Civilization 1's first child therefore reused civilization 2's first founder ID. Births now get civilization-scoped IDs (`person:<civ>-<seq>`), which keeps person IDs globally unique so migrants can change allegiance without overwriting anyone.

## Validation

- Focused unit tests cover the logistics module. They check that a treaty is required, route and cargo validation, delay, hazard loss, travel death, failed delivery, and a transfer only after arrival.
- An acceptance scenario ratifies trade and migration treaties through ambassadors. It then shows goods and people actually arriving, checks that each report is private, and replays the history to the same hashes.
- A soak matrix runs seeded treaty-and-journey histories and checks the invariants and replay hash every day.
