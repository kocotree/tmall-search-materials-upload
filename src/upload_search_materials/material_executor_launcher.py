"""Launch one material job in the logged-in Windows desktop context."""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

from .runtime_config import RuntimeConfig


SESSION_PATTERN = re.compile(r"^\d{8}_\d{6}(?:_\d{2})?$")


class MaterialExecutorLaunchError(RuntimeError):
    reason_code = "MATERIAL_EXECUTOR_LAUNCH_FAILED"


def _validate_session(session_id: str) -> str:
    value = str(session_id).strip()
    if not SESSION_PATTERN.fullmatch(value):
        raise MaterialExecutorLaunchError("session_id_invalid")
    return value


def _project_root() -> Path:
    configured = str(os.environ.get("TMALL_PLUGIN_ROOT", "")).strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def _python_executable(project_root: Path) -> Path:
    active = type(project_root)(os.path.abspath(sys.executable))
    candidate = active.with_name("pythonw.exe")
    if not candidate.is_file():
        raise MaterialExecutorLaunchError("prepared_python_missing")
    return candidate


def _config_argument(runtime: RuntimeConfig) -> list[str]:
    path = runtime.config_path
    if path is None:
        return []
    resolved = type(_project_root())(path).resolve()
    allowed_roots = [(_project_root() / "config").resolve()]
    if runtime.user_data_root is not None:
        allowed_roots.append((runtime.user_data_root / "config").resolve())
    if not any(
        resolved == root or root in resolved.parents for root in allowed_roots
    ):
        raise MaterialExecutorLaunchError("config_outside_allowed_roots")
    if not resolved.is_file() or resolved.suffix.casefold() != ".json":
        raise MaterialExecutorLaunchError("config_invalid")
    return ["--config", str(resolved)]


def launch_material_executor(
    runtime: RuntimeConfig,
    session_id: str,
) -> dict[str, Any]:
    """Start a one-shot executor without inheriting the web service token."""

    session = _validate_session(session_id)
    project_root = _project_root()
    config_args = _config_argument(runtime)
    if os.name != "nt":
        raise MaterialExecutorLaunchError("windows_required")
    _python_executable(project_root)
    launcher = project_root / "scripts" / "launch-material-executor-desktop.ps1"
    if not launcher.is_file():
        raise MaterialExecutorLaunchError("desktop_launcher_missing")
    command = [
        "powershell.exe",
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(launcher),
        "-ProjectRoot",
        str(project_root),
        "-Session",
        session,
    ]
    if runtime.user_data_root is not None:
        command.extend(
            ("-UserDataRoot", str(runtime.user_data_root.resolve()))
        )
    if config_args:
        command.extend(("-Config", config_args[1]))
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            command,
            cwd=str(project_root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            creationflags=creationflags,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise MaterialExecutorLaunchError(str(error)) from error
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise MaterialExecutorLaunchError(
            detail or f"desktop_launcher_exit_{completed.returncode}"
        )
    return {
        "status": "requested",
        "launch_channel": "windows_explorer",
        "session_id": session,
    }
