## Why

The maintained high-value Playwright collector can reuse a persistent CDP tab that is still on the final page from a previous run, relabel that page as page 1, and incorrectly complete with only the final page's rows. This produced a false-success result of 5 products even though the same live dataset contained 255 products across 26 pages.

## What Changes

- Require every fresh high-value collection to establish and verify a first-page pagination origin before parsing or checkpointing rows.
- Separate fresh-run pagination reset from checkpoint-bound resume navigation.
- Fail closed with stable pagination-origin evidence when the current page cannot be proven or reset, instead of treating a disabled next-page control as successful completion.
- Validate terminal pagination using observed page state in addition to the next-page enabled state.
- Persist pagination-origin, page-transition, and terminal-page evidence with the collection attempt.
- Add regression and live-acceptance coverage for reused tabs left on the first, middle, and final pages.
- Invalidate the false-success output of session `20260730_032916` and require a clean rerun after implementation.

## Capabilities

### New Capabilities

- `high-value-pagination-origin`: Defines verified start, traversal, resume, terminal-page, and evidence behavior for maintained high-value Playwright collection.

### Modified Capabilities

None.

## Impact

- Affects the CDP page-selection and high-value pagination flow in `browser/session.py`, `browser/material_page.py`, `supplement_collection.py`, and `setup_collection.py`.
- Extends selector/profile requirements or DOM interpretation for current-page, first-page, and terminal pagination evidence.
- Extends attempt checkpoints, worker progress, error codes, and collection evidence without changing upload or publish behavior.
- Requires unit, integration, regression, and live CDP acceptance tests.
