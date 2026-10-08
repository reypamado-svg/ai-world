# AI Civilization World — Implementation Roadmap

**Spec:** docs/superpowers/specs/2026-09-21-ai-civilization-world-design.md

The specification is divided into five implementation plans. Each phase ends with working, testable software and fixes its public interfaces before the next phase begins.

## Phase 1: Rules Laboratory

Deliver a deterministic simulation CLI with seeded world generation, four balanced starting regions, 128 founders, individual lifecycles, resources, work, construction, monthly commands from scripted sovereigns, snapshots, event logs, and exact replay.

Detailed plan: docs/superpowers/plans/2026-09-21-rules-laboratory.md

Exit condition: 100 seeded multigenerational runs complete without invariant violations, and every replay matches its original checkpoint hashes.

## Phase 2: Civilization Layer

Add knowledge capabilities, teaching and skill loss, institutions, exploration and fog of knowledge, derived territory, messengers, ambassadors, translation, trade, migration, espionage, warfare, surrender, allegiance transfer, assimilation, elimination, and last-civilization detection.

Exit condition: scripted sovereigns can complete isolated development, first contact, treaty, trade, war, surrender, assimilation, extinction, and final-survivor scenarios without hidden-information leaks.

## Phase 3: Sovereign Gateway

Add private council reports, four-layer sovereign memory, structured command envelopes, equal budgets, OpenAI and Anthropic adapters, a same-PC local adapter, an authenticated second-PC adapter, retries, schema repair, provider isolation, and recorded raw replies.

Exit condition: provider-failure and hostile-message tests prove that no model can mutate state outside validated commands, and provider outages do not alter world time or standing orders.

## Phase 4: Observer Experience

Add a read-only web application showing the live map, settlements, families, people, projects, knowledge, commands, diplomacy, wars, metrics, and chronicle. Add pause, resume, display-speed control, checkpoint recovery, and replay inspection without live state editing.

Exit condition: all observer operations leave the authoritative world hash unchanged, except advancing or restoring already committed history.

## Phase 5: Sealed Trial World

Run balance calibration with rotated starting packages and thousands of scripted histories. Freeze the run manifest, rule hash, provider assignments, prompts, budgets, and remote endpoint policy. Launch one real model-played world (planned with four sovereigns; run with three) and monitor only integrity and infrastructure.

Exit condition: the signed manifest is immutable, all launch-gate checks pass, and the live world can recover from the last checkpoint without divergent replay.

## Dependency Order

Phase 1 defines deterministic state and event contracts. Phase 2 extends only those contracts. Phase 3 consumes Phase 2 reports and commands without gaining state access. Phase 4 reads committed state and events without becoming a writer. Phase 5 deploys the tested outputs of all earlier phases.

The detailed plan for each later phase is written only after its predecessor passes its completion gate. This keeps its file paths, signatures, migrations, and tests grounded in the interfaces that actually shipped rather than guessed in advance.

## Status

- **Phase 1, Rules Laboratory:** complete (`docs/superpowers/plans/2026-09-21-rules-laboratory.md`; exit test `tests/acceptance/test_rules_laboratory.py`).
- **Phase 2, Civilization Society:** complete (`docs/superpowers/plans/2026-09-30-phase-2-society.md`; exit test `tests/acceptance/test_phase_two_exit.py`).
- **Phase 3, Sovereign Gateway:** complete (`docs/superpowers/plans/2026-10-01-phase-3-sovereign-gateway.md`; exit test `tests/acceptance/test_phase_three_exit.py`).
- **Phase 4, Observer Experience:** complete (`docs/superpowers/plans/2026-10-01-phase-4-observer.md`; exit test `tests/acceptance/test_phase_four_exit.py`; checklist `docs/observer-acceptance.md`).
- **Phase 5, Sealed Trial World:** built; the live year is pending. Plan `docs/superpowers/plans/2026-10-07-sealed-trial.md`; exit tests `tests/acceptance/test_phase_five_exit.py` and `tests/acceptance/test_free_trial_exit.py` prove the gate on stand-ins. The year runs on the user's PC by `docs/sealed-trial-runbook.md`; its record goes in `docs/sealed-trial-record.md`, which closes the phase when filled in.
