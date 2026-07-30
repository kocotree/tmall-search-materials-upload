## Context

The maintained collector connects to a persistent user-controlled Chromium session over CDP and selects the last open page. A non-blank material-center page is reused as-is. The high-value collector then numbers its first observed DOM page as page 1 and treats a disabled next-page control as terminal.

This works when the reused tab is already on page 1, but it fails when a prior scan leaves the single-page application on a later page. Session `20260730_032916` proved the failure: its five collected IDs exactly equal the final five IDs from the preceding 255-row, 26-page scan.

## Goals / Non-Goals

**Goals:**

- Establish a deterministic pagination origin for fresh and resumed scans.
- Make page numbering correspond to the observed UI position.
- Preserve checkpoint resume semantics and attempt isolation.
- Fail closed when pagination state cannot be proven.
- Produce enough bounded evidence to reconcile page count and unique products.

**Non-Goals:**

- Replacing the maintained `supplement --scan-mode high-value` production path.
- Creating a second diagnostic collector.
- Changing product eligibility, slot semantics, image selection, approval, or publication.
- Persisting cookies, tokens, full DOM snapshots, or other sensitive browser state.

## Decisions

### 1. Add explicit pagination-state selectors and observations

Extend the machine-local high-value selector profile with purpose-scoped selectors for the selected page indicator and a control that reaches page 1. Where the UI exposes a total or final page indicator, capture it as corroborating terminal evidence.

This is preferred over inferring page position from row count because the final page can legitimately contain fewer rows, and over toggling the high-value filter because an already-selected filter can preserve SPA pagination state.

### 2. Normalize to page 1 after tab and filter preparation

Run pagination-origin normalization after opening the promotion tab, settling recognized guides, and verifying the high-value filter. The origin step reads the selected page, invokes the declared page-1 control when necessary, and waits until both selected-page state and ordered product IDs are stable.

No row parsing, checkpoint write, or page progress may occur before this succeeds. Clicking the promotion tab alone is not considered a reset because clicking an already-active SPA tab can be a no-op.

### 3. Resume from page 1 and replay navigation without re-persisting rows

For a checkpoint at page N, establish page 1 and then traverse the first N pages using the same identity-checked next-page transition. Existing checkpoint rows remain the source of truth; replayed pages are navigation only and are not re-emitted.

This costs up to N page transitions but avoids direct URL assumptions, hidden API usage, and ambiguity about SPA-internal pagination state.

### 4. Replace the single terminal predicate with a terminal proof

Completion requires a stable tuple:

`current_page + terminal_page_or_terminal_marker + next_enabled=false + ordered_product_id_hash`

The tuple is observed after a bounded quiet window. If the UI does not expose enough state to prove the terminal page, collection fails with `PAGINATION_TERMINAL_UNVERIFIED`.

This is preferred over retrying `is_enabled()` alone, which cannot distinguish a true final page from a reused final page, a loading state, or a mismatched control.

### 5. Persist bounded attempt-local pagination evidence

Write origin, transition, and terminal events to the current attempt directory. Each event includes attempt binding, page positions, product-ID hash, selector SHA-256, timestamp, and reason code. Do not persist full row text beyond existing CSV/checkpoint artifacts.

Checkpoint validation reconciles the final accepted pagination event with `last_completed_page`, row count, CSV hash, and unique IDs before publishing `current/`.

### 6. Preserve and quarantine the known false-success session

Do not delete or rewrite session `20260730_032916`. Add compatibility recognition or an explicit audit marker so its five-row output cannot be selected as authoritative current collection evidence. A new timestamp session must rerun after the fix.

## Risks / Trade-offs

- [Pagination markup varies across live deployments] → Make selectors purpose-scoped, validate them against the current DOM, and fail closed when ambiguous.
- [Returning to page 1 adds time to checkpoint resume] → Prefer deterministic correctness; retain checkpoint rows so only navigation is replayed.
- [Product ordering changes during a long scan] → Bind every transition to ordered ID hashes and stop for manual recovery on checkpoint mismatch.
- [A legitimate one-page result resembles the previous failure] → Accept it only when page 1 and terminal state are independently verified.
- [Live pages can transiently disable controls] → Require a bounded quiet window and stable page/row observation before terminal completion.

## Migration Plan

1. Extend selector schema, local candidate generation, current-DOM validation, and error documentation.
2. Add pagination-origin and terminal-proof helpers to the maintained collector.
3. Extend checkpoint and attempt evidence with backward-compatible optional reads for historical sessions.
4. Add unit and integration regression fixtures, including a browser initially left on the final page.
5. Mark session `20260730_032916` as invalid for downstream use without deleting its evidence.
6. Run a fresh live session and reconcile the observed live total, page count, CSV, checkpoint, and completeness matrix.
7. Roll back by disabling the new profile version and collector release; do not reuse outputs created without verified origin evidence.
