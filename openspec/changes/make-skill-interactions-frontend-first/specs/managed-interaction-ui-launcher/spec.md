## ADDED Requirements

### Requirement: A single command starts a usable interaction session
The system SHALL provide one recommended command that creates or resumes an isolated session, starts the interaction service in the background, waits for readiness, and returns the exact session URL without leaving the caller blocked.

#### Scenario: New session starts successfully
- **WHEN** the Agent invokes the managed launcher without a session ID
- **THEN** a timestamped session is created, the service becomes healthy, and the command returns the session ID, PID, port, runs root, and exact URL

#### Scenario: Existing session is resumed
- **WHEN** the Agent invokes the launcher with an explicit valid session ID
- **THEN** no new session is created and the returned URL points to that session's authoritative current stage

### Requirement: Launcher opens the frontend using the best available browser channel
After readiness succeeds, the Agent SHALL first use the Codex in-app browser when available; otherwise the launcher MAY open the system default browser, and browser-open failure SHALL NOT be misreported as service failure.

#### Scenario: In-app browser is available
- **WHEN** a healthy URL is returned and Codex browser control is callable
- **THEN** the Agent opens that exact URL in the in-app browser

#### Scenario: No browser can be opened
- **WHEN** the service is healthy but neither in-app nor system browser opening succeeds
- **THEN** the URL is shown to the user with `BROWSER_OPEN_FAILED` and the service remains available

### Requirement: Startup lifecycle is deterministic and safe
The launcher SHALL select an available loopback port from a bounded range, persist service ownership metadata, poll a health endpoint instead of sleeping blindly, and manage only processes it created.

#### Scenario: Default port is occupied
- **WHEN** the preferred port belongs to another process
- **THEN** the launcher selects another allowed port and does not terminate or reuse the unknown process

#### Scenario: Service fails before readiness
- **WHEN** the spawned process exits or health polling times out
- **THEN** the launcher stops only its own remaining process, preserves logs, and returns a stable reason code and recovery command

#### Scenario: Start is repeated for the same healthy session
- **WHEN** the launcher is invoked again for a session whose managed service is healthy
- **THEN** it returns the existing service URL rather than starting a duplicate

### Requirement: Business values never block page startup
The launcher SHALL require only executable runtime conditions before opening stage one; missing store, month, image roots, unavailable NAS mappings, and absent login state SHALL be rendered in the UI instead of requested by the launcher.

#### Scenario: No image source is configured
- **WHEN** runtime dependencies are ready but local image-source configuration is empty
- **THEN** stage one opens and asks for image sources through its frontend controls

#### Scenario: uv installation requires permission
- **WHEN** neither a usable environment nor uv is available
- **THEN** the Agent requests only the installation or system permission in chat and resumes UI startup after permission is resolved

### Requirement: Service status and stop operations are recoverable
The system SHALL expose idempotent status and stop operations bound to persisted session service metadata.

#### Scenario: User stops a managed service
- **WHEN** stop is requested for a service whose PID and ownership token match persisted metadata
- **THEN** the service is stopped and the session data remains available for later resume

#### Scenario: Persisted PID has been reused
- **WHEN** the recorded PID no longer matches the ownership token or listening endpoint
- **THEN** stop refuses to terminate it and reports `SERVICE_OWNERSHIP_MISMATCH`
