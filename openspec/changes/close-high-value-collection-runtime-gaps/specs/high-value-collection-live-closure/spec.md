## ADDED Requirements

### Requirement: Stage one exposes authoritative collection readiness
The system SHALL evaluate environment, selector profile, current DOM, CDP connection,
login/human-check, page identity, and store identity as separate reason-coded readiness
checks before collection submission.

#### Scenario: Production selector is missing
- **WHEN** stage one has no valid machine-local production selector profile
- **THEN** the page identifies the selector prerequisite as not ready, offers guided
  setup, and does not create a collection-ready handoff

#### Scenario: Login requires user action
- **WHEN** the CDP material page requires login, QR, SMS, CAPTCHA, or risk control
- **THEN** the page reports the exact user-controlled action without treating it as UI
  failure or attempting to store credentials

#### Scenario: Every prerequisite is ready
- **WHEN** the environment, production selector, current page, login state, and target
  store all validate
- **THEN** stage one may create the exact collection handoff bound to that readiness
  evidence

### Requirement: Selector bootstrap remains untrusted until live validation
The system MUST create missing selector profiles only as machine-local non-production
candidates and MUST NOT use them for collection until every required high-value field
passes current-DOM validation.

#### Scenario: New computer has only repository example selectors
- **WHEN** guided selector setup starts without a local production profile
- **THEN** the system creates or selects a Git-ignored candidate and does not execute the
  repository example against the live store

#### Scenario: Candidate validates on the material page
- **WHEN** all required selectors, page identity, and store identity validate
- **THEN** the system records profile name, version, purpose, validation timestamp, and
  SHA-256 before permitting maintained collection

#### Scenario: One candidate field fails
- **WHEN** current-DOM validation cannot locate a required high-value field
- **THEN** the candidate remains non-production and the failure names the field and
  controlled repair action

### Requirement: Delayed guide handling is bounded and safe
The maintained high-value collector SHALL settle delayed guides and safe popups using
whitelisted controls and SHALL restrict any force-click fallback to declared read-only
navigation or filter targets intercepted by a recognized guide.

#### Scenario: Delayed onboarding guide appears
- **WHEN** a whitelisted onboarding guide appears before or during promotion navigation
- **THEN** the collector progresses or closes it within bounded attempts and continues
  through the maintained collector path

#### Scenario: Recognized guide still intercepts a read-only filter
- **WHEN** bounded safe-popup settlement completes but the recognized guide still
  intercepts the high-value filter
- **THEN** the collector may force-click that declared read-only target once and records
  the fallback in evidence

#### Scenario: Unknown overlay blocks a target
- **WHEN** an unrecognized overlay or non-read-only target remains blocked
- **THEN** the collector stops with reason-coded bounded evidence and does not broadly
  force the action

### Requirement: Full collection runs as an owned managed worker
The system SHALL run long high-value collection in one session-bound managed worker whose
lifetime is independent of the foreground command or HTTP request.

#### Scenario: Valid handoff starts collection
- **WHEN** a collection-ready exact setup handoff has no matching live worker or completed
  result
- **THEN** the processor starts one owned worker, returns its attempt identity promptly,
  and prevents a duplicate worker for the same binding

#### Scenario: Foreground command ends
- **WHEN** the command that requested collection returns or its Codex turn ends
- **THEN** the owned worker continues collection and heartbeat/checkpoint progress remains
  observable

#### Scenario: Worker identity cannot be proven
- **WHEN** a PID exists but its ownership token or session/attempt binding does not match
- **THEN** the system does not terminate, reuse, or report that process as the collection
  worker

### Requirement: Progress is visible before and after checkpoints
The managed worker MUST atomically expose current phase, heartbeat, elapsed time,
current/last completed page, row count, and last checkpoint time when available.

#### Scenario: First page has not completed
- **WHEN** the worker is settling popups or selecting the high-value filter before its
  first checkpoint
- **THEN** the page displays the current live phase and heartbeat and does not report
  zero collected rows as a completed result

