## ADDED Requirements

### Requirement: Setup handoff prefers the maintained collector
The system SHALL provide one session-bound processing operation that validates the exact setup handoff and first invokes the maintained `supplement --scan-mode high-value` Playwright collector rather than independently reimplementing collection.

#### Scenario: Valid setup submission starts maintained collection
- **WHEN** an Agent processes a setup handoff whose session, stage, revision, and input SHA-256 match the active session
- **THEN** the system snapshots the inputs and invokes the maintained `high-value` collection path for that exact session

#### Scenario: Maintained collector has no recorded failure
- **WHEN** routine high-value collection has not produced a persisted reproducible execution error
- **THEN** the workflow continues through `supplement --scan-mode high-value` and does not independently reimplement collection

#### Scenario: Maintained collector produces a reproducible error
- **WHEN** `supplement --scan-mode high-value` records a reproducible selector, navigation, popup, parsing, pagination, or page-state failure
- **THEN** Playwright diagnostics may inspect the real DOM to repair the maintained selector profile or collector, after which the same maintained entry is tested and rerun

### Requirement: Playwright repair converges on the maintained path
Any Playwright-assisted repair MUST update the maintained selector profile or maintained collector and MUST include regression verification before live collection resumes.

#### Scenario: Diagnostic identifies a collector defect
- **WHEN** Playwright reproduction shows that the maintained popup or pagination logic is incorrect
- **THEN** the existing collector and its tests are updated, and no separate production collector is retained

### Requirement: Collection is checkpointed and idempotent
The system MUST incrementally persist collected rows and a checkpoint after each completed page, and MUST resume matching work without repeating completed pages or overwriting outputs bound to different inputs.

#### Scenario: Collector is interrupted after a completed page
- **WHEN** the collection process stops after atomically recording page N
- **THEN** an exact-session resume continues after page N using the same revision, input hash, and selector-profile hash

#### Scenario: Resume inputs differ
- **WHEN** a resume request has a different setup revision, input SHA-256, store, or selector-profile SHA-256
- **THEN** the system rejects reuse of the existing checkpoint and records a stale-input reason

### Requirement: Completeness follows successful collection
The processing operation SHALL generate the completeness matrix from the successful high-value collection and product snapshot, bind the result to the setup submission, complete setup, and advance to the completeness stage.

#### Scenario: Collection completes successfully
- **WHEN** all high-value pages are collected and the output passes schema validation
- **THEN** the system writes the completeness matrix and bound result before advancing the session

#### Scenario: Selector fails during collection
- **WHEN** a required current-page element is absent or invalid
- **THEN** the system records `SELECTOR_INVALID`, preserves prior checkpoints, and does not interpret the page as zero products or zero materials

### Requirement: Batch and exact slot evidence are distinct
The batch collector SHALL record target capacity, current count, and missing count from the same visible product-row evidence and SHALL mark exact slot identities as not collected.

#### Scenario: Row exposes target and current counts
- **WHEN** a row states a target of 9 and a current count of 2
- **THEN** the batch output records target 9, current 2, missing 7, and exact-slot status `not_collected`

#### Scenario: Exact slot identity is required for production
- **WHEN** a selected product approaches production approval
- **THEN** the workflow performs a fresh per-product exact-slot verification instead of inferring slot numbers from the batch missing count

### Requirement: Row-level product anomalies do not block valid products
The processor MUST preserve source-row reason codes for missing, invalid, or duplicate product IDs while allowing all valid product rows to continue through collection and completeness generation.

#### Scenario: Product table contains mixed valid and invalid rows
- **WHEN** preflight finds row-level ID anomalies but the table schema and at least one valid row remain
- **THEN** anomalous rows are reported as blocked and valid rows continue without a batch-level stop
