## 1. Reconcile the Existing Third-Stage Change

- [x] 1.1 Record a code and artifact baseline for the current `fix-third-stage-folder-gallery-flow` implementation before replacing its intermediate handoff.
- [x] 1.2 Update the earlier change design so folder confirmation starts a local job and final selection is its only Codex handoff.
- [x] 1.3 Update or supersede earlier tasks that require `process-confirmed-gallery` to claim an Agent handoff.
- [x] 1.4 Add a regression test that fails while folder confirmation creates `handoff.json`, agent wait, or a Codex processing claim.
- [x] 1.5 Add a regression test that fails while final image confirmation completes the stage without creating a final material handoff.

## 2. Local Revision and Gallery-Job State

- [x] 2.1 Define versioned gallery-job, gallery-attempt, progress, result, and error documents with stable status and reason-code enums.
- [x] 2.2 Define a gallery-job identity containing session, stage, input revision/SHA-256, selected products, folder-decisions SHA-256, strategy versions, and local resource identity.
- [x] 2.3 Add a SessionStore transaction that persists authoritative folder decisions and a new revision without creating a handoff.
- [x] 2.4 Make the local transaction idempotent for the same request ID and normalized folder identity.
- [x] 2.5 Preserve the current valid gallery across image-selection-only draft revisions and invalidate it when the adopted-folder scope expands.
- [x] 2.6 Add audit events that distinguish local draft save, local gallery start, retry, completion, failure, stale result, and final Codex handoff.
- [x] 2.7 Add state-store tests proving local actions never modify agent wait or processing-claim state.

## 3. Maintained Local Gallery Worker

- [x] 3.1 Replace the handoff-claiming gallery command with a command that claims an exact gallery job and attempt.
- [x] 3.2 Launch the gallery Worker from the managed page service using the project environment and hidden background-process settings.
- [x] 3.3 Verify Worker SID/login-session identity against the page service before accessing adopted folders.
- [x] 3.4 Persist PID, lease, heartbeat, current product/folder, discovered count, prepared count, and recovery action.
- [x] 3.5 Reuse confirmed-gallery enumeration, deterministic sampling, compliance inspection, allocation audit, and preview generation without modifying NAS sources.
- [x] 3.6 Publish `confirmed-gallery.json` and image-selection review context only after revalidating the current job, revision, input SHA, product boundary, and folder SHA under the session lock.
- [x] 3.7 Preserve failed and stale attempts under `gallery-attempts` while preventing them from becoming the current gallery.
- [x] 3.8 Treat readable empty and overlapping folders as zero-allocation audit outcomes rather than job failures.
- [x] 3.9 Return path-specific stable errors for unreadable folders and local-resource identity mismatch.
- [x] 3.10 Add Worker tests for success, duplicate claim, stale publication, unreadable path, empty folder, overlap, process interruption, lease expiry, and retry.

## 4. Direct Page APIs and User Experience

- [x] 4.1 Add `prepare-gallery`, gallery-job status, and gallery-job retry endpoints that never create Codex handoffs.
- [x] 4.2 Validate every product has at least one adopted folder before starting the local job.
- [x] 4.3 Return the existing running or completed job for a duplicate request with the same identity.
- [x] 4.4 Change “确认文件夹并加载图片” to call the local prepare endpoint instead of the generic Agent submit route.
- [x] 4.5 Poll or subscribe to gallery-job progress and automatically refresh into image selection after successful completion.
- [x] 4.6 Show current product/folder, discovered/prepared counts, heartbeat, stable failure text, and “重试加载图片”.
- [x] 4.7 Remove “等待 Agent/Codex” and “在聊天回复已提交” copy from the normal folder-to-gallery path.
- [x] 4.8 Keep folder changes and image-selection changes locally autosaved without creating handoff or losing the valid gallery.
- [x] 4.9 Change the final action label to “确认选图并提交给 Codex”.
- [x] 4.10 Add browser tests proving folder confirmation, refresh, retry, and image-selection drafts produce zero Codex wakeups.

## 5. Single Final Material Handoff

- [x] 5.1 Revalidate gallery identity, adopted-folder membership, authorization, readability, compliance, and SHA-256 uniqueness before final handoff creation.
- [x] 5.2 Enforce at least three valid unique selected images for every product in the complete third-stage boundary.
- [x] 5.3 Persist `selected-asset-preflight.json` and a versioned final-material-package identity before handoff creation.
- [x] 5.4 Make final selection call the standard SessionStore handoff transaction only after all preflight checks pass.
- [x] 5.5 Include product boundary, folder-decisions SHA, final asset decisions, folder/source identity, SHA-256, compliance results, revision, and input SHA in the handoff.
- [x] 5.6 Make retries of the same final request return the same handoff and reject conflicting revision or material identities.
- [x] 5.7 Remove synchronous stage completion and slot-plan creation from the final page request.
- [x] 5.8 Add or update the maintained Codex processor so it consumes the final material package and continues deterministic slot planning without rescanning folders.
- [x] 5.9 Add API and processor tests for shortages, partial-product selection, duplicates, stale gallery, excluded-folder assets, successful final handoff, retry, and downstream slot planning.

## 6. Historical Migration and Documentation

- [x] 6.1 Classify existing asset-matching handoffs as legacy folder handoffs or final material handoffs from their bound input.
- [x] 6.2 Convert an unclaimed legacy folder handoff into a local gallery job without deleting its revision or audit evidence.
- [x] 6.3 Allow a live legacy folder-processing claim to finish safely and migrate an expired claim into a local retry.
- [x] 6.4 Prevent all new sessions from writing the legacy intermediate handoff format.
- [x] 6.5 Add sanitized fixtures and migration tests for unclaimed, live, expired, completed, and final historical handoffs.
- [x] 6.6 Update the canonical Skill so local gallery preparation and one final Codex handoff are the only normal third-stage workflow.
- [x] 6.7 Update data schema, operations guide, recovery guidance, and button terminology.
- [x] 6.8 Regenerate and validate the repository Skill discovery entry.

## 7. Verification and Acceptance

- [x] 7.1 Run targeted SessionStore, API, Worker, frontend, gallery, preflight, and deterministic-slot tests.
- [x] 7.2 Run the full Python and frontend suites, Skill validator, dependency-lock check, strict validation for both related OpenSpec changes, and `git diff --check`.
- [x] 7.3 In a fresh session, confirm folders and prove no handoff, agent wait, processing claim, or Codex wakeup is created.
- [x] 7.4 Prove the local Worker reads only adopted folders, creates visible previews, and restores image selection after refresh.
- [x] 7.5 Interrupt and retry the local Worker, proving same-session recovery works without a chat reply.
- [x] 7.6 Save multiple image-selection drafts and prove the gallery remains valid with zero handoffs.
- [x] 7.7 Submit an insufficient selection and prove no handoff is created; then satisfy every product and prove exactly one final handoff is created.
- [x] 7.8 Let Codex consume the final material handoff and prove it continues slot planning without repeating folder or image confirmation.
- [x] 7.9 Confirm the acceptance run does not modify NAS sources or create approval, production-confirmation, upload, or publish artifacts.
- [x] 7.10 Record sanitized evidence reconciling local job attempts, gallery identity, two page actions, the single final handoff, and downstream state.
