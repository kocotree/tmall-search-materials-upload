## 1. Live Failure Baseline and Compatibility

- [x] 1.1 Add an isolated fixture reproducing session `20260729_090032`: current stage `processing`, an older `SELECTOR_PROFILE_NOT_FOUND` result, valid newer selector/store evidence, an expired or dead worker, and no first checkpoint.
- [x] 1.2 Add regression tests proving the current API/UI does not present historical selector failure and dead-worker processing as two simultaneous active states.
- [x] 1.3 Inventory the existing `process-setup`, `supplement --scan-mode high-value`, selector-profile, checkpoint, claim, managed-service, and frontend status entry points and record which existing contracts are reused.
- [x] 1.4 Define versioned `collection_readiness`, `collection_attempt`, `worker_status`, and progress envelopes with compatibility reads for historical sessions.
- [x] 1.5 Verify baseline tests and fixtures contain no cookies, tokens, QR payloads, SMS codes, production writes, or source-material reads.

## 2. Stage-One Readiness and Selector Bootstrap

- [x] 2.1 Implement independent readiness checks for project environment, selector schema, selector current-DOM validation, CDP connection, login/human-check, material-page identity, and target-store identity.
- [x] 2.2 Return readiness reason codes, safe Chinese explanations, next actions, check timestamps, and evidence identities through the stage-one API.
- [x] 2.3 Allow incomplete drafts but prevent a collection-ready submission while any machine-checkable prerequisite is invalid or any required native login action is incomplete.
- [x] 2.4 Render each prerequisite separately in the configuration page and distinguish configuration UI, CDP Chrome, user action, blocking configuration, and collector failure.
- [x] 2.5 Add a guided action that creates a Git-ignored non-production selector candidate when no local production profile exists.
- [x] 2.6 Validate every required `high_value_collection` field, page identity, human-check selector, and store identity against the current CDP page before promoting a candidate.
- [x] 2.7 Persist promoted profile name, version, supported purpose, current-DOM validation timestamp, field results, URL identity, and SHA-256.
- [x] 2.8 Keep failed candidates non-production, name the exact failed field, and expose the controlled Playwright repair action without using repository examples in production.
- [x] 2.9 Add API/frontend tests for missing profile, invalid candidate, login required, wrong store, fully ready, draft saving, and final submission gating.

## 3. Maintained Collector Guide and Popup Hardening

- [x] 3.1 Model delayed onboarding handling as a bounded state machine with a quiet window, whitelist, attempt count, and explicit terminal reasons.
- [x] 3.2 Extend the maintained selector profile schema for current safe guide progress/close controls without adding production upload actions.
- [x] 3.3 Restrict force-click fallback to declared read-only promotion navigation and high-value filter targets intercepted by a recognized guide.
- [x] 3.4 Persist bounded safe failure evidence containing URL, selector-profile identity, target field, overlay summary, timestamp, and screenshot/DOM reference without sensitive page content.
- [x] 3.5 Add regression tests for guides appearing before navigation, after navigation, after a delay, across multiple steps, and for unknown/unsafe overlays.
- [x] 3.6 Prove collector failures never become zero-row or zero-material success and preserve all completed checkpoints.
- [x] 3.7 Delete disposable diagnostics after evidence capture and test that routine collection still reaches only the maintained `supplement --scan-mode high-value` implementation.

## 4. Managed Collection Worker

- [x] 4.1 Define a session-bound worker manifest with attempt ID, PID, ownership token, session/stage/revision, input SHA, selector SHA, purpose, timestamps, phase, page/row progress, checkpoint time, log path, and terminal status.
- [x] 4.2 Implement an owned background worker launcher that returns promptly and does not depend on the lifetime of the invoking shell, HTTP request, or Codex turn.
- [x] 4.3 Prevent duplicate live workers for the same attempt binding and return the existing worker/status for idempotent repeated requests.
- [x] 4.4 Launch without a visible blocking console and keep stdout/stderr in the exact session logs.
- [x] 4.5 Publish atomic heartbeats and phases for profile validation, CDP connection, popup settlement, promotion navigation, high-value selection, page collection, checkpoint writing, completeness building, and completion.
- [x] 4.6 Write page and row progress only from durable checkpoint state; before page one completes, expose phase/heartbeat without claiming zero rows.
- [x] 4.7 Bind every progress, checkpoint, and terminal write to the active attempt and reject stale worker identities.
- [x] 4.8 Add service/CLI status commands for current worker, phase, heartbeat, checkpoint, logs, and exact recovery action.
- [x] 4.9 Add process-ownership tests covering PID reuse, token mismatch, duplicate launch, foreground caller exit, worker crash, and terminal completion.

## 5. Attempt-Derived Status and Recovery

