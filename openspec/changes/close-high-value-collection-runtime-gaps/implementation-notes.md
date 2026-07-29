## Reused contracts and entry points

- `interaction.session.SessionStore`
  - Reused timestamp session directories, revision-bound `input.json` /
    `handoff.json`, atomic JSON writes, processing leases, and result validation.
  - Extended claims with `attempt_id`, exact recoverability, and per-attempt result
    history; historical schema-version-1 sessions remain readable.
- `setup_collection.process_setup_collection`
  - Remains the only setup-to-completeness business processor.
  - Extended with attempt binding and progress callbacks; it still invokes the maintained
    supplement collector and never uploads or publishes.
- `supplement_collection.collect_supplement_material_status`
  - Remains the only production high-value collection implementation.
  - Extended with pre-checkpoint claim validation, attempt-compatible checkpoint
    upgrades, CSV SHA-256, uniqueness checks, and phase callbacks.
- `browser.material_page.scan_recommended_material_status`
  - Reused the existing `商品分类 → 搜推高价值` pagination/parser.
  - Force click is now restricted to declared read-only targets after a recognized guide
    intercept; unknown overlays stop collection.
- `browser.config`
  - Reused purpose-scoped, machine-local selector profiles.
  - Candidate profiles stay `production: false` until current-DOM validation succeeds.
- `interaction.web` and `interaction/static/app.js`
  - Reused the configuration page and session APIs.
  - Added independent readiness checks and authoritative attempt/worker status without
    adding browser automation to frontend JavaScript.
- `collection_worker`
  - New lifecycle wrapper only; it launches `process_setup_collection` in the project
    virtual environment and does not implement collection logic itself.
- `scripts/bootstrap.ps1` / `scripts/start-ui.ps1`
  - Reused project-local `.venv` and `.uv-cache`.
  - Dependency synchronization stays in bootstrap; routine UI and collection commands
    do not resolve packages.

## Compatibility and evidence

- A legacy checkpoint without `attempt_id` may be upgraded only after all older stable
  binding fields match. A conflicting attempt ID is rejected.
- Current status precedence is completed bound result, live owned worker, proven-dead
  recoverable worker, indeterminate/lease state, current blocker, then draft.
- An older result remains immutable history and is not returned as the active setup
  result while a later attempt is current.
- Worker ownership requires PID, private ownership token, session/attempt binding, and
  process-creation identity. Permission-denied identity checks are indeterminate and
  cannot be reclaimed or killed.
- All current automated fixtures use temporary local paths and synthetic store/product
  values. They contain no cookies, passwords, QR payloads, SMS codes, production writes,
  or NAS source-material reads.

## Deferred live evidence

The following require company network and a user-controlled logged-in CDP Chrome:

- current production DOM validation and delayed seven-step guide acceptance;
- real first checkpoint while the invoking command has returned;
- all visible high-value pages exactly once;
- real worker interruption after page N, immediate reclaim, and duplicate-free resume;
- NAS-backed downstream material matching.

These remain unchecked in `tasks.md` until real evidence paths are recorded.
