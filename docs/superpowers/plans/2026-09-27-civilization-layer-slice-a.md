# Civilization Layer Slice A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Add deterministic knowledge, teaching, skill loss, expeditions, observations, and private maps to the Rules Laboratory.

**Architecture:** Keep capability and observation records inside WorldState, serialize them through the canonical state hash, and resolve their effects in advance_day. The command validator accepts only owner-scoped teaching and expedition requests; the engine records results as ordered domain events. Diplomacy and conflict will consume these records later, but are excluded from this slice.

**Tech Stack:** Python 3.12, Pydantic 2, NumPy deterministic streams, pytest, Hypothesis, Ruff, mypy.

**Spec:** docs/superpowers/specs/2026-09-27-civilization-layer-design.md

## Global Constraints

- Preserve deterministic daily transitions, canonical state hashes, checkpoint loading, and replay.
- The engine is the sole authority; commands express requests and never directly alter facts.
- A civilization report may contain only its own people, observations, inventory, projects, decrees, and visible events.
- Use stable EntityId ordering and named StableRng streams for every random resolution.
- No OpenAI, Anthropic, local-model, HTTP, dashboard, diplomacy, trade, territory, or combat integration belongs in this slice.
- Keep all quantities integer-valued and persist every authoritative record in the existing state snapshot.

## Review Focus

- A repeated sighting updates confidence and date without duplicating a private-map record; Task 4.
- A dead or foreign person cannot be accepted as a teacher, apprentice, or explorer; Task 3.
- A council report never includes an undiscovered tile or rival observation; Task 4.
- A capability is forgotten only after its final living practitioner is gone and no retained record preserves it; Task 3.
- A persisted exploration history replays to the same state hash and event order; Task 5.

---

## File Structure

- src/sovereign_world/capabilities.py — capability definitions, civilization knowledge, teaching assignments, and daily teaching/forgetting resolution.
- src/sovereign_world/exploration.py — observation, expedition, route validation, and daily travel resolution.
- src/sovereign_world/state.py — stores capability and observation state, initializes founder knowledge, and extends invariants.
- src/sovereign_world/commands.py — typed teaching/expedition orders, owner checks, and privacy-safe council report summaries.
- src/sovereign_world/engine.py — applies accepted orders and invokes teaching and expeditions at fixed transition points.
- src/sovereign_world/scripted.py — gives the baseline sovereign a deterministic first survey.
- tests/test_capabilities.py — learning, teaching, retained records, and loss.
- tests/test_exploration.py — routes, observations, and discovery.
- tests/test_commands.py — direct-order validation and report privacy.
- tests/test_engine.py — transition ordering and accepted-order integration.
- tests/integration/test_replay.py — persisted discovery replay.
- tests/acceptance/test_civilization_discovery.py — isolated-development acceptance scenario.

### Task 1: Capability domain and deterministic knowledge rules

**Files:**
- Create: src/sovereign_world/capabilities.py
- Create: tests/test_capabilities.py

**Interfaces:**
- Produces: CapabilityId, CapabilityRecord, TeachingAssignment, KnowledgeDayResult, and advance_knowledge_day(records, people, day) -> KnowledgeDayResult.
- Consumes: EntityId and Person from existing domain modules.

- [ ] **Step 1: Write failing capability-record tests**

~~~
def test_teaching_awards_a_capability_only_after_the_required_days() -> None:
    result = advance_knowledge_day(records, people, day=3)
    assert CapabilityId.SURVEYING in result.records.known_capabilities
    assert people[apprentice_id].skills[CapabilityId.SURVEYING.value] > 0
~~~

Add tests for deterministic record ordering and for a retained written record preventing the last-practitioner loss.

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: python -m pytest tests/test_capabilities.py -v  
Expected: FAIL because the capability domain does not exist.

- [ ] **Step 3: Implement capability types and advance_knowledge_day**

Define CapabilityId as a closed StrEnum for the eleven capabilities named in the spec. Define frozen Pydantic records with tuples sorted by EntityId: CapabilityRecord(capability, practitioner_ids, retained_record, discovered_day) and TeachingAssignment(assignment_id, teacher_id, apprentice_id, capability, started_day, required_days=30). advance_knowledge_day must reject unavailable teachers/apprentices, increase the apprentice's matching string skill once per completed assignment, add the apprentice to a record, and return learned and forgotten capabilities. A capability can be forgotten only when it has no living practitioner and retained_record is false.

- [ ] **Step 4: Run the focused tests to verify they pass**

Run: python -m pytest tests/test_capabilities.py -v  
Expected: PASS.

- [ ] **Step 5: Commit**

~~~
git add src/sovereign_world/capabilities.py tests/test_capabilities.py
git commit -m "feat: add deterministic civilization knowledge"
~~~

