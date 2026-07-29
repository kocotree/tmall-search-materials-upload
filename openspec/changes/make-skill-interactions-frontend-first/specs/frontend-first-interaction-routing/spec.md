## ADDED Requirements

### Requirement: Structured user input is frontend-first
Every stage field and manual decision SHALL declare an interaction policy, and the Agent SHALL present frontend controls before asking for the same value in Codex chat.

#### Scenario: Stage one needs store and image roots
- **WHEN** stage one is active and those fields are empty
- **THEN** the Agent opens the stage-one page and waits for its handoff instead of asking for the values in chat

#### Scenario: Later stage needs product or image decisions
- **WHEN** a product, folder, image, slot, crop, copy, dry-run, approval, or production decision is required
- **THEN** the corresponding frontend stage is opened with the current saved state

### Requirement: Chat fallback requires an explicit reason
The Agent SHALL use Codex chat for structured stage input only after a frontend attempt produces an allowed stable fallback reason, and SHALL tell the user why the page cannot be used.

#### Scenario: Frontend is healthy
- **WHEN** the current stage page is reachable and supports the required field
- **THEN** chat fallback is prohibited

#### Scenario: Frontend service cannot start
- **WHEN** startup returns `UI_START_FAILED` after bounded recovery attempts
- **THEN** the Agent may ask only for the current stage's missing fallback-eligible fields and records that reason

#### Scenario: Page schema lacks a required field
- **WHEN** a required structured value has no supported frontend control
- **THEN** the Agent records `SCHEMA_GAP`, uses a schema-compatible chat fallback if one exists, and identifies the missing frontend capability for follow-up

### Requirement: Fallback data uses the authoritative stage schema
Business data collected through chat fallback SHALL be written to the same session, stage, revision, draft, field validation, and handoff pipeline as frontend input.

#### Scenario: Chat fallback value is valid
- **WHEN** the user supplies a valid fallback-eligible value
- **THEN** it is saved in the authoritative stage draft with interaction-channel audit metadata and becomes visible when the page recovers

#### Scenario: Revision changed in another tab
- **WHEN** chat fallback tries to write against a stale revision
- **THEN** the write is rejected with the same revision conflict used by frontend saves

#### Scenario: Chat answer fails field validation
- **WHEN** a fallback answer does not satisfy the stage schema
- **THEN** no handoff is created and the exact field error is returned

### Requirement: Fallback is auditable and reversible
Each fallback write SHALL record its channel, reason code, detail, actor, timestamp, session, stage, revision, and input hash, and the user SHALL be able to review or change it in the frontend after recovery.

#### Scenario: Frontend returns after fallback
- **WHEN** the user reopens the session page
- **THEN** fallback values are hydrated with a visible source indicator and can be edited through normal revision rules

#### Scenario: Agent resumes the task in a new Codex session
- **WHEN** a new Agent loads the exact session and current handoff
- **THEN** it can determine which values came from frontend and which came from chat fallback without relying on conversation history

### Requirement: Safety-critical confirmation remains exact
Chat fallback SHALL NOT weaken store checks, immutable approval manifests, production confirmation, or publish authorization.

#### Scenario: Approval page is unavailable
- **WHEN** the exact approval stage cannot be opened and chat fallback is allowed
- **THEN** the Agent presents the immutable task IDs, content hashes, store, expiry, and action, and accepts only an explicit confirmation bound to that checklist

#### Scenario: User gives broad approval
- **WHEN** the user replies with an ambiguous statement such as "全部继续"
- **THEN** no approval or publish authorization is persisted

### Requirement: Frontend recovery precedes repeated questioning
After a frontend failure, the Agent SHALL offer a precise recovery action and SHALL NOT repeatedly ask for values already persisted in the session.

#### Scenario: Service is restarted
- **WHEN** the managed UI recovers after one or more fallback values were recorded
- **THEN** the Agent opens the recovered current-stage page and continues from saved state

#### Scenario: User starts another Codex window
- **WHEN** the user supplies the generated recovery prompt in a fresh window
- **THEN** the Agent resolves the exact runs root, session ID, stage, revision, and input hash before asking for any additional input
