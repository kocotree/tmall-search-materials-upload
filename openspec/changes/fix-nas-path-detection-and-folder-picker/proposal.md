## Why

The stage-one configuration page currently reports every unreachable image source as “当前不可访问” and launches its folder dialog from a background Flask request through `tkinter`. On the current computer, the service session cannot see the configured `Y:` and `Z:` NAS mappings, while the folder-picker failure is collapsed into a generic “选择窗口不可用”, so the user cannot distinguish a missing drive mapping from a bad path, denied access, unavailable NAS, or an unusable GUI channel.

## What Changes

- Replace boolean image-source reachability checks with reason-coded, read-only diagnostics for local paths, mapped drives, and UNC paths.
- Show whether a drive is unmapped, the network host is unavailable, the directory is missing, access is denied, or the path is usable, together with an actionable recovery message.
- Prefer UNC paths for portable NAS configuration while continuing to accept mapped drive paths when the service process can access them.
- Replace the Flask-thread `tkinter` dialog with a bounded Windows native folder-selection helper that runs in an interactive process and returns a selected absolute path or a stable failure reason.
- Preserve manual path entry as the required fallback for UNC paths, remote environments, cancelled dialogs, and unavailable desktop sessions.
- Keep path detection read-only: it must not enumerate image files, mount drives, request or store NAS credentials, or mutate source directories.
- Improve the configuration UI so each source displays the actual diagnostic reason instead of the current generic messages.

## Capabilities

### New Capabilities

- `nas-path-access-diagnostics`: Diagnose local, mapped-drive, and UNC image-source paths with stable status/reason codes and safe recovery guidance.
- `windows-native-folder-selection`: Open a user-triggered Windows folder selector through an interactive helper, with cancellation, timeout, concurrency, and manual-entry fallback behavior.

### Modified Capabilities

None.

## Impact

- Affects `runtime_config.py`, the interaction API, the folder-picker helper, managed UI process behavior, and the stage-one image-source frontend.
- Adds structured diagnostic fields to the image-source check response and stable folder-picker failure codes while retaining existing paths and manual input compatibility.
- Requires Windows unit/integration tests for missing drive mappings, UNC reachability, denied/missing paths, helper cancellation/failure/timeout, and frontend messages.
- Does not alter material indexing, collection, upload, approval, authentication, or NAS permissions.
