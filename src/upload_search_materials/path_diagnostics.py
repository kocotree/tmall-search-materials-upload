"""Reason-coded, read-only diagnostics for local and NAS image roots."""

from __future__ import annotations

import ctypes
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path, PureWindowsPath
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Iterable, Mapping, TypedDict

from .path_probe import probe_directory_metadata
from .time_utils import iso_timestamp


DRIVE_REMOTE = 4
DEFAULT_PATH_PROBE_TIMEOUT = 2.0
DEFAULT_PATH_BATCH_TIMEOUT = 6.0
MAX_PATH_PROBE_WORKERS = 8

RECOVERY_COPY = {
    "PATH_AVAILABLE": "路径可访问。",
    "INVALID_PATH": "路径格式无效，请填写绝对本机路径、映射盘路径或 UNC 路径。",
    "DRIVE_NOT_MAPPED": (
        "当前窗口无法打开这个盘符。请点击“选择文件夹”重新选择一个当前可访问的目录，"
        "或直接粘贴本机能够打开的完整文件夹路径。"
    ),
    "NETWORK_HOST_UNAVAILABLE": "NAS 主机当前不可达，请检查公司网络或 VPN。",
    "NETWORK_SHARE_NOT_FOUND": "NAS 共享目录不存在或当前账号不可见。",
    "PATH_NOT_FOUND": "路径不存在，请检查目录名称。",
    "ACCESS_DENIED": "当前 Windows/NAS 身份没有该目录的读取权限。",
    "PATH_CHECK_TIMEOUT": "NAS 响应超时，请检查网络后重试。",
    "PATH_CHECK_FAILED": "路径检测失败，请重试或手工核对。",
}


class ImageSourceDiagnostic(TypedDict):
    label: str
    path: str
    status: str
    available: bool
    path_kind: str
    reason_code: str
    message: str
    next_action: str
    checked_at: str
    portable_path_suggestion: str
    windows_error: int | None


def classify_windows_path(path: str) -> str:
    text = str(path).strip()
    if not text or "\x00" in text:
        return "invalid"
    windows = PureWindowsPath(text)
    if text.startswith("\\\\"):
        anchor = windows.anchor.rstrip("\\")
        parts = [part for part in anchor.split("\\") if part]
        return "unc" if len(parts) >= 2 else "invalid"
    if windows.drive and windows.root:
        return "drive"
    native = Path(text).expanduser()
    return "local" if native.is_absolute() else "invalid"


def _drive_root(path: str) -> str:
    drive = PureWindowsPath(path).drive
    return f"{drive}\\"


def windows_drive_type(root: str) -> int:
    if os.name != "nt":
        return 0
    return int(ctypes.windll.kernel32.GetDriveTypeW(str(root)))


def mapped_drive_unc(root: str) -> str:
    if os.name != "nt":
        return ""
    drive = PureWindowsPath(root).drive
    if not drive:
        return ""
    size = ctypes.c_ulong(2048)
    buffer = ctypes.create_unicode_buffer(size.value)
    result = ctypes.windll.mpr.WNetGetConnectionW(
        drive, buffer, ctypes.byref(size)
    )
    return buffer.value if result == 0 else ""


def portable_unc_suggestion(path: str, unc_root: str) -> str:
    if not unc_root:
        return ""
    windows = PureWindowsPath(path)
    relative = str(windows)[len(windows.anchor) :].lstrip("\\")
    return (
        str(PureWindowsPath(unc_root) / relative)
        if relative
        else str(PureWindowsPath(unc_root))
    )


