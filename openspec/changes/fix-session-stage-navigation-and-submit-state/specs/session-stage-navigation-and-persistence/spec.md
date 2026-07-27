## ADDED Requirements

### Requirement: Existing session opens at its current stage
When a valid `session_id` is present, the application SHALL load the session snapshot before choosing the initial active stage and SHALL activate the valid `session.current_stage`.

#### Scenario: Historical task resumes at image review
- **WHEN** a historical session reports `current_stage` as `image_review`
- **THEN** the first interactive stage shown SHALL be `image_review` rather than `setup`

#### Scenario: Invalid current stage falls back safely
- **WHEN** the session snapshot contains a missing or unsupported `current_stage`
- **THEN** the application SHALL activate the first known stage, display a recoverable warning, and SHALL NOT modify the session

### Requirement: Navigation reflects the complete session snapshot
The application SHALL populate every stage navigation item from the authoritative revision and status in the session snapshot before normal interaction begins.

#### Scenario: Completed prior stages are visible immediately
- **WHEN** setup, completeness, and asset matching are already completed
- **THEN** all three navigation items SHALL display completed status immediately without requiring the user to visit them

#### Scenario: Locked stage explains why actions are unavailable
- **WHEN** the user views a stage whose server status is `completed`, `ready_for_agent`, or `processing`
- **THEN** save and submit actions SHALL be unavailable and the page SHALL display the lock reason and a route to the current actionable stage

### Requirement: Hydration and rendering are revision-idempotent
Loading server input, constructing result components, and normalizing default UI state SHALL NOT mark the form dirty, trigger automatic saving, or create a new revision unless a user-originated edit changes a persisted value.

#### Scenario: Opening image review has no write side effect
- **WHEN** the user opens an existing draft image-review stage and makes no edit
- **THEN** its revision, input SHA-256, handoff state, and persisted decisions SHALL remain unchanged

#### Scenario: Rewriting the same hidden JSON value is silent
- **WHEN** a renderer computes a hidden JSON value equal to the hydrated persisted value
- **THEN** the control SHALL NOT emit a user-edit signal or schedule automatic saving

#### Scenario: Real user edit still autosaves
- **WHEN** the user changes a crop, decision, slot assignment, or text field
- **THEN** the form SHALL become dirty and the latest value SHALL be saved according to the debounce policy

### Requirement: Submit intent is not lost during persistence
The application MUST preserve a user submit intent received while a draft save is in flight and MUST either execute it after the save completes or present an explicit recoverable failure.

#### Scenario: Submit follows an in-flight autosave
- **WHEN** automatic draft saving is in progress and the user clicks “提交给 Agent”
- **THEN** the page SHALL show that submission is queued and SHALL submit the latest values after the save succeeds

#### Scenario: Draft failure does not pretend submission succeeded
- **WHEN** the in-flight draft save fails before a queued submission
- **THEN** the application SHALL retain the dirty values, cancel or pause submission, and display an actionable error

#### Scenario: Stage switch cancels obsolete queued intent
- **WHEN** the user switches stages or sessions before a queued submission starts
- **THEN** the obsolete intent SHALL NOT write to either the previous or newly selected stage

### Requirement: Persistence feedback is observable
Every manual save or submit action SHALL result in an immediate visible state transition, including queued, saving, submitting, succeeded, validation failed, or transport failed.

#### Scenario: Rapid repeated click receives feedback
- **WHEN** the user clicks save or submit while another persistence operation is active
- **THEN** the application SHALL display whether the action was merged, queued, or rejected and SHALL NOT silently ignore the click

#### Scenario: Server validation remains authoritative
- **WHEN** a queued or direct submission fails server validation
- **THEN** field errors and the general validation message SHALL be displayed and the stage SHALL remain editable
