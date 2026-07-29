"""Bounded Windows-native folder selection for the localhost UI."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
from typing import Any, Callable

from ..path_diagnostics import diagnose_image_source


DEFAULT_PICKER_TIMEOUT = 120.0
_PICKER_LOCK = threading.Lock()

PICKER_MESSAGES = {
    "FOLDER_PICKER_BUSY": "已有一个目录选择窗口，请先完成或取消它。",
    "FOLDER_PICKER_TIMEOUT": "目录选择窗口等待超时，请重试或手工粘贴路径。",
    "FOLDER_PICKER_GUI_UNAVAILABLE": (
        "当前运行环境不能显示原生窗口，请手工粘贴本机或 UNC 路径。"
    ),
    "FOLDER_PICKER_UNSUPPORTED": (
        "当前系统不支持 Windows 原生目录窗口，请手工粘贴路径。"
    ),
    "FOLDER_PICKER_START_FAILED": (
        "无法启动 Windows 目录选择助手，请手工粘贴路径。"
    ),
    "FOLDER_PICKER_PROTOCOL_ERROR": (
        "目录选择助手返回无效响应，请重试或手工粘贴路径。"
    ),
    "FOLDER_PICKER_INVALID_RESULT": (
        "选择结果不是可访问的绝对目录，请重新选择或手工填写。"
    ),
}


class FolderPickerError(RuntimeError):
    def __init__(
        self,
        reason_code: str,
        *,
        detail: str = "",
    ) -> None:
        self.reason_code = reason_code
        self.message = PICKER_MESSAGES.get(
            reason_code, PICKER_MESSAGES["FOLDER_PICKER_PROTOCOL_ERROR"]
        )
        self.detail = detail
        super().__init__(f"{reason_code}: {self.message}")


def _helper_script() -> Path:
    return Path(__file__).with_name("native_folder_picker.ps1")


def _validated_initial_path(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    diagnostic = diagnose_image_source(
        {"label": "initial", "path": text},
        timeout_seconds=1.0,
    )
    return text if diagnostic["available"] else ""


def _read_result(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise FolderPickerError(
            "FOLDER_PICKER_PROTOCOL_ERROR"
        ) from error
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise FolderPickerError("FOLDER_PICKER_PROTOCOL_ERROR")
    return value


def choose_directory(
    initial_path: str | None = None,
    *,
    timeout_seconds: float = DEFAULT_PICKER_TIMEOUT,
    platform: str | None = None,
    popen: Callable[..., subprocess.Popen] = subprocess.Popen,
    powershell: str | None = None,
) -> str | None:
    """Open one owned Windows native dialog and return an absolute directory."""

    selected_platform = platform or os.name
    if selected_platform != "nt":
        raise FolderPickerError("FOLDER_PICKER_UNSUPPORTED")
    if not _PICKER_LOCK.acquire(blocking=False):
        raise FolderPickerError("FOLDER_PICKER_BUSY")
    process: subprocess.Popen | None = None
    try:
        executable = powershell or shutil.which("powershell.exe")
        script = _helper_script()
        if not executable or not script.is_file():
            raise FolderPickerError("FOLDER_PICKER_START_FAILED")
        with tempfile.TemporaryDirectory(
            prefix="tmall-folder-picker-"
        ) as temporary:
            root = Path(temporary)
            request_path = root / "request.json"
            result_path = root / "result.json"
            request_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "initial_path": _validated_initial_path(
                            initial_path
                        ),
                        "title": "选择图片源根目录",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            command = [
                executable,
                "-NoLogo",
                "-NoProfile",
                "-STA",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-RequestPath",
                str(request_path),
                "-ResultPath",
                str(result_path),
            ]
            try:
                process = popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                    creationflags=0,
                )
            except OSError as error:
                raise FolderPickerError(
                    "FOLDER_PICKER_START_FAILED",
                    detail=str(getattr(error, "winerror", "") or ""),
                ) from error
            try:
                process.wait(timeout=max(1.0, timeout_seconds))
            except subprocess.TimeoutExpired as error:
                process.kill()
                process.wait(timeout=2)
                raise FolderPickerError(
                    "FOLDER_PICKER_TIMEOUT"
                ) from error
            if process.returncode != 0 or not result_path.is_file():
                raise FolderPickerError("FOLDER_PICKER_START_FAILED")
            result = _read_result(result_path)
            status = str(result.get("status", ""))
            if status == "cancelled":
                return None
            if status == "error":
                reason = str(
                    result.get(
                        "reason_code",
                        "FOLDER_PICKER_GUI_UNAVAILABLE",
                    )
                )
                if reason not in PICKER_MESSAGES:
                    reason = "FOLDER_PICKER_GUI_UNAVAILABLE"
                raise FolderPickerError(reason)
            if status != "selected":
                raise FolderPickerError(
                    "FOLDER_PICKER_PROTOCOL_ERROR"
                )
            selected = str(result.get("path", "")).strip()
            diagnostic = diagnose_image_source(
                {"label": "selected", "path": selected},
                timeout_seconds=1.0,
            )
            if not selected or not diagnostic["available"]:
                raise FolderPickerError(
                    "FOLDER_PICKER_INVALID_RESULT"
                )
            return selected
    finally:
        _PICKER_LOCK.release()
