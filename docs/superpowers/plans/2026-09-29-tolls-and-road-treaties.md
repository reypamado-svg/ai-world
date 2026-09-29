# Tolls and Road Treaties Plan

**Goal:** Two follow-ups to roads (slice 4 of `2026-09-29-territory.md`):

- a civilization can charge a **toll** to foreign parties travelling its roads;
- two civilizations bound by a **trade treaty** can build their roads into each other's land, so their networks join, and pass each other's tolls free.

The user's decisions on the three open questions are recorded in section 4 and applied throughout. Nothing here is built yet.

## Where roads can be built today

A road crew may work on:

- the civilization's own land;
- unowned frontier land.

It may not work on land it has *seen* owned by another civilization. If a tile turns out to be foreign when the crew arrives, work stops and the crew walks home.

So roads already run beyond a civilization's borders, into the frontier. A road belongs to no one; the record only notes which civilization's crew last worked on it. When a road is built into the frontier:

- its builder's influence reaches further along it, so territory tends to follow the road outward;
- the same road also carries any neighbour's influence, so a road built toward a rival can end up inside the rival's land.

What is not possible today is building *inside* someone else's territory. That is what the trade treaty's new road terms would change.

## Research summary

### Tolls in history

- **Collection needed people at chokepoints.**
  - Roman *portoria* were collected at provincial borders, ports and bridges by stationed collectors. The *quadragesima Galliarum* was a 2.5% duty on goods.
  - Medieval Rhine lords kept dozens of toll castles, each one a garrison over a narrow way.
  - The Danish *Sound Dues* (1429–1857) were collected at Helsingør castle, at a rate of roughly 1–2% of cargo.
  - In every case a toll needed a staffed post the traveller could not easily go around.
- **Tolls were paid in kind or as a share of cargo.** Envoys were customarily exempt.
- **Tolls could be avoided.** Travellers took longer routes around toll stations, and tolls shaped routes as much as they raised revenue.
- **Treaties were how tolls were cut.**
  - The Treaty of Speyer (1544) eased the Sound Dues for Dutch ships.
  - The Mainz Convention (1831) capped Rhine tolls.
  - The German *Zollverein* (1834) abolished tolls between its members.
  - The Copenhagen Convention (1857) bought out the Sound Dues.
  - "Freedom of transit" was later codified in the Barcelona Convention (1921) and GATT Article V.

### Roads across borders

- **Joining roads was a matter of agreement.**
  - In the 1936 Convention on the Pan-American Highway, each state agreed to build its own section so the sections would meet.
  - The Asian Highway agreement (2003) and the EU's trans-European networks follow the same model.
  - Earlier, road building inside another polity usually went with dependence. Roman roads through client kingdoms and the Inca road network extended power as they went.
- **A road is also an instrument of reach.** If one party builds deep into the other's land, the builder's influence travels with it. That is historically accurate, and in this simulation it happens by itself: influence follows roads.

### Game precedents

- **Civilization:**
  - *Open Borders* lets units cross territory.
  - Roads that connect cities, sometimes through a partner's land, create trade connections.
- **Europa Universalis IV:** *military access* is a separate right from alliance.
- **Old World:** open-borders agreements let units use a partner's roads.
- **Shared lesson:** passage rights, building rights and toll exemptions are distinct terms. Folding them into one "friendship" flag makes them hard to reason about.

## Proposed design

### 1. Toll posts

- A civilization may place a toll only where it has people to collect it. That means one of its own road tiles that is also:
  - a settlement (a town gate); or
  - a garrison's tile (a toll post).
- **New order `set_toll`.** It names:
  - the tile;
  - a cargo rate from 0 to 2 000 basis points (at most 20%);
  - a food charge per person, from 0 to 2.

  A rate of 0 removes the toll.
- **The toll lapses** when its tile stops having living collectors (the garrison is disbanded or the settlement empties), or when the tile is no longer owned. A `toll_lapsed` event records it. A lapsed post's chest stays on the tile, and can still be fetched by a relocation that recalls the collectors. Seizing an abandoned chest is left for war.
- **Who pays:** foreign parties that enter the tile on the way out, once per journey:
  - **shipments** pay the rate on each cargo resource, rounded down, with at least 1 unit whenever the rate is above 0 and the cargo is at least 1;
  - **migrants and road crews** pay the food charge per living traveller, from their packs.
- **Who does not pay:**
  - envoys, by custom;
  - explorers, who carry nothing to take;
  - the owner's own parties;
  - parties of a trade-treaty partner (see section 2).
