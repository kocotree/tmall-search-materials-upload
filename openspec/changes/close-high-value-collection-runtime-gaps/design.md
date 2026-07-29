## Context

`stabilize-search-material-collection-workflow` introduced the intended
`process-setup → supplement --scan-mode high-value` path, purpose-scoped selector
profiles, CDP login handoff, checkpoints, and processing leases. The live session
`20260729_090032` reached that path but exposed its remaining runtime gaps:

- stage one accepted a submission while no production selector profile existed;
- uv failed against a protected user cache although the project environment existed;
- a new delayed onboarding overlay intercepted promotion/filter clicks;
- a foreground command window ended before the first checkpoint;
- the worker exited while the stage retained an active processing lease;
- a historical `SELECTOR_PROFILE_NOT_FOUND` result remained visible beside a newer
  `processing` state and valid selector/store evidence.

The selector profile and store page eventually validated, but neither the promotion CSV
nor checkpoint existed. No later workflow stage and no upload/publish action ran.

This change is an incremental closure layer over the existing stabilization change. It
does not introduce a second collector or replace its evidence contracts.

## Goals / Non-Goals

**Goals:**

- Prevent a fresh user from submitting a collection attempt whose locally checkable
  selector/runtime prerequisites are already known to be invalid.
- Give a new computer a guided, machine-local selector bootstrap/repair path that
  converges on the maintained collector.
- Make delayed guide handling deterministic, bounded, safe, and diagnosable.
- Move collection lifetime out of the foreground HTTP/Agent command window.
- Expose useful progress before and after the first checkpoint.
- Reconcile worker liveness, claim ownership, checkpoint identity, historical attempts,
  and current stage status.
- Resume an interrupted exact session without repeated pages or duplicate rows.
- Use the existing project environment and project-local uv cache deterministically.
- Complete a real browser acceptance run without uploading or publishing.

**Non-Goals:**

- Automating login, CAPTCHA, QR, SMS, risk controls, VPN, or store permissions.
- Generating arbitrary selectors from an LLM and trusting them without live validation.
- Adding a second Playwright collector or using a private platform API.
- Collecting exact numbered empty slots during the batch scan.
- Uploading, approving, or publishing material as part of collection acceptance.
- Killing processes whose ownership cannot be proven.

## Decisions

### 1. Separate configuration validity from collection readiness

Stage one will expose a versioned `collection_readiness` object with individual checks
for environment, selector schema, selector current-DOM validation, CDP connection,
login/human-check, page identity, and store identity.

The page may save incomplete drafts. Final submission is enabled only when all
machine-checkable prerequisites are ready and the user has completed required native
login actions. A reason-coded “needs user action” state is not misreported as UI or
collector failure.

Alternative considered: keep accepting setup and report errors after Agent claim.
Rejected because it consumes claims for failures known before submission and leaves users
with contradictory states.

### 2. Bootstrap selectors as an untrusted local candidate, then promote by validation

If no production profile exists, the frontend offers a guided local bootstrap. It
creates a Git-ignored candidate containing required high-value fields and explicit
`production=false`. It cannot be used for collection until the current CDP material page
validates every required field, store/page identity, and safe-popup controls. Successful
validation writes profile version, supported purpose, validation time, page identity,
and SHA-256, then promotes the local candidate to production.

When validation fails, the page names the failed field and offers the controlled
Playwright repair path. A repair changes the maintained profile/collector and adds a
regression test; temporary diagnostics are deleted after their evidence is captured.

Alternative considered: mark a copied example profile as production. Rejected because
example placeholders are intentionally unsafe and may silently produce false zeroes.

### 3. Constrain overlay recovery to safe read-only targets

The maintained collector will use a bounded state machine:

1. wait for delayed overlays during a configured quiet window;
2. progress or close only whitelisted guide/dialog controls;
3. re-evaluate target visibility and overlay state;
4. retry ordinary click a bounded number of times;
5. allow one force click only for declared read-only navigation/filter targets and only
   when the identified overlay is the interceptor;
6. otherwise persist `SELECTOR_INVALID`/`POPUP_BLOCKED` evidence and stop.

Failure evidence includes page URL, profile identity, target field, safe overlay summary,
bounded DOM excerpt or screenshot reference, and timestamp. It excludes credentials and
full page dumps containing sensitive content.

Alternative considered: broadly force every failed click. Rejected because it can act
through unrelated confirmation or production overlays.

### 4. Run collection as an owned managed worker

`process-setup` will validate and launch or reuse one session-bound collection worker,
then return a machine-readable accepted/status response rather than holding the invoking
command until all pages finish.

