## ADDED Requirements

### Requirement: CDP browser opens the official material page
The system SHALL discover or launch an approved user-controlled CDP browser and navigate a page to the configured official material-center URL rather than leaving only a blank tab.

#### Scenario: New CDP browser is launched
- **WHEN** setup collection requires login and no approved CDP browser is running
- **THEN** the system launches the isolated browser profile and opens the official material-center or login URL

#### Scenario: Existing CDP browser is reused
- **WHEN** an approved CDP endpoint is already reachable
- **THEN** the system validates its page and store identity before reusing it

### Requirement: Authentication remains user-controlled
The system MUST stop for login, QR, SMS, CAPTCHA, or risk-control interactions and MUST NOT store or expose authentication secrets.

#### Scenario: Login is required
- **WHEN** the material page displays an unauthenticated state
- **THEN** the session records `LOGIN_INTERACTION_REQUIRED`, shows the exact user action, and waits without collecting

#### Scenario: Human check appears
- **WHEN** CAPTCHA, QR confirmation, SMS, or platform risk control is detected
- **THEN** automated actions pause until the user completes the check

### Requirement: The UI explains the two-browser boundary
The interaction page SHALL distinguish the Skill workflow page from the CDP Chrome used for the live store and SHALL display CDP connectivity, login state, observed store, observed URL, and the next action.

#### Scenario: Configuration page is open but CDP is not connected
- **WHEN** the managed interaction UI is healthy and the CDP endpoint is unavailable
- **THEN** the page identifies the configuration browser separately and explains that a live-store Chrome must be opened

### Requirement: Login completion resumes maintained collection
After the user completes login, the processor SHALL revalidate the exact session, page identity, store identity, selector profile, and human-check state before continuing the maintained collector.

#### Scenario: User logs into the correct store
- **WHEN** resume observes the configured store and valid material page
- **THEN** collection continues without requiring the user to navigate promotion controls manually

#### Scenario: User logs into a different store
- **WHEN** the observed store differs from the configured target
- **THEN** the batch is blocked with `STORE_IDENTITY_MISMATCH` and no collection rows are written
