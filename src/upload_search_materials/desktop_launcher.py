"""Narrow entry point intended for an explicitly authorized desktop user."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

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
QIANNIU_LOGIN_URL = "https://login.taobao.com/member/login.jhtml"
LOGIN_SURFACE_SELECTORS = (
    'input[type="password"]',
    'iframe[src*="login" i]',
    'iframe[src*="passport" i]',
    'form[action*="login" i]',
    ':text-is("扫码登录")',
    ':text-is("账号登录")',
    ':text-is("请登录")',
)


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


def _is_login_url(url: str) -> bool:
    normalized = str(url).strip().casefold()
    return any(
        marker in normalized
        for marker in ("login", "passport", "oauth", "authorize")
    )


def _is_material_center_url(url: str) -> bool:
    normalized = str(url).strip().casefold()
    return any(
        marker in normalized
        for marker in ("material-center", "material-management")
    )


def _login_entry_url(material_center_url: str) -> str:
    return f"{QIANNIU_LOGIN_URL}?{urlencode({'redirectURL': material_center_url})}"


def _has_login_surface(scopes: list[Any]) -> bool:
    return any(
        _has_visible_locator(scope, selector, limit=10)
        for scope in scopes
        for selector in LOGIN_SURFACE_SELECTORS
    )


def _has_material_center_surface(
    scopes: list[Any],
    selectors: dict[str, str],
) -> bool:
    for field in ("promotion_tab", "high_value_filter", "material_page"):
        selector = str(selectors.get(field, "")).strip()
        if selector and any(
            _has_visible_locator(scope, selector, limit=10)
            for scope in scopes
        ):
            return True
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
    allowed_runs_root: Path | None = None,
) -> dict[str, Any]:
    fixed_project = (
        Path(project_root).resolve()
        if project_root is not None
        else Path(__file__).resolve().parents[2]
    )
    repository_root = fixed_project.parent.resolve()
    resolved_runs = Path(runs_root).resolve()
    resolved_allowed_runs = (
        Path(allowed_runs_root).resolve()
        if allowed_runs_root is not None
        else None
    )
    if not _inside(resolved_runs, repository_root) and not (
        resolved_allowed_runs is not None
        and _inside(resolved_runs, resolved_allowed_runs)
    ):
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


def _request_login_browser_attention(page: Any) -> None:
    """Best-effort foregrounding reserved for required user interaction."""

    try:
        page.bring_to_front()
    except Exception:
        # Login state remains authoritative even when Windows refuses focus.
        pass


def _open_login_entry(page: Any, material_center_url: str) -> str:
    """Open the official login page and return to the material center after login."""

    login_url = _login_entry_url(material_center_url)
    try:
        page.goto(login_url, wait_until="domcontentloaded", timeout=15_000)
    except Exception:
        # A timeout can occur after the interactive login surface has already
        # rendered. Keep the browser visible and let the login gate poll again.
        pass
    _request_login_browser_attention(page)
    return str(getattr(page, "url", "") or login_url)


def inspect_login_browser(runtime: RuntimeConfig) -> dict[str, Any]:
    """Ensure the visible browser exists and return a safe login gate state.

    This check deliberately does not need the target store, which is a business
    value entered later.  It only proves that the material-center page exposes
    a visible authenticated store identity and no human challenge. Normal
    authenticated checks stay in the background; only a login or human check
    asks Windows to foreground the user-controlled browser.
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
                _request_login_browser_attention(page)
                return {
                    **browser,
                    "ready": False,
                    "login_state": "human_check",
                    "reason_code": "HUMAN_CHECK",
                    "observed_url": str(page.url),
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
            observed_url = str(page.url)
            if _is_login_url(observed_url) or _has_login_surface(scopes):
                _request_login_browser_attention(page)
                return {
                    **browser,
                    "ready": False,
                    "login_state": "interaction_required",
                    "observed_url": observed_url,
                    "reason_code": "LOGIN_INTERACTION_REQUIRED",
                }
            if _is_material_center_url(observed_url):
                if not _has_material_center_surface(scopes, selectors):
                    observed_url = _open_login_entry(
                        page,
                        runtime.material_center_url,
                    )
                    return {
                        **browser,
                        "ready": False,
                        "login_state": "interaction_required",
                        "observed_url": observed_url,
                        "reason_code": "LOGIN_INTERACTION_REQUIRED",
                    }
                return {
                    **browser,
                    "ready": False,
                    "login_state": "store_unrecognized",
                    "observed_url": observed_url,
                    "reason_code": "STORE_IDENTITY_NOT_FOUND",
                }
            return {
                **browser,
                "ready": False,
                "login_state": "opening_material_center",
                "observed_url": observed_url,
                "reason_code": "MATERIAL_CENTER_OPENING",
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
    configured_plugin_root = str(
        os.environ.get("TMALL_PLUGIN_ROOT", "")
    ).strip()
    project_root = Path(
        kwargs.get("project_root")
        or configured_plugin_root
        or Path(__file__).resolve().parents[2]
    ).resolve()
    runtime = load_runtime_config(
        kwargs.get("config"),
        start=project_root,
    )
    launch = validate_desktop_launch(
        runs_root=kwargs.get("runs_root") or runtime.runs_root,
        session_id=kwargs.get("session_id"),
        port_start=kwargs.get("port_start", 8765),
        port_end=kwargs.get("port_end", 8795),
        config=kwargs.get("config"),
        project_root=project_root,
        allowed_runs_root=runtime.runs_root,
    )
    login_browser = ensure_login_browser(runtime)
    result = start_service(
        launch["runs_root"],
        session_id=launch["session_id"],
        port_start=launch["port_start"],
        port_end=launch["port_end"],
        config=str(launch["config"]) if launch["config"] else None,
        managed_desktop=True,
        project_root=launch["project_root"],
        workspace_root=runtime.workspace_root,
    )
    return {**result, "login_browser": login_browser}
