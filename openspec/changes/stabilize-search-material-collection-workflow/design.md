## Context

The repository already implements `scan_recommended_material_status()` and exposes it through `tmall-materials supplement --scan-mode high-value`. That maintained path handles the promotion tab, the “搜推高价值” filter, delayed popup settlement, pagination, row parsing, incremental CSV writes, and checkpoints. A fresh interactive task nevertheless stops after setup because no single operation owns the transition from a claimed setup handoff to browser preparation, collection, completeness generation, and stage completion.

The collector also loads one global selector schema that includes export, scan, upload, and remote-verification selectors. The repository example contains placeholders and is explicitly forbidden for production, while no machine-local production profile discovery contract exists. As a result, a fresh Agent may inspect the DOM and improvise a one-off script rather than repair and call the maintained collector.

The workflow must remain portable across computers, keep authentication user-controlled, never commit credentials or production paths, preserve read-only collection semantics, and remain resumable after a Codex task or browser process is interrupted.

## Goals / Non-Goals

**Goals:**

- Make one idempotent operation responsible for setup handoff processing through completion of high-value collection and completeness generation.
- Ensure routine collection first uses the maintained `supplement --scan-mode high-value` Playwright collector, while retaining a controlled Playwright repair path when that collector produces a reproducible error.
- Make production selector profiles machine-local, discoverable, purpose-scoped, validated against the current DOM, versioned, and auditable.
- Make the CDP login boundary visible and deterministic without storing authentication data.
- Recover safely from interrupted `processing` states without duplicate collection or unsafe process termination.
- Persist sufficient evidence to audit store identity, selectors, page identity, checkpoints, product-row anomalies, and stage transitions.
- Keep runtime sessions and machine-local browser/selector artifacts out of source control.

**Non-Goals:**

- Bypassing login, QR codes, CAPTCHA, SMS, risk controls, or platform permissions.
- Replacing Playwright with private APIs or a new scraping framework.
- Collecting base-material exports or videos.
- Determining exact remote slot numbers during the batch high-value scan.
- Automatically approving, publishing, or retrying a publish action.
- Committing production selectors, Chrome profiles, cookies, or task runs.

## Decisions

### 1. Add a session-bound setup collection processor

A maintained command/service operation will accept an exact `runs_root` and `session_id`, validate the setup handoff identity tuple, acquire a processing lease, snapshot inputs, run product preflight, resolve the selector profile, prepare the CDP page, call the existing high-value collector, generate the completeness matrix, and write the setup result.

The operation will be idempotent by binding outputs to setup revision, input SHA-256, selector-profile SHA-256, and collection checkpoint identity. Re-entry will resume or reuse matching outputs and will reject stale or conflicting inputs.

Alternative considered: continue allowing each Agent to assemble individual commands. Rejected because it produces non-deterministic artifacts and cannot define reliable recovery boundaries.

### 2. Prefer and repair one maintained Playwright implementation

Routine collection will first call `supplement --scan-mode high-value`, which reaches `scan_recommended_material_status()` through the CLI/service boundary. When that path produces a persisted and reproducible selector, navigation, popup, parsing, pagination, or page-state error, Playwright diagnostics may inspect the current DOM and reproduce the failure. The resulting repair must update the maintained selector profile or maintained collector, add or update a regression test, and then rerun the same `supplement --scan-mode high-value` entry. Diagnostic code may be temporary evidence, but it must not become a separate production collector.

Alternative considered: permanently generate a new collector per task from the live DOM. Rejected because parallel production scripts are unaudited, hard to reproduce, and duplicate popup/pagination logic.

### 3. Validate selectors by operation

Selector requirements will be split into named purposes such as `high_value_collection`, `basic_export`, `publish`, and `remote_verification`. The supplement command will validate only the high-value collection set plus shared store/human-check fields.

Production profile discovery precedence will be:

1. explicit `--selectors`;
2. `TMALL_SELECTORS_FILE`;
3. machine-local `config/local-paths.json` selector-profile entry;
4. a well-known Git-ignored machine-local profile under `config/`.

Repository example selectors remain test fixtures and will be rejected as production input by marker/version metadata or placeholder detection.

Alternative considered: keep one global required-selector set. Rejected because unrelated publish fields currently block read-only collection.

### 4. Separate profile validation from DOM validation

Static validation checks profile schema, supported purpose, version, non-placeholder values, and file readability. Runtime DOM validation checks store identity, human-check state, material-center page identity, promotion navigation, filter visibility, rows, and pagination controls.

