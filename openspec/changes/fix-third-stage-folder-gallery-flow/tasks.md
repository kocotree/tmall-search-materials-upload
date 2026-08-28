## 1. Failure Baseline and State Contract

> Tasks requiring the first-round handoff or an Agent claim are superseded by
> `defer-asset-handoff-until-final-selection`. Retain completed normalization,
> hydration, gallery identity, preview, and final-boundary work.

- [x] 1.1 Add a sanitized regression fixture for session `20260730_055346` covering three products, fourteen folder candidates, zero asset candidates, missing `source_types`, and the page-open draft revision.
- [ ] 1.2 Add failing interaction tests proving that opening and hydrating the folder-review page currently creates a draft revision without a user edit.
- [x] 1.3 Define the `folder_review`, `gallery_preparing`, and `image_selection` workflow-step values plus backward-compatible inference for historical results.
- [x] 1.4 Define a versioned gallery preparation identity containing session, stage, prepared revision, input SHA-256, selected-product boundary, and normalized folder-decisions SHA-256.
- [x] 1.5 Add stable reason codes and Chinese recovery guidance for no adopted folder, inaccessible adopted folder, stale gallery identity, preparation failure, and per-product image shortage.

## 2. Server-Side Input Normalization and Submission Routing

- [x] 2.1 Normalize every authoritative folder candidate into an explicit `confirmed` or `rejected` decision on draft save and first-round submission.
- [x] 2.2 Preserve exact user decisions and notes while applying deterministic defaults only to omitted folder candidates.
- [x] 2.3 Normalize missing or empty new-task `source_types` to `["image"]` while preserving non-empty historical values.
- [x] 2.4 Reject first-round submission when any selected product has no adopted folder, with product-specific recovery text.
- [x] 2.5 Route the existing asset-matching submit endpoint by authoritative workflow step: folder review creates a handoff, image selection runs final preflight.
- [x] 2.6 Make duplicate first-round submissions with the same request and folder identity idempotent, without creating duplicate handoffs.
- [ ] 2.7 Add API tests for omitted defaults, empty source types, all-folders-rejected, request retries, revision conflicts, and historical inputs.

## 3. Maintained Gallery Preparation and Identity

- [x] 3.1 Add or consolidate a maintained asset-matching processor that claims the exact first-round handoff and calls the existing confirmed-gallery preparation path.
- [ ] 3.2 Validate session, stage, revision, input SHA-256, selected products, folder-decisions SHA-256, and local resource identity before reading adopted folders.
- [ ] 3.3 Persist gallery preparation progress with workflow step, current product/folder, discovered and prepared counts, heartbeat, and recovery action.
- [ ] 3.4 Write `confirmed-gallery.json`, preview-cache output, allocation audit, and the image-selection review context atomically under the current preparation identity.
- [ ] 3.5 Reject late or stale processor results from becoming the current gallery while preserving their attempt evidence.
- [x] 3.6 Fail closed on inaccessible adopted paths with path-specific reason codes, preserving submitted decisions and refusing an empty-success gallery.
- [x] 3.7 Preserve empty-folder and overlapping-folder zero-allocation audit rows without treating them as access failures.
- [ ] 3.8 Add processor tests for success, inaccessible paths, empty folders, stale identity, duplicate claim, restart, and historical result inference.

## 4. Frontend Two-Step Experience and Hydration Idempotence

- [x] 4.1 Render a visible third-stage step indicator for “筛选文件夹” and “选择图片”, derived from the authoritative workflow step.
- [x] 4.2 In folder review, hide the empty gallery, explain that adopting a folder does not adopt its images, and label the action “确认文件夹并加载图片”.
- [x] 4.3 Hide or render `source_types` as a read-only “图片” value instead of a required free-form user field.
- [x] 4.4 Add an explicit hydration transaction that suppresses dirty state, autosave, persistence request creation, and handoff creation during server-value and component initialization.
- [x] 4.5 Build the complete default folder-decision payload at save or submit time without dispatching synthetic user-input events during rendering.
- [x] 4.6 Show gallery preparation progress, Agent heartbeat, failure reason, and exact same-session recovery action after the first submission.
- [x] 4.7 In image selection, label the action “确认选图并进入坑位编排” and show discovered, prepared, valid, selected, 100-limit, and 30-per-batch counts.
- [x] 4.8 Refresh or reopen the page into image selection when a valid current gallery exists, without reverting to “尚未扫描”.
- [ ] 4.9 Add frontend tests comparing revision, input SHA-256, handoff, audit events, and dirty state before and after read-only hydration.
- [x] 4.10 Merge each completed image preflight into the latest formal selection state and defer same-stage hydration while image-selection edits or preflights are pending.
- [x] 4.11 Highlight the current product in the reusable product navigator while its folder, gallery, compose, or crop section crosses the viewport activation line.

## 5. Gallery Invalidation and Final Selection Boundary

- [x] 5.1 Compute and compare normalized folder-decisions SHA-256 independently from asset-selection draft revisions.
- [x] 5.2 Immediately remove candidates and clear image/authorization decisions when a prepared folder is rejected.
- [x] 5.3 Mark the gallery stale and return to folder confirmation when a folder not represented in the current gallery is newly adopted.
- [x] 5.4 Keep an existing gallery valid across asset-decision-only autosaves when the folder decision identity is unchanged.
- [x] 5.5 Revalidate submitted assets against current adopted folders, authorization, file readability, compliance status, and source SHA-256 uniqueness.
- [x] 5.6 Require at least three valid unique selected images for every product entering the third stage, not only products already present in `asset_decisions`.
- [x] 5.7 Preserve the existing selected-asset preflight and deterministic slot-plan creation only after all products pass.
- [ ] 5.8 Add tests for folder rejection, unprepared re-adoption, selection-only drafts, stale gallery rejection, partial-product selection, duplicate images, and successful stage advancement.

## 6. Skill and Operational Documentation

- [x] 6.1 Update the canonical Skill so the two third-stage submissions and their Agent/user responsibilities are the only normal workflow.
- [x] 6.2 Update the data schema with workflow-step, gallery identity, progress, stale-gallery, and final-preflight fields.
- [x] 6.3 Update the operations guide with first-round handoff processing, offline Agent recovery, refresh behavior, and historical-task compatibility.
- [x] 6.4 Update error handling with the new stable reason codes and user-facing recovery actions.
- [x] 6.5 Regenerate the repository Skill discovery entry and validate its canonical SHA-256.

## 7. Verification and Live Acceptance

- [x] 7.1 Run targeted session, API, frontend, folder-gallery, confirmed-assets, preflight, and deterministic-slot tests.
- [ ] 7.2 Run the full Python/frontend suite, Skill validator, OpenSpec strict validation, dependency lock check, and `git diff --check`.
- [ ] 7.3 Open a fresh folder-review session without editing and prove revision, input SHA-256, handoff, and audit events remain unchanged.
- [ ] 7.4 Accept default and edited folder decisions, submit once, and prove only adopted folders are enumerated into a revision-bound gallery with visible previews.
- [ ] 7.5 Refresh the image-selection page and prove it remains in the second substep without duplicate preparation or revision creation.
- [ ] 7.6 Select images for multiple products, verify exact per-product shortages, then satisfy all products and prove deterministic slot-plan creation.
- [ ] 7.7 Confirm the acceptance run does not modify NAS sources or create dry-run, approval, production-confirmation, upload, or publish artifacts.
- [ ] 7.8 Record sanitized live evidence paths and reconcile session state, two submissions, gallery identity, candidate counts, preview counts, preflight, and next-stage state.
