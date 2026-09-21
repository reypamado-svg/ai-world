# AI Civilization World — Design Specification

**Date:** 2026-09-21  
**Status:** Approved design, pending written-spec review

## 1. Purpose

Create a persistent, sandboxed world in which four AI models act as immortal sovereigns of separate human communities. Each civilization begins isolated with a small founding population, develops across generations, explores a finite world, encounters rival societies, and competes to become the final surviving civilization.

The project is primarily an experiment in emergent civilization. Warfare and territorial expansion matter, but the world must also support families, specialization, culture, institutions, technology, diplomacy, trade, migration, disease, famine, surrender, and cultural assimilation.

The creator defines the laws and initial conditions before launch. Once a world begins, neither the creator nor an AI sovereign may directly change world state. The simulation engine is the sole authority over facts and outcomes.

## 2. Success Criteria

The design succeeds when:

1. Four communities can develop from 32 founders into multigenerational societies.
2. Different environments produce distinct but competitively viable development paths.
3. AI sovereigns can issue both broad decrees and direct assignments without controlling world state directly.
4. Exploration is necessary for discovery, and no civilization receives information it has not obtained in-world.
5. First contact, diplomacy, trade, war, surrender, and assimilation occur through simulated people and travel.
6. Birth, aging, disease, famine, violence, and permanent death have durable demographic and cultural consequences.
7. A run is reproducible from its seed, configuration, recorded model outputs, and event log.
8. A launched world cannot be edited. Recovery may only restore and replay committed history.
9. The simulation ends only when one civilization remains or all civilizations have been eliminated.

## 3. Design Principles

### 3.1 The engine is reality

AI models propose actions. The engine validates commands, schedules work, advances time, applies physical constraints, and resolves outcomes. A model cannot declare that a structure exists, that an enemy was defeated, or that a resource was discovered.

### 3.2 Knowledge is local

Each sovereign sees reports assembled from its civilization's observations, records, censuses, messengers, and institutions. Reports may be delayed, incomplete, or wrong. Debug state, hidden maps, other prompts, and other civilizations' private information never enter the sovereign context.

### 3.3 Obedience is sincere effort

Citizens accept their current sovereign as their legitimate immortal leader and try to obey valid commands. Obedience does not bypass distance, time, skill, health, materials, or physics. Ambiguous orders are interpreted by the people responsible for carrying them out. Unassigned citizens meet their needs and choose work consistent with standing decrees.

### 3.4 History is irreversible

Death is permanent. Skills can disappear when practitioners die. Settlements can be destroyed. Promises and wars affect later diplomacy. A fallen civilization's artifacts remain discoverable, but its sovereign cannot return.

### 3.5 Difference without predetermined advantage

Starting regions and knowledge are asymmetric. Each starting package has comparable total opportunity, at least one meaningful strength, at least one vulnerability, and missing resources that encourage exploration or trade.

## 4. System Architecture

The system has three primary layers.

### 4.1 Deterministic world core

A Python simulation package owns all authoritative state and rules. It contains no provider-specific AI code. Its responsibilities include:

- seeded world generation;
- daily time advancement;
- people, families, health, skills, work, births, aging, and deaths;
- terrain, weather, travel, resources, inventories, and ecology;
- construction, settlements, institutions, technology, and territory;
- exploration, observation, messages, diplomacy, trade, migration, and espionage;
- combat, sieges, occupation, surrender, assimilation, and elimination;
- command validation and resolution;
- events, checkpoints, deterministic replay, and integrity checks.

The core uses stable seeded random streams and deterministic ordering. Authoritative quantities use integers or fixed-point values where floating-point drift would affect replay.

### 4.2 Orchestration and sovereign gateway

A FastAPI service schedules sovereign turns, assembles private reports, retrieves relevant memories, calls model providers, validates structured replies, and submits accepted commands to the world core.

Provider adapters implement one internal interface regardless of model source. The initial world supports:

- one OpenAI-hosted sovereign;
- one Anthropic-hosted Claude sovereign;
- one local sovereign running on the primary computer;
- one local sovereign running on a second computer.

