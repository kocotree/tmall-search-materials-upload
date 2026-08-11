"""Portable, machine-local path resolution for the material workflow."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
from typing import Mapping, Sequence

from .path_diagnostics import diagnose_image_sources


PRODUCTS_PATTERN = "天猫商品信息表*产品数据表*数据总表.csv"
RULES_PATTERN = "天猫商品信息表*每月推品规则*Grid View.csv"
LOCAL_CONFIG_RELATIVE = Path("config/local-paths.json")
NAS_SOURCES_RELATIVE = Path("config/nas-sources.yaml")
LOCAL_SELECTORS_RELATIVE = Path("config/selectors.local.yaml")
PACKAGE_DOCS_RELATIVE = Path("src/upload_search_materials/docs")
DEFAULT_CDP_PROFILE_RELATIVE = Path(".local-cache/cdp-profile")
DEFAULT_CDP_URL = "http://127.0.0.1:9222"
DEFAULT_MATERIAL_CENTER_URL = (
    "https://myseller.taobao.com/home.htm/material-center/material-management"
)
MAX_IMAGE_SOURCES = 50
SOURCE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{2,63}$")


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
    selectors_file: Path | None = None
    cdp_url: str = DEFAULT_CDP_URL
    browser_executable: Path | None = None
    browser_profile_dir: Path = DEFAULT_CDP_PROFILE_RELATIVE
    material_center_url: str = DEFAULT_MATERIAL_CENTER_URL
    config_path: Path | None = None


def load_runtime_config(
    config_path: str | Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    start: Path | None = None,
) -> RuntimeConfig:
    """Resolve paths without requiring a particular username, drive letter, or cwd."""

    env = os.environ if environ is None else environ
    explicit_config = config_path or env.get("TMALL_CONFIG_FILE")
    environment_workspace = env.get("TMALL_WORKSPACE_ROOT")
    preliminary_root = _find_workspace_root(environment_workspace, start=start)
    selected_config = _select_config_path(explicit_config, preliminary_root)
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
        else workspace_root / "runs"
    )
    folder_index_value = (
        env.get("TMALL_FOLDER_INDEX_ROOT") or document.get("folder_index_root")
    )
    folder_index_root = (
        _resolve_configured_path(folder_index_value, workspace_root)
        if folder_index_value
        else workspace_root / ".local-cache" / "folder-index"
    )
    selectors_value = env.get("TMALL_SELECTORS_FILE") or document.get(
        "selectors_file"
    )
    selectors_file = (
        _resolve_configured_path(selectors_value, workspace_root)
        if selectors_value
        else workspace_root / LOCAL_SELECTORS_RELATIVE
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
        else workspace_root / DEFAULT_CDP_PROFILE_RELATIVE
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
        selectors_file=selectors_file,
        cdp_url=cdp_url,
        browser_executable=browser_executable,
        browser_profile_dir=browser_profile_dir,
        material_center_url=material_center_url,
        config_path=selected_config,
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
        source_id = str(item.get("source_id", "")).strip().casefold()
        if not source_id:
            source_id = "source-" + hashlib.sha256(
                label.casefold().encode("utf-8")
            ).hexdigest()[:12]
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

    sources = normalize_image_sources(value, runtime.workspace_root)
    target = runtime.config_path or runtime.workspace_root / LOCAL_CONFIG_RELATIVE
    document = _read_config(target) if target.is_file() else {}
    document["image_sources"] = list(sources)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, target)
    return replace(runtime, image_sources=sources, config_path=target)


def save_selector_profile_path(
    runtime: RuntimeConfig, value: str | Path
) -> RuntimeConfig:
    """Persist one validated machine-local selector profile path."""

    text = str(value).strip()
    if not text or "\x00" in text or len(text) > 1000:
        raise ValueError("selectors_file is required")
    selected = _resolve_configured_path(text, runtime.workspace_root)
    if not selected.is_file():
        raise ValueError("selectors_file does not exist")
    target = runtime.config_path or runtime.workspace_root / LOCAL_CONFIG_RELATIVE
    document = _read_config(target) if target.is_file() else {}
    document["selectors_file"] = str(selected)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, target)
    return replace(
        runtime,
        selectors_file=selected,
        config_path=target,
    )


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


def _select_config_path(value: str | Path | None, workspace_root: Path) -> Path | None:
    if value:
        path = Path(value).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"runtime config not found: {path}")
        return path
    candidate = workspace_root / LOCAL_CONFIG_RELATIVE
    return candidate if candidate.is_file() else None


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
    if path.is_absolute() or PureWindowsPath(text).is_absolute():
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
