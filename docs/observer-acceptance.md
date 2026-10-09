# Observer acceptance checklist (Phase 4)

Every promise the Phase 4 plan (`docs/superpowers/plans/2026-10-01-phase-4-observer.md`) and
the roadmap's exit gate make about the observer, each with the tests that prove it. Python
tests are named `test_…` and live under `tests/`; browser tests are named by their file under
`observer/tests/` and their title. `tests/acceptance/test_phase_four_checklist.py` checks that
every test named here exists, so this list cannot quietly go stale.

Run them:

```sh
.venv/bin/pytest -m "not soak"                                   # Python, about 28 minutes
cd observer && node --test --test-concurrency=1 tests/*.test.mjs # browser, about 23 minutes
```

Never run the two at once.

## The exit gate

> All observer operations leave the authoritative world hash unchanged, except advancing or
> restoring already committed history. (Roadmap, Phase 4.)

| Promise | Proved by |
|---|---|
| One fresh run, taken end to end: exported, served, every route asked, every day equal to its replay, every perspective equal to its council report and unchanged by hidden facts, and the run's files and times unchanged after it all | `test_a_fresh_run_passes_through_the_whole_observer` |
| A run with the observer attached and polling saves the same journal (councils and prompt hashes included), checkpoints and files as one without | `test_a_run_is_the_same_with_the_observer_attached` |
| A run driven from the page (pauses, lookahead) saves what a plain `run` saves | `test_a_run_driven_from_the_page_saves_what_run_saves`, `test_a_held_run_saves_what_an_unbroken_run_saves` |
| Reading and serving a run write nothing to it | `test_reading_a_run_writes_nothing`, `test_serving_writes_nothing_to_the_run`, `test_serving_perspectives_writes_nothing_to_the_run`, `test_the_same_run_exports_the_same_bytes_and_is_left_alone`, `server.test.mjs` › "a live run opens on its newest day, steps, and follows days saved later" |
| The observer never builds a store, so it cannot write through one | `test_the_server_never_builds_a_store`, `test_the_reader_never_builds_a_store`, `test_the_tail_never_imports_the_store_class` |
| The engine never imports the observer | `test_engine_never_imports_the_observer_and_the_exporter_avoids_persistence`, `test_the_runner_knows_nothing_of_the_observer` |

## The creator covenant (spec §4.3, §15)

The observer may inspect everything but changes nothing; its only controls are pause, resume,
speed and lookahead.

| Promise | Proved by |
|---|---|
| Speed is a presentation clock: camera, zoom, follow and pause never move what is drawn | `bands.test.mjs` › "camera, zoom and follow never change presentation positions; pause freezes them" |
| The control channel carries only pause and resume, and holds the run between days | `test_the_controlled_runner_waits_for_resume_and_reports_each_day`, `test_the_runner_goes_on_only_while_the_page_plays_close_behind`, `test_the_control_route_checks_what_it_is_given` |
| Without a runner the control answers 409 | `test_without_a_runner_the_control_answers_409` |
| A run cut back under a live runner stops it before it can write | `test_a_run_cut_back_under_a_live_runner_is_stopped_before_it_can_write`, `test_a_new_history_stops_the_runner_and_leaves_the_page_a_reason` |

## Binding revisions R1–R10

