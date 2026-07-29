# Active-change conflict boundaries

The following active changes overlap this implementation:

- `complete-manual-ai-decision-boundaries`: preserve copywriting AI, but
  supersede AI slot planning.
- `make-ai-default-slot-planning`: directly conflicts with deterministic slot
  planning and is superseded for new tasks only.
- `reorder-slot-planning-before-image-processing`: remains the structural
  plan-before-crop baseline.
- `replace-rule-fallback-with-ai-manual-slot-editor`: keep its shared candidate
  pool and manual editor, while superseding AI-only draft ownership.
- `simplify-fifth-stage-three-step-ui`: its three-page navigation is superseded
  by two-page processing/copy navigation for new tasks.

Resolution rules:

1. Keep historical AI/rule drafts and standalone `image_review` files readable.
2. Do not create new slot-planning Agent requests.
3. New slot drafts have `deterministic`, `manual`, or `manual_override` sources.
4. AI remains available only for title and description generation.
5. Do not renumber or rename persisted stage IDs.
