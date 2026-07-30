## Implementation Baseline

- Git baseline: `3578dbc62a1611dec4cf04cc166a68e7d6cd6ec8`
- Working tree already contains the uncommitted implementation and artifacts for `fix-third-stage-folder-gallery-flow`.
- The current first-round path calls the generic asset-matching submit route, creates `handoff.json`, and expects `process-confirmed-gallery` to claim it.
- The current final-selection path performs preflight, completes `asset_matching`, and creates a deterministic slot draft synchronously without a final Codex handoff.
- This change preserves the existing folder defaults, hydration guard, gallery identity, candidate preparation, image preflight, and deterministic slot algorithm while replacing those two orchestration boundaries.
