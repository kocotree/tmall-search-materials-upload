"""Windows SMB source catalog, access checks, and safe folder selection."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import time
from typing import Callable, Iterable

import yaml

from .platform_support import (
    AssetSourceUnavailable,
    RuntimePlatform,
    detect_runtime_platform,
    resolve_asset_root,
)


@dataclass(frozen=True)
class NasSource:
    source_id: str
    label: str
    host: str
    share: str
    windows_path: str
    subpaths: tuple[str, ...]
    license_status: str = "unknown"

    def root_for(self, platform: RuntimePlatform) -> str:
        if platform == "windows":
            return self.windows_path
        raise AssetSourceUnavailable(
            "NAS_PLATFORM_UNSUPPORTED", f"NAS 辅助连接暂不支持当前系统：{platform}"
        )


@dataclass(frozen=True)
class NasSourceStatus:
    source_id: str
    state: str
    reason_code: str
    mount_path: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {
            "source_id": self.source_id,
            "state": self.state,
            "reason_code": self.reason_code,
            "mount_path": self.mount_path,
            "message": self.message,
        }


@dataclass(frozen=True)
class SelectedNasFolder:
    source_id: str
    label: str
    relative_path: str
    root: Path
    license_status: str


def _normalized_subpath(value: str, *, allow_empty: bool = False) -> str:
    normalized = PurePosixPath(value.replace("\\", "/"))
    if normalized.is_absolute() or ".." in normalized.parts:
        raise AssetSourceUnavailable(
            "NAS_SUBPATH_INVALID", f"NAS 子目录必须是共享根下的安全相对路径：{value}"
        )
    rendered = normalized.as_posix()
    if rendered == ".":
        if allow_empty:
            return ""
        raise AssetSourceUnavailable("NAS_SUBPATH_INVALID", "NAS 子目录不能为空")
    return rendered


def load_nas_sources(path: Path) -> dict[str, NasSource]:
    config_path = Path(path)
    try:
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise AssetSourceUnavailable(
            "NAS_CONFIG_NOT_FOUND", f"无法读取 NAS 配置：{config_path}"
        ) from error
    except yaml.YAMLError as error:
        raise AssetSourceUnavailable(
            "NAS_CONFIG_INVALID", f"NAS 配置 YAML 无效：{config_path}"
        ) from error
    rows = loaded.get("nas_sources") if isinstance(loaded, dict) else None
    if not isinstance(rows, list) or not rows:
        raise AssetSourceUnavailable(
            "NAS_CONFIG_INVALID", "NAS 配置必须包含非空 nas_sources 列表"
        )
    sources: dict[str, NasSource] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise AssetSourceUnavailable("NAS_CONFIG_INVALID", "NAS 来源必须是对象")
        forbidden = {str(key).casefold() for key in row} & {
            "password", "passwd", "token", "secret",
        }
        if forbidden:
            raise AssetSourceUnavailable(
                "NAS_CONFIG_CONTAINS_SECRET", "NAS 配置不得保存密码、Token 或 Secret"
            )
        source_id = str(row.get("id", "")).strip().casefold()
        host = str(row.get("host", "")).strip()
        share = str(row.get("share", "")).strip().strip("/\\")
        if (
            not source_id
            or not host
            or not share
            or "://" in host
            or "/" in host
            or "\\" in host
            or "/" in share
            or "\\" in share
        ):
            raise AssetSourceUnavailable(
                "NAS_CONFIG_INVALID", "每个 NAS 来源必须提供有效的 id、host 和 share"
            )
        if source_id in sources:
            raise AssetSourceUnavailable(
                "NAS_CONFIG_DUPLICATE_ID", f"NAS 来源 ID 重复：{source_id}"
            )
        raw_subpaths = row.get("subpaths", [])
        if not isinstance(raw_subpaths, list):
            raise AssetSourceUnavailable(
                "NAS_CONFIG_INVALID", f"NAS 来源 {source_id} 的 subpaths 必须是列表"
            )
        subpaths = tuple(_normalized_subpath(str(value)) for value in raw_subpaths)
        license_status = str(row.get("license_status", "unknown")).strip()
        if license_status not in {"confirmed", "unknown"}:
            raise AssetSourceUnavailable(
                "NAS_CONFIG_INVALID", f"NAS 来源 {source_id} 的 license_status 无效"
            )
        sources[source_id] = NasSource(
            source_id=source_id,
            label=str(row.get("label", source_id)).strip() or source_id,
            host=host,
            share=share,
            windows_path=str(row.get("windows_path", rf"\\{host}\{share}")).strip(),
            subpaths=subpaths,
            license_status=license_status,
        )
    return sources


def check_nas_source(
    source: NasSource,
    *,
    platform: RuntimePlatform | None = None,
    path_resolver: Callable[..., object] = resolve_asset_root,
) -> NasSourceStatus:
    current_platform = platform or detect_runtime_platform()
    expected_path = source.root_for(current_platform)
    try:
        checked = path_resolver(expected_path, platform=current_platform)
    except AssetSourceUnavailable as error:
        if current_platform == "windows" and error.reason_code == "ASSET_ROOT_NOT_FOUND":
            return NasSourceStatus(
                source.source_id, "not_mounted", "NAS_NOT_MOUNTED",
                expected_path, "Windows 尚未建立该 UNC 共享的访问会话",
            )
        return NasSourceStatus(
            source.source_id, "blocked", error.reason_code, expected_path, str(error)
        )
    return NasSourceStatus(
        source.source_id, "ready", "", str(checked.path), "NAS 来源已挂载且可读"
    )


def launch_nas_mount(
    source: NasSource,
    *,
    platform: RuntimePlatform | None = None,
    launcher: Callable[[list[str]], object] | None = None,
) -> None:
    """Open the OS-owned SMB UI; never pass or persist credentials."""

    current_platform = platform or detect_runtime_platform()
    if current_platform != "windows":
        raise AssetSourceUnavailable(
            "NAS_PLATFORM_UNSUPPORTED", f"NAS 辅助连接暂不支持当前系统：{current_platform}"
        )
    command = ["explorer.exe", source.windows_path]
    run = launcher or (lambda value: subprocess.run(value, check=True))
    try:
        run(command)
    except (OSError, subprocess.CalledProcessError) as error:
        raise AssetSourceUnavailable(
            "NAS_MOUNT_LAUNCH_FAILED", f"无法打开系统 NAS 连接流程：{source.label}"
        ) from error


def prepare_nas_source(
    source: NasSource,
    *,
    allow_mount: bool,
    wait_seconds: float = 30,
    platform: RuntimePlatform | None = None,
    checker: Callable[[NasSource], NasSourceStatus] | None = None,
    launcher: Callable[[list[str]], object] | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> NasSourceStatus:
    current_platform = platform or detect_runtime_platform()
    check = checker or (
        lambda value: check_nas_source(value, platform=current_platform)
    )
    status = check(source)
    if status.state == "ready" or status.reason_code != "NAS_NOT_MOUNTED":
        return status
    if not allow_mount:
        return status
    launch_nas_mount(source, platform=current_platform, launcher=launcher)
    deadline = time.monotonic() + max(0, wait_seconds)
    while time.monotonic() <= deadline:
        status = check(source)
        if status.state != "not_mounted":
            return status
        remaining = max(0, deadline - time.monotonic())
        if remaining <= 0:
            break
        sleeper(min(1, remaining))
    return NasSourceStatus(
        source.source_id, "blocked", "NAS_MOUNT_TIMEOUT",
        source.root_for(current_platform), "等待系统完成 NAS 挂载超时；完成认证后重新检查",
    )


def select_nas_folders(
    sources: dict[str, NasSource],
    selections: Iterable[str],
    *,
    allow_mount: bool = False,
    wait_seconds: float = 30,
    platform: RuntimePlatform | None = None,
) -> list[SelectedNasFolder]:
    current_platform = platform or detect_runtime_platform()
    selected: list[SelectedNasFolder] = []
    checked_statuses: dict[str, NasSourceStatus] = {}
    for raw_selection in selections:
        source_id, separator, raw_subpath = raw_selection.partition(":")
        if not separator or source_id not in sources:
            raise AssetSourceUnavailable(
                "NAS_SOURCE_SELECTION_INVALID",
                f"NAS 文件夹选择格式应为 source_id:subpath：{raw_selection}",
            )
        source = sources[source_id]
        subpath = _normalized_subpath(raw_subpath)
        if subpath not in source.subpaths:
            raise AssetSourceUnavailable(
                "NAS_SUBPATH_NOT_ALLOWED", f"NAS 子目录未在配置允许列表中：{raw_selection}"
            )
        if source_id not in checked_statuses:
            checked_statuses[source_id] = prepare_nas_source(
                source, allow_mount=allow_mount, wait_seconds=wait_seconds,
                platform=current_platform,
            )
        status = checked_statuses[source_id]
        if status.state != "ready":
            raise AssetSourceUnavailable(status.reason_code, status.message)
        mount_root = Path(status.mount_path).resolve()
        root = (mount_root / Path(*PurePosixPath(subpath).parts)).resolve()
        if not root.is_relative_to(mount_root):
            raise AssetSourceUnavailable(
                "NAS_SUBPATH_OUTSIDE_SHARE", f"NAS 子目录解析后离开共享根：{raw_selection}"
            )
        checked = resolve_asset_root(root, platform=current_platform)
        selected.append(SelectedNasFolder(
            source_id, source.label, subpath, checked.path, source.license_status
        ))
    return selected


def browse_nas_folders(
    source: NasSource,
    *,
    relative_path: str = "",
    platform: RuntimePlatform | None = None,
) -> list[dict[str, str]]:
    current_platform = platform or detect_runtime_platform()
    status = check_nas_source(source, platform=current_platform)
    if status.state != "ready":
        raise AssetSourceUnavailable(status.reason_code, status.message)
    root = Path(status.mount_path).resolve()
    normalized = _normalized_subpath(relative_path, allow_empty=True)
    target = (
        (root / Path(*PurePosixPath(normalized).parts)).resolve()
        if normalized else root
    )
    if not target.is_relative_to(root):
        raise AssetSourceUnavailable("NAS_SUBPATH_INVALID", "浏览路径不能离开 NAS 共享根目录")
    resolve_asset_root(target, platform=current_platform)
    try:
        directories = sorted(
            (path for path in target.iterdir() if path.is_dir()),
            key=lambda path: (path.name.casefold(), path.name),
        )
    except PermissionError as error:
        raise AssetSourceUnavailable(
            "ASSET_ROOT_PERMISSION_DENIED", f"没有权限浏览 NAS 目录：{target}"
        ) from error
    except OSError as error:
        raise AssetSourceUnavailable(
            "ASSET_ROOT_IO_ERROR", f"浏览 NAS 目录失败，连接可能已经中断：{target}"
        ) from error
    return [
        {
            "name": path.name,
            "relative_path": (
                PurePosixPath(normalized, path.name).as_posix()
                if normalized else path.name
            ),
        }
        for path in directories
    ]


def statuses_json(statuses: Iterable[NasSourceStatus]) -> str:
    return json.dumps(
        [status.as_dict() for status in statuses], ensure_ascii=False, indent=2
    )


def nas_config_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_nas_selection(
    path: Path, *, expected_config_path: Path
) -> list[str]:
    try:
        loaded = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AssetSourceUnavailable("NAS_SELECTION_INVALID", "无法读取 NAS 选择文件") from error
    if not isinstance(loaded, dict) or loaded.get("schema_version") != 1:
        raise AssetSourceUnavailable("NAS_SELECTION_INVALID", "NAS 选择文件 schema 无效")
    if loaded.get("nas_config_sha256") != nas_config_sha256(expected_config_path):
        raise AssetSourceUnavailable(
            "NAS_SELECTION_CONFIG_CHANGED", "NAS 配置在用户选择后发生变化，请重新选择"
        )
    selections = loaded.get("selected_folders")
    if not isinstance(selections, list) or not selections:
        raise AssetSourceUnavailable("NAS_SELECTION_EMPTY", "NAS 选择文件没有已勾选的素材目录")
    return [str(value) for value in selections]
