## 1. Evidence and Change Coordination

- [x] 1.1 Create a sanitized regression fixture from session `20260730_004051` containing session revision 0, input/snapshot revision 1, no handoff, and advisory wait state.
- [x] 1.2 Record the first `WinError 5`, subsequent draft/submit 409 responses, repeated wait creation events, and sandbox-versus-desktop path probe outcomes without credentials or source contents.
- [x] 1.3 Map this change to `harden-handoff-and-live-workflow-reliability` and `fix-nas-path-detection-and-folder-picker`, marking only genuinely superseded unfinished tasks and preserving independent live acceptance.
- [x] 1.4 Run and record targeted session, persistence, wait, service, path diagnostic, and folder-picker baselines before implementation.

## 2. Idempotent Stage Transactions

- [x] 2.1 Define a versioned stage transaction envelope with request ID, operation, base/target revision, normalized input SHA, progress state, timestamps, and exact session/stage identity.
- [x] 2.2 Add frontend request IDs for explicit save, autosave, queued save-then-submit, and direct submit while preserving one ID across retries.
- [x] 2.3 Implement locked transaction prepare, replay, commit, archive, and safe read-time reconciliation.
- [x] 2.4 Treat an existing input or revision snapshot as completed work only when target revision, request identity, and canonical content match.
- [x] 2.5 Return the original successful response for duplicate completed requests without creating a revision, handoff, history record, or side effect.
- [x] 2.6 Return `REVISION_CONTENT_CONFLICT` for different content targeting an existing revision and preserve every existing artifact.
- [x] 2.7 Expose incomplete transaction state and the next safe recovery action through stage status and recovery APIs.
- [x] 2.8 Make the frontend reload authoritative stage state after a persistence conflict and show repairable versus genuine conflict messages.
- [x] 2.9 Add migration logic for legacy sessions with snapshot/input ahead of session state and no transaction envelope.

## 3. Windows Atomic Replacement

- [x] 3.1 Extend the shared persistence utility to classify Windows sharing violations, short-lived writable-target access denial, read-only targets, and genuine ACL denial.
- [x] 3.2 Retry transient `WinError 5` only while the target is non-read-only, the parent passes an owned write probe, and the caller still owns the transaction lock.
- [x] 3.3 Bound retry attempts and elapsed time, return `PERSISTENCE_ACCESS_DENIED` for non-transient failures, and clean only the writer's own temporary/probe files.
- [x] 3.4 Add tests for a reader briefly holding `session.json`, permanent access denial, read-only target, parent denial, interrupted retry, and concurrent writers.

## 4. Short and Accurate Agent Wait

- [x] 4.1 Change advisory wait expiry to at most 30 seconds and setup's default total wait budget to two minutes.
- [x] 4.2 Make segmented `wait-handoff` reuse and renew one `wait_id` without changing `started_at`.
- [x] 4.3 Persist and project a server-authoritative `budget_expires_at` separately from heartbeat lease expiry.
- [x] 4.4 Clear the owned wait in `finally` on normal budget timeout, cancellation, service error, stage/session change, and successful claim.
- [x] 4.5 Preserve natural expiry only for abnormal Agent termination and reject stale renew/clear operations from another waiter.
- [x] 4.6 Render online heartbeat and total wait remaining separately, with a monotonically decreasing total timer.
- [x] 4.7 Prioritize submit failure and recovery guidance over the listening badge while still reporting whether the Agent remains online.
- [x] 4.8 Add concurrency and frontend tests for renewal, normal cleanup, abnormal expiry, duplicate waiters, failed submit, stage change, and claim transition.

## 5. Authorized Desktop User Runtime

