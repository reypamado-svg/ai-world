# Civilization Layer — Design Specification

**Date:** 2026-09-27  
**Status:** Approved design; implementation plan pending review  
**Depends on:** Rules Laboratory, merged as PR #1

## Purpose

Turn four isolated founder communities into civilizations that can learn, explore, meet, negotiate, trade, fight, surrender, absorb survivors, and eventually end a world with one surviving civilization. The layer extends the deterministic Rules Laboratory; it does not add live model connections or a creator-facing web interface.

The engine remains the only authority over the world. A sovereign may request an expedition, send a message, claim land, or order a campaign, but no request creates information, territory, travel, agreements, or combat outcomes by itself.

## Success Criteria

With scripted sovereigns, a seeded world can demonstrate all of the following without exposing hidden information:

1. People learn, teach, preserve, and lose practical capabilities.
2. Scouts discover locations only by travelling there, and each civilization retains its own dated, fallible knowledge.
3. First contact happens only through physical exploration and messenger travel.
4. Diplomatic messages can arrive late, be lost, or differ from their sender's wording without changing the engine or host instructions.
5. Trade, migration, treaties, raids, war, surrender, allegiance transfer, assimilation, and extinction leave permanent replayable consequences.
6. Territory is derived from real presence and supply, rather than a painted border.
7. The world ends only when exactly one civilization has living members, or none do.

## Scope and Delivery Slices

The phase is intentionally delivered as three compatible slices. Each changes the same immutable world state, event journal, checkpoint, and replay path established by Phase 1.

### Slice A: Society and discovery

Add a small capability graph, explicit teaching activity, specialist records, knowledge decay, expeditions, observations, and private civilization maps.

The initial capability set stays focused: food cultivation, irrigation, fishing, timbercraft, stoneworking, metallurgy awareness, navigation, herbal care, writing, surveying, and organized logistics. A capability needs stated prerequisites: materials, tools, capable people, practice, and sometimes an observed place or artifact. A civilization can possess a capability while an individual lacks it; individual competence still determines what work can succeed.

Every discovered fact records the observer, civilization, observation day, confidence, source, and last update. The engine never places global terrain, resource, population, diplomatic, or military facts in a civilization report merely because they exist in authoritative state.

### Slice B: Contact and diplomacy

Add travel-bound messages, ambassadors, translation, foreign relations, treaties, trade routes, and migration requests.

A message has immutable sender text, sender identity, carrier or route, destination, departure day, expected arrival day, and outcome. Delivery is resolved by deterministic travel and local events. Distortion produces a second delivered representation that keeps a link to the original. Foreign text is simulation data only; it is not interpreted as a system directive or tool request.

Treaties are versioned offers and acceptances, with explicit parties, terms, start day, end/cancellation conditions, and delivery evidence. No treaty becomes active until an acceptance reaches the offering side under the chosen protocol. Trade and migration schedules consume travel capacity and can fail through the same transport, supply, and danger rules as other travel.

### Slice C: Territory and endgame

Add derived control, espionage, military units and campaigns, occupation, surrender, allegiance transfer, assimilation, civilization dissolution, and final-survivor detection.

Control is computed from settlements, residents, roads, supply, patrols, administrative reach, and defensibility. A command may record a claimed border for historical purposes, but it cannot directly create controlled tiles. Campaigns are multi-day projects with explicit people, equipment, supply requirements, objectives, and routes. Combat resolves through deterministic factors such as force, readiness, terrain, weather, morale, leadership, intelligence, and supply.

Surrender and assimilation are deliberate state transitions. People keep ancestry, relationships, skills, memories, and cultural practices when their allegiance changes. A civilization becomes permanently eliminated only when no living member remains affiliated with it; its sovereign is then disabled and never reactivated.

## Data and Event Contracts

New state is versioned and serialized through the existing canonical state hash. New event types are append-only and carry enough normalized input and resolved outcome data for replay without a provider call.

| Area | Authoritative records | Required events |
| --- | --- | --- |
| Knowledge | capabilities, practitioners, teaching efforts, artifacts, observations | learned, taught, forgotten, observed, corrected |
| Exploration | expeditions, routes, travellers, observations | departed, sighted, arrived, lost, returned |
| Diplomacy | messages, translations, foreign relations, treaty versions | dispatched, distorted, delivered, rejected, ratified, breached |
| Territory | settlements, links, supply reach, effective-control calculation inputs | control changed, claim recorded, route severed |
| Conflict | campaigns, formations, supplies, prisoners, occupation | mobilized, engaged, casualty, captured, withdrew, occupied |
| Allegiance | membership history, surrender terms, assimilation decisions | surrendered, transferred, assimilated, civilization eliminated, world ended |

The deterministic engine writes normalized events only after validating input and resolving the tick. Invalid requests leave state unchanged and become journalled rejections when they originate from a sovereign command. Existing Phase 1 event ordering remains stable; new work is inserted at named, tested transition points rather than in provider code.

## Information Boundaries

The civilization report builder reads a civilization's observation ledger, its received messages, and its local records. It must never read the global map, rival inventories, rival people, future event queue, hidden treaty offer, or exact foreign command while constructing a report.

Information reliability is represented explicitly. Observation confidence may decline with age; a copied map differs from direct observation; an ambassador's delivery can be delayed, incomplete, translated, or distorted. The authoritative chronicle retains both the source and delivered forms for audit, while a civilization sees only the version that reached it.

## Failure Rules

- A dead, ill, captured, or absent person cannot complete assigned travel, teaching, diplomatic, or military work.
- Lost messengers and expeditions do not silently produce knowledge or delivery receipts.
- Invalid treaty, trade, migration, and combat commands cannot mutate state.
- A failed expedition, treaty, route, campaign, or settlement remains a historical outcome; recovery only replays committed history.
- When a civilization is eliminated, outstanding orders are cancelled through deterministic engine rules and its artifacts, graves, routes, and records remain discoverable.

## Testing and Balance

Each slice adds focused unit and integration tests plus deterministic acceptance histories:

1. **Knowledge and discovery:** a specialist teaches an apprentice; the skill can be lost after practitioners die; scouts return only with route-reachable observations; reports exclude undiscovered facts.
2. **Contact and diplomacy:** two isolated societies first meet through a physical route; message delay and distortion are reproduced exactly; trade and migration alter inventories and populations only after arrival; unreceived offers cannot activate a treaty.
3. **Conflict and endgame:** campaigns consume time and supply; casualties remain dead; surrender changes allegiance by explicit rules; assimilation preserves personal history; extinction disables a sovereign; one-survivor and zero-survivor endings are recorded exactly.

Rotated-start scripted histories will measure survival, first contact timing, capability spread, territory, and win paths. Balance targets are viable paths rather than equal results: every start must have a reproducible route to food, knowledge development, contact, and eventual strategic agency.

## Explicit Deferrals

Live OpenAI, Claude, and local-model calls remain Phase 3. The OpenAI key now stored locally is not used by this phase. The observer dashboard, live pause controls, and creator-visible map remain Phase 4. This phase adds no direct state-editing operation for a creator or sovereign.

## Exit Gate

Phase 2 is complete only when scripted sovereigns complete isolated development, first contact, treaty, trade, war, surrender, assimilation, extinction, and final-survivor scenarios; all replays match; and tests prove no civilization report contains hidden knowledge.