def _safe_probe_document(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {
            "available": False,
            "reason_code": "PATH_CHECK_FAILED",
            "windows_error": None,
        }
    reason = str(value.get("reason_code", "PATH_CHECK_FAILED"))
    if reason not in RECOVERY_COPY:
        reason = "PATH_CHECK_FAILED"
    windows_error = value.get("windows_error")
    return {
        "available": bool(value.get("available"))
        and reason == "PATH_AVAILABLE",
        "reason_code": reason,
        "windows_error": (
            windows_error if isinstance(windows_error, int) else None
        ),
    }


def run_bounded_path_probe(
    path: str,
    *,
    timeout_seconds: float = DEFAULT_PATH_PROBE_TIMEOUT,
    popen: Callable[..., subprocess.Popen] = subprocess.Popen,
) -> dict[str, Any]:
    """Run one remote metadata probe in an owned disposable process."""

    with tempfile.TemporaryDirectory(prefix="tmall-path-probe-") as temporary:
        root = Path(temporary)
        request_path = root / "request.json"
        response_path = root / "response.json"
        request_path.write_text(
            json.dumps({"path": path}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        command = [
            sys.executable,
            "-m",
            "upload_search_materials.path_probe",
            "--request",
            str(request_path),
            "--response",
            str(response_path),
        ]
        creationflags = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            if os.name == "nt"
            else 0
        )
        try:
            process = popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                creationflags=creationflags,
            )
        except OSError:
            return _safe_probe_document({})
        try:
            process.wait(timeout=max(0.05, timeout_seconds))
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
            return {
                "available": False,
                "reason_code": "PATH_CHECK_TIMEOUT",
                "windows_error": None,
            }
        if process.returncode != 0 or not response_path.is_file():
            return _safe_probe_document({})
        try:
            return _safe_probe_document(
                json.loads(response_path.read_text(encoding="utf-8"))
            )
        except (OSError, ValueError, json.JSONDecodeError):
            return _safe_probe_document({})


def _diagnostic(
    source: Mapping[str, str],
    *,
    path_kind: str,
    probe: Mapping[str, Any],
    suggestion: str = "",
) -> ImageSourceDiagnostic:
    reason = str(probe.get("reason_code", "PATH_CHECK_FAILED"))
    if reason not in RECOVERY_COPY:
        reason = "PATH_CHECK_FAILED"
    available = bool(probe.get("available")) and reason == "PATH_AVAILABLE"
    message = RECOVERY_COPY[reason]
    return {
        "source_id": str(source.get("source_id", "")),
        "label": str(source["label"]),
        "path": str(source["path"]),
        "status": "available" if available else "unavailable",
        "available": available,
        "path_kind": path_kind,
        "reason_code": reason,
        "message": message,
        "next_action": message,
        "checked_at": iso_timestamp(),
        "portable_path_suggestion": suggestion,
        "windows_error": (
            probe.get("windows_error")
            if isinstance(probe.get("windows_error"), int)
            else None
        ),
    }


def diagnose_image_source(
    source: Mapping[str, str],
    *,
    remote_probe: Callable[..., dict[str, Any]] = run_bounded_path_probe,
    local_probe: Callable[[str], dict[str, Any]] = probe_directory_metadata,
    drive_exists: Callable[[str], bool] = os.path.isdir,
    drive_type: Callable[[str], int] = windows_drive_type,
    unc_resolver: Callable[[str], str] = mapped_drive_unc,
    timeout_seconds: float = DEFAULT_PATH_PROBE_TIMEOUT,
) -> ImageSourceDiagnostic:
    path = str(source["path"])
    kind = classify_windows_path(path)
    if kind == "invalid":
        return _diagnostic(
            source,
            path_kind="invalid",
            probe={"available": False, "reason_code": "INVALID_PATH"},
        )
    if kind == "local":
        return _diagnostic(
            source,
            path_kind="local",
            probe=local_probe(path),
        )
    if kind == "unc":
        return _diagnostic(
            source,
            path_kind="unc",
            probe=remote_probe(path, timeout_seconds=timeout_seconds),
        )

    root = _drive_root(path)
    if not drive_exists(root):
        fallback = str(source.get("canonical_unc", "")).strip()
        if fallback.startswith("\\\\"):
            return _diagnostic(
                source,
                path_kind="unc_fallback",
                probe=remote_probe(
                    fallback, timeout_seconds=timeout_seconds
                ),
                suggestion=fallback,
            )
        return _diagnostic(
            source,
            path_kind="mapped_drive",
            probe={
                "available": False,
                "reason_code": "DRIVE_NOT_MAPPED",
            },
        )
    remote = drive_type(root) == DRIVE_REMOTE
    unc_root = unc_resolver(root) if remote else ""
    probe = (
        remote_probe(path, timeout_seconds=timeout_seconds)
        if remote
        else local_probe(path)
    )
    return _diagnostic(
        source,
        path_kind="mapped_drive" if remote else "local",
        probe=probe,
        suggestion=portable_unc_suggestion(path, unc_root),
    )


def diagnose_image_sources(
    sources: Iterable[Mapping[str, str]],
    *,
    per_path_timeout: float = DEFAULT_PATH_PROBE_TIMEOUT,
    batch_timeout: float = DEFAULT_PATH_BATCH_TIMEOUT,
) -> tuple[ImageSourceDiagnostic, ...]:
    values = list(sources)
    if not values:
        return ()
    deadline = time.monotonic() + max(0.1, batch_timeout)

    def diagnose(source: Mapping[str, str]) -> ImageSourceDiagnostic:
        remaining = max(0.05, deadline - time.monotonic())
        return diagnose_image_source(
            source,
            timeout_seconds=min(per_path_timeout, remaining),
        )

    with ThreadPoolExecutor(
        max_workers=min(MAX_PATH_PROBE_WORKERS, len(values))
    ) as executor:
        return tuple(executor.map(diagnose, values))
