## Purpose

Ensure maintained high-value material collection begins from a verified pagination origin, traverses every visible page exactly once, and never reports a reused middle or terminal page as a complete fresh scan.

## ADDED Requirements

### Requirement: Fresh collection starts from a verified first page
Before parsing or checkpointing rows for a fresh high-value collection, the system MUST observe the current pagination position, reset it to the first page when necessary, and verify that the resulting position is page 1.

#### Scenario: Reused tab is already on the first page
- **WHEN** a fresh collection attaches to a tab whose selected high-value result page is verifiably page 1
- **THEN** the system proceeds without changing pages and records the verified first-page origin

#### Scenario: Reused tab is on a middle page
- **WHEN** a fresh collection attaches to a tab whose selected high-value result page is greater than page 1
- **THEN** the system returns to page 1, verifies the transition, and only then parses or checkpoints rows

#### Scenario: Reused tab is on the terminal page
- **WHEN** a fresh collection attaches to a terminal result page whose next-page control is disabled
- **THEN** the system returns to and verifies page 1 instead of treating the terminal page as a complete one-page scan

### Requirement: Unverifiable pagination origin fails closed
The system MUST NOT emit a successful checkpoint or completed result when it cannot determine or establish the required pagination origin.

#### Scenario: Current page cannot be determined
- **WHEN** the selected page indicator cannot be observed unambiguously
- **THEN** collection stops with `PAGINATION_ORIGIN_UNVERIFIED` and preserves diagnostic evidence without publishing a current result

#### Scenario: First-page reset does not change the observed position
- **WHEN** the system requests page 1 but cannot verify page 1 within the bounded transition window
- **THEN** collection stops with `PAGINATION_ORIGIN_RESET_FAILED` and does not parse the visible rows as page 1

### Requirement: Resume is bound to the checkpoint pagination position
A resumed collection MUST first establish a verified first-page origin and then deterministically advance through exactly the checkpointed completed pages before collecting the next page.

#### Scenario: Resume after page N
- **WHEN** a valid checkpoint records page N as complete
- **THEN** the system verifies page 1, advances through pages 1 to N while validating page transitions, and begins new persistence at page N+1

#### Scenario: Resume cannot reproduce checkpoint position
- **WHEN** the system cannot traverse to the page boundary recorded by the checkpoint or observes inconsistent product identities
- **THEN** resume stops with `PAGINATION_CHECKPOINT_MISMATCH` without duplicating or replacing completed rows

### Requirement: Terminal completion requires corroborating pagination evidence
A disabled next-page control MUST NOT be the sole proof that a high-value scan is complete. The system MUST corroborate terminal state with the observed current-page position and stable row identity.

#### Scenario: Verified final page
- **WHEN** the next-page control is disabled, the current position is the observed terminal page, and the visible row identity is stable
- **THEN** the system may complete the scan and records terminal pagination evidence

#### Scenario: Disabled next control on an unverified page
- **WHEN** the next-page control is disabled but the current or terminal page position cannot be verified
- **THEN** collection stops with `PAGINATION_TERMINAL_UNVERIFIED` instead of completing

#### Scenario: Next control is transiently disabled
- **WHEN** the next-page control is disabled during an unsettled page transition or row update
- **THEN** the system waits for the bounded stability condition and does not declare completion from the transient state

### Requirement: Every accepted page transition is identity-checked
The system MUST verify that an accepted next-page transition changes the visible ordered product identity and advances the observed page position by exactly one.

#### Scenario: Normal next-page transition
- **WHEN** the current page is N and the next-page action succeeds
- **THEN** the system observes page N+1 with a different ordered product identity before parsing or checkpointing it

#### Scenario: Pagination skips or remains unchanged
- **WHEN** a next-page action leaves the page at N, skips beyond N+1, or produces an unchanged product identity
- **THEN** collection stops with a stable pagination transition error and preserves the last complete checkpoint

### Requirement: Pagination evidence is attempt-bound
The system MUST persist the pagination origin, each accepted transition, the terminal observation, selector identity, ordered product identity hashes, and timestamps under the current immutable collection attempt.

#### Scenario: Successful multi-page collection
- **WHEN** collection completes across multiple pages
- **THEN** attempt evidence reconciles the first-page origin, every page number, unique product count, final checkpoint, and terminal-page observation

#### Scenario: Pagination failure
- **WHEN** origin, transition, resume, or terminal validation fails
- **THEN** evidence identifies the exact observed page state and reason code without recording credentials or sensitive browser state

### Requirement: Historical false-success output is not reused
Known collection output produced from an unverified pagination origin MUST NOT authorize product selection, approval, or publication.

#### Scenario: Session 20260730_032916 is inspected
- **WHEN** the workflow encounters the five-row completed output from session `20260730_032916`
- **THEN** it identifies the output as invalid pagination-origin evidence and requires a clean maintained-collector rerun while preserving the original audit files