- **A party that cannot pay in full looks for another way round.** It stops on the tile before the post and looks for a detour: the shortest way, over land its civilization knows, from where it stands to any later tile of its route. The detour must avoid the post and every other known toll the party cannot pay. The party takes the detour only if its pack still covers the rest of the journey. That means the rest of the way out, plus the way back for a shipment.
  - If it takes the detour, its route is rewritten, and a `toll_avoided` event records the change.
  - Otherwise it is **turned back** with its goods intact and walks home under the new outcome `turned_back`.
  - A toll is a gate, not a robbery. Seizing goods is left for war.
- **The chest at the post.** Takings do not reach the owner's store at once. They go into a **chest** kept at the post:
  - At the capital's own gate the chest is the storehouse itself, so takings are stored at once.
  - Everywhere else, the chest is emptied at a regular interval. `set_toll` also names a **deposit interval** of 10–90 days, 30 by default, and a **deposit route** from the post to one of the owner's settlements.
  - When a deposit is due, the engine sends a courier party. The couriers are the post's collectors, drawn in id order, as many as the chest needs at 50 units each, always leaving at least one collector behind. They carry the chest home as an internal `deposit` journey, and the goods reach the store only on arrival.
  - Couriers face the usual delays and hazards. Goods carried by a party that perishes are lost.
  - Anything the couriers cannot carry stays in the chest for the next deposit.
  - A deposit that finds no spare collector, or no route it still knows, is skipped, and the owner receives a notice.
  - Collectors away on a deposit do not count as collectors. A post whose collectors are all away collects nothing until they return.
- **Knowledge.**
  - Explorers and travellers who see a toll post record its rates as observations. Reports gain `known_tolls`.
  - Validation and provisioning count only *known* tolls, so an unknown toll can surprise a party and turn it back. A sovereign can learn to route around posts.
- **Events and notices.**
  - `toll_paid`, `toll_collected`, `toll_avoided` and `toll_refused`, and for deposits `toll_deposit_dispatched`, `toll_deposited` and `toll_deposit_skipped`.
  - Each side gets a private notice: the payer learns what it paid and where, and the owner learns what it collected.

### 2. Road terms of the trade treaty

The existing `trade` treaty gains road terms, and no new treaty kind is added. It is still offered, accepted, cancelled and repudiated as before. While it is in force:

1. **Building rights.** Each party's road crews may work on the other's land. Validation and the on-arrival check treat the partner's tiles as allowed. When the treaty ends, a crew on partner land stops with the reason `treaty_ended` and walks home.
2. **Free passage.** Neither party's parties pay the other's tolls. This is the Zollverein term, and the engine applies it, so it cannot be breached by accident.
3. **Shared road maps.** When the treaty takes effect, each party learns the other's own roads and tolls, dated to that day. This is the map the ambassadors carried. Later changes are learned only by observation, as usual.
4. **Joined roads.** When a continuous road of any grade first links a settlement of each partner, a `roads_joined` event records the day and the route length. It fires again if the link is broken and later restored.

### 3. What stays the same

- **Roads still serve everyone.** Tolls charge travellers, and parties may go round them; they do not close roads. A civilization at war can still march on your road, and destroying it belongs to the war slice.
- **Treaties never create territory.** A partner's road deep in your land carries its influence into your land, and could win tiles if you are weak there. The treaty gives the right to build; the consequences are left to emerge.

## 4. Decisions

1. **Where toll takings go.** They are held in a chest at the post. A courier party carries them home at a regular deposit interval, and they reach the owner's store only on arrival.
2. **Treaty shape.** The road terms are folded into the existing `trade` treaty.
   - Every trade treaty now also grants building rights, free passage and shared road maps.
   - Ending a trade treaty ends all of these together.
3. **A party that cannot pay.** It takes a detour around the post if it knows one and can afford it, and otherwise it is turned back with its goods intact.

## Validation (when built)

- **Focused tests:**
  - a toll is charged only at a staffed, owned road tile;
  - shipments pay a share of cargo, and migrants pay food per head;
  - envoys, explorers, own parties and treaty partners pass free;
  - a party that cannot pay takes a known, affordable detour, and otherwise is turned back intact;
  - takings wait in the chest until couriers carry them home at the deposit interval;
  - a deposit leaves at least one collector behind;
  - goods are lost with a perished courier party, and what the couriers cannot carry waits for the next deposit;
  - a toll lapses when its garrison leaves;
  - unknown tolls are not counted in planning;
  - under a trade treaty, crews build on partner land, and stop when the treaty ends;
  - shared road maps are dated;
  - `roads_joined` fires when two partners' settlements become linked.
- **Acceptance test:** two trade partners build toward each other, their roads join, and a shipment between them pays no toll. After one side repudiates the treaty, the next shipment pays, and the takings reach the owner's store only when the next deposit arrives.
- **Stress tests:** stress runs with toll posts and a road treaty check every day that:
  - no toll is ever charged to an exempt party;
  - no toll is charged where there is no collector;
  - everything paid is accounted for, whether in a chest, on a courier, in a store, or lost with a perished party.
