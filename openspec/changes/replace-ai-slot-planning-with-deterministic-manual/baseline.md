# Implementation baseline

- Branch: `codex/non-ai-slot-planning`
- Baseline commit: `fe891da feat: complete image review and slot planning baseline`
- Working tree: dirty before this change; existing image review, slot workflow, UI,
  documentation, and test edits are user-owned and must be preserved.
- Historical compatibility session: `20260725_095720`
- Internal workflow baseline: nine stable stage IDs, including the standalone
  `image_review` stage between `asset_matching` and `slots_copy`.
- User interface baseline: the slot workflow exposes `compose`, `process`, and
  `copy` subpages.
- Compatibility rule: stable stage IDs, authorization references, dry-run gates,
  and historical stage files remain readable. New tasks may hide or bypass an
  internal legacy stage without deleting it.
- Verification baseline: the previous implementation reported 468 Python tests
  passing and 2 skipped. This change must establish a new full-suite baseline
  after implementation.
