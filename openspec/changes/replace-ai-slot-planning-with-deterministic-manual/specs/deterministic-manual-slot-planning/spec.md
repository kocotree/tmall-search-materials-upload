## ADDED Requirements

### Requirement: Asset-selection guidance
The system SHALL calculate slot-planning guidance per product from the current usable preflight-approved selections and the backend missing-slot count, without requiring an AI request.

#### Scenario: Selection can fill part of the missing slots
- **WHEN** a product is missing 6 slots and the user has selected 14 usable unique images
- **THEN** the page SHALL report that 4 complete slots can be generated, show the planned balanced counts `4 + 4 + 3 + 3`, and report that 4 additional images are needed to fill all 6 slots at the minimum of 3 images each

#### Scenario: Fewer than three usable images
- **WHEN** a product has fewer than 3 usable unique selected images
- **THEN** the page SHALL report that no complete slot can be generated and SHALL identify the minimum additional selection needed

#### Scenario: Missing-slot count is zero
- **WHEN** the backend missing-slot count for a product is zero
- **THEN** the automatic planner SHALL create no slot and SHALL keep every selected image in the unused candidate pool

### Requirement: Deterministic slot count and balanced sizes
The system SHALL calculate the automatic slot count as `min(missing_slots, floor(usable_unique_images / 3))`, SHALL place 3–9 images in every complete automatic slot, and SHALL produce the same assignment for the same ordered input and policy revision.

#### Scenario: Nine images and at least three missing slots
- **WHEN** a product has 9 usable unique images and at least 3 missing slots
- **THEN** the planner SHALL create 3 slots containing `3 + 3 + 3` images

#### Scenario: Nine images and two missing slots
- **WHEN** a product has 9 usable unique images and 2 missing slots
- **THEN** the planner SHALL create 2 slots containing `5 + 4` images

#### Scenario: Nine images and one missing slot
- **WHEN** a product has 9 usable unique images and 1 missing slot
- **THEN** the planner SHALL create 1 slot containing 9 images

#### Scenario: More images than the missing slots can accept
- **WHEN** the selected usable image count exceeds `missing_slots × 9`
- **THEN** the planner SHALL assign at most 9 images to each missing slot and SHALL retain every surplus image in the unused candidate pool with an explicit reason

### Requirement: Stable image ordering and diversity
The planner SHALL preserve the user's asset-selection order as the primary priority and SHALL use a deterministic source-folder interleave before balanced partitioning so that repeated execution never randomly reshuffles the draft.

#### Scenario: Multiple source folders
- **WHEN** selected images come from multiple confirmed source folders
- **THEN** the planner SHALL preserve order within each source folder and SHALL interleave folders using a stable key before dividing images among slots

#### Scenario: Repeated planning
- **WHEN** the planner is run twice with identical selection order, source identities, missing-slot count, and policy revision
- **THEN** both runs SHALL produce identical slot IDs, image order, unused-image order, and ratio decisions

### Requirement: Deterministic common-ratio selection
Every automatic slot SHALL use exactly one ratio from `3:4` or `1:1`, and every assigned image MUST have a feasible crop or native output for that ratio.

#### Scenario: Ratio score selects a winner
- **WHEN** both ratios are feasible for all images in a slot
- **THEN** the planner SHALL rank ratios by native-match count, recommended-resolution pass count, average retained image fraction, compression count, and a fixed final tie-break priority, and SHALL save the score components with the decision

#### Scenario: Initial group has no common ratio
- **WHEN** the initial balanced group has no ratio feasible for every image
- **THEN** the planner SHALL deterministically swap or move images from the remaining pool before declaring the group incomplete

#### Scenario: No valid complete grouping exists
- **WHEN** the selected images cannot form a 3–9 image group sharing one allowed ratio
- **THEN** the system SHALL keep the affected images in the candidate pool, SHALL explain the ratio conflict, and SHALL require manual adjustment rather than emitting an invalid slot

### Requirement: Manual editing of the automatic draft
The system SHALL allow the user to add or delete slots, add or remove images, reorder images, and change the slot ratio after automatic planning.

#### Scenario: User edits an automatic slot
- **WHEN** the user changes any automatic assignment
- **THEN** the current draft SHALL become a manual override while preserving the prior deterministic-plan revision in its audit history

#### Scenario: Image uniqueness across slots
- **WHEN** an image is assigned to one slot
- **THEN** the image SHALL disappear from every other candidate picker for the same product and the server SHALL reject any stale request that attempts to assign it twice

#### Scenario: Incomplete manual draft
- **WHEN** a user-created or edited slot contains fewer than 3 images
- **THEN** the system SHALL allow draft saving but SHALL block slot confirmation and image processing

### Requirement: Explicit deterministic replan
The system SHALL offer an explicit replan action that replaces the current unconfirmed draft only after showing its impact and validating the exact current revision.

#### Scenario: Replan after manual edits
- **WHEN** the user requests deterministic replanning after manually editing the draft
- **THEN** the page SHALL warn that current assignments will be replaced and the server SHALL require the current plan revision

#### Scenario: Stale replan request
- **WHEN** another browser tab changes the plan before a replan request is accepted
- **THEN** the server SHALL reject the stale request and SHALL preserve the newer draft

### Requirement: No AI slot-planning path for new tasks
New tasks SHALL NOT create, discover, claim, retry, or adopt an AI request for slot planning.

#### Scenario: Asset-selection submission
- **WHEN** the user confirms at least 3 usable preflight-approved selections for each submitted product
- **THEN** the system SHALL invoke only the deterministic planner and SHALL NOT create an Agent handoff for slot planning

#### Scenario: Automatic planning failure
- **WHEN** deterministic planning cannot create a complete slot
- **THEN** the system SHALL preserve an empty or current manual draft and SHALL direct the user to adjust selections or edit manually, without falling back to AI

#### Scenario: Historical AI draft
- **WHEN** an old task contains an AI or AI-with-rule-fallback slot draft
- **THEN** the system SHALL keep it readable for audit, and any replan or edit SHALL migrate the active draft to deterministic or manual source