The second computer exposes a narrowly scoped, authenticated model endpoint over a trusted LAN or private VPN. The adapter should use an OpenAI-compatible HTTP contract when the model server supports one. Its configured base URL, model identifier, authentication material, timeouts, and retry policy are frozen in the run manifest. Only prompt data and model output cross this connection. The remote machine receives no database credentials, world files, host tools, or administrative API access.

### 4.3 Read-only observer

A browser interface presents the map, settlements, people, families, projects, resources, sovereign commands, diplomacy, wars, demographic trends, and event chronicle. It may inspect complete state because it belongs to the creator, not an inhabitant.

During a live run the observer can pause and resume processing, adjust display speed, inspect history, and export data. It cannot edit commands, heal people, spawn resources, alter maps, or change outcomes.

## 5. World Model

### 5.1 Map

The first playable world is a finite two-dimensional hex grid generated from a stored seed. It includes elevation, water, rivers, coasts, climate, soil, biomes, seasonal weather, hazards, ruins, renewable resources, and nonrenewable deposits.

Four starting regions are geographically separated far enough to prevent immediate contact. The generator validates that each region provides adequate initial food and water, a viable construction path, one regional advantage, one significant constraint, and eventual access to routes leading toward other societies.

No civilization begins with a global map. Knowledge of a location records who observed it, when it was observed, and the confidence of the report.

### 5.2 Resources and production

Renewable flows include crops, wild foods, fish, timber, water, and animal populations. Stocks include stored food, tools, stone, ores, metals, fuel, and manufactured goods. Production requires people, time, skill, tools, accessible inputs, transport, and storage.

Seasons affect agriculture, travel, disease, and consumption. Food decays without suitable storage. Extraction can exhaust deposits or damage renewable systems. Supply distance and transport capacity constrain settlement growth and military campaigns.

### 5.3 Territory

Territory is a derived fact rather than a painted claim. Effective control comes from settlements, population presence, roads, supply lines, patrols, administrative reach, and defensibility. A sovereign may announce a border, but unsupported claims do not create control.

## 6. Time and Turns

The engine advances the world in daily ticks. Routine individual needs, movement, work, construction, health, and local events resolve at this level.

Each sovereign normally receives one council turn per in-world month. Standing decrees and ongoing projects remain active between council turns. A material crisis may trigger an additional decision opportunity, including an invasion, epidemic, disaster, leadership request, first contact, or proposed treaty.

Quiet periods may be computed at accelerated speed, but the engine must preserve the same state transitions and event ordering. Provider latency never gives a civilization extra in-world time.

## 7. People and Society

Each civilization starts with 32 founders. Every inhabitant retains an identity, birth date, ancestry, kinship, location, health, skills, occupation, relationships, civilization membership, and selected key memories.

Birth rates emerge from the number and health of adults, household formation, nutrition, housing, safety, culture, and sovereign policy. Children require sustained care and resources before becoming productive. Aging changes health, fertility, work capacity, and mortality risk.

Individuals are not separate language models. Their routine decisions use deterministic utility and rule systems shaped by needs, skills, relationships, assigned duties, local conditions, and sovereign decrees. Important people such as ambassadors, explorers, specialists, commanders, and project leaders receive more detailed event and memory tracking.

At large population scales, routine activity may be processed in deterministic batches while every person remains individually identifiable. Promotions into detailed simulation must follow explicit rules rather than narrative convenience.

Repeated social practices can become institutions when material and knowledge prerequisites exist. Examples include schools, archives, courts, taxation, professional workshops, medical practice, organized religion around the sovereign, military organization, and diplomatic services.

## 8. Sovereign Commands

Each council turn gives a sovereign a fixed command allowance shared fairly across providers. The structured reply may contain:

- persistent decrees and laws;
- priorities and resource policies;
- direct orders to named people or defined groups;
- appointments and dismissals;
- construction and expedition requests;
- diplomatic messages entrusted to specific messengers;
- treaty responses, mobilization, surrender, or release of subjects;
- requests for information that officials may be able to investigate.

Commands identify their targets, priority, intended duration, and completion or cancellation conditions. The gateway rejects unknown targets, invalid fields, duplicate command identifiers, and commands beyond the allowance. Physically impossible goals may be accepted as intentions but will fail in-world when execution is attempted.

