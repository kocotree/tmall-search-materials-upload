## 1. Baseline and Contracts

- [x] 1.1 Add regression fixtures for the current `Y:`/`Z:` configuration when the managed service session has no corresponding drive mappings.
- [x] 1.2 Capture the current path-check response and folder-picker 503 response so the two failures remain independently testable.
- [x] 1.3 Define typed image-source diagnostic fields, stable reason codes, Chinese recovery copy, and legacy `status` compatibility.
- [x] 1.4 Define the native helper request/result envelope, cancellation semantics, timeout, lock ownership, and safe diagnostic fields.

## 2. NAS Path Access Diagnostics

- [x] 2.1 Classify configured roots as local fixed paths, mapped-drive paths, or UNC paths without enumerating directory contents.
- [x] 2.2 Replace boolean `Path.is_dir()` handling with metadata-only probing and deterministic Windows error mapping.
- [x] 2.3 Detect a missing drive-letter mapping separately from a missing child directory and return `DRIVE_NOT_MAPPED`.
- [x] 2.4 Distinguish unavailable UNC hosts, missing shares, missing directories, access denial, invalid syntax, timeouts, and unknown failures.
- [x] 2.5 Implement a bounded disposable helper for network metadata probes so an offline NAS cannot leave Flask workers blocked.
- [x] 2.6 Apply per-batch deadlines and bounded concurrency for the existing one-to-fifty image-source range.
- [x] 2.7 Resolve a mapped drive's UNC target when Windows exposes one and return it only as an explicit `portable_path_suggestion`.
- [x] 2.8 Prove diagnostics never enumerate files, open images, write markers, mount shares, request credentials, or persist authentication material.

## 3. Windows Native Folder Selection

- [x] 3.1 Replace the Flask-thread `tkinter` call with a dedicated Windows single-threaded-apartment native helper.
- [x] 3.2 Launch the helper in an interactive window context from an explicit user action and avoid `CREATE_NO_WINDOW` for that owned child.
- [x] 3.3 Pass only a validated optional initial directory and return results through a private bounded request/result channel.
- [x] 3.4 Validate that a selected result is absolute, exists, and is a directory before returning it to the frontend.
- [x] 3.5 Preserve the existing input value on cancellation, unsupported platforms, unavailable GUI sessions, helper startup/protocol failure, and invalid results.
- [x] 3.6 Enforce one active picker per service and return `FOLDER_PICKER_BUSY` for concurrent requests.
- [x] 3.7 Enforce a picker deadline, terminate only the helper owned by that request, and return `FOLDER_PICKER_TIMEOUT`.
- [x] 3.8 Remove or demote `tkinter` so it is not the primary production picker path.

## 4. Interaction API and Frontend

- [x] 4.1 Extend `/api/runtime/image-sources/check` with structured diagnostics while keeping the legacy availability status.
- [x] 4.2 Extend `/api/runtime/folder-picker` with stable success, cancellation, busy, timeout, GUI-unavailable, unsupported, startup-failed, and invalid-result responses.
- [x] 4.3 Render each image-source diagnostic reason and next action in its own stage-one row.
- [x] 4.4 Replace generic folder-picker failure copy with the API's safe reason-specific message and keep manual input focused and usable.
- [x] 4.5 Explain mapped-drive versus UNC portability and provide an explicit action for accepting a returned UNC suggestion.
- [x] 4.6 Ensure detecting or selecting a path does not automatically save machine-local configuration or submit the stage.
- [x] 4.7 Preserve one-to-fifty add/remove source behavior, draft autosave, explicit machine-local save, and stage submission semantics.

## 5. Skill and Operations Guidance

- [x] 5.1 Update the canonical Skill to prefer UNC for portable NAS roots while accepting accessible mapped drives.
- [x] 5.2 Document that code cannot bypass NAS permissions, create drive mappings, or obtain credentials and that availability is evaluated in the managed service identity.
- [x] 5.3 Update the operations guide, frontend contract, error handling, and data schema with diagnostics, picker reason codes, and recovery steps.
- [x] 5.4 Update `test_plan.md` with local, mapped, UNC, offline NAS, denied-access, cancellation, timeout, and non-interactive picker acceptance cases.
- [x] 5.5 Regenerate and validate the repository Skill discovery entry and Agent metadata if canonical Skill content changes.

## 6. Automated and Live Verification

- [x] 6.1 Add unit tests for path classification, Windows error mapping, drive mapping state, UNC suggestions, safe evidence, and deadlines.
- [x] 6.2 Add helper protocol tests for selection, cancellation, busy, timeout, unsupported platform, unavailable desktop, invalid path, and process ownership.
- [x] 6.3 Add interaction API and frontend tests proving reason-specific messages and preservation of existing input values.
- [x] 6.4 Run the full Python and Node suites, Skill validator, OpenSpec strict validation, `uv lock --check`, and `git diff --check`.
- [ ] 6.5 On the current computer, verify an ordinary local directory is detected and can be selected through the native helper. Superseded by `stabilize-live-ui-persistence-and-local-access` tasks 7.1-7.7 and 8.5, which also require verified desktop identity and window visibility.
- [ ] 6.6 With user-provided NAS access, verify one real UNC image root reports `PATH_AVAILABLE` without enumerating its contents.
- [x] 6.7 Verify the current unavailable `Y:`/`Z:` mappings report `DRIVE_NOT_MAPPED` with UNC/manual-entry recovery instead of the generic message.
- [x] 6.8 Confirm no credentials, mappings, source-file reads, source mutations, indexing, collection, approval, or upload occurred during configuration tests.
