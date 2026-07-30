## 1. Baseline and Change Coordination

- [x] 1.1 Build a traceability matrix mapping the ten live-test problems to requirements, current working-tree fixes, source modules, tests, and acceptance evidence.
- [x] 1.2 Map this change against `make-skill-interactions-frontend-first`, `stabilize-search-material-collection-workflow`, and `close-high-value-collection-runtime-gaps`, identifying tasks to reuse, supersede, or leave independent.
- [x] 1.3 Capture a sanitized fixture for session `20260729_171047` covering prior selector failure, result-history archival failure, checkpoint-attempt mismatch, blocked recovery, popup delay, and successful 255-row completion.
- [x] 1.4 Run the existing targeted suites before refactoring and record the baseline without staging, reverting, or overwriting unrelated user changes.
- [x] 1.5 Verify all fixtures and evidence exclude cookies, credentials, QR/SMS data, production approvals, upload actions, and source-image contents.

## 2. Agent Wait Lease and Bounded Waiting

- [x] 2.1 Add a versioned optional `agent_wait` envelope to session state with wait ID, claimant, exact session/stage, expected revision, timestamps, and expiry.
- [x] 2.2 Implement locked create, renew, read, expire, and clear operations for `agent_wait` without granting processing rights.
- [x] 2.3 Add API/CLI operations that register and renew a wait lease and return the authoritative current handoff state.
- [x] 2.4 Extend `wait-handoff` to support 30-second segments that distinguish normal timeout, valid handoff, stage change, session change, and service failure.
- [x] 2.5 Implement stage-class watch budgets of 5, 10, or 15 minutes and ensure approval/production timeout never creates authorization.
- [x] 2.6 Clear the matching wait lease when a processing claim is successfully created, while allowing expired waits to disappear without cleanup work.
- [x] 2.7 Add concurrency tests proving multiple waiters remain advisory and only one processing claim can be created.

## 3. Current-Chat “已提交” Recovery

- [x] 3.1 Implement one recovery resolver with precedence for bound completed result, live processing, recoverable processing, ready handoff, needs-user-input/blocked, and draft.
- [x] 3.2 Add a current-session resume command that requires an explicit session binding and never selects the newest runs directory.
- [x] 3.3 Make the “已提交” route recompute and validate session, current stage, revision, handoff identity, and input SHA before claim.
- [x] 3.4 Return “not formally submitted” for draft stages without constructing or mutating a handoff.
- [x] 3.5 Return live progress for repeated “已提交” while processing and reuse a completed result after processing has finished.
- [x] 3.6 Require the full page-generated recovery instruction when the chat is new, context is missing, or more than one session is plausible.
- [x] 3.7 Add tests proving “已提交” cannot approve, publish, change stores, or satisfy any exact production confirmation.

## 4. Frontend Handoff Status and User Feedback

- [x] 4.1 Extend the session API projection with authoritative waiting, waiting-expired, ready, processing, recoverable, completed, and blocked display states.
- [x] 4.2 Show “Codex 正在监听” only while the server-side wait lease is valid and display its bounded remaining time.
- [x] 4.3 Show the exact “在当前聊天输入已提交” recovery prompt when a handoff is ready but no wait lease is valid.
- [x] 4.4 Replace waiting feedback with processing claim and Worker progress immediately after claim.
- [x] 4.5 Preserve normal editing while waiting and preserve existing freeze/withdraw rules after formal submission.
- [x] 4.6 Add frontend tests for lease renewal, lease expiry, submit during wait, submit after wait expiry, duplicate acknowledgement, and stage navigation.
- [x] 4.7 Verify hydration and status rendering do not mark forms dirty, save drafts, increment revisions, or create handoffs.

## 5. Unified Atomic Persistence and Encoding

