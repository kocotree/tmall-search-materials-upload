"""Offline preflight for the one prepared project runtime."""

from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Callable

from .browser.session import CdpStatus, inspect_cdp_endpoint
from .persistence import read_json
from .runtime_config import RuntimeConfig
from .time_utils import iso_timestamp


BOOTSTRAP_ACTION = "运行 scripts\\bootstrap.cmd -WithTests"


class RuntimePreflightError(RuntimeError):
    def __init__(self, reason_code: str, next_action: str):
        super().__init__(f"{reason_code}:{next_action}")
        self.reason_code = reason_code
        self.next_action = next_action


def environment_fingerprint(project_root: Path) -> dict[str, Any]:
    """Describe the single prepared module environment used at runtime."""

    project = Path(project_root).resolve()
    lock = project / "uv.lock"
    python_candidates = (
        project / ".venv" / "Scripts" / "python.exe",
        project / ".venv" / "bin" / "python",
    )
    executable_candidates = (
        project / ".venv" / "Scripts" / "tmall-materials.exe",
        project / ".venv" / "bin" / "tmall-materials",
    )
    python = next(
        (candidate for candidate in python_candidates if candidate.is_file()),
        python_candidates[0],
    )
    executable = next(
        (candidate for candidate in executable_candidates if candidate.is_file()),
        executable_candidates[0],
    )
    source_package = project / "src" / "upload_search_materials"
    recorded_path = project / ".environment-fingerprint.json"
    lock_sha = (
        hashlib.sha256(lock.read_bytes()).hexdigest()
        if lock.is_file()
        else ""
    )
    files_ready = bool(
        lock_sha
        and python.is_file()
        and (executable.is_file() or source_package.is_dir())
    )
    launch_identity = (
        str(executable.resolve())
        if executable.is_file()
        else f"{python.resolve()} -m upload_search_materials.cli"
    )
    try:
        recorded = read_json(recorded_path) if recorded_path.is_file() else None
    except (OSError, json.JSONDecodeError):
        recorded = {"schema_version": 0}
    if recorded is None:
        fingerprint_status = "legacy_prepared" if files_ready else "missing"
        fingerprint_match = files_ready
    else:
        if not isinstance(recorded, dict):
            recorded = {"schema_version": 0}
        fingerprint_match = bool(
            int(recorded.get("schema_version", 0)) == 1
            and recorded.get("lock_sha256") == lock_sha
            and str(
                recorded.get(
                    "launch_identity", recorded.get("executable", "")
                )
            )
            == launch_identity
        )
        fingerprint_status = "matched" if fingerprint_match else "stale"
    return {
        "schema_version": 1,
        "project_root": str(project),
        "uv_cache_dir": str(project / ".uv-cache"),
        "lock_path": str(lock),
        "lock_sha256": lock_sha,
        "python": str(python),
        "executable": str(executable),
        "launch_identity": launch_identity,
        "source_package": str(source_package),
        "fingerprint_path": str(recorded_path),
        "fingerprint_status": fingerprint_status,
        "prepared": files_ready and fingerprint_match,
        "checked_at": iso_timestamp(),
    }


def preflight_runtime_environment(
    runtime: RuntimeConfig,
    *,
    selectors_path: Path,
    cdp_url: str,
    runner: Callable[..., Any] = subprocess.run,
    cdp_probe: Callable[[str], CdpStatus] = inspect_cdp_endpoint,
) -> dict[str, Any]:
    """Check prepared local state without dependency resolution or network I/O."""

    project = runtime.workspace_root
    environment = environment_fingerprint(project)
    if not environment["prepared"]:
        raise RuntimePreflightError(
            "ENVIRONMENT_NOT_PREPARED", BOOTSTRAP_ACTION
        )
    selected = Path(selectors_path)
    if not selected.is_file():
        raise RuntimePreflightError(
            "SELECTOR_PROFILE_NOT_FOUND",
            "在阶段一页面创建并验证本机生产选择器",
        )
    probe_code = (
        "import json,sys;"
        "import flask,playwright,yaml,PIL,openpyxl;"
        "print(json.dumps({'major':sys.version_info.major,"
        "'minor':sys.version_info.minor}))"
    )
    try:
        completed = runner(
            [str(environment["python"]), "-c", probe_code],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimePreflightError(
            "ENVIRONMENT_INTERPRETER_UNAVAILABLE", BOOTSTRAP_ACTION
        ) from error
    if completed.returncode != 0:
        raise RuntimePreflightError(
            "ENVIRONMENT_IMPORTS_MISSING", BOOTSTRAP_ACTION
        )
    try:
        version = json.loads(completed.stdout.strip())
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise RuntimePreflightError(
            "ENVIRONMENT_INTERPRETER_INVALID", BOOTSTRAP_ACTION
        ) from error
    if (
        int(version.get("major", 0)),
        int(version.get("minor", 0)),
    ) < (3, 11):
        raise RuntimePreflightError(
            "ENVIRONMENT_PYTHON_UNSUPPORTED", BOOTSTRAP_ACTION
        )
    cache = Path(environment["uv_cache_dir"])
    try:
        cache.mkdir(parents=True, exist_ok=True)
        descriptor, probe_name = tempfile.mkstemp(
            prefix=".write-probe-", dir=cache
        )
        os.close(descriptor)
        Path(probe_name).unlink()
    except OSError as error:
        raise RuntimePreflightError(
            "PROJECT_CACHE_NOT_WRITABLE",
            "修复项目 .uv-cache 权限后重新检查",
        ) from error
    cdp = cdp_probe(cdp_url)
    if not cdp.connected:
        raise RuntimePreflightError(
            cdp.reason_code or "CDP_UNAVAILABLE",
            cdp.next_action or "启动或恢复用于千牛登录的独立 Chrome",
        )
    return {
        "ready": True,
        "reason_code": "READY",
        "next_action": "",
        "environment": environment,
        "python_version": version,
        "selectors_path": str(selected.resolve()),
        "cdp_endpoint": cdp.endpoint,
        "offline": True,
    }
