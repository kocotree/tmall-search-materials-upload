## ADDED Requirements

### Requirement: No standalone suitability-review stage for new tasks
New tasks SHALL perform image suitability checks as an automatic checkpoint inside the asset-selection stage and SHALL NOT expose a separate suitability-review navigation item, page, submission, or second candidate decision.

#### Scenario: User adopts images
- **WHEN** the user adopts images in the asset-selection stage
- **THEN** the system SHALL inspect only those adopted images incrementally and SHALL keep the user on the same page until submission requirements are satisfied

#### Scenario: New-task navigation
- **WHEN** the stage navigation is rendered for a new task
- **THEN** it SHALL proceed from asset selection directly to slot processing and SHALL NOT display “图片适用性检测”

### Requirement: Selected-image preflight remains mandatory
The automatic checkpoint MUST verify source readability, source SHA-256, duplicate identity, format, file size, dimensions, `1:1 / 3:4` feasibility, minimum and recommended resolution, and compression availability before deterministic planning.

#### Scenario: Selected image is valid
- **WHEN** an adopted source can produce at least one allowed ratio with both output dimensions at least 720 pixels and satisfies the configured size/compression policy
- **THEN** the system SHALL mark it usable and SHALL preserve both ratio capabilities for later slot planning

#### Scenario: Selected image is unusable
- **WHEN** an adopted source is unreadable, unsupported, duplicate, below minimum output dimensions, below minimum file size, or above the maximum size without an available compression provider
- **THEN** the system SHALL mark the card unusable, SHALL explain the exact reason, and SHALL exclude it from the usable count

### Requirement: Suitability information is visible during selection
Each selected-image card SHALL display the original width and height, original ratio, format, original file size, policy status, both allowed-ratio assessments, and compression requirement without requiring a separate page.

#### Scenario: Background inspection completes
- **WHEN** incremental inspection returns for a selected card
- **THEN** the card SHALL update in place and SHALL NOT rebuild or reset the surrounding selection gallery

#### Scenario: Inspection is still running
- **WHEN** an adopted image has not finished inspection
- **THEN** the page SHALL show a pending state and SHALL NOT count the image as usable for submission

### Requirement: One- or two-image selections remain drafts
The asset-selection stage SHALL allow saving any non-empty selection draft, but SHALL require at least 3 usable unique selected images for every submitted product before automatic planning and transition.

#### Scenario: User selects one image
- **WHEN** a product has exactly 1 usable selected image
- **THEN** the page SHALL report `0` complete slots, SHALL state that 2 more usable images are required, and SHALL keep the submit-and-plan action disabled

#### Scenario: User selects two images
- **WHEN** a product has exactly 2 usable selected images
- **THEN** the page SHALL report `0` complete slots, SHALL state that 1 more usable image is required, and SHALL keep the submit-and-plan action disabled

#### Scenario: User selects three images
- **WHEN** a product has exactly 3 usable selected images and at least one missing backend slot
- **THEN** the submit-and-plan action SHALL become eligible and SHALL preview one complete 3-image slot

### Requirement: Preflight is cached and revision-bound
The system SHALL persist a task-local suitability snapshot bound to the asset-selection revision, policy SHA-256, source identity, and inspection-provider version, and SHALL reuse it only while those identities remain current.

#### Scenario: Reopening unchanged selections
- **WHEN** the user reopens a task with unchanged selected assets, source identities, policy, and provider version
- **THEN** the system SHALL reuse cached inspection records without rescanning unrelated shared-drive files

#### Scenario: Source identity changes
- **WHEN** the source SHA-256 or file metadata no longer matches the cached record
- **THEN** the system SHALL invalidate that record and SHALL re-inspect only the affected selected image

### Requirement: Preflight produces no derivative output
The asset-selection checkpoint SHALL calculate crop feasibility and suggested boxes but SHALL NOT generate final cropped or compressed files.

#### Scenario: Selection is submitted
- **WHEN** all selected images pass preflight and deterministic planning begins
- **THEN** no derived image SHALL exist until the user confirms a slot ratio and completes crop/compression in the slot-processing stage

### Requirement: Historical suitability stages remain recoverable
Historical tasks containing an `image_review` state or `04-image-review` artifacts SHALL remain readable and SHALL map forward without deleting their inputs, snapshots, revisions, or audit records.

#### Scenario: Historical image review is complete
- **WHEN** an old task has a current completed suitability snapshot
- **THEN** the system SHALL reuse or migrate the compatible records into the selected-asset preflight contract and SHALL open the appropriate downstream page

#### Scenario: Historical image review is incomplete
- **WHEN** an old task is paused in the standalone suitability page
- **THEN** recovery SHALL return the user to asset selection with existing decisions preserved and SHALL explain that suitability now runs automatically
