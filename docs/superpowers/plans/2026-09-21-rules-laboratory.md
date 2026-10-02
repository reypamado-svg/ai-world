# Rules Laboratory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Build a deterministic, replayable rules laboratory that simulates four isolated communities from seeded terrain through multiple generations under scripted sovereign commands.

**Architecture:** A pure Python world core owns immutable configuration and mutable simulation state. Daily state transitions emit ordered domain events; monthly scripted-sovereign turns submit schema-validated commands through the same boundary later used by real models. SQLite stores manifests and checkpoints, while an append-only JSONL journal stores accepted inputs and emitted events for exact replay.

**Tech Stack:** Python 3.12, Pydantic 2, NumPy 2, Typer, SQLite, pytest, Hypothesis, Ruff, mypy

**Spec:** docs/superpowers/specs/2026-09-21-ai-civilization-world-design.md

## Global Constraints

- The final world has four civilizations and exactly 32 founders per civilization.
- The engine advances in daily ticks; sovereign councils occur every 30 in-world days.
- All authoritative state transitions must be deterministic for a fixed manifest and accepted command stream.
- Authoritative quantities use integers or fixed-point integer units.
- The world core contains no hosted-model, local-model, HTTP, or provider-specific code.
- Models and scripted sovereigns can affect state only through validated command envelopes.
- Birth, aging, disease, famine, and death are persistent; death is irreversible.
- Each start must have viable water, food, construction material, one advantage, and one vulnerability.
- The creator-facing CLI may initialize, run, inspect, checkpoint, and replay; it may not edit a launched state.
- Python source files remain focused by responsibility and fully typed under mypy strict mode.

## Review Focus

- A world generator that cannot place four viable starts must fail after a bounded number of attempts with the seed and reason, rather than hang or silently relax constraints. Task 2 pins this behavior.
- Birth and death on the same tick must preserve valid parent-child links and never leave a living person pointing to an unknown parent. Task 3 pins this behavior.
- Work that would consume unavailable or negative inventory must fail atomically without partial production. Task 4 pins this behavior.
- Multiple events scheduled for the same day must resolve in a stable documented order independent of dictionary or set ordering. Task 5 pins this behavior.
- A truncated journal tail or corrupt checkpoint must be detected; recovery must use only the last verified checkpoint and complete journal record. Task 7 pins this behavior.

---

## File Structure

- pyproject.toml — package metadata, dependencies, tool settings, and CLI entry point.
- src/sovereign_world/config.py — immutable run configuration and manifest hashing.
- src/sovereign_world/ids.py — typed identifiers and deterministic ID allocation.
- src/sovereign_world/rng.py — stable named random streams.
- src/sovereign_world/hexmap.py — axial coordinates, tiles, distance, and neighbors.
- src/sovereign_world/worldgen.py — seeded terrain and balanced starting-region placement.
- src/sovereign_world/people.py — people, kinship, founders, aging, births, and mortality.
- src/sovereign_world/resources.py — fixed-unit inventories, recipes, and conservation checks.
- src/sovereign_world/work.py — worker assignments and construction projects.
- src/sovereign_world/commands.py — decrees, direct orders, envelopes, and validation.
- src/sovereign_world/events.py — ordered domain-event schema and serialization.
- src/sovereign_world/state.py — aggregate authoritative WorldState.
- src/sovereign_world/engine.py — daily transition pipeline and monthly council scheduling.
- src/sovereign_world/scripted.py — deterministic sovereign protocol and baseline policies.
- src/sovereign_world/persistence.py — SQLite manifests/checkpoints and JSONL journal.
- src/sovereign_world/replay.py — checkpoint loading, journal replay, and hash verification.
- src/sovereign_world/cli.py — init, run, inspect, checkpoint, replay, and verify commands.
- tests/ — mirrors the source modules with unit, property, integration, and acceptance tests.

### Task 1: Project Foundation, Manifest, IDs, and Stable Randomness

