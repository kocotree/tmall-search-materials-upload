"""Portable, machine-local path resolution for the material workflow."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path, PureWindowsPath
from typing import Mapping, Sequence


PRODUCTS_PATTERN = "天猫商品信息表*产品数据表*数据总表.csv"
RULES_PATTERN = "天猫商品信息表*每月推品规则*Grid View.csv"
LOCAL_CONFIG_RELATIVE = Path("upload-search-materials/config/local-paths.json")


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
    runs_value = env.get("TMALL_RUNS_ROOT") or document.get("runs_root")
    runs_root = (
        _resolve_configured_path(runs_value, workspace_root)
        if runs_value
        else workspace_root / "runs"
    )
    return RuntimeConfig(
        workspace_root=workspace_root,
        products=products,
        rules=rules,
        image_sources=image_sources,
        runs_root=runs_root,
        config_path=selected_config,
    )


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
            if (resolved / "docs").is_dir() and (
                resolved / "upload-search-materials"
            ).is_dir():
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
    docs = workspace_root / "docs"
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
    sources: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        label = str(item.get("label", "")).strip()
        raw_path = str(item.get("path", "")).strip()
        if not label or not raw_path:
            continue
        sources.append(
            {"label": label, "path": str(_resolve_configured_path(raw_path, workspace_root))}
        )
    return tuple(sources)