The session stores a public worker reference; a private machine-local worker manifest
stores PID, ownership token, session, stage, revision, input SHA, selector SHA, command
purpose, start time, heartbeat, phase, current/last completed page, row count, checkpoint
time, log path, and terminal status. The worker is launched without a visible blocking
console and writes progress atomically.

Alternative considered: increase the shell timeout. Rejected because Codex turns,
terminals, and HTTP requests remain interruptible and give no durable worker ownership.

### 5. Derive current status from one attempt model

Each processing attempt has an `attempt_id` binding claim, worker, revision, input hash,
selector hash, and checkpoint identity. Status resolution uses:

1. completed bound result;
2. live owned worker plus valid lease;
3. confirmed dead owned worker with resumable checkpoint;
4. expired or indeterminate claim;
5. current blocking/user-action result;
6. draft/ready state.

A result from an earlier attempt remains immutable history but is labeled superseded and
cannot be rendered as the active blocker while a later attempt is running or recoverable.
The UI shows attempt time and source for historical errors.

Alternative considered: let `session.json`, handoff, worker, and latest `result.json`
each independently drive badges. Rejected because the live session demonstrated
contradictory status.

### 6. Reclaim immediately only when ownership and death are proven

If the worker manifest, PID, ownership token, and session binding match and the owned
process is confirmed exited, the current attempt becomes `recoverable` immediately; it
does not wait for the nominal lease expiry. If ownership or liveness is indeterminate,
the system never kills the PID and waits for lease expiry.

Recovery validates revision, input hash, selector hash, CSV/checkpoint hashes, last
completed page, and row uniqueness. It resumes after the last atomically completed page.
Late writes from the old claim are rejected.

### 7. Report progress before the first checkpoint

The worker publishes bounded phases such as `validating_profile`, `connecting_cdp`,
`settling_popups`, `opening_promotion`, `selecting_high_value`, `collecting_page`,
`writing_checkpoint`, `building_completeness`, and `completed`.

This distinguishes a slow first-page interaction from a dead worker. The UI shows the
phase, elapsed time, page/row progress when known, last heartbeat, last checkpoint, and
specific recovery action. Absence of a first checkpoint is not presented as “0 rows”.

### 8. Make uv execution local and offline-stable

Project launchers set a project-local `UV_CACHE_DIR`, verify the lock/environment
fingerprint, and prefer the existing `.venv` executable for routine runs. Dependency
sync is a separate environment-preparation action and is never started implicitly by a
collection resume.

Alternative considered: rely on the global uv cache and resolve on every command.
Rejected because protected/offline user environments can fail before business logic.

### 9. Treat live acceptance as release evidence

Automated tests remain necessary but do not close this change. A real fresh timestamp
session must:

- start with no usable local production profile;
- complete guided selector setup and user-controlled login;
- handle the delayed onboarding state using the maintained collector;
- write the first checkpoint and finish all visible high-value pages;
- be interrupted after at least one completed page;
- show dead-worker/recoverable status correctly;
- resume the exact session without duplicate rows or repeated pages;
- reach completeness review with no approve, publish, upload, or credential persistence.

## Risks / Trade-offs

- [Selector bootstrap promotes an incorrect candidate] → Require current-DOM validation
  of every purpose field and retain an explicit profile identity/hash.
- [Force click bypasses an unsafe overlay] → Restrict it to whitelisted read-only targets
  and a recognized onboarding overlay; otherwise stop.
- [Worker metadata says alive after PID reuse] → Bind PID to ownership token, attempt,
  session, and health evidence; never act on PID alone.
- [Old and new workers race on checkpoint writes] → Require claim/attempt identity on
  every progress and terminal write; reject stale identities atomically.
- [NAS/CDP/network slowness looks dead] → Use heartbeat and phase-specific deadlines,
  not a single short command timeout.
- [Local selector profiles reduce portability] → Keep guided setup deterministic and
  machine-local; never commit production paths or credentials.

## Migration Plan

1. Add the readiness and attempt schemas with compatibility reads for existing sessions.
2. Add guided selector bootstrap without changing the production default.
3. Harden popup handling and regression tests in the maintained collector.
4. Add the managed worker and status API behind a local feature flag.
5. Migrate new sessions to attempt-derived status; historical sessions retain immutable
   evidence and can be explicitly resumed.
6. Run isolated automated tests, then the real fresh-session acceptance with zero
   production actions.
7. Make the managed worker and readiness gate the default only after acceptance passes.
8. Roll back by disabling managed-worker launch; retain checkpoints, attempt evidence,
   selector profiles, and immutable results for audit.

## Open Questions

- The exact quiet-window duration for delayed guides should be calibrated during live
  acceptance, with a bounded default and machine-local override.
- Windows process identity beyond PID and ownership token may use creation time or a
  local health endpoint; implementation should choose the strongest available
  non-privileged signal.
