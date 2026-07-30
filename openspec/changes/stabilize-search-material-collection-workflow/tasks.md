## 1. Baseline and Runtime Safety

- [x] 1.1 Add regression fixtures for the observed fresh-session path: setup revision 3 claimed, no production selector profile, blank CDP tab, interrupted processing, and mixed valid/invalid product rows.
- [x] 1.2 Add the default root `runs/`, machine-local selector profiles, CDP profiles, locks, and generated diagnostics to Git ignore rules without deleting existing user evidence.
- [x] 1.3 Add a test proving a new default runtime session does not appear in `git status` and contains no authentication material.
- [x] 1.4 Record the pre-change failure behavior and exact recovery commands in `test_plan.md`.

## 2. Purpose-Scoped Production Selector Profiles

- [x] 2.1 Split selector requirements into shared safety fields and operation-specific sets for high-value collection, basic export, publish, and remote verification.
- [x] 2.2 Update `load_selectors` and CLI callers to request an explicit selector purpose so `supplement` no longer requires export or publish fields.
- [x] 2.3 Define selector-profile metadata for schema version, profile version, supported purposes, production marker, and optional material-center URL.
- [x] 2.4 Implement deterministic selector discovery precedence for explicit CLI path, `TMALL_SELECTORS_FILE`, local configuration, and the Git-ignored default machine-local profile.
- [x] 2.5 Reject repository examples, known placeholders, ambiguous profile discovery, unsupported purposes, and unreadable files with stable reason codes.
- [x] 2.6 Implement runtime DOM validation for store, human-check state, page identity, promotion tab, high-value filter, rows, and pagination.
- [x] 2.7 Persist selector-profile name, version, SHA-256, purpose, URL, timestamp, and per-field validation evidence in the current task.
- [x] 2.8 Add unit and CLI tests for scan-only profiles, publish-only failures, precedence, placeholder rejection, DOM failures, and evidence output.
- [x] 2.9 Add a frontend-first selector profile setup/repair panel as the primary path, with a diagnostic CLI fallback for environments where the page cannot perform the repair.

## 3. Managed CDP Login and Navigation

- [x] 3.1 Add machine-local CDP configuration for executable, profile directory, endpoint, and official material-center URL without hard-coded usernames or drive mappings.
- [x] 3.2 Implement approved CDP endpoint discovery and ownership-safe launch of a dedicated browser profile when no endpoint is available.
- [x] 3.3 Navigate a new or blank CDP page to the configured official material-center URL and never leave login preparation at an unexplained new tab.
- [x] 3.4 Detect connectivity, unauthenticated state, human checks, observed store, observed URL, and material-page identity without persisting credentials.
- [x] 3.5 Return stable `LOGIN_INTERACTION_REQUIRED`, `HUMAN_CHECK`, `STORE_IDENTITY_MISMATCH`, and page/selector reason codes with exact recovery instructions.
- [x] 3.6 Extend the interaction API and setup page to distinguish the Skill UI from CDP Chrome and display connection, login, store, URL, and next-action status.
- [x] 3.7 Resume the exact session after user login, revalidate store/page/profile identity, and automatically continue the maintained collector without asking the user to navigate promotion controls.
- [x] 3.8 Add tests for blank-tab navigation, existing endpoint reuse, correct/wrong store, login pause/resume, human checks, and ownership-safe process behavior.

## 4. Deterministic Setup-to-Collection Processor

- [x] 4.1 Add one CLI/service processing entry that accepts exact `runs_root` and `session_id` and validates setup session, stage, revision, and input SHA-256.
- [x] 4.2 Move input snapshot, SHA-256 manifest generation, product-table schema preflight, and row-level anomaly output into the maintained processor.
- [x] 4.3 Generate the required `scan-summary.json` with batch status, source row numbers, and reason codes instead of relying on Agent-created ad-hoc artifacts.
- [x] 4.4 Resolve and validate the production high-value selector profile before browser collection.
- [x] 4.5 Prefer the existing `supplement --scan-mode high-value` implementation without `--max-pages`, preserving delayed-popup handling, full pagination, incremental CSV writes, and checkpointing.
- [x] 4.6 Add a persisted reproducible-error boundary for selector, navigation, popup, parsing, pagination, and page-state failures before Playwright diagnostic repair is allowed.
- [x] 4.7 Require Playwright-assisted fixes to update the maintained selector profile or collector, add a regression test, and rerun the original supplement entry without retaining a second production script.
- [x] 4.8 Generate `completeness-matrix.json` from the collected high-value output and valid product snapshot, preserving row-level anomalies separately.
- [x] 4.9 Write a result bound to the exact setup submission, mark setup completed, and advance to the completeness stage only after every required output validates.
- [x] 4.10 Make identical processor calls idempotent and reject checkpoint reuse when revision, input hash, store, or selector-profile hash differs.
- [x] 4.11 Add integration tests for success, partial-page interruption/resume, selector failure, popup/navigation/parser/pagination repair, empty/invalid tables, mixed row anomalies, stale inputs, and repeated identical calls.