Successful validation writes selector profile name, version, SHA-256, validated URL, timestamp, and field results to task evidence. Failed validation writes `SELECTOR_INVALID` with the failed field and must not write zero-material results.

### 5. Manage the CDP login handoff without owning authentication

The processor will discover or launch a user-controlled CDP browser using a machine-local profile and navigate it to the official material-center URL. The interaction page will distinguish the Skill UI from the CDP Chrome window and display CDP connectivity, login requirement, observed store, observed URL, and the next user action.

If login or a human check is required, the processor releases active browser work but retains a resumable lease state and returns `LOGIN_INTERACTION_REQUIRED` or `HUMAN_CHECK`. After the user resumes the exact session, the processor revalidates page and store identity and continues the same maintained collector.

Alternative considered: ask users to navigate to the final page manually. Rejected because navigation is automatable and inconsistent instructions create avoidable errors.

### 6. Use batch capacity counts, then exact slot verification

The batch scan records `target_capacity`, `current_count`, and `missing_count`, with evidence derived from the same visible row. It explicitly sets exact slot identity status to `not_collected`. It must not populate or imply concrete slot numbers.

Exact empty slot identities are collected later for selected products immediately before approval/publish planning, using the correct store, exact product ID, current page evidence, and a fresh timestamp. A changed capacity or occupied slot invalidates the affected plan.

### 7. Replace indefinite processing with leases

A claim will store a unique claim ID, Agent/task identity when available, claimed time, heartbeat time, lease expiry, revision, and input hash in one authoritative session state. The handoff file remains immutable submission evidence and exposes the claim reference rather than acting as a second mutable authority.

An active unexpired lease blocks another claimant. An expired lease permits an explicit idempotent reclaim that resumes from verified checkpoints. A reclaim never repeats a completed page or overwrites a result bound to different inputs.

### 8. Standardize evidence and time

All persisted timestamps will be timezone-aware ISO 8601. The processor will produce:

- input manifest and hashes;
- product preflight and row-level reason codes;
- CDP/store/page evidence;
- selector-profile validation evidence;
- promotion CSV and checkpoint;
- scan summary with batch versus row-level status;
- completeness matrix;
- stage result bound to session, stage, revision, and input SHA-256.

Product-row anomalies remain row-level blocks and are visible in the completeness UI; they do not stop valid rows.

### 9. Treat runtime data as local state

The default `runs/`, CDP profile directories, machine-local selector profiles, locks, and generated diagnostics will be Git-ignored. Tests will verify ignore behavior. The application will never place cookies, tokens, QR data, or credentials in task evidence.

## Risks / Trade-offs

- [A real DOM change invalidates maintained selectors] → Record the exact failed field, preserve the prior checkpoint, and require profile repair plus browser acceptance before resuming.
- [A stale lease is reclaimed while the old Agent is still alive] → Use claim IDs, ownership-aware heartbeats, atomic state writes, and checkpoint/output identity checks; reject late writes from the old claim.
- [Automatic navigation opens an unexpected account or store] → Validate visible store identity before any collection and block the batch on mismatch.
- [Purpose-scoped schemas diverge] → Keep shared fields centralized and test each CLI command against its declared selector purpose.
- [Batch missing counts become stale before publish] → Treat them as selection context only and require exact per-product verification before production approval.
- [Machine-local selector profiles reduce out-of-box portability] → Provide a guided validation/setup page and deterministic discovery without shipping unsafe production values.

## Migration Plan

1. Add `runs/` and machine-local selector/CDP paths to Git ignore rules without deleting existing task evidence.
2. Introduce selector-profile metadata and purpose-scoped validation while retaining an explicit compatibility loader for tests and older local profiles.
3. Add the setup collection processor behind a CLI/service entry and exercise it against fake pages and an isolated session.
4. Add lease fields with migration for sessions currently in `ready_for_agent` or `processing`; stale legacy processing sessions require explicit recovery.
5. Update the interaction UI and Skill documentation to use the processor and explain the two-browser boundary.
6. Run a fresh-session browser acceptance against the current store, stop at completeness review, and only then make the new processor the default path.
7. Rollback by disabling the processor entry while retaining existing CLI commands and immutable task evidence; no production approval state is migrated automatically.

## Open Questions

None for implementation start. The guided frontend is the primary selector setup/repair path, with a reason-coded diagnostic CLI fallback. The CDP manager reuses a configured, reachable endpoint only after validation; otherwise it launches the configured dedicated profile.
