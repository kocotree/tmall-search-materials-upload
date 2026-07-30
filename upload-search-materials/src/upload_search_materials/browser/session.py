from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import time
from typing import Mapping
from urllib.error import URLError
from urllib.request import build_opener, ProxyHandler

from playwright.sync_api import sync_playwright

from ..time_utils import iso_timestamp


class StoreIdentityError(RuntimeError):
    pass


class HumanCheckRequired(RuntimeError):
    pass


class LoginInteractionRequired(RuntimeError):
    pass


class CdpUnavailable(RuntimeError):
    pass


_LOOPBACK_OPENER = build_opener(ProxyHandler({}))


@dataclass(frozen=True)
class CdpStatus:
    connected: bool
    endpoint: str
    pages: tuple[dict[str, str], ...] = ()
    reason_code: str = ""
    next_action: str = ""


def inspect_cdp_endpoint(cdp_url: str, timeout_seconds: float = 2.0) -> CdpStatus:
    endpoint = str(cdp_url).rstrip("/")
    try:
        with _LOOPBACK_OPENER.open(
            f"{endpoint}/json/list", timeout=timeout_seconds
        ) as response:
            document = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, ValueError, json.JSONDecodeError):
        return CdpStatus(
            connected=False,
            endpoint=endpoint,
            reason_code="CDP_UNAVAILABLE",
            next_action="启动或恢复用于千牛登录的独立 Chrome",
        )
    pages = tuple(
        {
            "id": str(item.get("id", "")),
            "title": str(item.get("title", "")),
            "url": str(item.get("url", "")),
        }
        for item in document
        if isinstance(item, Mapping) and item.get("type") == "page"
    )
    return CdpStatus(connected=True, endpoint=endpoint, pages=pages)


def launch_cdp_browser(
    *,
    executable: Path | None,
    profile_dir: Path,
    cdp_url: str,
    material_center_url: str,
) -> dict[str, object]:
    """Launch a visible, user-controlled CDP browser without storing secrets."""

    current = inspect_cdp_endpoint(cdp_url)
    if current.connected:
        return {
            "status": "connected",
            "reused": True,
            "endpoint": current.endpoint,
            "pages": list(current.pages),
        }
    candidate = Path(executable).expanduser() if executable else None
    if candidate is None:
        discovered = shutil.which("chrome") or shutil.which("msedge")
        candidate = Path(discovered) if discovered else None
    if candidate is None or not candidate.is_file():
        raise CdpUnavailable(
            "BROWSER_EXECUTABLE_REQUIRED: configure browser_executable"
        )
    endpoint = str(cdp_url).rstrip("/")
    if not endpoint.startswith("http://127.0.0.1:"):
        raise CdpUnavailable("CDP_ENDPOINT_NOT_LOOPBACK")
    try:
        port = int(endpoint.rsplit(":", 1)[1])
    except (IndexError, ValueError) as error:
        raise CdpUnavailable("CDP_ENDPOINT_INVALID") from error
    profile = Path(profile_dir).expanduser().resolve()
    profile.mkdir(parents=True, exist_ok=True)
    ownership_token = secrets.token_urlsafe(24)
    process = subprocess.Popen(
        [
            str(candidate.resolve()),
            f"--remote-debugging-port={port}",
            "--remote-debugging-address=127.0.0.1",
            f"--user-data-dir={profile}",
            material_center_url,
        ],
        cwd=str(profile),
        close_fds=True,
    )
    state = {
        "schema_version": 1,
        "pid": process.pid,
        "ownership_token": ownership_token,
        "endpoint": endpoint,
        "material_center_url": material_center_url,
        "started_at": iso_timestamp(),
    }
    temporary = profile / ".tmall-cdp-service.json.tmp"
    target = profile / ".tmall-cdp-service.json"
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, target)
    return {**state, "status": "starting", "reused": False}


