## 1. Baseline and contract audit

- [ ] 1.1 Compare the current candidate allocation, gallery submission and deterministic slot behavior against every scenario in the bounded material candidate selection spec
- [ ] 1.2 Add focused baseline tests for the behavior that already conforms so later fixes cannot regress it
- [ ] 1.3 Record each implementation or documentation discrepancy, including the current stable-sampling terminology conflict

## 2. Per-product candidate allocation

- [ ] 2.1 Refactor task-local path discovery to assign every unique image path to one stable adopted folder before allocation
- [ ] 2.2 Preserve an audit allocation row for every adopted folder, including empty and fully overlapping folders with zero candidates
- [ ] 2.3 Enforce the independent 100-candidate limit for every product while including all unique paths when the total is at most 100
- [ ] 2.4 Implement and verify one-per-non-empty-folder coverage followed by proportional remaining-capacity allocation when all folders can be represented
- [ ] 2.5 Implement deterministic allocation and `FOLDER_COVERAGE_LIMIT_EXCEEDED` reporting when more than 100 non-empty folders compete for the candidate window
- [ ] 2.6 Ensure allocation rounding is deterministic, never exceeds per-folder capacity and always sums to the intended candidate window

## 3. Stable sampling identity and audit data

- [ ] 3.1 Define a versioned candidate sampling strategy identity and derive per-folder sampling from task, product and stable folder identities
- [ ] 3.2 Normalize and sort path inputs before deterministic pseudo-random selection so filesystem enumeration order cannot change a resumed task
- [ ] 3.3 Extend the per-product scan summary with adopted, non-empty, represented and uncovered folder counts plus complete-coverage status
- [ ] 3.4 Extend each folder allocation with discovered count, base allocation, proportional allocation, sampled count and zero-allocation reason
- [ ] 3.5 Preserve backward-compatible reading of historical gallery results that lack the new strategy and audit fields without rebuilding their candidate sample

## 4. Material-selection interaction and validation

- [ ] 4.1 Update the gallery summary to distinguish discovered paths, prepared candidates, valid candidates, the 100-per-product limit and the 30-image page size
- [ ] 4.2 Display a clear incomplete-coverage warning and zero-allocation folder details when the candidate limit cannot represent all non-empty folders
- [ ] 4.3 Verify that first display never auto-selects candidate images and that folder adoption alone does not create image or authorization decisions
- [ ] 4.4 Verify rejecting a folder immediately removes its candidates and clears bound image and authorization decisions, while re-adoption does not restore old selections
- [ ] 4.5 Enforce at least three readable, policy-compatible, SHA-256-unique selected images per submitted product without adding a lower selection maximum

## 5. Preflight and deterministic slot integration

- [ ] 5.1 Verify selected-asset preflight preserves product, folder, selection order and source SHA-256 identities while removing invalid and duplicate images
- [ ] 5.2 Verify slot count is `min(missing_slots, floor(valid_unique_selected_images / 3))` and each created slot contains 3–9 images
- [ ] 5.3 Verify total slot consumption is `min(valid_unique_selected_images, slot_count * 9)` with balanced slot sizes
- [ ] 5.4 Verify source-folder interleaving remains deterministic and preserves the order within each source queue
- [ ] 5.5 Keep capacity overflow and ratio-incompatible images in the unused-candidate pool with accurate reason codes

## 6. Documentation consistency

- [ ] 6.1 Update the canonical `SKILL.md` and regenerate or synchronize its discovery entry without changing unrelated workflow boundaries
- [ ] 6.2 Update `references/operations-guide.md` to describe coverage-first proportional allocation and task-stable pseudo-random sampling
- [ ] 6.3 Update `references/data-schema.md` to remove the contradictory no-random-sampling statement and document all new strategy, coverage and allocation fields
- [ ] 6.4 Check that candidate, selected and final-used image limits are described consistently across all referenced instructions

## 7. Regression and acceptance checks

- [ ] 7.1 Add allocation tests for 61 non-empty folders and verify 61 base candidates plus 39 proportionally allocated remainder positions
- [ ] 7.2 Add tests for more than 100 non-empty folders, empty folders, nested folders, overlapping folders and deterministic zero-allocation audit rows
- [ ] 7.3 Add same-task repeatability, filesystem-order independence, different-task sampling and multi-product independent-limit tests
- [ ] 7.4 Add interaction tests for 30-image pagination, incomplete-coverage messaging, manual image adoption and folder decision synchronization
- [ ] 7.5 Add submission and slot tests for minimum three images, duplicate rejection, `3+3+3`, `5+4`, capacity overflow and unused candidates
- [ ] 7.6 Run the focused candidate-selection, interaction, preflight and slot-planning test suites and record any unrelated failures separately
- [ ] 7.7 Run the complete project test suite and verify the canonical and discovery Skill hashes match
