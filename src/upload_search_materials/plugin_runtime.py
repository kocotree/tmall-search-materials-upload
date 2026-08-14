"""Commands that keep every child process on the current Plugin source."""

from __future__ import annotations

import os
from pathlib import Path
import sys
from typing import Mapping


def plugin_launcher_path(project_root: Path) -> Path:
    launcher = Path(project_root).resolve() / "scripts" / "run-plugin.py"
    if not launcher.is_file():
        raise FileNotFoundError(f"PLUGIN_LAUNCHER_MISSING:{launcher}")
    return launcher


def plugin_cli_command(
    project_root: Path,
    *arguments: str,
    python: str | Path | None = None,
) -> list[str]:
    # Keep the selected environment path verbatim. Resolving a symlink can
    # cross into a different checkout's .venv and silently change dependencies.
    interpreter = Path(os.path.abspath(os.fspath(python or sys.executable)))
    return [
        str(interpreter),
        str(plugin_launcher_path(project_root)),
        *(str(argument) for argument in arguments),
    ]


def plugin_child_environment(
    project_root: Path,
    *,
    base: Mapping[str, str] | None = None,
    workspace_root: Path | None = None,
) -> dict[str, str]:
    project = Path(project_root).resolve()
    source = str(project / "src")
    environment = dict(base if base is not None else os.environ)
    inherited = [
        entry
        for entry in environment.get("PYTHONPATH", "").split(os.pathsep)
        if entry and entry != source
    ]
    environment["PYTHONPATH"] = os.pathsep.join([source, *inherited])
    environment["TMALL_PLUGIN_ROOT"] = str(project)
    environment["TMALL_WORKSPACE_ROOT"] = str(
        Path(workspace_root).resolve() if workspace_root else project
    )
    return environment