def ensure_cdp_browser(
    *,
    executable: Path | None,
    profile_dir: Path,
    cdp_url: str,
    material_center_url: str,
    startup_timeout_seconds: float = 10.0,
) -> dict[str, object]:
    """Reuse or launch the dedicated CDP browser and wait for its endpoint."""

    launch = launch_cdp_browser(
        executable=executable,
        profile_dir=profile_dir,
        cdp_url=cdp_url,
        material_center_url=material_center_url,
    )
    if launch.get("status") == "connected":
        return launch
    deadline = time.monotonic() + max(0.1, startup_timeout_seconds)
    while time.monotonic() < deadline:
        status = inspect_cdp_endpoint(cdp_url, timeout_seconds=0.25)
        if status.connected:
            return {
                **launch,
                "status": "connected",
                "endpoint": status.endpoint,
                "pages": list(status.pages),
            }
        time.sleep(0.1)
    raise CdpUnavailable(
        "CDP_START_TIMEOUT: browser launched but endpoint is unavailable"
    )


def assert_store_identity(page, selector: str, expected_store: str) -> str:
    locator = page.locator(selector)
    actual = locator.inner_text().strip() if locator.count() else ""
    if actual != expected_store.strip():
        raise StoreIdentityError(f"目标店铺={expected_store.strip()}; 当前店铺={actual or '<missing>'}")
    return actual


def detect_human_check(page, selector: str) -> None:
    locator = page.locator(selector)
    if locator.is_visible():
        raise HumanCheckRequired("检测到验证码、扫码、短信或风控页面，需要用户处理")


def validate_collection_page(
    page,
    selectors: Mapping[str, str],
    *,
    expected_store: str,
    profile_name: str,
    profile_version: str,
    profile_sha256: str,
) -> dict[str, object]:
    """Validate live store/page identity before collection and return safe evidence."""

    checked_at = iso_timestamp()
    url = str(getattr(page, "url", "") or "")
    human_selector = str(selectors.get("human_check", "")).strip()
    if human_selector:
        detect_human_check(page, human_selector)
    store_selector = str(selectors.get("store_name", "")).strip()
    store_locator = page.locator(store_selector)
    if not store_selector or not store_locator.count():
        raise LoginInteractionRequired(
            "LOGIN_INTERACTION_REQUIRED: visible store identity is unavailable"
        )
    if not str(store_locator.inner_text()).strip():
        raise LoginInteractionRequired(
            "LOGIN_INTERACTION_REQUIRED: visible store identity is empty"
        )
    observed_store = assert_store_identity(
        page, store_selector, expected_store
    )
    if "material-center" not in url and "material-management" not in url:
        raise LoginInteractionRequired(
            "MATERIAL_PAGE_REQUIRED: navigate to the official material center"
        )
    field_results: dict[str, str] = {}
    for field in (
        "promotion_tab",
        "high_value_filter",
        "promotion_rows",
        "promotion_next_page",
    ):
        selector = str(selectors.get(field, "")).strip()
        field_results[field] = (
            "configured" if selector else "missing"
        )
    return {
        "schema_version": 1,
        "target_store": expected_store.strip(),
        "observed_store": observed_store,
        "store_match": True,
        "page_url": url,
        "page_identity": "material_center",
        "selector_profile": {
            "name": profile_name,
            "version": profile_version,
            "sha256": profile_sha256,
            "purpose": "high_value_collection",
        },
        "field_results": field_results,
        "verified_at": checked_at,
    }


@contextmanager
def open_cdp_page(cdp_url: str, material_center_url: str | None = None):
    """Connect to a user-launched Chromium session without persisting credentials."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(cdp_url)
        if not browser.contexts:
            raise RuntimeError("CDP 浏览器没有可用上下文")
        context = browser.contexts[0]
        page = context.pages[-1] if context.pages else context.new_page()
        if material_center_url and (
            not str(page.url).strip()
            or str(page.url).startswith(("about:", "chrome:", "edge:"))
        ):
            page.goto(material_center_url)
        yield page
