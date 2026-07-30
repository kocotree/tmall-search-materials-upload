## 1. Failure Baseline and Contracts

- [x] 1.1 Add a sanitized regression fixture proving that session `20260730_032916` contains exactly the final five IDs from the preceding 255-row, 26-page collection.
- [x] 1.2 Add a failing browser-page test whose fresh collection starts with the fake browser selected on the final page and currently completes with five rows.
- [x] 1.3 Define versioned pagination-origin, transition, terminal, and failure evidence envelopes with attempt, selector, page, product-ID hash, and timestamp bindings.
- [x] 1.4 Add stable error codes and Chinese recovery guidance for unverified origin, failed reset, checkpoint mismatch, transition mismatch, and unverified terminal state.

## 2. Selector Profile and Readiness

- [x] 2.1 Extend the `high_value_collection` selector contract with an unambiguous selected-page indicator and page-1 control, plus supported terminal-page evidence.
- [x] 2.2 Update local selector candidate generation without altering repository examples into production profiles.
- [x] 2.3 Extend static selector validation to reject missing, placeholder, or ambiguous pagination-origin fields.
- [x] 2.4 Extend current-DOM validation to record counts and interpreted values for selected page, page 1, next page, and terminal evidence.
- [x] 2.5 Update stage-one readiness so final setup submission is blocked when pagination origin cannot be verified on the current material page.
- [x] 2.6 Add selector and readiness tests for first, middle, final, single-page, missing-control, and ambiguous-control DOM states.

## 3. Fresh-Run Pagination Origin

- [x] 3.1 Add a read-only pagination-state parser that returns the observed current page, terminal page or marker, next enabled state, and ordered product-ID hash.
- [x] 3.2 Add a bounded first-page normalization helper that runs after promotion-tab and high-value-filter preparation.
- [x] 3.3 Verify both selected-page state and stable ordered product IDs after a page-1 reset before permitting row parsing.
- [x] 3.4 Prevent page progress, CSV writes, checkpoints, and completed results before pagination-origin verification succeeds.
- [x] 3.5 Record the verified origin event under the current immutable attempt directory.
- [x] 3.6 Add tests for a reused tab initially on page 1, a middle page, the final page, and a page whose reset transition never settles.

## 4. Traversal, Terminal Proof, and Resume

- [x] 4.1 Require every accepted next-page transition to advance the observed page by exactly one and change the ordered product identity.
- [x] 4.2 Replace the single `next_page.is_enabled()` completion predicate with a bounded stable terminal proof.
- [x] 4.3 Treat transiently disabled next-page controls during loading as unsettled state instead of successful completion.
- [x] 4.4 Preserve the last complete checkpoint and emit stable failure evidence when a transition remains unchanged, skips a page, or loses pagination identity.
- [x] 4.5 Normalize resumed scans to verified page 1 and replay exactly the checkpointed completed-page transitions without re-persisting their rows.
- [x] 4.6 Reject resume when replayed page identities or the checkpoint boundary cannot be reconciled.
- [x] 4.7 Add tests for multi-page completion, legitimate one-page completion, transient disablement, unchanged rows, skipped pages, and checkpoint resume from a tab left on the final page.

## 5. Attempt Evidence and Historical Isolation

- [x] 5.1 Persist origin, transition, and terminal events atomically with attempt progress and checkpoints.
- [x] 5.2 Reconcile pagination evidence, `last_completed_page`, unique row count, CSV SHA-256, checkpoint SHA-256, store, revision, input SHA-256, and selector SHA-256 before publishing `current/`.
- [x] 5.3 Add backward-compatible reads that classify historical attempts without pagination-origin evidence as legacy rather than silently verified.
- [x] 5.4 Add a non-destructive audit marker or compatibility rule that prevents session `20260730_032916` from authorizing downstream selection, approval, or publish.
- [x] 5.5 Verify that quarantining the false-success session preserves every original file and audit event.

## 6. UI, Skill, and Operations Guidance

- [x] 6.1 Show observed pagination origin, current page, last checkpoint page, terminal proof, and pagination failures in worker and completeness status.
- [x] 6.2 Update the canonical Skill to require verified first-page origin and terminal proof before accepting full-scan completion.
- [x] 6.3 Update the operations guide, data schema, and error handling references with pagination selectors, evidence, recovery, and historical-session quarantine behavior.
- [x] 6.4 Regenerate the repository Skill discovery entry and validate its canonical hash if canonical Skill content changes.

## 7. Verification and Live Acceptance

- [x] 7.1 Run targeted selector, readiness, browser-page, supplement, setup-processor, checkpoint, attempt-status, API, and frontend tests.
- [x] 7.2 Run the full Python and frontend suites, Skill validator, OpenSpec strict validation, lock check, and `git diff --check`.
- [x] 7.3 In a fresh timestamp session, deliberately leave the CDP tab on the previous scan's final page and verify the maintained collector returns to page 1 before writing its first checkpoint.
- [x] 7.4 Complete every visible high-value page exactly once and reconcile the current live page count and unique product total with pagination evidence, CSV, checkpoint, worker status, and completeness matrix.
- [x] 7.5 Interrupt a separate run after page N, resume from the same checkpoint while the tab is on an unrelated page, and verify zero duplicate rows or repeated checkpoint writes.
- [x] 7.6 Confirm live acceptance performs no upload, approval, publish, credential capture, or source-material mutation.
- [x] 7.7 Record live evidence paths and only then allow the pagination-origin capability and the related outstanding full-pagination acceptance task to be marked complete.