### Task 2: Persist knowledge and regional starting capabilities

**Files:**
- Modify: src/sovereign_world/state.py:18-109
- Modify: src/sovereign_world/worldgen.py:16-37
- Modify: tests/test_worldgen.py
- Modify: tests/test_persistence.py

**Interfaces:**
- Consumes: CapabilityRecord from Task 1 and StartingRegion.viability.strength.
- Produces: CivilizationState.capabilities: tuple[CapabilityRecord, ...] and stronger validate_world(state) checks.

- [ ] **Step 1: Write failing initialization and round-trip tests**

~~~
def test_each_start_has_one_known_regional_capability() -> None:
    state = build_initial_state(manifest)
    assert all(civilization.capabilities for civilization in state.civilizations.values())

def test_checkpoint_round_trip_preserves_capability_records(tmp_path: Path) -> None:
    assert state_hash(store.load_checkpoint()) == state_hash(initial_state)
~~~

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: python -m pytest tests/test_worldgen.py tests/test_persistence.py -v  
Expected: FAIL because civilization capability state is absent.

- [ ] **Step 3: Extend initial state and invariants**

Map each existing regional strength to one CapabilityId; reject a generated start whose strength has no mapping. Seed a civilization record with founders whose existing skill key matches that capability. Add capability records to CivilizationState with sorted serialization. Make validate_world reject duplicate capabilities, duplicate practitioners, foreign people, dead practitioners, or practitioners whose matching skill is missing.

- [ ] **Step 4: Run the focused tests to verify they pass**

Run: python -m pytest tests/test_worldgen.py tests/test_persistence.py -v  
Expected: PASS.

- [ ] **Step 5: Commit**

~~~
git add src/sovereign_world/state.py src/sovereign_world/worldgen.py tests/test_worldgen.py tests/test_persistence.py
git commit -m "feat: persist regional civilization knowledge"
~~~

### Task 3: Validated teaching orders and daily capability transitions

**Files:**
- Modify: src/sovereign_world/commands.py:18-178
- Modify: src/sovereign_world/engine.py:53-279
- Modify: tests/test_commands.py
- Modify: tests/test_engine.py

**Interfaces:**
- Consumes: Task 1 TeachingAssignment, KnowledgeDayResult, and advance_knowledge_day.
- Produces: DirectOrderKind.START_TEACHING, DirectOrder.assignment_id, DirectOrder.capability, and deterministic capability_learned/capability_forgotten events.

- [ ] **Step 1: Write failing validation and engine tests**

~~~
def test_foreign_teacher_order_is_rejected() -> None:
    validation = validate_envelope(envelope, state)
    assert validation.errors[0].code == "foreign_person"

def test_last_unrecorded_practitioner_death_forgets_capability() -> None:
    result = advance_day(state, StableRng(state.config.seed))
    assert "capability_forgotten" in {event.kind for event in result.events.events}
~~~

Also cover a dead teacher and duplicate assignment ID.

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: python -m pytest tests/test_commands.py tests/test_engine.py -v  
Expected: FAIL because teaching orders are unsupported.

- [ ] **Step 3: Implement owner-scoped teaching command handling**

Add START_TEACHING without widening the command allowance or schema version. Add optional assignment_id, teacher_id, apprentice_id, and capability fields to DirectOrder; require all four only for this kind. Extend validate_envelope to reject duplicate assignment IDs, missing fields, foreign people, dead people, and a teacher who lacks the capability. In _run_councils, add only validated assignments. In advance_day, resolve teaching after ordinary work and reconcile capability loss again after population mortality, so a death takes effect on that day's state. Emit capability_learned/capability_forgotten through EventPhase.WORK so Phase 1 ordering remains stable.

- [ ] **Step 4: Run the focused tests to verify they pass**

Run: python -m pytest tests/test_commands.py tests/test_engine.py -v  
Expected: PASS.

- [ ] **Step 5: Commit**

~~~
git add src/sovereign_world/commands.py src/sovereign_world/engine.py tests/test_commands.py tests/test_engine.py
git commit -m "feat: resolve validated teaching orders"
~~~

### Task 4: Expeditions, observations, and private maps

**Files:**
- Create: src/sovereign_world/exploration.py
- Modify: src/sovereign_world/state.py:18-109
- Modify: src/sovereign_world/commands.py:18-178
- Modify: src/sovereign_world/engine.py:53-279
- Modify: src/sovereign_world/scripted.py:24-75
- Create: tests/test_exploration.py
- Modify: tests/test_commands.py