- [x] 5.1 Define a fixed, narrow managed-workbench launcher accepting only validated config, runs root, exact session, and bounded port parameters.
- [x] 5.2 Launch the prepared project runtime in the user-authorized desktop context and keep the service bound to `127.0.0.1` with an ownership token.
- [x] 5.3 Record and verify Windows SID, login session ID, interactive-desktop availability, PID creation identity, and safe drive visibility in service state.
- [x] 5.4 Ensure path diagnostics, folder picker, folder indexing, and material Workers are descendants of and match the verified service identity.
- [x] 5.5 Fail before source access with `LOCAL_RESOURCE_IDENTITY_MISMATCH` when a child identity cannot be proven.
- [x] 5.6 Keep the sandbox launch path for workflows without local-resource access, but never claim mapped paths are usable from a different identity.
- [x] 5.7 Add tests proving launcher arguments cannot invoke arbitrary commands, escape allowed paths, bind externally, or weaken approval/upload gates.

## 6. Portable Per-Computer Source Bindings

- [x] 6.1 Add stable image-source IDs/names separated from each computer's local mapped-drive or UNC binding.
- [x] 6.2 Migrate the existing `config/local-paths.json` format without changing current labels or committing machine-specific paths.
- [x] 6.3 Store optional canonical UNC suggestions and last verified identity/time without storing credentials, directory listings, or image contents.
- [x] 6.4 Validate each binding in the verified desktop identity and distinguish missing binding, network failure, missing path, and access denial.
- [x] 6.5 Add tests for two computers mapping the same source to different drive letters, UNC fallback, missing VPN, and absent NAS permission.

## 7. Reliable Windows Folder Picker

- [x] 7.1 Route explicit picker requests through the verified desktop helper instead of a background Flask thread or sandbox-owned GUI process.
- [x] 7.2 Add request ID, ownership token, PID identity, single-instance lock, optional initial directory, and private result channel.
- [x] 7.3 Report `started`, `window_visible`, `selected`, and `cancelled` states with separate visibility and selection deadlines.
- [x] 7.4 Activate an existing owned picker on duplicate clicks or return `FOLDER_PICKER_BUSY` without spawning hidden duplicates.
- [x] 7.5 Return `FOLDER_PICKER_NOT_VISIBLE` when visibility cannot be proven, terminate only the owned helper, preserve the current input, and focus manual entry.
- [x] 7.6 Validate selected paths as absolute, existing directories accessible to the same desktop identity before updating the form.
- [x] 7.7 Add Windows tests for successful foreground display, delayed display, cancellation, duplicate click, hidden window, timeout, stale helper, invalid result, and manual fallback.

## 8. Integrated Recovery and UI Verification

- [x] 8.1 Replay the sanitized half-commit fixture and verify it converges to one consistent draft or completes the original submit without deleting revision evidence.
- [x] 8.2 Simulate failure after each transaction milestone and verify exact idempotent recovery and zero duplicate handoffs/events.
- [ ] 8.3 Verify a failed submit displays the persistence error while the Agent wait state remains accurate and then clears at the two-minute budget.
- [ ] 8.4 Verify the current Y:/Z: sources fail in sandbox identity and pass in the authorized desktop identity without source enumeration.
- [ ] 8.5 Verify the folder picker becomes visible from the managed page and returns one selected path without changing configuration until the user explicitly saves.
- [ ] 8.6 Start a fresh frontend session and complete stage-one submission, automatic claim, collection startup, and recovery display without revision conflicts.
- [ ] 8.7 Stop live acceptance before approval, upload, production confirmation, or publish and record safe evidence paths.

## 9. Validation and Documentation

- [ ] 9.1 Run targeted transaction, persistence, wait, frontend, service identity, path, picker, Worker, and migration suites with a short writable basetemp.
- [ ] 9.2 Run the complete Python and Node suites, Skill validation, OpenSpec strict validation, environment lock check, absolute-path scan, and `git diff --check`.
- [x] 9.3 Update the canonical Skill and operations guide with two-minute setup wait, 30-second lease, idempotent recovery, desktop launcher, per-machine bindings, and picker fallback.
- [ ] 9.4 Regenerate and validate Skill discovery metadata after canonical Skill changes.
- [x] 9.5 Update related OpenSpec task mappings and keep this change unarchived until automated and live Windows acceptance both pass.
