## Purpose

为大量素材文件夹建立可解释、可重复且有明确上限的逐商品候选选择契约，并确保人工选图、图片预检和最终坑位消费使用同一组可审计素材身份。

## ADDED Requirements

### Requirement: Only adopted folders contribute image candidates
The system SHALL enumerate image files only from folders currently adopted for the exact product, and SHALL NOT enumerate or retain candidates from a folder rejected for that product.

#### Scenario: Rejecting an adopted folder
- **WHEN** the user changes an adopted folder to rejected
- **THEN** the system SHALL immediately remove that folder's candidates and clear their image-selection and authorization decisions for the bound product

#### Scenario: Re-adopting a folder
- **WHEN** the user changes a rejected folder back to adopted
- **THEN** the system SHALL restore eligible candidates from the prepared folder result without restoring prior image-selection or authorization decisions

### Requirement: Candidate limits are applied independently per product
The system SHALL prepare at most 100 image candidates for each product, independently of candidate counts for other products in the same task.

#### Scenario: Product has no more than one hundred unique image paths
- **WHEN** the adopted folders for one product contain between 1 and 100 unique image paths
- **THEN** the system SHALL include every unique image path in that product's candidate pool before file validation removes invalid candidates

#### Scenario: Product has more than one hundred unique image paths
- **WHEN** the adopted folders for one product contain more than 100 unique image paths
- **THEN** the system SHALL prepare exactly 100 paths for inspection unless a path becomes unavailable during preparation

#### Scenario: Multiple products are selected
- **WHEN** two or more products each have adopted folders
- **THEN** the system SHALL calculate the 100-candidate limit separately for every product

### Requirement: Sampling covers folders before proportional remainder allocation
When a product has more image paths than its candidate limit, the system SHALL first allocate one candidate to every non-empty adopted folder when the limit can represent all such folders, and SHALL distribute remaining capacity proportionally by each folder's remaining unique image count.

#### Scenario: Sixty-one non-empty folders exceed the candidate limit in aggregate
- **WHEN** one product has 61 non-empty adopted folders containing more than 100 unique image paths in aggregate
- **THEN** the system SHALL allocate at least one candidate to each folder and proportionally distribute the remaining 39 candidate positions

#### Scenario: More folders than the candidate limit
- **WHEN** one product has more than 100 non-empty adopted folders and more than 100 unique image paths
- **THEN** the system SHALL keep the candidate pool at 100, deterministically select which folders receive capacity, and report that complete folder coverage was not possible

#### Scenario: Empty or fully overlapping folder
- **WHEN** an adopted folder contributes no unique image path because it is empty or all paths were already attributed to another adopted folder
- **THEN** the system SHALL allocate zero candidates to that folder and preserve a zero-allocation audit record

### Requirement: Task sampling is stable and explicitly identified
The system MUST derive sampling from the current task identity, product identity and folder identity so unchanged inputs produce the same candidate identities and allocation counts when the same task is resumed.

#### Scenario: Same task is resumed
- **WHEN** candidate preparation is repeated for the same task with unchanged adopted folders and unchanged source paths
- **THEN** the system SHALL produce the same allocation and candidate identities regardless of filesystem enumeration order

#### Scenario: A new task is created
- **WHEN** the same product and folders are prepared under a different task identity
- **THEN** the system MAY produce a different candidate sample while continuing to satisfy the same limits and folder-allocation rules

### Requirement: Candidate preparation is auditable
The system SHALL record the per-product candidate strategy, limit, task-bound sampling identity, total discovered unique paths, inspected count, failures and an allocation row for every adopted folder.

#### Scenario: Candidate preparation completes
- **WHEN** a product candidate pool is prepared
- **THEN** each folder allocation SHALL identify the folder and report discovered unique paths, sampled paths and any zero-allocation or incomplete-coverage reason

#### Scenario: User reviews a large folder set
- **WHEN** the user opens a result prepared from multiple adopted folders
- **THEN** the page SHALL distinguish discovered paths, prepared candidates, valid candidates and the 30-image display page size

### Requirement: Image adoption remains a manual decision
The system SHALL NOT automatically adopt an image merely because its folder is adopted or because the image appears in the candidate pool.

#### Scenario: Candidate pool is first displayed
- **WHEN** candidate preparation finishes
- **THEN** every image SHALL remain unselected until the user explicitly adopts it

#### Scenario: User submits selected images
- **WHEN** the user submits the material-selection stage for a product
- **THEN** the system SHALL require at least three readable, policy-compatible and SHA-256-unique adopted images for that product

#### Scenario: User selects many candidates
- **WHEN** the user adopts more images than the eventual slot capacity
- **THEN** the system SHALL retain the valid decisions without imposing an additional selection maximum below the product's candidate-pool limit

### Requirement: Final slot consumption is bounded and deterministic
For each product, the system SHALL create no more than `min(missing_slots, floor(valid_unique_selected_images / 3))` complete slots, SHALL assign 3–9 images to each slot, and SHALL use no more than `min(valid_unique_selected_images, slot_count * 9)` images.

#### Scenario: Nine images and three missing slots
- **WHEN** a product has nine valid unique adopted images and three missing slots
- **THEN** the initial plan SHALL contain three slots of three images

#### Scenario: Nine images and two missing slots
- **WHEN** a product has nine valid unique adopted images and two missing slots
- **THEN** the initial plan SHALL contain two balanced slots containing five and four images

#### Scenario: Selected images exceed slot capacity
- **WHEN** the number of valid unique adopted images exceeds nine times the created slot count
- **THEN** the system SHALL preserve excess images in an unused-candidate pool with an explainable capacity reason

#### Scenario: Multiple source folders contribute selected images
- **WHEN** selected images originate from multiple adopted folders
- **THEN** the system SHALL preserve order within each source and deterministically interleave sources before balanced slot partitioning