| Rule | Proved by |
|---|---|
| **R1** A close-zoom art proof | `depth.test.mjs` › "every depth case is ordered and rendered correctly", `order-snapshot.test.mjs` › "draw order matches the recorded snapshot" |
| **R2** The terrain contract carries every engine field, stone included, and keeps engine units apart from presentation | `test_every_tile_round_trips_in_exactly_one_chunk`, `test_export_is_byte_identical_across_runs`, `test_day0_matches_the_engine_starts` |
| **R3** Robust depth ordering with visual tests | `depth.test.mjs` › "no depth cycles and every asset meets the footprint contract", `depth.test.mjs` › "negative control: naive centre-depth ordering fails the long-storehouse case" |
| **R4** A large-world streaming test | `streaming.test.mjs` › "rapid jumps across a huge world stay within budgets and leave nothing behind" |
| **R5** Indoor citizens shown by highlight and occupancy, never through roofs | `depth.test.mjs` › "a citizen who went through a door is not drawn over the building", `depth.test.mjs` › "selecting an indoor citizen highlights their building instead of drawing them" |
| **R6** Chronicle locations from historical state; safe journal handling | `test_an_event_with_its_own_tile_is_placed_there`, `test_ids_are_looked_up_in_the_days_world_in_order`, `test_endings_look_at_the_day_before_first`, `test_an_id_missing_from_one_day_is_found_in_the_other`, `test_an_arrival_and_a_return_are_placed_where_the_party_stands_that_day`; journal safety below |
| **R7** The server choice (FastAPI) | `test_every_api_route_needs_the_token` (it walks FastAPI's own route table) |
| **R8** Benchmarks run on scripted worlds with no provider calls | `test_a_hundred_thousand_people_take_well_under_a_megabyte_a_day` |
| **R9** Resident, outdoor and visible counts reported separately | `counts.test.mjs` › "counts are consistent with", `crowd.test.mjs` › "counts and budgets are consistent at" |
| **R10** A real access boundary between sovereigns and the observer | access boundary below |

## Journal safety (O2)

| Promise | Proved by |
|---|---|
| A line still being written is left until complete | `test_a_line_still_being_written_is_left_until_it_is_complete` |
| A flipped byte stops the tail at the last good record, with no repair | `test_a_flipped_byte_stops_the_tail_at_the_last_good_record` |
| Truncation or replacement starts a new history | `test_truncation_and_replacement_start_a_new_history`, `test_a_journal_cut_back_starts_a_new_history`, `test_a_journal_cut_back_is_a_new_history` |
| A day appended while reading is picked up | `test_a_day_appended_while_reading_is_picked_up`, `test_refresh_finds_the_days_saved_since` |
| A live run is read through its write-ahead log | `test_a_run_whose_writer_is_still_open_reads_from_its_write_ahead_log` |
| Old (format 1) runs read the same way | `test_a_format_one_run_reads_the_same_way` |

## Access boundary (O3, O5)

| Promise | Proved by |
|---|---|
| Every `/api` route, every method, answers 401 without the token, with a wrong one, or with it in the address | `test_every_api_route_needs_the_token` |
| The page's code is served without the token; recorded runs and tests are not served | `test_the_page_is_served_without_the_token_but_not_the_runs_or_tests` |
| A wrong token stops the page with a plain message | `server.test.mjs` › "a wrong token is refused with a plain message" |
| The token is read from the address once and dropped from it | `server-source.test.mjs` › "the token is taken from the fragment and dropped from the address" |
| The runner never sees the token | `test_the_runner_never_sees_the_token` |
| An answer from another history of the run is never shown | `server-source.test.mjs` › "an answer from another history is refused, not shown", `server-source.test.mjs` › "a day whose record and people come from two histories is refused" |

## Watching from outside (slice H)

| Promise | Proved by |
|---|---|
| A viewer may look at every route but never steer; on a public observer nobody may | `test_a_viewer_may_look_at_everything_but_control_nothing`, `test_a_public_observer_lets_nobody_steer`, `viewer.test.mjs` › "nobody may steer a public observer, and its answers carry the security headers" |
| The owner's token works only on the observer's own machine | `test_the_owners_token_works_only_on_the_observers_own_machine` |
| Tokens are checked and both always compared in constant time | `test_tokens_are_checked_and_both_always_compared` |
| Requests are limited per visitor and in all; large or unmeasured bodies are refused | `test_requests_are_limited_per_client_and_in_all`, `test_large_or_unmeasured_bodies_are_refused`, `viewer.test.mjs` › "a client asking too often is held back, then served again" |
| Every answer carries the security headers, and the page needs nothing they refuse | `test_every_answer_carries_the_security_headers`, `test_the_page_needs_nothing_the_security_policy_refuses`, `viewer.test.mjs` › "a shared link is viewing only, keeps its token for a reload, and steps a day" |
| A viewer sees no path from the observer's machine | `test_a_viewer_sees_no_path_from_this_machine` |
| A public observer runs no world and listens only on its own machine | `test_a_public_observer_runs_no_world_and_stays_on_this_machine` |
| A viewing page never asks to steer; the owner's own window on a public observer is viewing only | `server-source.test.mjs` › "a viewing page never asks to steer the runner", `viewer.test.mjs` › "the owner's own window on a shared observer is viewing only and drops its token" |
| The runner never sees the viewer token | `test_the_runner_never_sees_the_token` |
