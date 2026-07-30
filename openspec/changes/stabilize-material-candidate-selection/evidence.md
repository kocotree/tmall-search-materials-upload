## Baseline audit

The implementation audit compared candidate preparation, gallery submission,
selected-asset preflight and deterministic slot planning with every scenario in
the bounded material candidate selection spec.

### Conforming behavior retained

- Candidate limits were already applied independently per product.
- Totals at or below the candidate limit already retained every unique path.
- Allocation already guaranteed one candidate per non-empty folder when the
  window could represent every folder, then distributed remaining capacity
  proportionally.
- Candidate display did not automatically create image-selection decisions.
- Folder rejection already removed bound candidates, selections and licenses;
  re-adoption did not restore old selections.
- Submission already required three usable unique images per selected product.
- Slot count, 3–9 image bounds, balanced sizes, source interleaving and capacity
  overflow behavior already matched the deterministic contract.

### Discrepancies found and corrected

- Empty and fully overlapping adopted folders disappeared from allocation audit
  output instead of retaining zero-allocation rows.
- Results did not report incomplete folder coverage when more than 100 non-empty
  folders competed for a 100-candidate product window.
- Sampling had no explicit strategy version or hashed task-bound sampling
  identity, and folder seeds used paths rather than stable folder IDs.
- Per-product summaries omitted non-empty, represented and uncovered folder
  counts and complete-coverage status.
- Folder allocations omitted coverage-base, proportional-remainder and
  zero-allocation-reason fields.
- Selected-asset preflight retained the candidate directory but omitted the
  explicit stable `folder_id` and `folder_path`.
- The gallery did not distinguish discovered paths, prepared candidates, valid
  candidates, the per-product limit and the 30-image page size.
- The data schema said candidates were not randomly sampled, while the code and
  operations guide used deterministic pseudo-random task sampling.

## Verification evidence

- Focused candidate, selected-asset preflight and slot tests: 34 passed.
- Candidate, preflight, slot and interaction tests after UI changes: 129 passed.
- JavaScript syntax check: passed.
- Complete project tests using a repository-local pytest temporary directory:
  670 passed with one existing openpyxl workbook-style warning.
- Canonical Skill and repository discovery entry validation: passed with SHA-256
  `7bfd03e1457f700d99f6a6b57898854198138d5fb2feda5c3e02f5940953603a`.

The Windows pytest temporary-directory cleanup callback can emit an ignored
`PermissionError` after successful completion because the sandbox cannot inspect
the user temp root. It does not change the pytest exit code or test results.
The first complete-suite run in the restricted sandbox also produced two
environment-only failures: Node could not inspect the user directory and a
runtime-isolation assertion saw `AppData` in pytest's system temporary path.
Re-running with the repository-local temporary directory and the required Node
path access passed all 670 tests.