#### Scenario: A page checkpoint completes
- **WHEN** page N rows and checkpoint are atomically persisted
- **THEN** progress reports page N, the durable row count, checkpoint time, and the next
  collection phase

#### Scenario: Progress heartbeat stops
- **WHEN** no valid worker heartbeat exists within the phase-specific boundary
- **THEN** the UI distinguishes indeterminate or recoverable processing from active work
  and shows the appropriate recovery action

### Requirement: Current stage status is derived from one attempt identity
The system MUST bind claim, worker, progress, checkpoint, and result to one attempt ID and
MUST present historical results separately from the current active or recoverable state.

#### Scenario: Historical selector error precedes a new attempt
- **WHEN** an earlier attempt produced `SELECTOR_PROFILE_NOT_FOUND` and a later attempt
  has valid selector evidence and a live worker
- **THEN** the stage reports the later attempt as current and labels the selector error as
  superseded history

#### Scenario: Owned worker exits without terminal result
- **WHEN** ownership is proven and the worker process has exited after writing no terminal
  result
- **THEN** the attempt becomes recoverable without falsely displaying active processing

#### Scenario: Worker liveness is indeterminate
- **WHEN** ownership or process death cannot be proven and the lease remains valid
- **THEN** the system does not kill or reclaim the worker until the lease expires

### Requirement: Exact-session recovery preserves checkpoint identity
Recovery MUST validate the setup revision, input SHA-256, selector SHA-256, attempt
identity, CSV/checkpoint identity, last completed page, and row uniqueness before
resuming.

#### Scenario: Worker stops after page N
- **WHEN** page N is atomically complete and the owned worker exits
- **THEN** explicit recovery continues after page N without repeating a completed page
  or duplicating product rows

#### Scenario: Recovery inputs changed
- **WHEN** the setup revision, input hash, store, selector hash, or checkpoint identity
  differs
- **THEN** the system rejects checkpoint reuse and reports the exact stale binding

#### Scenario: Old worker writes after reclaim
- **WHEN** an earlier claim attempts a progress, checkpoint, or result write after a new
  attempt is issued
- **THEN** the write is rejected and cannot overwrite the current attempt

### Requirement: Routine execution uses project-local environment state
The collection launcher SHALL use a project-local uv cache and an already validated
project virtual environment for routine execution and SHALL separate dependency
preparation from collection.

#### Scenario: User uv cache is inaccessible
- **WHEN** the global uv cache cannot be read or written but the project environment and
  lock are valid
- **THEN** collection uses the project-local cache/environment and reaches business
  validation without a global-cache failure

#### Scenario: Offline collection resume
- **WHEN** dependencies are already installed and the machine has no package-index access
- **THEN** exact-session resume does not implicitly resolve or download dependencies

### Requirement: Playwright repair converges and remains disposable
Playwright diagnostics SHALL only follow a persisted reproducible maintained-collector
failure and MUST result in a maintained selector/profile or collector regression fix
before the original collection entry is rerun.

#### Scenario: Temporary diagnostic script is used
- **WHEN** a reproducible current-DOM defect requires additional inspection
- **THEN** its bounded evidence is retained, the temporary script is deleted, and no
  second production collector remains

### Requirement: Live acceptance closes the collection path
The change MUST NOT be declared complete until a real fresh timestamp session proves
guided setup, user-controlled login, delayed-guide handling, first checkpoint, complete
pagination, interruption recovery, and completeness generation through the maintained
collector.

#### Scenario: Fresh-session full scan succeeds
- **WHEN** a user configures a new session, completes login, and starts maintained
  high-value collection
- **THEN** every visible page is scanned exactly once, evidence/checkpoints are complete,
  and the session advances to completeness review

#### Scenario: Live scan is interrupted after a checkpoint
- **WHEN** the owned worker is stopped after at least one completed page and then
  recovered
- **THEN** the same session completes without duplicate rows or repeated completed pages

#### Scenario: Acceptance remains read-only
- **WHEN** live acceptance completes
- **THEN** no approve, publish, upload, credential persistence, or source-material
  mutation has occurred
