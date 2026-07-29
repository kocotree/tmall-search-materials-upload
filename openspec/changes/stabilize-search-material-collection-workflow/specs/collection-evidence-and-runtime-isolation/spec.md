## ADDED Requirements

### Requirement: Collection evidence is complete and bound
The task SHALL persist store, page, selector, input, product-preflight, collection, checkpoint, completeness, and stage-result evidence bound to the exact session, stage, revision, and input SHA-256.

#### Scenario: High-value collection completes
- **WHEN** collection and completeness generation succeed
- **THEN** the task contains verifiable hashes and references connecting the setup submission to the collected CSV and completeness result

### Requirement: Store identity evidence is durable
Before collection, the system MUST persist the target store, observed store, observed page URL, verification time, selector-profile identity, and match result without persisting credentials.

#### Scenario: Store identity matches
- **WHEN** the visible store equals the configured store
- **THEN** a positive store-identity evidence record is written before collection rows

### Requirement: Persisted timestamps include timezone
Every newly persisted workflow timestamp MUST be ISO 8601 with an explicit UTC offset or `Z`.

#### Scenario: Session and evidence files are created
- **WHEN** the system writes creation, update, heartbeat, validation, collection, approval, or evidence timestamps
- **THEN** every timestamp includes timezone information and can be ordered unambiguously

### Requirement: Runtime artifacts remain outside source control
Default task runs, machine-local selector profiles, CDP profiles, locks, logs, and generated diagnostics MUST be ignored by Git and MUST NOT be staged by normal repository-wide adds.

#### Scenario: A new task is created under the default runs root
- **WHEN** Git status is inspected after the task writes runtime evidence
- **THEN** the runtime directory does not appear as an untracked source change

### Requirement: Sensitive authentication data is excluded
Task evidence and logs MUST NOT contain passwords, cookies, tokens, QR payloads, SMS codes, or saved browser authentication data.

#### Scenario: User completes login in CDP Chrome
- **WHEN** login succeeds and collection evidence is written
- **THEN** evidence records only non-secret page/store state and references no authentication material

### Requirement: Row anomalies are visible to the user
The completeness result SHALL include counts and source-row reason codes for product-table anomalies while keeping them separate from valid high-value products.

#### Scenario: Missing and duplicate IDs are present
- **WHEN** product preflight reports row-level anomalies
- **THEN** the completeness page displays the anomaly summary and excludes those rows from matching without blocking valid products