## 5. Recoverable Processing Leases

- [x] 5.1 Extend session state with claim ID, claimant identity when available, claimed time, heartbeat time, lease expiry, bound revision, and bound input SHA-256.
- [x] 5.2 Make claim acquisition atomic and reject concurrent claims while an unexpired lease is active.
- [x] 5.3 Keep submitted handoff evidence immutable while status APIs and the UI derive current processing state from the authoritative lease.
- [x] 5.4 Add ownership-aware heartbeat renewal and reject writes from expired or replaced claims.
- [x] 5.5 Add explicit reclaim for expired processing that validates checkpoints and existing bound outputs before resuming.
- [x] 5.6 Return existing completed results for identical submissions and prevent a new claim from overwriting a differently bound result.
- [x] 5.7 Add migration and recovery handling for legacy sessions already stuck in `processing` without lease fields.
- [x] 5.8 Add UI status and recovery actions for active Agent, stale Agent, resumable checkpoint, and manual repair states.
- [x] 5.9 Add concurrency, stale-write, late-response, legacy-session, and completed-result idempotency tests.

## 6. Slot Semantics, Evidence, and Time

- [x] 6.1 Change batch collection output to explicitly store target capacity, current count, missing count, and `exact_slot_status=not_collected`.
- [x] 6.2 Prevent completeness and planning code from interpreting a batch missing count as concrete empty slot identifiers.
- [x] 6.3 Add or adapt the per-product pre-production verifier to collect exact current empty slot identities immediately before approval/publish planning.
- [x] 6.4 Invalidate affected plans when exact slot verification detects changed capacity or an occupied target slot.
- [x] 6.5 Persist target store, observed store, page URL, page identity, selector identity, match result, and verification time before collection rows are written.
- [x] 6.6 Centralize timezone-aware ISO 8601 generation and migrate new session, event, heartbeat, validation, checkpoint, and result timestamps to it.
- [x] 6.7 Display product-row anomaly counts and source-row reason codes in the completeness UI while excluding them from selectable products.
- [x] 6.8 Add schema, parser, UI, exact-verification, invalidation, timestamp, and evidence tests.

## 7. Skill, Operations, and User Guidance

- [x] 7.1 Update the canonical Skill to prefer `supplement --scan-mode high-value`, allow reason-coded Playwright diagnosis when it fails, and require every repair to converge back into the maintained selector profile or collector with tests.
- [x] 7.2 Regenerate and validate the repository Skill discovery entry and Agent metadata after canonical Skill changes.
- [x] 7.3 Update the operations guide with selector-profile discovery, frontend repair, diagnostic fallback, CDP launch/login, collection, recovery, and exact-slot verification commands.
- [x] 7.4 Update error handling and data-schema references with selector purpose errors, claim lease fields, evidence files, and batch-versus-exact slot semantics.
- [x] 7.5 Update the frontend interaction contract so login remains a controlled chat/native-page boundary while every structured collection decision remains frontend-first.
- [x] 7.6 Update `test_plan.md` with fresh-computer selector setup, two-browser UX, delayed seven-step guide, interruption recovery, and Git-isolation acceptance.

## 8. End-to-End Verification

> Reconciliation: unchecked live tasks 8.3–8.5 are not silently completed. Their
> acceptance scope is superseded by
> `harden-handoff-and-live-workflow-reliability` tasks 12.2–12.6.

- [x] 8.1 Run the full Python and frontend test suites, selector-profile validation tests, Skill validator, OpenSpec strict validation, lock check, and `git diff --check`.
- [x] 8.2 Run a deterministic fake-browser acceptance covering setup submission through completeness-stage hydration.
- [ ] 8.3 Start a fresh timestamp session on the current computer, use the frontend to configure it, and verify CDP Chrome opens the official material center instead of a blank tab.
- [ ] 8.4 After user-controlled login, verify the processor directly invokes the maintained high-value collector, handles the delayed guide/popups, scans every page, and writes checkpoint/evidence files without a temporary script.
- [ ] 8.5 Interrupt collection after at least one page, expire/reclaim the processing lease, and verify exact-session resume without duplicate rows or repeated completed pages.
- [x] 8.6 Verify the completeness page shows valid products, row anomalies, target/current/missing counts, and `exact slots not yet collected` without claiming concrete empty slot numbers.
- [x] 8.7 Stop at completeness review/dry-run boundaries and confirm no approval, upload, publish, credential capture, or source-material mutation occurred.
