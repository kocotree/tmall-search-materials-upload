## Context

Stage one stores one to fifty image-source roots and exposes “检测路径” and “选择文件夹”. Reachability currently calls `Path.is_dir()` in the managed Flask process, which reduces every failure to `unavailable`. The configured roots on the current computer use `Y:` and `Z:`, but those drive mappings do not exist in the service logon session, so the boolean result is correctly false but operationally unhelpful.

The directory picker currently creates `tkinter.Tk()` inside a Flask request thread. The managed service is a background process created with `CREATE_NO_WINDOW`; a web request is therefore not a reliable owner for a native interactive dialog. The frontend then replaces every non-success response with “选择窗口不可用”, hiding the actual cause.

The solution must remain frontend-first, work on Windows, support portable UNC paths, avoid collecting credentials, and never enumerate or mutate source material during configuration.

## Goals / Non-Goals

**Goals:**

- Distinguish usable paths from missing mappings, unreachable NAS hosts/shares, missing directories, denied access, timeouts, and invalid syntax.
- Keep network checks bounded so an offline NAS cannot block the Flask worker indefinitely.
- Provide a reliable user-triggered Windows folder dialog with stable cancellation and failure behavior.
- Preserve manual entry and make UNC paths the portable recommendation.
- Expose safe reason codes and recovery messages in the stage-one UI and API.

**Non-Goals:**

- Automatically mount drive letters, authenticate to NAS, prompt for or store credentials.
- Rewrite a mapped-drive path into UNC unless Windows can resolve the mapping and the user explicitly accepts the suggestion.
- Enumerate image files, build an asset index, or test write access during configuration.
- Make folder selection mandatory; manual input remains authoritative.
- Support native folder dialogs on headless or non-Windows systems beyond a reason-coded fallback.

## Decisions

### 1. Return a structured diagnostic instead of a boolean

Each source check returns the normalized path, path kind (`local`, `mapped_drive`, or `unc`), availability, stable reason code, safe detail, checked time, and next action. Initial reason codes include:

- `PATH_AVAILABLE`
- `INVALID_PATH`
- `DRIVE_NOT_MAPPED`
- `NETWORK_HOST_UNAVAILABLE`
- `NETWORK_SHARE_NOT_FOUND`
- `PATH_NOT_FOUND`
- `ACCESS_DENIED`
- `PATH_CHECK_TIMEOUT`
- `PATH_CHECK_FAILED`

Windows error codes will be mapped deterministically. The API will not include credentials, directory contents, exception traces, or unrelated environment variables.

`Path.is_dir()` is not sufficient because it suppresses useful `OSError` detail. The probe will use metadata-only filesystem operations and explicit Windows error mapping.

### 2. Bound network checks in a disposable helper process

UNC and remote mapped-drive metadata calls can block below Python's timeout controls. Network probes will therefore run in a short-lived helper process with a per-batch timeout. If it does not respond, only that owned helper is terminated and the source returns `PATH_CHECK_TIMEOUT`.

Local fixed-drive checks may run directly because they do not have the same network blocking risk. A single request may check up to the existing fifty sources, with bounded concurrency and an overall deadline.

Using a thread pool alone was rejected because a blocked Windows network filesystem call cannot be safely cancelled and would leave Flask worker threads behind.

### 3. Prefer UNC without silently changing user configuration

UNC paths are recommended because `\\server\share\path` is independent of per-session drive-letter mappings. Mapped paths remain valid when the managed service can access them.

If Windows reports that a mapped drive has a UNC target, the API may return that target as `portable_path_suggestion`; the UI must require an explicit user action before replacing the configured value. Missing mappings are reported, not recreated.

### 4. Move the native dialog into a dedicated interactive helper

The Flask request will no longer instantiate `tkinter`. On Windows, it will launch a project-owned helper in single-threaded apartment mode that invokes the native directory dialog. The helper:

- is launched only from an explicit user click;
- is not created with `CREATE_NO_WINDOW`;
- receives an optional validated initial directory;
- writes a small result envelope through a private temporary file or pipe;
- returns `selected`, `cancelled`, or a stable failure reason;
- is subject to one-picker-at-a-time locking and a bounded timeout;
- is terminated only when it is the helper owned by that request;
- validates that a returned value is an absolute existing directory before it reaches the frontend.

A PowerShell `-STA`/Windows Forms or COM implementation is preferred because Windows ships the required runtime. A Python `tkinter` fallback may be retained only if it passes an explicit interactive-desktop capability check; it must not be the primary path.

Browser `showDirectoryPicker()` and `<input webkitdirectory>` were rejected as the primary solution because browsers intentionally do not expose an authoritative absolute filesystem path to the server.

### 5. Preserve manual entry and display the real outcome

The stage-one row will continue to allow typing or pasting local, mapped, and UNC paths. Folder-picker failure never clears the existing value. The UI displays the API reason and recovery action, for example:

- “Z: 未在当前服务会话映射；请连接网络盘或粘贴 UNC 路径”
- “NAS 主机当前不可达；请检查公司网络/VPN”
- “没有读取权限；请使用有权限的 Windows/NAS 账号”
- “当前运行环境不能显示原生窗口；请手工粘贴路径”

The generic “当前不可访问” and “选择窗口不可用” remain only as last-resort copy for unknown legacy responses.

### 6. Keep security and task isolation boundaries unchanged

Path diagnostics are read-only metadata checks. They do not list files, open images, write marker files, connect network shares, or access credentials. Diagnostics saved into a task contain only the configured path and safe status fields.

Machine-local image-source configuration remains outside portable task inputs until the user saves or submits it through the existing frontend flow.

## Risks / Trade-offs

- [The service logon session still lacks the user's mapped drive] → Report `DRIVE_NOT_MAPPED`, recommend UNC, and never claim the NAS is accessible.
- [An offline UNC host blocks Windows filesystem calls] → Isolate network checks in a helper process and enforce deadlines.
- [The background service cannot reach an interactive desktop] → Return `FOLDER_PICKER_GUI_UNAVAILABLE` and preserve manual entry.
- [PowerShell execution policy or Windows Forms is unavailable] → Use a project-owned invocation mode, return a stable helper error, and do not fall back to an unbounded Flask-thread dialog.
- [A selected mapped drive works on one computer but not another] → Show the mapped/UNC distinction and offer an explicit portable-path suggestion where resolvable.
- [Reason-code mapping differs across Windows versions] → Preserve the stable top-level code and retain the numeric Windows error in a safe diagnostic field for tests/support.

## Migration Plan

1. Introduce the structured diagnostic model and keep the legacy `status` field for frontend compatibility.
2. Add the bounded path-probe helper and reason mapping.
3. Add the native folder-selection helper and switch the API to it.
4. Update stage-one rendering and documentation, then add Windows integration tests.
5. Validate with one local path, one accessible UNC path, one unmapped drive, one unavailable NAS path, picker selection, picker cancellation, and a non-interactive failure.

Rollback can restore the old API handlers while retaining manual input and saved paths. No task or source-material migration is required.

## Open Questions

- What canonical UNC roots correspond to the current `Y:` and `Z:` mappings on company computers?
- Should an explicitly accepted UNC suggestion update only the current task draft or also the machine-local default configuration?
