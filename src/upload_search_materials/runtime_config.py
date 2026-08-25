"""Windows machine-local path resolution for the material workflow."""

from __future__ import annotations

import ctypes
from dataclasses import dataclass, field, replace
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import threading
import time
from typing import Callable, Mapping, Sequence
import unicodedata

from .path_diagnostics import (
    diagnose_image_sources,
    mapped_drive_unc,
    portable_unc_suggestion,
)
from .platform_support import AssetSourceUnavailable, resolve_asset_root


PRODUCTS_PATTERN = "天猫商品信息表*产品数据表*数据总表.csv"
RULES_PATTERN = "天猫商品信息表*每月推品规则*Grid View.csv"
LOCAL_CONFIG_RELATIVE = Path("config/local-paths.json")
NAS_SOURCES_RELATIVE = Path("config/nas-sources.yaml")
LOCAL_SELECTORS_RELATIVE = Path("config/selectors.local.yaml")
BUNDLED_SELECTORS_RELATIVE = Path("config/selectors.production.yaml")
PACKAGE_DOCS_RELATIVE = Path("src/upload_search_materials/docs")
DEFAULT_CDP_PROFILE_RELATIVE = Path(".local-cache/cdp-profile")
DEFAULT_CDP_URL = "http://127.0.0.1:9222"
DEFAULT_MATERIAL_CENTER_URL = (
    "https://myseller.taobao.com/home.htm/material-center/material-management?tab=recommend"
)
DEFAULT_TEAM_FOLDER_INDEX_ROOT = Path(
    r"\\192.168.110.20\浙江酷趣\天猫部\搜推素材索引-虾米"
)
TEAM_FOLDER_INDEX_RELATIVE_PARTS = (
    "浙江酷趣",
    "天猫部",
    "搜推素材索引-虾米",
)
TEAM_FOLDER_INDEX_DISCOVERY_TIMEOUT_SECONDS = 1.5
DEFAULT_TEAM_FOLDER_INDEX_NAS_SOURCE_ID = "zhejiang-kuqu"
IMAGE_SOURCE_DISCOVERY_TIMEOUT_SECONDS = 1.5
DEFAULT_IMAGE_SOURCE_PROFILES = (
    (
        "小红书 KOC 置换 · 买家秀",
        (
            "浙江酷趣",
            "运营中心",
            "营销板块",
            "小红书koc置换&买家秀",
            "优质买家秀",
        ),
    ),
    (
        "小红书 KOC 置换 · 淘宝买家秀",
        (
            "浙江酷趣",
            "运营中心",
            "营销板块",
            "小红书koc置换&淘宝买家秀",
            "优质买家秀",
        ),
    ),
    ("视觉部 · 模特图", ("视觉部", "1-模特图")),
)
MAX_IMAGE_SOURCES = 50
SOURCE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{2,63}$")


def image_source_path_key(value: object) -> str:
    """Return the drive/server-independent identity key for an image root."""

    text = unicodedata.normalize("NFC", str(value).strip()).replace("/", "\\")
    if not text or "\x00" in text:
        raise ValueError("image source path is required")

    if text.startswith("\\\\"):
        parts = [part for part in text.lstrip("\\").split("\\") if part]
        if len(parts) < 2:
            raise ValueError("UNC image source path must include a share")
        parts = parts[1:]
    else:
        windows_path = PureWindowsPath(text)
        remainder = text[len(windows_path.drive):] if windows_path.drive else text
        parts = [part for part in remainder.strip("\\").split("\\") if part]

    normalized_parts: list[str] = []
    for part in parts:
        normalized = unicodedata.normalize("NFC", part).casefold()
        if normalized == ".":
            continue
        if normalized == "..":
            raise ValueError("image source path must not contain '..'")
        normalized_parts.append(normalized)
    if not normalized_parts:
        raise ValueError("image source path must include a directory below its root")
    return "/".join(normalized_parts)


def stable_image_source_id(value: object) -> str:
    """Derive a cross-machine source ID from the normalized source path key."""

    path_key = image_source_path_key(value)
    return "source-" + hashlib.sha256(path_key.encode("utf-8")).hexdigest()[:12]