- [x] 5.1 Inventory every JSON/CSV/checkpoint/manifest atomic writer and reader in session, service, setup, Worker, supplement, and browser runtime modules.
- [x] 5.2 Create one shared atomic persistence utility that creates parents, uses unique same-directory temporary files, flushes/fsyncs, replaces atomically, and cleans only its own temporary file.
- [x] 5.3 Add bounded Windows replacement retry for transient sharing violations without retrying identity or permission failures.
- [x] 5.4 Standardize JSON output as UTF-8 without BOM, accept UTF-8 and UTF-8-SIG input, and document canonical bytes used for SHA identity.
- [x] 5.5 Preserve Excel-compatible UTF-8-SIG CSV output and verify CSV hashes and readers remain stable.
- [x] 5.6 Migrate existing writers incrementally and remove duplicate implementations only after compatibility tests pass.
- [x] 5.7 Add tests for first-time parent creation, concurrent writers, interrupted temporary writes, BOM input, long Windows paths, and cleanup failure.

## 6. Canonical Runtime Environment Entry

- [x] 6.1 Define the canonical module root, project virtual environment, project cache, and environment fingerprint in one runtime resolver.
- [x] 6.2 Update managed UI, collection, recovery, and test entry points to use the resolver instead of the caller’s current directory.
- [x] 6.3 Preflight interpreter version, required imports, lock/fingerprint identity, writable project cache, local selectors, configuration, and CDP before claiming a business handoff.
- [x] 6.4 Return stable reason codes and a single explicit bootstrap action for incomplete environments.
- [x] 6.5 Prove prepared offline environments run without dependency resolution or network access.
- [x] 6.6 Add regression coverage for simultaneous root and module `.venv` directories, missing Flask/Playwright, protected user cache, and PowerShell `npm.ps1` policy restrictions.

## 7. Attempt-Isolated Collection and Recovery State

- [x] 7.1 Move Worker CSV, checkpoint, selector error, log, manifest, and attempt result writes into the owning attempt directory.
- [x] 7.2 Add atomic current-result publication after validating revision, input/selector/store identity, CSV/checkpoint SHA, row count, and unique product IDs.
- [x] 7.3 Add compatibility reads/projections for historical consumers of the shared promotion CSV and checkpoint paths.
- [x] 7.4 Migrate legacy shared checkpoints by validating non-attempt identity, archiving them to the old attempt, and failing closed on any other mismatch.
- [x] 7.5 Reuse checkpoint state only for the same attempt and resume strictly after the last atomically completed page.
- [x] 7.6 Reject all late progress, checkpoint, result, and publication writes from stale claims or attempts.
- [x] 7.7 Consolidate blocked, needs-user-input, recoverable, live Worker, historical result, and completed result rendering into the single resolver.
- [x] 7.8 Permit repaired blocked/needs-user-input handoffs to resume without asking the user to re-enter unchanged business values.
- [x] 7.9 Add migration and regression tests for empty failed checkpoint, page-N checkpoint, changed input, changed selector, superseded history, and duplicate-free resume.

## 8. Windows Worker Ownership

- [x] 8.1 Centralize Win32 process identity calls with explicit ctypes argument and return signatures.
- [x] 8.2 Preserve launcher PID and creation identity while allowing the verified child Worker to claim its actual PID.
- [x] 8.3 Bind private Worker ownership to token, session, stage, attempt, revision, input SHA, selector SHA, launcher identity, and actual process identity.
- [x] 8.4 Treat PID reuse, inaccessible process identity, token mismatch, and launcher mismatch as indeterminate ownership.
- [x] 8.5 Ensure indeterminate processes are never killed or reclaimed before lease expiry.
- [x] 8.6 Add Windows parent/child launcher, PID reuse, fast exit, duplicate launch, crash, heartbeat, and recovery tests using a short writable basetemp.

## 9. Browser Popup and Outcome Verification

