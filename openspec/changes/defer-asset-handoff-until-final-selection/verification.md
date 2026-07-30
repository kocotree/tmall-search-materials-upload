# Verification Evidence

Verified on 2026-07-30 on branch `codex/non-ai-slot-planning`.

- Folder confirmation uses the local `prepare-gallery` API. API and state-store
  tests prove it creates no `handoff.json` or processing claim and does not
  consume an advisory Agent wait.
- Gallery jobs bind the exact session, revision/input SHA-256, folder-decision
  SHA-256, selected products, strategy versions, and Windows resource identity.
  Tests cover idempotent reuse, successful publication, stale attempts,
  identity mismatch, heartbeat expiry, retry, and legacy unclaimed/live/expired
  handoffs.
- The Worker reuses the deterministic per-product 100-candidate allocator,
  inspection, SHA-256, allocation audit, and task-local preview generation.
  Existing confirmed-assets tests cover empty and overlapping folders.
- Final submission rejects one- and two-image shortages, leaves the stage
  `ready_for_agent`, writes preflight and the final material package, and creates
  a `final_material_selection` handoff. It does not create a slot plan in the
  HTTP request.
- The maintained final-handoff processor verifies the input and material
  identity, completes asset matching, and produces the deterministic slot plan
  without enumerating source folders again.
- Browser acceptance verifies the final submit/processor/page transition on
  narrow and wide layouts. The source roots remain read-only; all generated
  previews and evidence stay below the timestamped session.
- Full Python suite: 701 passed, 2 skipped, plus the separately executed Node
  UI-state test passed. The Skill entry validator, dependency lock check,
  strict validation for both related OpenSpec changes, and `git diff --check`
  passed.