**Files:**
- Create: pyproject.toml
- Create: src/sovereign_world/__init__.py
- Create: src/sovereign_world/config.py
- Create: src/sovereign_world/ids.py
- Create: src/sovereign_world/rng.py
- Test: tests/test_config.py
- Test: tests/test_rng.py

**Interfaces:**
- Consumes: none
- Produces: WorldConfig, RunManifest, EntityId, IdAllocator, StableRng.stream(name)

- [ ] **Step 1: Create project metadata and failing manifest tests**

Configure Python 3.12, the sovereign-world CLI entry point, Pydantic 2, NumPy 2, Typer, pytest, Hypothesis, Ruff, and strict mypy. Add tests that assert configuration is immutable, contains four civilizations with 32 founders each, rejects nonpositive dimensions, and hashes identically after JSON round-trip.

~~~python
def test_manifest_hash_survives_round_trip() -> None:
    config = WorldConfig(seed=41, width=48, height=48)
    manifest = RunManifest.new(config=config, engine_version="0.1.0")
    restored = RunManifest.model_validate_json(manifest.model_dump_json())
    assert restored.content_hash() == manifest.content_hash()
~~~

- [ ] **Step 2: Run the tests and verify the expected failure**

Run: pytest tests/test_config.py -v

Expected: collection fails because sovereign_world.config does not exist.

- [ ] **Step 3: Implement immutable configuration and canonical manifest hashing**

Use frozen Pydantic models. Serialize the manifest with sorted keys and compact separators before SHA-256 hashing. Exclude no fields from the hash.

~~~python
class WorldConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    seed: int
    width: int = Field(ge=24)
    height: int = Field(ge=24)
    civilizations: int = Field(default=4, ge=4, le=4)
    founders_per_civilization: int = Field(default=32, ge=32, le=32)
    council_interval_days: int = Field(default=30, ge=30, le=30)