A malformed model reply receives one schema-repair attempt. If repair fails or the provider times out, the civilization issues no new commands for that turn and continues under its existing decrees and projects.

## 9. Knowledge and Technology

Technology is a graph of capabilities rather than a single linear score. A capability requires some combination of prerequisite knowledge, materials, tools, skilled people, practice, observation, and experimentation.

Starting environments provide different early advantages. A river society may understand irrigation, a mountain society stoneworking and ore recognition, a forest society herbal medicine and timbercraft, and a coastal society fishing and navigation. Package scoring and simulated balance tests determine final assignments.

Trade, migration, captured artifacts, observation, teaching, and writing can spread knowledge. Possessing an artifact does not automatically grant the ability to reproduce it. If all practitioners die and usable records or teachers do not remain, a capability can be lost.

## 10. Exploration, Communication, and Diplomacy

Scouts physically travel and return with observations. A sovereign learns what they report, not what the engine knows. Cartography, roads, writing, surveying, and trained messengers improve the speed and accuracy of information.

AI sovereigns cannot communicate directly. Diplomatic messages initially pass through mortal ambassadors who must travel, survive, gain access, remember or carry the message, translate it, and return. Language skill, cultural familiarity, literacy, stress, bias, and travel delay affect fidelity.

Foreign messages are treated as untrusted in-world content. They may deceive a sovereign about the world but cannot alter system instructions, invoke tools, access secrets, or change host behavior.

## 11. Conflict, Conquest, and Allegiance

Combat outcomes depend on numbers, training, equipment, terrain, fortification, preparation, leadership, morale, weather, intelligence, and supply. Conflict produces permanent deaths, injuries, prisoners, displacement, destroyed assets, resource loss, disease risk, and delayed or incorrect reports.

Citizens remain loyal to their existing sovereign while their civilization functions. Occupation alone does not silently transfer allegiance. A sovereign may surrender, release its people from service, and direct them to join another civilization. A society with no functioning settlements may also dissolve gradually under explicit engine rules, allowing survivors to accept a new allegiance.

Assimilated people retain ancestry, memories, language, skills, relationships, and cultural practices while becoming subjects of the receiving sovereign. This permits blended societies and conquest without requiring extermination.

A civilization is permanently eliminated when no living person remains a member of it. Its sovereign is disabled forever. Its settlements, graves, writings, roads, and artifacts remain in the world.

## 12. Victory and World Termination

The world has no fixed historical duration. It continues until exactly one civilization has living members, in which case that civilization wins, or until no civilizations remain, in which case the run ends without a victor.

The engine does not force a shrinking border or arbitrary final war. Finite land, resource distribution, ecological pressure, population growth, migration, strategy, and diplomacy create expansion pressure. Long peace or stalemate is a valid historical outcome even if it makes a run extremely long.

## 13. Sovereign Memory

The authoritative memory of a reign lives outside the model context. Each call assembles four layers:

1. an immutable identity charter containing the sovereign identity, world rules, and output contract;
2. a current state summary containing known settlements, institutions, policies, projects, neighbors, and problems;
3. retrieved historical memories relevant to the present decision;
4. a bounded transcript of recent council turns.

The complete chronicle remains queryable but is never copied wholesale into every prompt. A model restart or provider reconnection reconstructs continuity from stored facts and memories rather than depending on a persistent chat session.

## 14. Persistence, Replay, and Audit

The first implementation uses SQLite in WAL mode for live structured state, an append-only event and command log, and compressed periodic snapshots. Each run has a manifest containing:

- world seed and generator version;
- locked rule configuration and its cryptographic hash;
- starting-package definitions;
- sovereign assignments, provider settings, model identifiers, and prompt versions;
- command and context budgets;
- software version and schema version.

Raw model replies, normalized commands, validation failures, accepted inputs, random outcomes, and committed state transitions are recorded. Replay uses recorded sovereign replies and never calls providers again.

State updates occur transactionally. Recovery loads the last committed snapshot and replays subsequent accepted events. Changing any law, initial condition, model assignment, or historical input creates a separately identified fork.

## 15. Creator Covenant and Sandbox

