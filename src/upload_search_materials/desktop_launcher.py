"""Narrow entry point intended for an explicitly authorized desktop user."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .browser.config import SelectorConfigError, load_selector_profile
from .browser.session import (
    CdpUnavailable,
    ensure_cdp_browser,
    open_cdp_page,
)
from .collection_readiness import DEFAULT_CANDIDATE_SELECTORS
from .interaction.service import start_service
from .runtime_config import load_runtime_config
from .runtime_config import RuntimeConfig


SESSION_PATTERN = re.compile(r"^\d{8}_\d{6}(?:_\d{2})?$")


class DesktopLauncherError(ValueError):
    reason_code = "DESKTOP_LAUNCH_ARGUMENT_INVALID"


def _page_scopes(page: Any) -> list[Any]:
    scopes = [page]
    for frame in list(getattr(page, "frames", ()) or ()):
        if frame is not None and all(frame is not scope for scope in scopes):
            scopes.append(frame)
    return scopes


def _visible_locator_texts(
    scope: Any,
    selector: str,
    *,
    limit: int,
) -> list[str]:
    try:
        locator = scope.locator(selector)
        count = min(int(locator.count()), limit)
    except Exception:
        return []
    values: list[str] = []
    for index in range(count):
        try:
            candidate = locator.nth(index)
            if not candidate.is_visible():
                continue
            value = str(candidate.inner_text()).strip()
        except Exception:
            continue
        if value:
            values.append(value)
    return values


def _has_visible_locator(scope: Any, selector: str, *, limit: int) -> bool:
    try:
        locator = scope.locator(selector)
        count = min(int(locator.count()), limit)
    except Exception:
        return False
    for index in range(count):
        try:
            if locator.nth(index).is_visible():
                return True
        except Exception:
            continue
    return False


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def validate_desktop_launch(
    *,
    runs_root: Path,
    session_id: str | None,
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
    if session_id is not None and not SESSION_PATTERN.fullmatch(
        str(session_id)
    ):
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
        "session_id": str(session_id) if session_id is not None else None,
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


def inspect_login_browser(runtime: RuntimeConfig) -> dict[str, Any]:
    """Ensure the visible browser exists and return a safe login gate state.

    This check deliberately does not need the target store, which is a business
    value entered later.  It only proves that the material-center page exposes
    a visible authenticated store identity and no human challenge.
    """

    browser = ensure_login_browser(runtime)
    if not browser.get("connected"):
        return {
            **browser,
            "ready": False,
            "login_state": "browser_unavailable",
        }
    selectors = dict(DEFAULT_CANDIDATE_SELECTORS)
    if runtime.selectors_file is not None:
        try:
            profile = load_selector_profile(
                runtime.selectors_file,
                purpose="high_value_collection",
                production=False,
            )
        except SelectorConfigError:
            profile = None
        if profile is not None:
            selectors.update(profile.selectors)
    try:
        with open_cdp_page(
            runtime.cdp_url,
            runtime.material_center_url,
        ) as page:
            page.bring_to_front()
            try:
                page.wait_for_timeout(500)
            except Exception:
                pass
            scopes = _page_scopes(page)
            challenge_visible = any(
                _has_visible_locator(
                    scope,
                    selectors["human_check"],
                    limit=10,
                )
                for scope in scopes
            )
            if challenge_visible:
                return {
                    **browser,
                    "ready": False,
                    "login_state": "human_check",
                    "reason_code": "HUMAN_CHECK",
                }
            observed_store = ""
            for scope in scopes:
                values = _visible_locator_texts(
                    scope,
                    selectors["store_name"],
                    limit=20,
                )
                if values:
                    observed_store = values[0]
                    break
            if observed_store:
                return {
                    **browser,
                    "ready": True,
                    "login_state": "authenticated",
                    "observed_store": observed_store,
                    "observed_url": str(page.url),
                    "reason_code": "READY",
                }
            return {
                **browser,
                "ready": False,
                "login_state": "interaction_required",
                "observed_url": str(page.url),
                "reason_code": "LOGIN_INTERACTION_REQUIRED",
            }
    except Exception as error:
        return {
            **browser,
            "ready": False,
            "login_state": "browser_unavailable",
            "reason_code": str(error).split(":", 1)[0],
            "message": str(error),
        }


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
