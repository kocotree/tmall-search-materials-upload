## ADDED Requirements

### Requirement: Processing claims use an authoritative lease
Claiming a submitted stage SHALL atomically record one authoritative claim ID, bound revision, input SHA-256, claimant identity when available, claimed time, heartbeat time, and lease expiry in session state.

#### Scenario: Unclaimed handoff is processed
- **WHEN** a valid `ready_for_agent` handoff has no active lease
- **THEN** exactly one claimant receives a processing lease bound to that submission

#### Scenario: Another claimant arrives during an active lease
- **WHEN** a second Agent attempts to claim the same stage before lease expiry
- **THEN** the system rejects the second claim and reports the active claim identity and expiry

### Requirement: Immutable handoff evidence does not conflict with claim state
The submitted handoff SHALL remain immutable submission evidence while current claim status is read from the authoritative session lease, and APIs/UI SHALL present the combined state without implying that an already claimed handoff is unclaimed.

#### Scenario: Handoff has been claimed
- **WHEN** the handoff file still records its original submitted status and the session has an active processing lease
- **THEN** status APIs and the UI report the stage as processing with the active claim reference

### Requirement: Expired processing is explicitly recoverable
An expired processing lease SHALL permit an explicit idempotent reclaim that validates existing checkpoints and outputs before resuming.

#### Scenario: Agent task ended without a result
- **WHEN** heartbeat and lease expiry pass while the stage has no completed bound result
- **THEN** the UI offers an exact-session recovery action and a new claimant can resume from verified evidence

#### Scenario: Late response from old claimant
- **WHEN** an expired claimant attempts to write after a new claim was issued
- **THEN** the write is rejected as stale and cannot overwrite the current result or checkpoint

### Requirement: Completed bound results are not repeated
The processor MUST return the existing result for an identical completed submission and MUST reject attempts to overwrite it with a different claim or input identity.

#### Scenario: Identical processing request is repeated
- **WHEN** the same session, stage, revision, and input SHA-256 already have a completed result
- **THEN** the operation returns that result without recollecting pages
