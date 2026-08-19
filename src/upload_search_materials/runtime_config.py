"""Windows machine-local path resolution for the material workflow."""

from __future__ import annotations

import ctypes
from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import threading
import time
from typing import Callable, Mapping, Sequence
import unicodedata

from .path_diagnostics import diagnose_image_sources
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
    return "\\".join(normalized_parts)


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


@dataclass(frozen=True)
class DiscoveredPath:
    path: Path | None
    status: str
    candidates: tuple[Path, ...] = ()


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
    image_source_history = _merge_image_source_history(
        workspace_root,
        document.get("image_source_history"),
        document.get("image_sources"),
    )
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
        image_source_history=image_source_history,
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


def _image_source_path_key(value: object, workspace_root: Path) -> str:
    normalized = str(_resolve_configured_path(value, workspace_root))
    return normalized.casefold().rstrip("\\/")


def _image_source_history(
    value: object, workspace_root: Path
) -> tuple[dict[str, str], ...]:
    """Read valid historical identities without applying current-list uniqueness."""

    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    records: dict[tuple[str, str], dict[str, str]] = {}
    for item in value:
        if not isinstance(item, Mapping):
            continue
        source_id = str(item.get("source_id", "")).strip().casefold()
        raw_path = str(item.get("path", "")).strip()
        if (
            not SOURCE_ID_PATTERN.fullmatch(source_id)
            or not raw_path
            or len(raw_path) > 1000
            or "\x00" in raw_path
        ):
            continue
        normalized_path = str(_resolve_configured_path(raw_path, workspace_root))
        path_key = normalized_path.casefold().rstrip("\\/")
        label = str(item.get("label", "")).strip() or source_id
        record = {
            "source_id": source_id,
            "label": label[:100],
            "path": normalized_path,
        }
        canonical_unc = str(item.get("canonical_unc", "")).strip()
        if canonical_unc.startswith("\\\\"):
            record["canonical_unc"] = canonical_unc
        records[(source_id, path_key)] = record
    return tuple(records.values())


def _merge_image_source_history(
    workspace_root: Path, *values: object
) -> tuple[dict[str, str], ...]:
    records: dict[tuple[str, str], dict[str, str]] = {}
    for value in values:
        for record in _image_source_history(value, workspace_root):
            key = (
                record["source_id"],
                _image_source_path_key(record["path"], workspace_root),
            )
            records[key] = record
    return tuple(records.values())


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
    history = _merge_image_source_history(
        runtime.workspace_root,
        document.get("image_source_history"),
        document.get("image_sources"),
        runtime.image_source_history,
        runtime.image_sources,
    )
    sources = normalize_image_sources(value, runtime.workspace_root)
    history = _merge_image_source_history(
        runtime.workspace_root,
        history,
        sources,
    )
    document["image_sources"] = list(sources)
    document["image_source_history"] = list(history)
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
        image_source_history=history,
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
