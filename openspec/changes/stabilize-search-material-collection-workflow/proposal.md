## Why

The Skill already contains Playwright logic for collecting “搜推高价值” material status, but a fresh task does not reliably invoke it after setup. Missing production-selector discovery, over-broad selector validation, an unmanaged CDP login handoff, and incomplete processing recovery cause Agents to improvise browser scripts, leave sessions stuck in `processing`, and produce evidence that is difficult to audit or resume.

## What Changes

- Add one deterministic setup-to-collection orchestration path that validates the setup handoff, snapshots inputs, prepares the CDP browser, invokes the existing `supplement --scan-mode high-value` collector, builds the completeness matrix, writes the bound stage result, and advances the session.
- Prefer the existing `supplement --scan-mode high-value` Playwright collector for every routine collection. If that maintained path produces a reproducible selector, navigation, popup, parsing, pagination, or page-state error, Playwright diagnostics may inspect the real DOM and repair the existing selector profile or collector implementation; the repaired maintained path must pass tests and be rerun instead of leaving a separate production script.
- Split selector validation by operation so high-value collection requires only store, human-check, promotion navigation, popup, row, and pagination selectors; export and publish selectors are validated only by their own commands.
- Add machine-local production selector profiles with deterministic discovery, schema/version metadata, current-DOM validation, hashes, and reason-coded invalidation. Repository example selectors remain non-production fixtures.
- Manage the CDP login handoff explicitly: open the official material-center URL, expose connection/login/store/page status, keep authentication user-controlled, and automatically resume the maintained collector after the user finishes login.
- Define two levels of slot evidence: batch collection records target/current/missing counts, while exact slot identities are collected only during the later per-product pre-publish verification. The batch collector must not imply that exact slot numbers are known.
- Add lease and heartbeat recovery for interrupted Agent processing, with explicit resume/reclaim behavior and a single authoritative claim state.
- Persist store identity, selector profile/version/hash, page URL, collection timestamps, checkpoints, and result bindings as task evidence using timezone-aware timestamps.
- Isolate runtime artifacts from source control, including the default `runs/` root, CDP profiles, and machine-local selector configuration.
- Surface row-level product-table anomalies in the completeness result without blocking valid products.

## Capabilities

### New Capabilities

- `high-value-collection-orchestration`: Deterministic setup-to-collection execution using the maintained Playwright collector, checkpointing, completeness generation, and explicit batch-versus-exact slot semantics.
- `purpose-scoped-selector-profiles`: Machine-local production selector discovery, operation-specific validation, DOM verification, versioning, hashing, and safe invalidation.
- `managed-cdp-collection-handoff`: User-controlled login with automatic official-page navigation, observable CDP/store/page state, and deterministic continuation into collection.
- `recoverable-stage-processing`: Lease-based Agent claims, heartbeat expiry, idempotent resume/reclaim, and authoritative handoff/session state.
- `collection-evidence-and-runtime-isolation`: Auditable store/selector/page/collection evidence, timezone consistency, row-level anomaly reporting, and Git-safe runtime artifact isolation.

### Modified Capabilities

None. The repository currently has no promoted main specs; this change introduces the missing contracts as new capabilities.

## Impact

- Affects the interaction session manager, managed UI service, setup handoff processor, CLI `supplement` command, browser session/navigation code, selector loader, completeness generation, task evidence schemas, and local configuration discovery.
- Adds operation-specific selector schemas and machine-local selector-profile state without committing production selectors or authentication data.
- Requires deterministic and browser acceptance tests for fresh-task startup, login continuation, delayed popups, full pagination, selector invalidation, interruption recovery, evidence output, and runtime ignore rules.
- Does not broaden production authority: login remains user-controlled, collection remains read-only, dry-run remains the default, and approval/publish still require exact manual authorization.
