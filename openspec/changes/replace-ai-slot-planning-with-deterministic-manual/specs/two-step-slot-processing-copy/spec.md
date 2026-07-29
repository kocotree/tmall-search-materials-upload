## ADDED Requirements

### Requirement: Slot-processing stage has two progressive subpages
The slot-processing stage SHALL be the next visible stage after asset selection and SHALL expose only “图片裁剪和压缩” and “AI 生成标题和描述” as progressive subpages for new tasks.

#### Scenario: Entering slot processing with an automatic draft
- **WHEN** asset-selection submission and automatic preflight create a deterministic slot draft
- **THEN** the slot-processing stage SHALL open the “图片裁剪和压缩” subpage and SHALL show the editable slot summary before crop controls

#### Scenario: No third planning tab
- **WHEN** the slot-processing navigation is rendered for a new task
- **THEN** it SHALL NOT render a separate slot-planning tab, AI slot-planning button, AI planning status, confidence, prompt-copy action, or rule fallback action

### Requirement: Slot confirmation gates crop controls
The first slot-processing subpage SHALL combine deterministic-draft review and manual slot editing with crop and compression, but SHALL NOT enable crop processing until the exact slot-plan revision is confirmed.

#### Scenario: Unconfirmed automatic draft
- **WHEN** the user enters the first subpage with an unconfirmed deterministic draft
- **THEN** the page SHALL show slot assignments and the shared unused candidate pool while crop controls remain hidden or disabled

#### Scenario: Valid slot confirmation
- **WHEN** every slot has 3–9 unique same-product images and one common target ratio
- **THEN** confirmation SHALL lock that exact plan revision and SHALL reveal crop and compression controls without generating copy

#### Scenario: Slot edit after confirmation
- **WHEN** a confirmed assignment, order, or target ratio is changed
- **THEN** affected image outputs and downstream copy SHALL be invalidated and the workflow SHALL return to unconfirmed slot review on the first subpage

### Requirement: Crop and compression remain visual and manual
The first subpage SHALL retain direct `3:4 / 1:1` ratio selection, visual crop-box adjustment, output preview, source metadata, compression confirmation, and actual-file validation.

#### Scenario: Processing completes
- **WHEN** every confirmed image has a valid crop decision and required compression acknowledgement
- **THEN** the system SHALL write derived files only inside the task directory, verify their actual SHA-256, size, dimensions, and ratio, and unlock the copy subpage

#### Scenario: Source identity changed
- **WHEN** a source image SHA-256 differs from the identity captured before processing
- **THEN** processing SHALL stop for that image and SHALL require the user to return to asset selection for renewed preflight or restore the source

### Requirement: AI is limited to final title and description generation
The second subpage SHALL allow AI to generate title and description only from confirmed processed slot images and SHALL keep AI output advisory until the user confirms it.

#### Scenario: Copy generation is requested
- **WHEN** all outputs for a slot are ready and the user requests AI copy
- **THEN** the Agent request SHALL contain the final ordered images and allowed product facts, and SHALL NOT be allowed to change slot count, image membership, order, ratio, crop, or compression

#### Scenario: AI copy fails
- **WHEN** AI title or description generation fails, times out, or returns invalid text
- **THEN** the processed images and slot plan SHALL remain unchanged and the user SHALL be able to retry or enter copy manually

#### Scenario: User edits AI copy
- **WHEN** the user changes an AI-generated title or description
- **THEN** the edited copy SHALL be stored as a manual override and SHALL require user confirmation before dry-run

### Requirement: Historical slot-processing state remains recoverable
The system SHALL map historical three-subpage tasks and historical AI/规则 slot sources into the two-subpage workflow without deleting their audit data.

#### Scenario: Historical task already has processed outputs
- **WHEN** an old task has a confirmed slot plan and current processed outputs
- **THEN** it SHALL open the appropriate crop/output or copy subpage according to its durable workflow state and SHALL retain prior request and response files for audit

#### Scenario: Historical task only has an unconfirmed plan
- **WHEN** an old task has an unconfirmed AI or rule slot draft
- **THEN** it SHALL open the slot-review portion of the first subpage and SHALL migrate to deterministic or manual source only after explicit replan or edit

### Requirement: Downstream authorization gates remain unchanged
The two-subpage redesign SHALL NOT bypass dry-run, exact task authorization, production confirmation, store identity, approval-manifest, or real-upload gates.

#### Scenario: Copy is confirmed
- **WHEN** all slot copy is confirmed
- **THEN** the workflow SHALL proceed only to dry-run review and SHALL NOT perform a production write
