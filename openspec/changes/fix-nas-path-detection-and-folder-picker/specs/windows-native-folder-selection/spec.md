## ADDED Requirements

### Requirement: Folder selection uses an interactive native helper
On Windows, the system SHALL handle an explicit “选择文件夹” action through a dedicated interactive native helper rather than constructing a `tkinter` window inside a Flask request thread.

#### Scenario: User selects an existing folder
- **WHEN** the user clicks “选择文件夹” and confirms an existing directory in the native dialog
- **THEN** the helper returns the absolute path, the frontend fills that row, and the path remains pending until a reachability check or submission

#### Scenario: User cancels the dialog
- **WHEN** the user closes or cancels the native dialog
- **THEN** the API returns `cancelled=true`, the existing input value is preserved, and no error is shown

#### Scenario: An initial directory is usable
- **WHEN** the row already contains an accessible absolute directory
- **THEN** the native dialog opens at that directory without enumerating image content in the Flask process

### Requirement: Folder-picker execution is bounded and ownership-safe
The system MUST allow at most one picker per service instance and MUST manage only the helper process created for the current request.

#### Scenario: Another picker is already active
- **WHEN** a second request arrives before the first picker finishes
- **THEN** it returns `FOLDER_PICKER_BUSY` without opening another window

#### Scenario: Picker exceeds its deadline
- **WHEN** the owned helper does not return before the configured timeout
- **THEN** the service terminates that helper, returns `FOLDER_PICKER_TIMEOUT`, and remains healthy

#### Scenario: Helper returns an invalid path
- **WHEN** the helper output is relative, malformed, nonexistent, or not a directory
- **THEN** the API rejects it with `FOLDER_PICKER_INVALID_RESULT` and does not update the frontend value

### Requirement: Folder-picker failures are reason-coded and recoverable
The API SHALL distinguish unavailable GUI sessions, unsupported platforms, helper startup failures, and helper protocol failures.

#### Scenario: Interactive desktop is unavailable
- **WHEN** the managed process cannot display a native window in the user's desktop session
- **THEN** the API returns `FOLDER_PICKER_GUI_UNAVAILABLE` and instructs the user to paste a local or UNC path manually

#### Scenario: Native helper cannot start
- **WHEN** the Windows helper runtime cannot be launched
- **THEN** the API returns `FOLDER_PICKER_START_FAILED` with safe diagnostic detail and leaves the existing input untouched

#### Scenario: Platform has no supported helper
- **WHEN** the service is running outside a supported Windows interactive environment
- **THEN** the API returns `FOLDER_PICKER_UNSUPPORTED` and keeps manual entry available

### Requirement: Manual and UNC entry remains available
The frontend MUST keep the path text input usable regardless of native picker availability.

#### Scenario: Picker is unavailable
- **WHEN** any folder-picker failure occurs
- **THEN** the page displays the exact reason and allows the user to paste an absolute local, mapped-drive, or UNC path

#### Scenario: Picker failure follows an existing value
- **WHEN** a row already contains a path and picker startup or execution fails
- **THEN** the existing value is not cleared, normalized, or replaced
