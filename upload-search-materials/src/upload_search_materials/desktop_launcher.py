"""Narrow entry point intended for an explicitly authorized desktop user."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .browser.session import CdpUnavailable, ensure_cdp_browser
from .interaction.service import start_service
from .runtime_config import load_runtime_config
from .runtime_config import RuntimeConfig


SESSION_PATTERN = re.compile(r"^\d{8}_\d{6}(?:_\d{2})?$")


class DesktopLauncherError(ValueError):
    reason_code = "DESKTOP_LAUNCH_ARGUMENT_INVALID"


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def validate_desktop_launch(
    *,
    runs_root: Path,
    session_id: str,
    port_start: int,
    port_end: int,
    config: Path | None,
    project_root: Path | None = None,
) -> dict[str, Any]:
    fixed_project = (
        Path(project_root).resolve()
        if project_root is not None
        else Path(__file__).resolve().parents[2]
    )
    repository_root = fixed_project.parent.resolve()
    resolved_runs = Path(runs_root).resolve()
    if not _inside(resolved_runs, repository_root):
        raise DesktopLauncherError("runs_root_outside_project")
    if not SESSION_PATTERN.fullmatch(str(session_id)):
        raise DesktopLauncherError("session_id_invalid")
    if not (
        1024 <= int(port_start) <= int(port_end) <= 65535
        and int(port_end) - int(port_start) <= 100
    ):
        raise DesktopLauncherError("port_range_invalid")
    resolved_config = Path(config).resolve() if config else None
    allowed_config_root = (fixed_project / "config").resolve()
    if resolved_config is not None and (
        not resolved_config.is_file()
        or not _inside(resolved_config, allowed_config_root)
        or resolved_config.suffix.casefold() != ".json"
    ):
        raise DesktopLauncherError("config_path_invalid")
    return {
        "project_root": fixed_project,
        "runs_root": resolved_runs,
        "session_id": str(session_id),
        "port_start": int(port_start),
        "port_end": int(port_end),
        "config": resolved_config,
    }


def ensure_login_browser(runtime: RuntimeConfig) -> dict[str, Any]:
    """Open the visible login browser before showing configuration UI."""

    try:
        cdp = ensure_cdp_browser(
            executable=runtime.browser_executable,
            profile_dir=runtime.browser_profile_dir,
            cdp_url=runtime.cdp_url,
            material_center_url=runtime.material_center_url,
        )
        login_browser = {
            "status": str(cdp.get("status", "connected")),
            "connected": cdp.get("status") == "connected",
            "reused": bool(cdp.get("reused")),
            "endpoint": str(cdp.get("endpoint", runtime.cdp_url)),
            "page_count": len(cdp.get("pages", [])),
        }
    except CdpUnavailable as error:
        login_browser = {
            "status": "unavailable",
            "connected": False,
            "reason_code": str(error).split(":", 1)[0],
            "message": str(error),
            "endpoint": runtime.cdp_url,
        }
    return login_browser


def launch_desktop_workbench(**kwargs: Any) -> dict[str, Any]:
    launch = validate_desktop_launch(**kwargs)
    runtime = load_runtime_config(
        launch["config"],
        start=launch["project_root"],
    )
    login_browser = ensure_login_browser(runtime)
    result = start_service(
        launch["runs_root"],
        session_id=launch["session_id"],
        port_start=launch["port_start"],
        port_end=launch["port_end"],
        config=str(launch["config"]) if launch["config"] else None,
    )
    return {**result, "login_browser": login_browser}