class RunManifest(BaseModel):
    model_config = ConfigDict(frozen=True)
    run_id: UUID
    engine_version: str
    config: WorldConfig

    @classmethod
    def new(cls, config: WorldConfig, engine_version: str) -> "RunManifest":
        return cls(run_id=uuid4(), engine_version=engine_version, config=config)

    def content_hash(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()
~~~

- [ ] **Step 4: Add stable named RNG streams and deterministic IDs**

Derive each stream seed from SHA-256 of manifest seed plus stream name. Use a documented PCG64 implementation from NumPy rather than Python's version-dependent random module. IdAllocator creates IDs from the entity namespace and monotonic sequence.

~~~python
class StableRng:
    def __init__(self, root_seed: int) -> None:
        self.root_seed = root_seed

    def stream(self, name: str) -> np.random.Generator:
        material = f"{self.root_seed}:{name}".encode()
        seed = int.from_bytes(hashlib.sha256(material).digest()[:16], "big")
        return np.random.Generator(np.random.PCG64(seed))
~~~

- [ ] **Step 5: Verify deterministic independence**

Test that the same named stream repeats exactly, different names differ, and requesting streams in a different order does not change their sequences.

Run: pytest tests/test_config.py tests/test_rng.py -v

Expected: all tests pass.

- [ ] **Step 6: Run static checks and commit**

Run: ruff check . && mypy src && pytest tests/test_config.py tests/test_rng.py -v

Expected: all checks pass.

Commit:

~~~bash
git add pyproject.toml src/sovereign_world tests/test_config.py tests/test_rng.py
git commit -m "feat: establish deterministic world foundation"
~~~

### Task 2: Hex World and Balanced Starting Regions

**Files:**
- Create: src/sovereign_world/hexmap.py
- Create: src/sovereign_world/worldgen.py
- Test: tests/test_hexmap.py
- Test: tests/test_worldgen.py

**Interfaces:**
- Consumes: WorldConfig, StableRng
- Produces: HexCoord, Terrain, Tile, WorldMap, StartingRegion, GeneratedWorld, WorldGenerationError, generate_world(config, rng, max_attempts=100)

- [ ] **Step 1: Write failing coordinate and generation tests**

Cover six unique neighbors, symmetric hex distance, identical maps for identical seeds, four nonoverlapping starts, minimum start separation, and all viability requirements.

~~~python
def test_generated_starts_are_viable_and_separated() -> None:
    config = WorldConfig(seed=9, width=48, height=48)
    generated = generate_world(config, StableRng(config.seed))
    assert len(generated.starts) == 4
    assert all(start.viability.has_water for start in generated.starts)
    assert all(start.viability.food_units_per_day >= 64 for start in generated.starts)
    for left, right in combinations(generated.starts, 2):
        assert left.center.distance(right.center) >= 12
~~~

- [ ] **Step 2: Implement axial coordinates and immutable tiles**

HexCoord uses q and r axial coordinates. neighbors returns the six axial offsets in a fixed tuple order. WorldMap stores tiles in a tuple indexed by row-major offset and exposes tile(coord), contains(coord), and neighbors(coord).

- [ ] **Step 3: Implement seeded terrain generation**

Generate elevation, moisture, temperature, water, biome, soil, timber, stone, and ore from separately named RNG streams. Quantize all resource values to integer units before storing them. Derive rivers after elevation so the same map is independent of later population logic.

- [ ] **Step 4: Implement bounded start selection**

Score candidate radius-four regions for accessible water, daily food capacity, timber or stone, hazard load, one regional strength, and one scarcity. Use deterministic sorting by score then coordinate. Reject candidates within 12 hexes of a selected start.

If four viable starts cannot be selected, regenerate terrain up to max_attempts using attempt-specific named streams. Then raise WorldGenerationError containing the original seed, attempt count, and failed constraints.

~~~python
def generate_world(
    config: WorldConfig,
    rng: StableRng,
    max_attempts: int = 100,
) -> GeneratedWorld:
    failures: Counter[str] = Counter()
    for attempt in range(max_attempts):
        world_map = _generate_map(config, rng.stream(f"world:{attempt}"))
        starts, reasons = _select_starts(world_map, count=4, min_distance=12)
        if len(starts) == 4:
            return GeneratedWorld(world_map=world_map, starts=tuple(starts))
        failures.update(reasons)
    raise WorldGenerationError(config.seed, max_attempts, dict(failures))
~~~

- [ ] **Step 5: Pin bounded failure and deterministic output**

Use a deliberately impossible 24-by-24 fixture with min_distance larger than the map. Assert failure occurs after exactly three attempts and reports the seed. Hash canonical tile JSON and assert two runs match.

Run: pytest tests/test_hexmap.py tests/test_worldgen.py -v

Expected: all tests pass.

- [ ] **Step 6: Commit**

~~~bash
git add src/sovereign_world/hexmap.py src/sovereign_world/worldgen.py tests/test_hexmap.py tests/test_worldgen.py
git commit -m "feat: generate balanced deterministic worlds"
~~~

### Task 3: Founders, Kinship, Birth, Aging, and Mortality

**Files:**
- Create: src/sovereign_world/people.py
- Test: tests/test_people.py
- Test: tests/property/test_population_invariants.py

**Interfaces:**
- Consumes: EntityId, IdAllocator, StartingRegion, StableRng
- Produces: Person, Population, FoundingPopulation, create_founders, advance_population_day

- [ ] **Step 1: Write failing founder and lifecycle tests**

Assert 32 founders per civilization, valid ages from 18 through 45, no founder parent links, unique IDs, placement in the assigned start, and deterministic creation.

Write a same-day birth-and-parent-death test. The newborn must retain both known parent IDs even when a parent becomes dead on that tick; every referenced parent must remain present in Population.people.

~~~python
def test_birth_and_parent_death_preserve_kinship(
    birth_due_population: Population,
) -> None:
    result = advance_population_day(
        birth_due_population,
        day=300,
        rng=StableRng(7).stream("birth-death-test"),
    )
    child = result.people[result.births[0].person_id]
    assert child.parent_ids == result.births[0].parent_ids
    assert all(parent_id in result.people for parent_id in child.parent_ids)
    assert any(not result.people[parent_id].alive for parent_id in child.parent_ids)
~~~

- [ ] **Step 2: Implement person and population records**

Person stores immutable identity and kinship fields plus mutable age-days, health basis points, nutrition debt, location, skill map, alive flag, and death day. Population keeps dead people as historical records and exposes living_ids in sorted ID order.

- [ ] **Step 3: Implement founder generation**

Generate 16 adults of each reproductive sex per civilization, ages 18–45, with deterministic health and starter skills derived from the regional package. Do not create marriages or children at initialization.

- [ ] **Step 4: Implement daily lifecycle ordering**

Resolve scheduled births first, then aging, nutrition and disease mortality, then natural mortality. Record death without deleting the Person. New conception decisions happen after mortality and schedule future births; they never create a child immediately.

Mortality probabilities use integer thresholds against a 0–999,999 RNG draw. Conception requires living eligible adults, sufficient household food and shelter, and no close-parent relationship.

- [ ] **Step 5: Add property tests for population invariants**

Use Hypothesis to advance small populations across random nutrition and disease inputs. Assert IDs remain unique, dead people never return to life, age never decreases, every parent reference resolves, and population counts equal living plus dead records.

Run: pytest tests/test_people.py tests/property/test_population_invariants.py -v

Expected: all tests pass.

- [ ] **Step 6: Commit**

~~~bash
git add src/sovereign_world/people.py tests/test_people.py tests/property/test_population_invariants.py
git commit -m "feat: simulate persistent population lifecycles"
~~~

### Task 4: Resources, Work, and Construction

**Files:**
- Create: src/sovereign_world/resources.py
- Create: src/sovereign_world/work.py
- Test: tests/test_resources.py
- Test: tests/test_work.py

**Interfaces:**
- Consumes: EntityId, Person, HexCoord
- Produces: Resource, Inventory, Recipe, WorkOrder, ConstructionProject, execute_work_day

- [ ] **Step 1: Write failing inventory atomicity tests**

Assert withdrawals cannot make inventory negative, failed recipes consume nothing, successful recipes conserve declared inputs and outputs, and overflow beyond storage capacity is explicitly returned as waste.

~~~python
def test_recipe_with_missing_input_is_atomic() -> None:
    inventory = Inventory(capacity=1_000, quantities={Resource.TIMBER: 4})
    before = inventory.model_copy(deep=True)
    with pytest.raises(InsufficientResource):
        inventory.apply(Recipe(inputs={Resource.TIMBER: 5}, outputs={Resource.PLANK: 2}))
    assert inventory == before
~~~

- [ ] **Step 2: Implement fixed-unit inventory operations**

Inventory validates nonnegative integer quantities and total capacity. plan_transaction returns an InventoryDelta without mutation. apply_delta validates every resulting quantity and capacity before constructing the replacement inventory.

- [ ] **Step 3: Implement recipes and work orders**

Recipe declares exact integer inputs, outputs, required skills, tool wear, and labor minutes. WorkOrder targets gathering, crafting, hauling, farming, care, or construction. Workers are processed by sorted person ID so iteration order cannot change results.

- [ ] **Step 4: Implement construction projects**

ConstructionProject stores location, required materials, required labor by skill, delivered materials, completed labor, and status. A project becomes complete only when all material and labor requirements are satisfied; completion emits one BuildingCompleted event and cannot repeat.

- [ ] **Step 5: Test failure atomicity and deterministic allocation**

Test two workers competing for one tool, interrupted hauling, insufficient storage, a worker dying before work resolution, and project completion on an exact boundary.

Run: pytest tests/test_resources.py tests/test_work.py -v

Expected: all tests pass.

- [ ] **Step 6: Commit**

~~~bash
git add src/sovereign_world/resources.py src/sovereign_world/work.py tests/test_resources.py tests/test_work.py
git commit -m "feat: add atomic economy and construction"
~~~

### Task 5: Ordered Events and the Daily Engine

**Files:**
- Create: src/sovereign_world/events.py
- Create: src/sovereign_world/state.py
- Create: src/sovereign_world/engine.py
- Test: tests/test_events.py
- Test: tests/test_engine.py
- Test: tests/property/test_engine_invariants.py

**Interfaces:**
- Consumes: GeneratedWorld, Population, Inventory, WorkOrder
- Produces: DomainEvent, EventBatch, WorldState, advance_day(state), state_hash(state)

- [ ] **Step 1: Write failing stable-order tests**

Construct the same WorldState with dictionaries inserted in opposite orders. Schedule hunger, work completion, birth, and death on the same day. Both runs must emit byte-identical event JSON and the same state hash.

- [ ] **Step 2: Define the event schema**

Every DomainEvent has run_id, day, phase, sequence, kind, actor_id, subject_id, and a typed payload. Phase is an integer enum with this fixed order:

1. COMMAND
2. MOVEMENT
3. CONSUMPTION
4. WORK
5. HEALTH
6. BIRTH
7. DEATH
8. PROJECT
9. REPORT

Events sort by day, phase, actor ID, subject ID, then sequence. Serialization uses sorted compact JSON.

- [ ] **Step 3: Define aggregate WorldState and canonical hashing**

WorldState contains manifest hash, current day, map, civilizations, people, inventories, work orders, projects, active decrees, and next ID sequences. state_hash serializes canonical sorted representations and excludes caches only.

- [ ] **Step 4: Implement the daily transition pipeline**

advance_day deep-copies or functionally replaces state, executes each phase in the documented order, validates invariants, increments the day only after success, and returns TransitionResult(state, events). Any exception discards the candidate state.

~~~python
def advance_day(state: WorldState, rng: StableRng) -> TransitionResult:
    candidate = state.model_copy(deep=True)
    events: list[DomainEvent] = []
    for phase in PHASE_ORDER:
        phase_events = PHASE_HANDLERS[phase](candidate, rng.stream(f"{state.day}:{phase.name}"))
        events.extend(sorted(phase_events, key=DomainEvent.sort_key))
    candidate.day += 1
    validate_world(candidate)
    return TransitionResult(state=candidate, events=EventBatch.assign_sequences(events))
~~~

- [ ] **Step 5: Add invariant and rollback properties**

Property tests assert no negative resources, no missing people, monotonic time, dead people remain dead, completed projects stay complete, and an injected phase exception leaves the input state hash unchanged.

Run: pytest tests/test_events.py tests/test_engine.py tests/property/test_engine_invariants.py -v

Expected: all tests pass.

- [ ] **Step 6: Commit**

~~~bash
git add src/sovereign_world/events.py src/sovereign_world/state.py src/sovereign_world/engine.py tests/test_events.py tests/test_engine.py tests/property/test_engine_invariants.py
git commit -m "feat: advance worlds through ordered daily transitions"
~~~

### Task 6: Command Boundary and Scripted Sovereigns

**Files:**
- Create: src/sovereign_world/commands.py
- Create: src/sovereign_world/scripted.py
- Modify: src/sovereign_world/engine.py
- Test: tests/test_commands.py
- Test: tests/test_scripted.py
- Test: tests/integration/test_monthly_council.py

**Interfaces:**
- Consumes: WorldState, WorldConfig.council_interval_days
- Produces: Decree, DirectOrder, CouncilReport, CommandEnvelope, CommandValidation, Sovereign protocol, plan_baseline_commands(report), BaselineSovereign.decide(report)

- [ ] **Step 1: Write failing command-boundary tests**

Cover unknown people, cross-civilization targets, duplicate IDs, more than eight commands, expired targets, negative priorities, and valid standing decrees. Assert every rejection returns a stable error code and causes no world mutation.

- [ ] **Step 2: Implement versioned command schemas**

CommandEnvelope contains schema_version, civilization_id, council_day, correlation_id, up to eight decrees or direct orders, and optional rationale text that is logged but never interpreted by the engine.

DirectOrder supports assign_work, start_project, cancel_project, relocate_group, and request_survey in Phase 1. Decree supports food_reserve_target, labor_priority, settlement_radius, and population_growth_policy.

- [ ] **Step 3: Implement semantic validation**

validate_envelope(envelope, state) returns accepted commands and explicit CommandError records. It verifies ownership, liveness, location knowledge, command count, target existence, allowed enum values, and council day. Validation never mutates state.

- [ ] **Step 4: Define scripted-sovereign protocol and baseline policy**

~~~python
class Sovereign(Protocol):
    def decide(self, report: CouncilReport) -> CommandEnvelope: ...

class BaselineSovereign:
    def decide(self, report: CouncilReport) -> CommandEnvelope:
        commands = plan_baseline_commands(report)
        return CommandEnvelope(
            schema_version=1,
            civilization_id=report.civilization_id,
            council_day=report.day,
            correlation_id=report.report_id,
            commands=commands[:8],
        )
~~~

CouncilReport contains only the civilization's own people, known tiles, inventories, projects, decrees, and events since its previous council.

plan_baseline_commands uses this fixed priority order: preserve 90 food-days, complete shelter for every household, assign unallocated adults to the region's strongest renewable resource, then add storage. It returns no more than eight commands and breaks equal choices by target ID.

- [ ] **Step 5: Schedule monthly councils**

On day 0 and every 30th day, create reports in sorted civilization order, call each scripted sovereign, validate all envelopes, and apply accepted commands in civilization order before the day's movement phase. A sovereign exception produces a SovereignUnavailable event and no commands.

- [ ] **Step 6: Verify isolation and standing orders**

Integration tests prove one civilization's report excludes another civilization and hidden tiles, invalid direct orders do not block valid commands in the same envelope, standing decrees continue for 60 days, and a failing sovereign does not stop other councils.

Run: pytest tests/test_commands.py tests/test_scripted.py tests/integration/test_monthly_council.py -v

Expected: all tests pass.

- [ ] **Step 7: Commit**

~~~bash
git add src/sovereign_world/commands.py src/sovereign_world/scripted.py src/sovereign_world/engine.py tests/test_commands.py tests/test_scripted.py tests/integration/test_monthly_council.py
git commit -m "feat: add validated sovereign command boundary"
~~~

### Task 7: Persistence, Checkpoints, and Exact Replay

**Files:**
- Create: src/sovereign_world/persistence.py
- Create: src/sovereign_world/replay.py
- Test: tests/test_persistence.py
- Test: tests/integration/test_replay.py
- Test: tests/integration/test_recovery.py

**Interfaces:**
- Consumes: RunManifest, WorldState, EventBatch, CommandEnvelope
- Produces: WorldStore.create, append_record, save_checkpoint, load_checkpoint, replay_run, verify_run

- [ ] **Step 1: Write failing round-trip and corruption tests**

Persist a 90-day run, reload the latest checkpoint, replay from day 0, and compare state hashes at days 30, 60, and 90.

Create a copy with half a final JSON record and assert recovery ignores only that uncommitted tail. Corrupt a committed record checksum and assert JournalCorruption identifies its byte offset. Corrupt the newest checkpoint and assert recovery selects the previous verified checkpoint.

- [ ] **Step 2: Implement SQLite metadata and checkpoint tables**

Use WAL mode, foreign keys, and transactions. Store run manifest JSON and hash once. Checkpoints store day, state JSON compressed with gzip, uncompressed SHA-256, previous checkpoint hash, and creation timestamp.

- [ ] **Step 3: Implement append-only journal framing**

Each JSONL record contains sequence, type, payload, previous_hash, and record_hash. record_hash covers the canonical record without record_hash. Flush and fsync after each committed council batch and checkpoint marker.

- [ ] **Step 4: Implement verified loading and replay**

Read complete newline-terminated records only. Verify sequence, previous_hash, and record_hash. replay_run loads the earliest requested verified checkpoint and applies recorded accepted commands; it never invokes sovereigns.

- [ ] **Step 5: Test crash recovery and deterministic hashes**

Inject failures before journal append, after journal append but before checkpoint, and during checkpoint replacement. Assert recovery produces either the prior committed day or the fully committed next day, never a mixed state.

Run: pytest tests/test_persistence.py tests/integration/test_replay.py tests/integration/test_recovery.py -v

Expected: all tests pass.

- [ ] **Step 6: Commit**

~~~bash
git add src/sovereign_world/persistence.py src/sovereign_world/replay.py tests/test_persistence.py tests/integration/test_replay.py tests/integration/test_recovery.py
git commit -m "feat: persist and replay verified world history"
~~~

### Task 8: CLI, Multigenerational Acceptance Run, and Soak Verification

**Files:**
- Create: src/sovereign_world/cli.py
- Create: tests/acceptance/test_rules_laboratory.py
- Create: tests/soak/test_seed_matrix.py
- Create: docs/rules-laboratory.md
- Modify: pyproject.toml

**Interfaces:**
- Consumes: all Phase 1 interfaces
- Produces: sovereign-world init, run, inspect, checkpoint, replay, verify

- [ ] **Step 1: Write failing CLI acceptance tests**

Use Typer CliRunner to initialize a run in a temporary directory, advance 3,650 days, inspect the four civilizations, checkpoint, replay, and verify hashes.

~~~python
def test_ten_year_rules_laboratory_run(tmp_path: Path) -> None:
    result = runner.invoke(app, ["init", str(tmp_path), "--seed", "21"])
    assert result.exit_code == 0
    assert runner.invoke(app, ["run", str(tmp_path), "--days", "3650"]).exit_code == 0
    verify = runner.invoke(app, ["verify", str(tmp_path)])
    assert verify.exit_code == 0
    assert "verified through day 3650" in verify.stdout
~~~

- [ ] **Step 2: Implement CLI commands without edit operations**

init creates and locks a manifest plus day-zero checkpoint. run advances from the latest verified state. inspect prints summaries without mutation. checkpoint writes a verified snapshot. replay writes no live state and reports comparison hashes. verify checks manifest, journal, checkpoints, invariants, and replay.

Do not add set, patch, heal, spawn, grant, delete, or map-edit commands.

- [ ] **Step 3: Add the multigenerational acceptance scenario**

Run four baseline sovereigns for 100 in-world years. Assert each start produces at least one second-generation adult in the viability fixture, at least one completed shelter and food-storage structure, no negative inventories, and exact replay.

This fixture uses forgiving mortality and abundant local resources to test mechanics, not competitive balance.

- [ ] **Step 4: Add the 100-seed soak matrix**

Parameterize seeds 0 through 99 for a 50-year accelerated run. Record survival years, peak population, births, deaths by cause, food-shortage days, completed buildings, and invariant failures. The test fails on any invariant error, replay mismatch, generator exhaustion, or engine exception; demographic outcomes may vary.

Mark the matrix soak so normal unit tests can exclude it.

- [ ] **Step 5: Document local operation**

docs/rules-laboratory.md includes exact environment setup, init, run, inspect, replay, verify, ordinary test, soak test, and data-directory layout commands. State clearly that Phase 1 uses scripted sovereigns and contains no real model integration.

- [ ] **Step 6: Run the complete phase gate**

Run:

~~~bash
ruff check .
mypy src
pytest -m "not soak" -v
pytest -m soak -v
sovereign-world init work/demo-world --seed 21 --width 48 --height 48
sovereign-world run work/demo-world --days 3650
sovereign-world verify work/demo-world
~~~

Expected: lint and types pass; all unit, property, integration, acceptance, and 100-seed soak tests pass; the demonstration world verifies through day 3650.

- [ ] **Step 7: Commit**

~~~bash
git add pyproject.toml src/sovereign_world/cli.py tests/acceptance tests/soak docs/rules-laboratory.md
git commit -m "feat: deliver replayable rules laboratory"
~~~

## Phase Completion Gate

Before beginning the Civilization Layer plan:

- every non-soak and soak test passes;
- replayed state hashes match original hashes at every checkpoint;
- all 100 seeds complete without invariant violations;
- all four start packages pass the viability fixture;
- the CLI exposes no state-editing command;
- the source tree contains no provider or model integration;
- docs/rules-laboratory.md reproduces a verified 10-year run from a clean environment.
