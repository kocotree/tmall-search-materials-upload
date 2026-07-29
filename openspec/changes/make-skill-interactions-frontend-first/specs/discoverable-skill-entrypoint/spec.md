## ADDED Requirements

### Requirement: Fresh Codex sessions discover the canonical workflow
The project SHALL expose `upload-search-materials` through a Codex-supported discovery location, and the discovered entry SHALL direct the Agent to the canonical Skill instructions before any task action.

#### Scenario: Fresh session opened at repository root
- **WHEN** a fresh Codex session opens the repository and receives a material-upload request
- **THEN** `upload-search-materials` appears in the available Skill catalog and its canonical instructions are loaded

#### Scenario: Canonical Skill is unavailable
- **WHEN** the discovery entry cannot resolve or read the canonical Skill
- **THEN** the Agent reports a stable Skill installation error and does not improvise the workflow from old documents

### Requirement: Trigger metadata encodes frontend-first behavior
The Skill frontmatter and Agent metadata SHALL describe both the supported material workflow and the requirement to open or resume the interaction UI before requesting structured business configuration.

#### Scenario: User starts a new upload task
- **WHEN** the user asks to start, test, resume, audit, or configure the material workflow
- **THEN** the Skill triggers and the Agent treats UI startup as the default first action

#### Scenario: User has not supplied store or source paths
- **WHEN** the Skill triggers without a store name, month, or image root
- **THEN** the missing values are treated as stage-one UI fields rather than Skill startup blockers

### Requirement: Discovery metadata cannot drift from the canonical Skill
The project SHALL validate that the discovery entry, `agents/openai.yaml`, and canonical Skill share the same name, supported trigger scope, and frontend-first startup contract.

#### Scenario: Metadata becomes stale
- **WHEN** validation detects that generated discovery or Agent metadata no longer matches the canonical Skill contract
- **THEN** validation fails with the exact stale artifact and regeneration command

### Requirement: Skill instructions use progressive disclosure
The canonical Skill SHALL keep low-freedom startup, routing, safety, and recovery instructions prominent and concise, and SHALL move detailed schemas and long examples to directly linked references.

#### Scenario: Agent loads the Skill
- **WHEN** the Skill body is loaded after triggering
- **THEN** the Agent encounters the frontend-first startup sequence before stage-specific business details