**Interfaces:**
- Produces: Observation, Expedition, ExpeditionStatus, and advance_expeditions(expeditions, people, world_map, day) -> ExpeditionDayResult.
- Produces: DirectOrderKind.START_EXPEDITION, DirectOrder.expedition_id, and DirectOrder.destination.
- Consumes: CivilizationState.known_tiles as the compatibility projection of its observation ledger.

- [ ] **Step 1: Write failing route, privacy, and no-duplication tests**

~~~
def test_expedition_discovers_each_travelled_tile_once() -> None:
    result = advance_expeditions((expedition,), people, world_map, day=4)
    assert result.observations[-1].tile == destination
    assert len({item.tile for item in result.observations}) == len(result.observations)

def test_council_report_excludes_rival_and_undiscovered_tiles() -> None:
    report = build_council_report(state, first_civilization)
    assert rival_tile not in report.known_tiles
~~~

Add a dead-explorer test and a second-sighting test that refreshes rather than duplicates an observation.

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: python -m pytest tests/test_exploration.py tests/test_commands.py -v  
Expected: FAIL because expeditions and observation records are absent.

- [ ] **Step 3: Implement deterministic travel and observations**

Create Observation(tile, observed_day, confidence_bp, observer_id, source="direct") and Expedition(expedition_id, explorer_ids, route, next_route_index=0, status="active"). Require each route to use adjacent in-bounds hexes and start at the explorers' shared current location. Each active tick advances one coordinate in sorted expedition-ID order, moves living explorers, and creates or refreshes one direct observation with confidence 10_000. Preserve known_tiles as the sorted projection of observation keys and validate it against the ledger. Stop an expedition as failed when an explorer dies or disappears; do not observe an untravelled tile.

Add START_EXPEDITION, which accepts only living own explorers, an ID, and a route beginning in known territory. Have BaselineSovereign request one deterministic adjacent survey only after starter construction requests. Emit expedition_started, tile_observed, expedition_returned, and expedition_failed under EventPhase.MOVEMENT.

- [ ] **Step 4: Run the focused tests to verify they pass**

Run: python -m pytest tests/test_exploration.py tests/test_commands.py -v  
Expected: PASS.

- [ ] **Step 5: Commit**

~~~
git add src/sovereign_world/exploration.py src/sovereign_world/state.py src/sovereign_world/commands.py src/sovereign_world/engine.py src/sovereign_world/scripted.py tests/test_exploration.py tests/test_commands.py
git commit -m "feat: add private maps and expeditions"
~~~

### Task 5: Slice-A replay and isolated-development acceptance gate

**Files:**
- Create: tests/acceptance/test_civilization_discovery.py
- Modify: tests/integration/test_replay.py
- Modify: docs/rules-laboratory.md

**Interfaces:**
- Consumes: the complete Slice-A state and event contracts from Tasks 1-4.
- Produces: one documented scripted-sovereign scenario proving learning, survey, private knowledge, persistence, and exact replay.

- [ ] **Step 1: Write failing end-to-end tests**

~~~
def test_scripted_society_learns_and_returns_a_private_survey(tmp_path: Path) -> None:
    final_state, records = run_scripted_world(tmp_path, days=180)
    assert any(civilization.capabilities for civilization in final_state.civilizations.values())
    assert any(event.kind == "tile_observed" for event in records.events)
    assert replay_run(WorldStore(tmp_path)) == final_state
~~~

Add a replay assertion comparing recorded state hashes and an explicit assertion that one civilization report omits another's observation.

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: python -m pytest tests/acceptance/test_civilization_discovery.py tests/integration/test_replay.py -v  
Expected: FAIL until all Slice-A behavior is integrated.

- [ ] **Step 3: Document and verify the slice**

Add a Civilization discovery section to the operator guide explaining that maps are civilization-private, observations are acquired only by travel, and replay uses recorded state. Do not add any creator state-edit command.

- [ ] **Step 4: Run the full quality gate**

Run:

~~~
python -m ruff check .
python -m mypy src
python -m pytest -m "not soak" -q
python -m pytest -m soak -q
~~~

Expected: all checks pass, the previous 100-seed suite still passes, and the new acceptance history replays exactly.

- [ ] **Step 5: Commit**

~~~
git add tests/acceptance/test_civilization_discovery.py tests/integration/test_replay.py docs/rules-laboratory.md
git commit -m "test: verify civilization discovery replay"
~~~

## Coverage Review

- Capability graph, teaching, preservation, and loss: Tasks 1-3.
- Expeditions, observations, private maps, and no hidden knowledge: Task 4.
- Deterministic events, persistence, exact replay, and scripted survival: Task 5.
- Diplomacy, trade, migration, territory, conflict, surrender, assimilation, and elimination: intentionally deferred to Slice-B and Slice-C plans after Slice A passes its acceptance gate.
