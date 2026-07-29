## ADDED Requirements

### Requirement: Image-source checks classify path access
The system SHALL return a structured, read-only diagnostic for every configured local, mapped-drive, or UNC image-source path.

#### Scenario: Accessible local or NAS directory
- **WHEN** the managed service can read directory metadata for the configured root within the deadline
- **THEN** the result reports `available=true`, `reason_code=PATH_AVAILABLE`, the path kind, and a timezone-aware check time

#### Scenario: Mapped drive is absent from the service session
- **WHEN** a configured drive-letter root is not mapped or mounted in the managed service's Windows logon session
- **THEN** the result reports `available=false`, `reason_code=DRIVE_NOT_MAPPED`, and instructs the user to connect the drive or enter a UNC path

#### Scenario: UNC host or share is unavailable
- **WHEN** Windows reports that the UNC host or share cannot be reached
- **THEN** the result reports `NETWORK_HOST_UNAVAILABLE` or `NETWORK_SHARE_NOT_FOUND` without claiming the child directory is merely missing

#### Scenario: Directory is missing or access is denied
- **WHEN** the host/share is reachable but the directory does not exist or the current identity lacks read access
- **THEN** the result reports `PATH_NOT_FOUND` or `ACCESS_DENIED` respectively

### Requirement: Network diagnostics are bounded
The system MUST prevent a slow or offline NAS path from indefinitely blocking the interaction service.

#### Scenario: Remote metadata operation exceeds its deadline
- **WHEN** a network path probe does not complete within the configured timeout
- **THEN** the system terminates only its owned probe helper, reports `PATH_CHECK_TIMEOUT`, and remains responsive to later requests

#### Scenario: Multiple sources are checked
- **WHEN** the user checks up to fifty configured roots
- **THEN** the system applies bounded concurrency and an overall deadline while returning one result for every source

### Requirement: Diagnostics do not access source content or credentials
The system MUST restrict configuration-time diagnostics to path syntax, mapping information, and directory metadata.

#### Scenario: A path is checked
- **WHEN** the diagnostic runs
- **THEN** it does not enumerate files, open images, create marker files, mount network shares, prompt for credentials, or persist authentication data

#### Scenario: Diagnostic evidence is returned or saved
- **WHEN** the UI or task stores a path-check result
- **THEN** it contains only the configured path, safe classification fields, timestamps, and recovery guidance

### Requirement: UNC paths are portable while mapped paths remain supported
The system SHALL accept both UNC and mapped-drive paths and SHALL explain their portability difference.

#### Scenario: Windows can resolve a mapped drive to UNC
- **WHEN** a configured mapped drive has a discoverable UNC target
- **THEN** the result may include a `portable_path_suggestion` but does not replace the user's path without explicit confirmation

#### Scenario: Mapping differs on another computer
- **WHEN** the saved drive letter is unavailable on the current computer
- **THEN** the UI preserves the value, reports `DRIVE_NOT_MAPPED`, and allows the user to paste the corresponding UNC path

### Requirement: The frontend presents actionable path status
The stage-one page SHALL render each source's reason-specific status and next action rather than reducing all failures to “当前不可访问”.

#### Scenario: A known diagnostic reason is returned
- **WHEN** the check API returns a stable reason code
- **THEN** the row displays a concise Chinese explanation and the recovery action associated with that code

#### Scenario: A legacy or unknown response is returned
- **WHEN** no recognized reason code is available
- **THEN** the UI uses a generic fallback message without discarding the configured path