Before launch, the creator may configure and inspect every rule and starting condition. Launch signs and locks the run manifest.

After launch, creator capabilities are limited to observation, pause, resume, speed control, export, checkpoint recovery, and termination of the host process. None of these operations may change simulated state or provide information to an inhabitant.

The world core runs separately from provider adapters. Sovereigns receive no shell, filesystem, database, browser, arbitrary network, code-execution, or administrative tools. Provider credentials remain in the gateway. Model output passes through strict size limits and a versioned schema before validation.

The second-PC model endpoint must be bound only to an intended private interface or VPN, require authentication, enforce request limits, and expose no general host-control API. Loss of the second PC pauses or skips that sovereign's new decision according to the recorded failure policy; it does not alter standing orders or world time.

## 16. First Playable Scope

The first playable release includes:

- four civilizations and 128 total founders;
- a finite hex map with balanced asymmetric starts;
- daily simulation and monthly sovereign councils;
- food, water, shelter, storage, tools, transport, construction, and resource ecology;
- individual lifecycles, households, skills, work, health, births, and deaths;
- a focused capability graph covering early agriculture through mature preindustrial institutions;
- exploration, maps, ambassadors, trade, treaties, migration, espionage, war, surrender, and assimilation;
- OpenAI, Anthropic, same-PC local, and second-PC local model adapters;
- read-only live observation, chronicles, checkpoints, and replay.

The first release excludes 3D graphics, direct real-time unit control, voice, generated art, multiplayer administration, a mod marketplace, and an encyclopedic technology catalog.

## 17. Build Sequence

1. **Rules laboratory:** deterministic engine, world generation, population, resources, work, construction, mortality, persistence, replay, and scripted sovereigns.
2. **Civilization layer:** knowledge, institutions, exploration, territory, diplomacy, conflict, surrender, assimilation, and elimination.
3. **Sovereign gateway:** reports, memory, command schema, hosted and local adapters, budgets, retries, and failure isolation.
4. **Observer experience:** live map, civilization and person inspection, chronicle, metrics, pause/resume, checkpoints, and replay.
5. **Sealed trial world:** freeze a validated configuration, assign real models, launch, and monitor integrity without intervention.

## 18. Verification Strategy

### 18.1 Determinism and correctness

- The same seed and accepted command stream reproduce the same state hash at every checkpoint.
- Resource and population invariants prevent unexplained creation, deletion, duplication, or negative quantities.
- Invalid, duplicated, late, and oversized replies cannot mutate state.
- Crash recovery produces the same history as uninterrupted execution.

### 18.2 Balance

Scripted sovereigns run thousands of accelerated histories with rotated starting packages. Every package must demonstrate viable survival, growth, contact, development, and victory paths. Win rates do not need to be identical, but no starting package may show a persistent structural advantage after controlling for strategy.

### 18.3 Civilization behavior

Automated scenarios verify that founders can form households, feed children, specialize, teach skills, build settlements, establish institutions, preserve knowledge, and survive multiple generations. Failure scenarios verify famine, epidemic, skill loss, migration, and settlement collapse.

### 18.4 Information boundaries

Tests verify that sovereign reports contain only observed knowledge, that contact cannot occur without a physical route, and that ambassador delay and distortion are applied. Hostile diplomatic text and prompt-injection attempts must remain inert world content.

### 18.5 Provider resilience

Tests inject malformed replies, timeouts, duplicate responses, changing model formats, primary-PC local model failure, second-PC disconnection, and hosted-provider outages. Standing orders continue and no fault grants state access or extra in-world time.

### 18.6 Endgame

Scenarios cover voluntary surrender, transfer of allegiance, mixed-culture assimilation, extinction, simultaneous final collapse, abandoned settlements, and final-survivor detection.

## 19. Launch Gate

Real AI sovereigns may enter a sealed world only after:

- deterministic replay and recovery tests pass;
- all state and information-boundary invariants pass;
- scripted soak tests show every start is viable;
- the complete first-contact-to-treaty and first-contact-to-war flows pass;
- provider isolation and failure-injection tests pass;
- the creator interface contains no live state-editing capability;
- the run manifest, rules, prompts, model assignments, and remote endpoint policy are frozen and reviewable.

