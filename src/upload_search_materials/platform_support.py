"""Cross-platform helpers for validating locally bound material roots."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import sys
from typing import Literal


RuntimePlatform = Literal["windows", "macos", "linux"]


@dataclass(frozen=True)
class AssetRootCheck:
    platform: RuntimePlatform
    path: Path


class AssetSourceUnavailable(RuntimeError):
    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code


def detect_runtime_platform(sys_platform: str | None = None) -> RuntimePlatform:
    value = (sys_platform or sys.platform).casefold()
    if value.startswith("win"):
        return "windows"
    if value == "darwin":
        return "macos"
    return "linux"


def _looks_like_unc_path(value: str) -> bool:
    return value.startswith("\\\\") or value.startswith("//")


def _looks_like_windows_drive(value: str) -> bool:
    return re.match(r"^[A-Za-z]:[\\/]", value) is not None


def resolve_asset_root(
    value: str | Path,
    *,
    platform: RuntimePlatform | None = None,
) -> AssetRootCheck:
    """Resolve one already-mounted root without mounting or storing credentials."""

    raw = str(value).strip()
    current_platform = platform or detect_runtime_platform()
    if not raw:
        raise AssetSourceUnavailable("ASSET_ROOT_EMPTY", "素材根目录不能为空")
    if raw.casefold().startswith("smb://"):
        raise AssetSourceUnavailable(
            "NAS_URL_UNSUPPORTED",
            "不直接读取 smb:// URL；请先通过系统界面连接 NAS",
        )
    if current_platform != "windows" and (
        _looks_like_unc_path(raw) or _looks_like_windows_drive(raw)
    ):
        raise AssetSourceUnavailable(
            "NAS_PATH_REQUIRES_MOUNT",
            "当前系统不能直接使用 Windows UNC/盘符路径；macOS 请使用 /Volumes 挂载点",
        )
    if current_platform == "windows" and raw.replace("\\", "/").startswith(
        "/Volumes/"
    ):
        raise AssetSourceUnavailable(
            "ASSET_PATH_PLATFORM_MISMATCH",
            "Windows 不能使用 macOS /Volumes 路径；请改用 UNC 路径或映射盘",
        )

    root = Path(raw).expanduser().resolve()
    try:
        metadata = root.stat()
    except FileNotFoundError as error:
        raise AssetSourceUnavailable(
            "ASSET_ROOT_NOT_FOUND", f"素材根目录不存在或 NAS 尚未挂载：{root}"
        ) from error
    except PermissionError as error:
        raise AssetSourceUnavailable(
            "ASSET_ROOT_PERMISSION_DENIED", f"没有权限读取素材根目录：{root}"
        ) from error
    except OSError as error:
        raise AssetSourceUnavailable(
            "ASSET_ROOT_IO_ERROR", f"素材根目录不可用，可能是 NAS 断线：{root}"
        ) from error
    if not root.is_dir() or not metadata:
        raise AssetSourceUnavailable(
            "ASSET_ROOT_NOT_DIRECTORY", f"素材根路径不是目录：{root}"
        )
    return AssetRootCheck(current_platform, root)


def local_file_uri(value: str | Path) -> str:
    return Path(value).expanduser().resolve().as_uri()