- [x] 5.1 Implement one status resolver that combines bound result, live owned worker, lease, checkpoint, blocking/user-action result, and draft/ready state in documented precedence.
- [x] 5.2 Store immutable historical results by attempt and mark older attempts superseded rather than overwriting or rendering them as current blockers.
- [x] 5.3 Update APIs and the page to show the active attempt separately from prior attempt history, including timestamps and reason codes.
- [x] 5.4 Confirm worker liveness using PID plus ownership token, attempt/session binding, and a non-privileged process identity signal; never act on PID alone.
- [x] 5.5 Mark a proven-dead owned worker immediately recoverable without waiting for nominal lease expiry.
- [x] 5.6 Keep indeterminate workers protected until lease expiry and never kill or reclaim an unproven process.
- [x] 5.7 Validate revision, input SHA, store, selector SHA, CSV/checkpoint identity, last completed page, and row uniqueness before reclaim/resume.
- [x] 5.8 Resume strictly after the last atomically completed page and reject late writes from the previous claim/attempt.
- [x] 5.9 Add regression tests for dead-before-first-checkpoint, dead-after-page-N, active slow worker, expired lease, stale result, stale claimant write, changed inputs, and duplicate-free resume.

## 6. Project-Local Runtime Environment

- [x] 6.1 Make all project launchers resolve a project-local uv cache without depending on the protected global user cache.
- [x] 6.2 Add an environment fingerprint check and prefer the existing project `.venv` executable for routine collection when lock and environment match.
- [x] 6.3 Separate dependency synchronization from collection and ensure collection/resume never implicitly downloads or resolves packages.
- [x] 6.4 Return reason-coded environment preparation errors before claiming a collection attempt.
- [x] 6.5 Add tests for inaccessible global cache, offline prepared environment, stale/missing environment, and identical collection command behavior.

## 7. Skill, UI, and Operations Contract

- [x] 7.1 Update the canonical Skill to require readiness gating, the managed collection worker, maintained collector convergence, and attempt-derived status.
- [x] 7.2 Update the operations guide with selector bootstrap, two-browser readiness, worker progress, logs, immediate/deferred recovery, and exact-session resume commands.
- [x] 7.3 Update frontend interaction, error handling, and data-schema references with readiness, attempt, worker, progress, superseded history, and recovery reason codes.
- [x] 7.4 Update `test_plan.md` with the real fresh-session, delayed-guide, first-checkpoint, full-pagination, interruption, and duplicate-free resume acceptance sequence.
- [x] 7.5 Regenerate the repository Skill discovery entry if canonical content changes and validate Agent metadata.
- [x] 7.6 Document that this change extends rather than forks `stabilize-search-material-collection-workflow`, and reconcile or supersede its remaining live tasks during archive. Reconciled in `harden-handoff-and-live-workflow-reliability/traceability.md`; the unchecked live tasks below remain explicit and are superseded by that change's section 12 acceptance.

## 8. Automated and Live Acceptance

> Reconciliation: unchecked live tasks 8.3–8.11 remain explicit evidence gaps;
> their unified acceptance owner is
> `harden-handoff-and-live-workflow-reliability` tasks 12.2–12.10.

- [x] 8.1 Run targeted readiness, selector, popup, worker, attempt-status, checkpoint, recovery, environment, API, and frontend tests.
- [x] 8.2 Run the full Python and Node suites, frontend syntax checks, Skill validator, OpenSpec strict validation, `uv lock --check`, and `git diff --check`.
- [ ] 8.3 Start a fresh timestamp session with no usable production selector and complete guided local selector setup through current-DOM validation.
- [ ] 8.4 Verify the managed CDP Chrome opens the official material center, user-controlled login succeeds, and the exact target store/page identity is displayed before submission.
- [ ] 8.5 Use only the maintained `supplement --scan-mode high-value` collector to handle the real delayed guide and write the first page CSV/checkpoint while the invoking command has already returned.
- [ ] 8.6 Verify the page shows live pre-checkpoint phase, heartbeat, page/row progress, checkpoint time, logs, and no stale historical error as the active state.
- [ ] 8.7 Complete every visible high-value page exactly once and reconcile page count, unique product IDs, checkpoint, selector/store evidence, and completeness matrix.
- [ ] 8.8 In a separate isolated run, interrupt the owned worker after at least one completed page, verify proven-dead immediate recovery, reclaim the exact session, and complete without duplicate rows or repeated pages.
- [ ] 8.9 Verify an indeterminate or ownership-mismatched process is not killed or reclaimed before lease expiry.
- [ ] 8.10 Confirm no temporary production collector, credential persistence, approve, publish, upload, source-material read, or source-material mutation occurred.
- [ ] 8.11 Record the live evidence paths and only then mark the collection path closed; otherwise retain the exact failed task and recovery condition.