def default_user_data_root(
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Return a writable, machine-local root outside the Plugin cache."""

    env = os.environ if environ is None else environ
    explicit = str(env.get("TMALL_USER_DATA_ROOT", "")).strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    base = str(env.get("LOCALAPPDATA", "")).strip()
    root = Path(base) if base else Path.home() / "AppData" / "Local"
    return (root / "tmall-search-materials").resolve()


def _bool_config_value(value: object) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().casefold()
    return text in {"1", "true", "yes", "y", "on", "启用", "是"}


def _bounded_text(value: object, *, limit: int, field_name: str) -> str:
    text = str(value or "").strip()
    if "\x00" in text:
        raise ValueError(f"{field_name} must not contain NUL")
    if len(text) > limit:
        raise ValueError(f"{field_name} is too long")
    return text


def normalize_lark_base_config(
    value: object,
    environ: Mapping[str, str] | None = None,
) -> LarkBaseConfig:
    """Normalize optional Feishu Base settings from local config/env."""

    document = value if isinstance(value, Mapping) else {}
    env = os.environ if environ is None else environ

    def configured(name: str, env_name: str, *, limit: int = 2000) -> str:
        return _bounded_text(
            env.get(env_name) or document.get(name),
            limit=limit,
            field_name=f"lark_base.{name}",
        )

    product_base_url = configured(
        "product_base_url", "TMALL_LARK_PRODUCT_BASE_URL"
    )
    product_base_token = configured(
        "product_base_token", "TMALL_LARK_PRODUCT_BASE_TOKEN", limit=200
    )
    product_table_id = configured(
        "product_table_id", "TMALL_LARK_PRODUCT_TABLE_ID", limit=200
    )
    upload_log_base_url = configured(
        "upload_log_base_url", "TMALL_LARK_UPLOAD_LOG_BASE_URL"
    )
    upload_log_base_token = configured(
        "upload_log_base_token", "TMALL_LARK_UPLOAD_LOG_BASE_TOKEN", limit=200
    )
    upload_log_table_id = configured(
        "upload_log_table_id", "TMALL_LARK_UPLOAD_LOG_TABLE_ID", limit=200
    )
    enabled_raw = env.get("TMALL_LARK_BASE_ENABLED")
    enabled_explicit = enabled_raw is not None or "enabled" in document
    enabled = _bool_config_value(
        enabled_raw if enabled_raw is not None else document.get("enabled")
    )
    if not enabled and not enabled_explicit and any(
        (
            product_base_url,
            product_base_token,
            product_table_id,
            upload_log_base_url,
            upload_log_base_token,
            upload_log_table_id,
        )
    ):
        enabled = True
    timeout_raw = (
        env.get("TMALL_LARK_COMMAND_TIMEOUT_SECONDS")
        or document.get("command_timeout_seconds")
        or 30
    )
    try:
        timeout = float(timeout_raw)
    except (TypeError, ValueError):
        timeout = 30.0
    timeout = min(max(timeout, 5.0), 120.0)
    return LarkBaseConfig(
        enabled=enabled,
        product_base_url=product_base_url,
        product_base_token=product_base_token,
        product_table_id=product_table_id,
        upload_log_base_url=upload_log_base_url,
        upload_log_base_token=upload_log_base_token,
        upload_log_table_id=upload_log_table_id,
        command_timeout_seconds=timeout,
    )


def _lark_base_document(config: LarkBaseConfig) -> dict[str, object]:
    return {
        "enabled": config.enabled,
        "product_base_url": config.product_base_url,
        "product_base_token": config.product_base_token,
        "product_table_id": config.product_table_id,
        "upload_log_base_url": config.upload_log_base_url,
        "upload_log_base_token": config.upload_log_base_token,
        "upload_log_table_id": config.upload_log_table_id,
        "command_timeout_seconds": config.command_timeout_seconds,
    }


@dataclass(frozen=True)
class DiscoveredPath:
    path: Path | None
    status: str
    candidates: tuple[Path, ...] = ()


@dataclass(frozen=True)
class LarkBaseConfig:
    """Machine-local Feishu Base integration settings.

    The Base URLs/tokens point at team documents and are intentionally kept out
    of tracked defaults.  Each Windows user stores them in the local runtime
    config or provides them through environment variables.
    """

    enabled: bool = False
    product_base_url: str = ""
    product_base_token: str = ""
    product_table_id: str = ""
    upload_log_base_url: str = ""
    upload_log_base_token: str = ""
    upload_log_table_id: str = ""
    command_timeout_seconds: float = 30.0

    @property
    def product_sync_configured(self) -> bool:
        return bool(
            self.enabled
            and (self.product_base_url or self.product_base_token)
            and (self.product_table_id or self.product_base_url)
        )

    @property
    def upload_log_configured(self) -> bool:
        return bool(
            self.enabled
            and (self.upload_log_base_url or self.upload_log_base_token)
            and (self.upload_log_table_id or self.upload_log_base_url)
        )


@dataclass(frozen=True)
class RuntimeConfig:
    workspace_root: Path
    products: DiscoveredPath
    rules: DiscoveredPath
    image_sources: tuple[dict[str, str], ...]
    runs_root: Path
    nas_sources_file: Path | None = None
    folder_index_root: Path = Path(".local-cache/folder-index")
    team_folder_index_root: Path | None = None
    team_folder_index_root_source: str = "default"
    team_folder_index_nas_source_id: str | None = None
    selectors_file: Path | None = None
    cdp_url: str = DEFAULT_CDP_URL
    browser_executable: Path | None = None
    browser_profile_dir: Path = DEFAULT_CDP_PROFILE_RELATIVE
    material_center_url: str = DEFAULT_MATERIAL_CENTER_URL
    config_path: Path | None = None
    user_data_root: Path | None = None
    image_source_history: tuple[dict[str, str], ...] = ()
    lark_base: LarkBaseConfig = field(default_factory=LarkBaseConfig)


def load_runtime_config(
    config_path: str | Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    start: Path | None = None,
) -> RuntimeConfig:
    """Resolve paths without requiring a particular username, drive letter, or cwd."""

    env = os.environ if environ is None else environ
    user_data_root = default_user_data_root(env)
    explicit_config = config_path or env.get("TMALL_CONFIG_FILE")
    environment_workspace = env.get("TMALL_WORKSPACE_ROOT")
    preliminary_root = _find_workspace_root(environment_workspace, start=start)
    selected_config = _select_config_path(
        explicit_config, preliminary_root, user_data_root
    )
    document = _read_config(selected_config)
    workspace_root = (
        preliminary_root
        if environment_workspace
        else _configured_workspace_root(
            document.get("workspace_root"), selected_config, preliminary_root
        )
    )
    products = _resolve_table(
        env.get("TMALL_PRODUCTS_CSV") or document.get("products_csv"),
        workspace_root,
        PRODUCTS_PATTERN,
    )
    rules = _resolve_table(
        env.get("TMALL_RULES_CSV") or document.get("rules_csv"),
        workspace_root,
        RULES_PATTERN,
    )
    image_sources = _image_sources(document.get("image_sources"), workspace_root)
    nas_sources_value = env.get("TMALL_NAS_SOURCES_FILE") or document.get(
        "nas_sources_file"
    )
    nas_sources_file = (
        _resolve_configured_path(nas_sources_value, workspace_root)
        if nas_sources_value
        else workspace_root / NAS_SOURCES_RELATIVE
    )
    if not nas_sources_file.is_file():
        nas_sources_file = None
    runs_value = env.get("TMALL_RUNS_ROOT") or document.get("runs_root")
    runs_root = (
        _resolve_configured_path(runs_value, workspace_root)
        if runs_value
        else user_data_root / "runs"
    )
    folder_index_value = (
        env.get("TMALL_FOLDER_INDEX_ROOT") or document.get("folder_index_root")
    )
    folder_index_root = (
        _resolve_configured_path(folder_index_value, workspace_root)
        if folder_index_value
        else user_data_root / "cache" / "folder-index"
    )
    environment_team_folder_index = str(
        env.get("TMALL_TEAM_FOLDER_INDEX_ROOT") or ""
    ).strip()
    configured_team_folder_index = str(
        document.get("team_folder_index_root") or ""
    ).strip()
    team_folder_index_value = (
        environment_team_folder_index
        or configured_team_folder_index
        or str(DEFAULT_TEAM_FOLDER_INDEX_ROOT)
    )
    team_folder_index_root_source = (
        "environment"
        if environment_team_folder_index
        else "config"
        if configured_team_folder_index
        else "default"
    )
    team_folder_index_root = _resolve_configured_path(
        team_folder_index_value, workspace_root
    )
    team_folder_index_nas_source_id = str(
        env.get("TMALL_TEAM_FOLDER_INDEX_NAS_SOURCE_ID")
        or document.get("team_folder_index_nas_source_id")
        or DEFAULT_TEAM_FOLDER_INDEX_NAS_SOURCE_ID
    ).strip().casefold() or None
    selectors_value = env.get("TMALL_SELECTORS_FILE") or document.get(
        "selectors_file"
    )
    selectors_file = (
        _resolve_configured_path(selectors_value, workspace_root)
        if selectors_value
        else user_data_root / "config" / "selectors.local.yaml"
    )
    if not selectors_file.is_file():
        selectors_file = None
    cdp_url = str(
        env.get("TMALL_CDP_URL")
        or document.get("cdp_url")
        or DEFAULT_CDP_URL
    ).strip()
    browser_value = env.get("TMALL_BROWSER_EXECUTABLE") or document.get(
        "browser_executable"
    )
    browser_executable = (
        _resolve_configured_path(browser_value, workspace_root)
        if browser_value
        else None
    )
    profile_value = env.get("TMALL_CDP_PROFILE_DIR") or document.get(
        "browser_profile_dir"
    )
    browser_profile_dir = (
        _resolve_configured_path(profile_value, workspace_root)
        if profile_value
        else user_data_root / "browser-profile"
    )
    material_center_url = str(
        env.get("TMALL_MATERIAL_CENTER_URL")
        or document.get("material_center_url")
        or DEFAULT_MATERIAL_CENTER_URL
    ).strip()
    lark_base = normalize_lark_base_config(document.get("lark_base"), env)
    return RuntimeConfig(
        workspace_root=workspace_root,
        products=products,
        rules=rules,
        image_sources=image_sources,
        runs_root=runs_root,
        nas_sources_file=nas_sources_file,
        folder_index_root=folder_index_root,
        team_folder_index_root=team_folder_index_root,
        team_folder_index_root_source=team_folder_index_root_source,
        team_folder_index_nas_source_id=team_folder_index_nas_source_id,
        selectors_file=selectors_file,
        cdp_url=cdp_url,
        browser_executable=browser_executable,
        browser_profile_dir=browser_profile_dir,
        material_center_url=material_center_url,
        config_path=selected_config,
        user_data_root=user_data_root,
        image_source_history=(),
        lark_base=lark_base,
    )


def normalize_image_sources(
    value: object, workspace_root: Path
) -> tuple[dict[str, str], ...]:
    """Validate and normalize one or more user-configured image roots."""

    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError("image_sources must be a list")
    if not 1 <= len(value) <= MAX_IMAGE_SOURCES:
        raise ValueError(f"image_sources must contain 1-{MAX_IMAGE_SOURCES} items")
    sources: list[dict[str, str]] = []
    labels: set[str] = set()
    paths: set[str] = set()
    source_ids: set[str] = set()
    for index, item in enumerate(value, start=1):
        if not isinstance(item, Mapping):
            raise ValueError(f"image_sources[{index}] must be an object")
        label = str(item.get("label", "")).strip()
        raw_path = str(item.get("path", "")).strip()
        if not label or len(label) > 100:
            raise ValueError(f"image_sources[{index}].label is required")
        if not raw_path or len(raw_path) > 1000 or "\x00" in raw_path:
            raise ValueError(f"image_sources[{index}].path is required")
        normalized_path = str(_resolve_configured_path(raw_path, workspace_root))
        source_id = stable_image_source_id(normalized_path)
        if not SOURCE_ID_PATTERN.fullmatch(source_id):
            raise ValueError(
                f"image_sources[{index}].source_id is invalid"
            )
        canonical_unc = str(item.get("canonical_unc", "")).strip()
        if canonical_unc and not canonical_unc.startswith("\\\\"):
            raise ValueError(
                f"image_sources[{index}].canonical_unc must be UNC"
            )
        label_key = label.casefold()
        path_key = normalized_path.casefold().rstrip("\\/")
        if label_key in labels:
            raise ValueError(f"duplicate image source label: {label}")
        if path_key in paths:
            raise ValueError(f"duplicate image source path: {normalized_path}")
        if source_id in source_ids:
            raise ValueError(f"duplicate image source id: {source_id}")
        labels.add(label_key)
        paths.add(path_key)
        source_ids.add(source_id)
        source = {
            "source_id": source_id,
            "label": label,
            "path": normalized_path,
        }
        if canonical_unc:
            source["canonical_unc"] = canonical_unc
        for key in ("last_verified_sid", "last_verified_at", "last_status"):
            text = str(item.get(key, "")).strip()
            if text:
                source[key] = text
        sources.append(source)
    return tuple(sources)


def save_image_sources(
    runtime: RuntimeConfig, value: object
) -> RuntimeConfig:
    """Persist image roots to the machine-local JSON without changing tracked files."""

    target = runtime.config_path or (
        runtime.user_data_root / "config/runtime.json"
        if runtime.user_data_root is not None
        else runtime.workspace_root / LOCAL_CONFIG_RELATIVE
    )
    document = _read_config(target) if target.is_file() else {}
    sources = normalize_image_sources(value, runtime.workspace_root)
    document["image_sources"] = list(sources)
    document.pop("image_source_history", None)
    if runtime.team_folder_index_root is not None:
        document.setdefault(
            "team_folder_index_root", str(runtime.team_folder_index_root)
        )
    if runtime.team_folder_index_nas_source_id:
        document.setdefault(
            "team_folder_index_nas_source_id",
            runtime.team_folder_index_nas_source_id,
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, target)
    return replace(
        runtime,
        image_sources=sources,
        image_source_history=(),
        config_path=target,
    )


def save_lark_base_config(
    runtime: RuntimeConfig, value: object
) -> RuntimeConfig:
    """Persist optional Feishu Base settings to the machine-local JSON."""

    target = runtime.config_path or (
        runtime.user_data_root / "config/runtime.json"
        if runtime.user_data_root is not None
        else runtime.workspace_root / LOCAL_CONFIG_RELATIVE
    )
    document = _read_config(target) if target.is_file() else {}
    config = normalize_lark_base_config(value, {})
    document["lark_base"] = _lark_base_document(config)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, target)
    return replace(
        runtime,
        lark_base=config,
        config_path=target,
    )


def inspect_team_folder_index_root(
    runtime: RuntimeConfig, value: object
) -> dict[str, object]:
    text = str(value or "").strip()
    if not text or "\x00" in text or len(text) > 1000:
        return {
            "status": "unavailable",
            "reason_code": "TEAM_INDEX_ROOT_REQUIRED",
            "message": "请选择团队索引文件夹。",
            "path": text,
            "snapshot_source_count": 0,
        }
    selected = _resolve_configured_path(text, runtime.workspace_root)
    try:
        checked = resolve_asset_root(selected)
    except AssetSourceUnavailable as error:
        return {
            "status": "unavailable",
            "reason_code": error.reason_code,
            "message": str(error),
            "path": str(selected),
            "snapshot_source_count": 0,
        }
    sources_root = checked.path / "sources"
    try:
        source_count = (
            sum(
                1
                for source_root in sources_root.iterdir()
                if source_root.is_dir()
                and (source_root / "current.json").is_file()
            )
            if sources_root.is_dir()
            else 0
        )
    except OSError:
        source_count = 0
    return {
        "status": "available",
        "reason_code": "",
        "message": (
            f"路径可访问，已发现 {source_count} 个索引来源。"
            if source_count
            else "路径可访问，尚无团队索引快照。"
        ),
        "path": str(checked.path),
        "snapshot_source_count": source_count,
    }


def _windows_logical_drive_roots() -> tuple[Path, ...]:
    """Return mounted Windows roots without enumerating their contents."""

    if os.name != "nt":
        return ()
    mask = int(ctypes.windll.kernel32.GetLogicalDrives())
    roots: list[Path] = []
    for index in range(26):
        if not mask & (1 << index):
            continue
        root = f"{chr(65 + index)}:\\"
        drive_type = int(ctypes.windll.kernel32.GetDriveTypeW(root))
        if drive_type in {2, 3, 4, 6}:
            roots.append(Path(root))
    return tuple(roots)


def _available_directories_with_timeout(
    paths: Sequence[Path],
    *,
    timeout_seconds: float,
    probe: Callable[[Path], bool] | None = None,
) -> tuple[Path, ...]:
    """Probe exact directories in daemon threads under one shared deadline."""

    if not paths:
        return ()
    check = probe or (lambda value: value.is_dir())
    states: dict[int, bool] = {}

    def inspect(index: int, path: Path) -> None:
        try:
            states[index] = bool(check(path))
        except OSError:
            states[index] = False

    threads = [
        threading.Thread(target=inspect, args=(index, path), daemon=True)
        for index, path in enumerate(paths)
    ]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))
    return tuple(path for index, path in enumerate(paths) if states.get(index) is True)


def discover_team_folder_index_root(
    runtime: RuntimeConfig,
    *,
    drive_roots: Sequence[Path] | None = None,
    timeout_seconds: float = TEAM_FOLDER_INDEX_DISCOVERY_TIMEOUT_SECONDS,
    probe: Callable[[Path], bool] | None = None,
) -> dict[str, object]:
    """Find the fixed team-index path without scanning any drive tree."""

    started_at = time.monotonic()
    total_timeout = max(0.0, float(timeout_seconds))
    current = runtime.team_folder_index_root
    if runtime.team_folder_index_root_source == "environment":
        return {
            "status": "configured",
            "path": str(current or ""),
            "candidates": [],
            "auto_fill": False,
            "message": "正在使用这台电脑的指定团队索引路径。",
        }
    if current is not None and runtime.team_folder_index_root_source == "config":
        current_timeout = min(total_timeout / 2, 0.75)
        available = _available_directories_with_timeout(
            (current,), timeout_seconds=current_timeout, probe=probe
        )
        if available:
            return {
                "status": "configured",
                "path": str(current),
                "candidates": [str(current)],
                "auto_fill": False,
                "message": "已保存的团队索引路径当前可访问。",
            }

    roots = tuple(drive_roots) if drive_roots is not None else _windows_logical_drive_roots()
    candidates = tuple(
        Path(root).joinpath(*TEAM_FOLDER_INDEX_RELATIVE_PARTS)
        for root in roots
    )
    remaining_timeout = max(0.0, total_timeout - (time.monotonic() - started_at))
    matches = _available_directories_with_timeout(
        candidates, timeout_seconds=remaining_timeout, probe=probe
    )
    if len(matches) == 1:
        return {
            "status": "discovered",
            "path": str(matches[0]),
            "candidates": [str(matches[0])],
            "auto_fill": True,
            "message": "已自动找到团队索引文件夹，提交任务时会保存。",
        }
    if len(matches) > 1:
        return {
            "status": "ambiguous",
            "path": "",
            "candidates": [str(path) for path in matches],
            "auto_fill": False,
            "message": "发现多个团队索引文件夹，请选择本次使用的文件夹。",
        }
    return {
        "status": "not_found",
        "path": "",
        "candidates": [],
        "auto_fill": False,
        "message": "未自动找到团队索引文件夹，请确认共享盘已在 Windows 中打开。",
    }


def _image_source_candidate(
    path: Path,
    *,
    canonicalize: Callable[[Path], str] | None = None,
) -> dict[str, str]:
    text = str(path)
    canonical = str(canonicalize(path)).strip() if canonicalize else ""
    if not canonical:
        windows = PureWindowsPath(text)
        if windows.drive and windows.root:
            unc_root = mapped_drive_unc(f"{windows.drive}\\")
            canonical = portable_unc_suggestion(text, unc_root)
    identity = canonical or str(path.resolve())
    candidate = {
        "path": text,
        "identity": identity.casefold().rstrip("\\/"),
    }
    if canonical.startswith("\\\\"):
        candidate["canonical_unc"] = canonical
    return candidate


def _deduplicate_image_source_candidates(
    paths: Sequence[Path],
    *,
    canonicalize: Callable[[Path], str] | None = None,
) -> list[dict[str, str]]:
    records: dict[str, dict[str, str]] = {}
    for path in paths:
        candidate = _image_source_candidate(path, canonicalize=canonicalize)
        records.setdefault(candidate.pop("identity"), candidate)
    return list(records.values())


def _default_image_source_discovery(
    drive_roots: Sequence[Path],
    *,
    timeout_seconds: float,
    probe: Callable[[Path], bool] | None,
    canonicalize: Callable[[Path], str] | None,
) -> dict[str, object]:
    paths_by_profile = [
        tuple(Path(root).joinpath(*relative_parts) for root in drive_roots)
        for _label, relative_parts in DEFAULT_IMAGE_SOURCE_PROFILES
    ]
    all_paths = tuple(path for paths in paths_by_profile for path in paths)
    available = set(
        _available_directories_with_timeout(
            all_paths,
            timeout_seconds=timeout_seconds,
            probe=probe,
        )
    )
    sources: list[dict[str, object]] = []
    discovered_count = 0
    ambiguous_count = 0
    for (label, relative_parts), profile_paths in zip(
        DEFAULT_IMAGE_SOURCE_PROFILES, paths_by_profile, strict=True
    ):
        candidates = _deduplicate_image_source_candidates(
            [path for path in profile_paths if path in available],
            canonicalize=canonicalize,
        )
        source: dict[str, object] = {
            "source_id": stable_image_source_id("\\".join(relative_parts)),
            "label": label,
            "path": candidates[0]["path"] if len(candidates) == 1 else "",
            "candidates": candidates,
        }
        if len(candidates) == 1:
            discovered_count += 1
            if candidates[0].get("canonical_unc"):
                source["canonical_unc"] = candidates[0]["canonical_unc"]
        elif len(candidates) > 1:
            ambiguous_count += 1
        sources.append(source)

    if not discovered_count and not ambiguous_count:
        return {
            "status": "not_found",
            "source": "defaults",
            "auto_fill": False,
            "image_sources": [],
            "message": "未自动找到默认图片文件夹，请手动选择本次图片源。",
        }
    missing_count = len(sources) - discovered_count - ambiguous_count
    status = (
        "ambiguous"
        if ambiguous_count
        else "partial"
        if missing_count
        else "discovered"
    )
    return {
        "status": status,
        "source": "defaults",
        "auto_fill": True,
        "image_sources": sources,
        "message": (
            f"已自动找到 {discovered_count} 个默认图片源；"
            f"还有 {ambiguous_count} 个需要选择位置。"
            if ambiguous_count
            else f"已自动找到 {discovered_count} 个默认图片源。"
            if not missing_count
            else f"已自动找到 {discovered_count} 个默认图片源，未找到的来源可手动选择。"
        ),
    }


def discover_image_sources(
    runtime: RuntimeConfig,
    *,
    drive_roots: Sequence[Path] | None = None,
    timeout_seconds: float = IMAGE_SOURCE_DISCOVERY_TIMEOUT_SECONDS,
    probe: Callable[[Path], bool] | None = None,
    canonicalize: Callable[[Path], str] | None = None,
) -> dict[str, object]:
    """Resolve saved or default image roots without scanning any drive tree."""

    started_at = time.monotonic()
    total_timeout = max(0.0, float(timeout_seconds))
    roots = (
        tuple(Path(root) for root in drive_roots)
        if drive_roots is not None
        else _windows_logical_drive_roots()
    )
    saved_sources = [dict(source) for source in runtime.image_sources]
    if saved_sources:
        saved_paths = tuple(Path(str(source["path"])) for source in saved_sources)
        current_timeout = min(total_timeout / 3, 0.5)
        current_available = set(
            _available_directories_with_timeout(
                saved_paths,
                timeout_seconds=current_timeout,
                probe=probe,
            )
        )
        if len(current_available) == len(saved_paths):
            return {
                "status": "configured",
                "source": "saved",
                "auto_fill": False,
                "image_sources": saved_sources,
                "message": f"已优先加载 {len(saved_sources)} 个常用图片源。",
            }

        paths_by_source: list[tuple[Path, ...]] = []
        candidate_paths: list[Path] = []
        for source, saved_path in zip(saved_sources, saved_paths, strict=True):
            if saved_path in current_available:
                paths_by_source.append(())
                continue
            relative_parts = tuple(PureWindowsPath(image_source_path_key(source["path"])).parts)
            paths = tuple(root.joinpath(*relative_parts) for root in roots)
            paths_by_source.append(paths)
            candidate_paths.extend(paths)
        remaining = max(0.0, total_timeout - (time.monotonic() - started_at))
        rebound_available = set(
            _available_directories_with_timeout(
                tuple(candidate_paths),
                timeout_seconds=remaining,
                probe=probe,
            )
        )
        usable_count = len(current_available)
        ambiguous_count = 0
        rebound_count = 0
        projected: list[dict[str, object]] = []
        for source, saved_path, source_paths in zip(
            saved_sources, saved_paths, paths_by_source, strict=True
        ):
            item: dict[str, object] = dict(source)
            if saved_path in current_available:
                projected.append(item)
                continue
            candidates = _deduplicate_image_source_candidates(
                [path for path in source_paths if path in rebound_available],
                canonicalize=canonicalize,
            )
            item["candidates"] = candidates
            if len(candidates) == 1:
                item["path"] = candidates[0]["path"]
                if candidates[0].get("canonical_unc"):
                    item["canonical_unc"] = candidates[0]["canonical_unc"]
                usable_count += 1
                rebound_count += 1
            elif len(candidates) > 1:
                ambiguous_count += 1
            projected.append(item)

        if usable_count or ambiguous_count:
            return {
                "status": "ambiguous" if ambiguous_count else "configured",
                "source": "saved",
                "auto_fill": bool(rebound_count or ambiguous_count),
                "image_sources": projected,
                "message": (
                    f"已优先加载常用图片源；还有 {ambiguous_count} 个来源需要选择位置。"
                    if ambiguous_count
                    else f"已优先加载常用图片源，并自动恢复 {rebound_count} 个盘符绑定。"
                    if rebound_count
                    else "已优先加载可访问的常用图片源。"
                ),
            }

    remaining = max(0.0, total_timeout - (time.monotonic() - started_at))
    return _default_image_source_discovery(
        roots,
        timeout_seconds=remaining,
        probe=probe,
        canonicalize=canonicalize,
    )


def save_team_folder_index_root(
    runtime: RuntimeConfig, value: object
) -> RuntimeConfig:
    inspection = inspect_team_folder_index_root(runtime, value)
    if inspection["status"] != "available":
        raise ValueError(str(inspection["message"]))
    target = runtime.config_path or (
        runtime.user_data_root / "config/runtime.json"
        if runtime.user_data_root is not None
        else runtime.workspace_root / LOCAL_CONFIG_RELATIVE
    )
    document = _read_config(target) if target.is_file() else {}
    selected = Path(str(inspection["path"]))
    document["team_folder_index_root"] = str(selected)
    if runtime.team_folder_index_nas_source_id:
        document.setdefault(
            "team_folder_index_nas_source_id",
            runtime.team_folder_index_nas_source_id,
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, target)
    return replace(
        runtime,
        team_folder_index_root=selected,
        team_folder_index_root_source="config",
        config_path=target,
    )


def save_selector_profile_path(
    runtime: RuntimeConfig, value: str | Path
) -> RuntimeConfig:
    """Install one validated selector profile outside the Plugin cache."""

    text = str(value).strip()
    if not text or "\x00" in text or len(text) > 1000:
        raise ValueError("selectors_file is required")
    selected = _resolve_configured_path(text, runtime.workspace_root)
    if not selected.is_file():
        raise ValueError("selectors_file does not exist")
    installed = (
        runtime.user_data_root / LOCAL_SELECTORS_RELATIVE
        if runtime.user_data_root is not None
        else runtime.workspace_root / LOCAL_SELECTORS_RELATIVE
    ).resolve()
    if selected.resolve() != installed:
        installed.parent.mkdir(parents=True, exist_ok=True)
        selector_temporary = installed.with_name(f".{installed.name}.tmp")
        selector_temporary.write_bytes(selected.read_bytes())
        os.replace(selector_temporary, installed)
    target = (
        runtime.user_data_root / "config/runtime.json"
        if runtime.user_data_root is not None
        else runtime.config_path
        or runtime.workspace_root / LOCAL_CONFIG_RELATIVE
    )
    document = _read_config(target) if target.is_file() else {}
    document["selectors_file"] = str(installed)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, target)
    return replace(
        runtime,
        selectors_file=installed,
        config_path=target,
    )


def initialize_bundled_selector_profile(
    runtime: RuntimeConfig,
) -> RuntimeConfig:
    """Install the bundled production baseline once for this Windows user."""

    if runtime.selectors_file is not None:
        return runtime
    installed = (
        runtime.user_data_root / LOCAL_SELECTORS_RELATIVE
        if runtime.user_data_root is not None
        else runtime.workspace_root / LOCAL_SELECTORS_RELATIVE
    ).resolve()
    if installed.is_file():
        return replace(runtime, selectors_file=installed)

    bundled = (
        runtime.workspace_root / BUNDLED_SELECTORS_RELATIVE
    ).resolve()
    if not bundled.is_file():
        return runtime

    # Import lazily to keep path resolution independent from browser startup.
    from .browser.config import load_selector_profile

    for purpose in ("high_value_collection", "exact_material_status"):
        load_selector_profile(bundled, purpose=purpose, production=True)

    installed.parent.mkdir(parents=True, exist_ok=True)
    temporary = installed.with_name(f".{installed.name}.{os.getpid()}.tmp")
    temporary.write_bytes(bundled.read_bytes())
    try:
        try:
            # Windows rename does not replace an existing destination. This
            # preserves a profile created concurrently by another startup.
            os.rename(temporary, installed)
        except FileExistsError:
            pass
    finally:
        temporary.unlink(missing_ok=True)
    return save_selector_profile_path(runtime, installed)


def inspect_image_sources(
    runtime: RuntimeConfig, value: object
) -> tuple[dict[str, object], ...]:
    """Return read-only reachability status for configured directories."""

    sources = normalize_image_sources(value, runtime.workspace_root)
    return diagnose_image_sources(sources)


def _find_workspace_root(configured: str | None, *, start: Path | None) -> Path:
    if configured:
        return Path(configured).expanduser().resolve()
    seeds = [start or Path.cwd(), Path(__file__)]
    seen: set[Path] = set()
    for seed in seeds:
        candidate = seed if seed.is_dir() else seed.parent
        for parent in (candidate, *candidate.parents):
            resolved = parent.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            if (
                (resolved / "SKILL.md").is_file()
                and (resolved / "pyproject.toml").is_file()
                and (resolved / "src" / "upload_search_materials").is_dir()
            ):
                return resolved
    return (start or Path.cwd()).resolve()


def _select_config_path(
    value: str | Path | None,
    workspace_root: Path,
    user_data_root: Path,
) -> Path | None:
    if value:
        path = Path(value).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"runtime config not found: {path}")
        return path
    candidate = user_data_root / "config/runtime.json"
    if candidate.is_file():
        return candidate
    legacy = workspace_root / LOCAL_CONFIG_RELATIVE
    return legacy if legacy.is_file() else None


def _read_config(path: Path | None) -> dict:
    if path is None:
        return {}
    with path.open("r", encoding="utf-8-sig") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError("runtime config must contain a JSON object")
    return value


def _configured_workspace_root(
    value: object, config_path: Path | None, fallback: Path
) -> Path:
    if not value:
        return fallback
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        base = config_path.parent if config_path else fallback
        path = base / path
    return path.resolve()


def _resolve_table(value: object, workspace_root: Path, pattern: str) -> DiscoveredPath:
    if value:
        path = _resolve_configured_path(value, workspace_root)
        return DiscoveredPath(path, "configured" if path.is_file() else "missing")
    docs = workspace_root / PACKAGE_DOCS_RELATIVE
    candidates = tuple(sorted(docs.glob(pattern))) if docs.is_dir() else ()
    if len(candidates) == 1:
        return DiscoveredPath(candidates[0].resolve(), "discovered", candidates)
    return DiscoveredPath(
        None,
        "ambiguous" if len(candidates) > 1 else "missing",
        tuple(path.resolve() for path in candidates),
    )


def _resolve_configured_path(value: object, workspace_root: Path) -> Path:
    text = str(value)
    path = Path(text).expanduser()
    if PureWindowsPath(text).is_absolute() and not path.is_absolute():
        return Path(str(PureWindowsPath(text)))
    if path.is_absolute():
        return path
    return (workspace_root / path).resolve()


def _image_sources(value: object, workspace_root: Path) -> tuple[dict[str, str], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    raw_sources: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        label = str(item.get("label", "")).strip()
        raw_path = str(item.get("path", "")).strip()
        if not label or not raw_path:
            continue
        source = {"label": label, "path": raw_path}
        for key in (
            "source_id",
            "canonical_unc",
            "last_verified_sid",
            "last_verified_at",
            "last_status",
        ):
            text = str(item.get(key, "")).strip()
            if text:
                source[key] = text
        raw_sources.append(source)
    if not raw_sources:
        return ()
    return normalize_image_sources(raw_sources, workspace_root)
