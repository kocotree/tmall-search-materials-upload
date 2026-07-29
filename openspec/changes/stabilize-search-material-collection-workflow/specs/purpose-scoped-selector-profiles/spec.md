## ADDED Requirements

### Requirement: Selector validation is operation-specific
Each browser operation MUST declare and validate only its required selector purpose plus shared safety selectors.

#### Scenario: High-value profile omits publish selectors
- **WHEN** a selector profile contains every required high-value collection selector but no upload or publish selectors
- **THEN** high-value collection validation succeeds while publish validation remains unavailable

#### Scenario: High-value selector is missing
- **WHEN** the profile lacks the promotion rows or high-value filter selector
- **THEN** high-value collection fails with `SELECTOR_INVALID` naming the missing field

### Requirement: Production profiles are discovered deterministically
The system SHALL resolve a machine-local production selector profile using documented precedence and SHALL never silently use the repository example profile for production collection.

#### Scenario: Explicit selector file is provided
- **WHEN** `--selectors` points to a valid production profile
- **THEN** that profile takes precedence over environment and local configuration

#### Scenario: Only the example profile is available
- **WHEN** no machine-local production profile is configured and only repository example selectors exist
- **THEN** collection stops with a configuration reason and does not execute the example profile against the live store

### Requirement: Profiles carry identity and validation evidence
A production selector profile MUST have schema/version identity, supported purposes, and non-placeholder selectors, and successful runtime validation SHALL persist its SHA-256 and current-DOM evidence.

#### Scenario: Profile validates against current material page
- **WHEN** static schema validation and runtime DOM checks succeed
- **THEN** task evidence records profile name, version, SHA-256, validated purpose, URL, timestamp, and field results

#### Scenario: Profile contains placeholder selectors
- **WHEN** a production profile contains known example placeholders such as `#store` or `#publish`
- **THEN** static validation rejects it before browser collection

### Requirement: Playwright diagnostics repair maintained collection
Diagnostic browser work after a persisted reproducible collector error MUST result in a reviewed selector-profile update or maintained collector change with tests, not a second production collection path.

#### Scenario: Selector changed in the live DOM
- **WHEN** current-page validation identifies a changed high-value filter selector
- **THEN** the diagnostic records the failed field and the repaired maintained profile must pass targeted browser acceptance before collection resumes

#### Scenario: Collector behavior is wrong
- **WHEN** the selector is valid but Playwright reproduction identifies incorrect navigation, popup, parsing, or pagination behavior
- **THEN** the maintained collector and its regression tests are repaired before the original supplement entry is rerun
