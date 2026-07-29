## Why

The real fresh-session run `20260729_090032` proved that the maintained high-value
collector still cannot reliably progress from submitted setup to its first checkpoint.
A missing selector profile can be submitted, machine-local runtime failures surface too
late, a changed onboarding overlay blocks clicks, long collection outlives the invoking
command, and a dead worker can leave contradictory `processing` and historical-result
state.

## What Changes

- Make stage-one readiness explicit: selector profile, CDP endpoint, page identity,
  target-store identity, login/human-check state, and runtime environment are evaluated
  before collection, with each unmet prerequisite shown as a reason-coded blocking or
  user-action state.
- Add a guided machine-local selector bootstrap and repair flow that validates the
  maintained profile against the current DOM; repository examples remain forbidden for
  production.
- Harden delayed onboarding, guide, and safe-popup handling in the maintained
  `supplement --scan-mode high-value` collector and require real-DOM regression evidence
  before the fix is considered accepted.
- Run full collection as a managed, observable worker instead of tying its lifetime to a
  short foreground command window. Persist heartbeat, current phase/page, row count,
  last checkpoint, and bounded error evidence.
- Reconcile worker liveness, processing leases, immutable handoff evidence, and stage
  results into one authoritative status view. A dead owned worker can be reclaimed
  without waiting for a misleading active state, while a live worker cannot be killed or
  duplicated.
- Preserve page-level atomic checkpointing and prove exact-session interruption/resume
  without duplicate rows or repeated completed pages.
- Standardize project-local uv/cache behavior so an already prepared environment does
  not fail on an inaccessible user cache or attempt unnecessary dependency resolution.
- Keep temporary Playwright diagnostics disposable: they may repair the maintained
  profile or collector after a persisted reproducible error, but never become a second
  production collection path.
- Add live acceptance covering fresh setup, login, delayed guides, first-checkpoint
  visibility, full pagination, interruption, reclaim, resume, and zero upload/publish
  actions.

## Capabilities

### New Capabilities

- `high-value-collection-live-closure`: Covers readiness gating, guided selector
  bootstrap, maintained-collector popup compatibility, managed worker observability,
  lease/liveness reconciliation, checkpoint resume, runtime cache isolation, and live
  acceptance for the setup-to-completeness path.

### Modified Capabilities

None. The related stabilization capabilities have not yet been promoted to
`openspec/specs`; this change is an incremental closure contract that explicitly builds
on `stabilize-search-material-collection-workflow`.

## Impact

- Affects the stage-one configuration UI and validation API, selector-profile discovery
  and repair flow, CDP material-page readiness checks, maintained Playwright collector,
  setup processor, managed worker/service lifecycle, processing-claim state, status and
  recovery APIs, checkpoint/evidence schemas, CLI launcher, uv bootstrap/cache handling,
  canonical Skill instructions, operations guide, and browser acceptance tests.
- Adds no production authority and performs no upload or publish. Authentication remains
  user-controlled, production selectors and profiles remain machine-local and
  Git-ignored, and exact approval/publish gates are unchanged.