- [x] 9.1 Replace fixed blind popup loops with an identified-control state machine that records popup identity and before/after state.
- [x] 9.2 Stop retrying a popup control after two consecutive actions produce no observable change.
- [x] 9.3 Verify promotion tab, high-value filter, and next-page actions using selected state, URL/tab state, page number, or row identity.
- [x] 9.4 Allow one DOM-click fallback only for declared read-only targets blocked by a recognized safe overlay.
- [x] 9.5 Prohibit force/DOM fallback for any write, approval, production confirmation, or publish target.
- [x] 9.6 Persist bounded selector evidence with action, target, retry, elapsed time, overlay summary, profile identity, and safe screenshot/DOM reference.
- [x] 9.7 Add regression tests for multiple simultaneous guides, delayed guides, swallowed clicks, successful DOM fallback, persistent no-op controls, unknown overlays, and unchanged pagination.
- [ ] 9.8 Re-run the maintained `supplement --scan-mode high-value` path and verify no temporary diagnostic collector remains.

## 10. Progress Observability and Input Quality

- [x] 10.1 Extend Worker progress with action, target, retry count, elapsed time, current/last page, rows, heartbeat, checkpoint, and next recovery action.
- [x] 10.2 Render actionable pre-checkpoint and popup-cleanup progress instead of a long-lived generic phase.
- [ ] 10.3 Use one reason-code vocabulary across logs, API status, page messages, evidence, and recovery commands.
- [x] 10.4 Generate a stage-one input-quality summary with total, valid, duplicate-ID, missing-ID, invalid-ID, excluded, and processable counts.
- [x] 10.5 Display source-row reason summaries before formal setup submission and reuse the same summary in completeness results.
- [x] 10.6 Allow valid rows to continue when some rows are invalid, and block browser collection only for unreadable/empty tables or missing required headers.
- [x] 10.7 Add tests reproducing the 611-row input with 44 row anomalies and verify the valid product path remains unchanged.

## 11. Automated Regression and Safety Validation

- [x] 11.1 Run targeted handoff, session, frontend, persistence, runtime, Worker, popup, checkpoint, setup, and data-quality suites.
- [x] 11.2 Run the complete Python suite with a short project-writable basetemp and report cleanup permission issues separately from test failures.
- [x] 11.3 Run frontend syntax, unit, and browser tests for wait status, submit/claim transitions, and no-write hydration.
- [x] 11.4 Run Skill validation, OpenSpec strict validation, environment lock checks, `git diff --check`, and cross-computer absolute-path scans.
- [x] 11.5 Verify duplicate “已提交”, duplicate frontend submit, concurrent waiters, concurrent claim, and stale Worker writes remain idempotent.
- [x] 11.6 Verify no test invokes approve, publish, upload, login automation, credential persistence, or source-image mutation.

## 12. Live Acceptance and Documentation

- [ ] 12.1 Replay sanitized session `20260729_171047` and verify all prior failure points resolve through the new authoritative state model.
- [ ] 12.2 Start a fresh timestamp session and verify the page shows a live wait lease, auto-continues when submitted during the window, and uses “已提交” after forced wait interruption. Superseded by `stabilize-live-ui-persistence-and-local-access` tasks 4.1-4.8 and 8.3/8.6 because the real test invalidated the original 90-second/five-minute lease assumptions.
- [ ] 12.3 Complete all visible high-value pages through the maintained collector and reconcile page count, 255 unique products or the current live total, checkpoint, CSV, and completeness matrix.
- [ ] 12.4 Interrupt a separate run before claim, after claim, and after page-N checkpoint; verify exact-session recovery and zero duplicate side effects.
- [ ] 12.5 Verify Windows launcher identity, prepared environment selection, atomic first-history write, attempt isolation, and UTF-8-SIG compatibility in the live workspace.
- [ ] 12.6 Record evidence paths and confirm the acceptance stops before approval, upload, or production publish.
- [x] 12.7 Update the canonical Skill and operations guide with bounded wait, wait lease, “已提交” semantics, attempt paths, progress, and recovery behavior.
- [x] 12.8 Regenerate the repository Skill discovery entry and verify its canonical SHA after Skill edits.
- [x] 12.9 Reconcile the three related OpenSpec changes by marking migrated tasks, documenting superseded work, and leaving unrelated incomplete live acceptance explicit.
- [ ] 12.10 Only mark this change ready to archive after automated and live acceptance evidence exists and all ten problem categories have a closed traceability entry.
