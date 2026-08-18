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
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
from urllib.request import build_opener, ProxyHandler, Request

from playwright.sync_api import Error as PlaywrightError, sync_playwright

from ..time_utils import iso_timestamp


class StoreIdentityError(RuntimeError):
    pass


class HumanCheckRequired(RuntimeError):
    pass


class LoginInteractionRequired(RuntimeError):
    pass


def recommendation_material_center_url(value: str) -> str:
    """Return the official material page with the recommendation tab selected."""

    parts = urlsplit(str(value).strip())
    query = [
        (key, item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
        if key.casefold() != "tab"
    ]
    query.append(("tab", "recommend"))
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )


def prepare_collection_page(
    page,
    material_center_url: str,
    selectors: Mapping[str, str],
) -> dict[str, object]:
    """Start loading the exact collection page and settle recognized safe guides."""

    target_url = recommendation_material_center_url(material_center_url)
    current_url = str(getattr(page, "url", "") or "").strip()
    if any(
        marker in current_url.casefold()
        for marker in ("login", "passport", "oauth", "authorize")
    ):
        return {"navigated": False, "login_required": True, "url": current_url}
    if current_url != target_url:
        page.goto(target_url, wait_until="domcontentloaded", timeout=15_000)
    from .material_page import _settle_safe_popups

    closed = _settle_safe_popups(page, dict(selectors), delay_ms=250)
    return {
        "navigated": current_url != target_url,
        "login_required": False,
        "closed_popups": closed,
        "url": str(getattr(page, "url", "") or target_url),
    }


class CdpUnavailable(RuntimeError):
    pass


_LOOPBACK_OPENER = build_opener(ProxyHandler({}))
_WINDOWS_BROWSER_CANDIDATES = (
    ("PROGRAMFILES", "Google/Chrome/Application/chrome.exe"),
    ("PROGRAMFILES(X86)", "Google/Chrome/Application/chrome.exe"),
    ("LOCALAPPDATA", "Google/Chrome/Application/chrome.exe"),
    ("PROGRAMFILES", "Microsoft/Edge/Application/msedge.exe"),
    ("PROGRAMFILES(X86)", "Microsoft/Edge/Application/msedge.exe"),
    ("LOCALAPPDATA", "Microsoft/Edge/Application/msedge.exe"),
)
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


def _open_cdp_target(
    cdp_url: str,
    target_url: str,
    *,
    timeout_seconds: float = 2.0,
) -> dict[str, str]:
    """Create the first visible page in an otherwise empty local CDP browser."""

    endpoint = str(cdp_url).rstrip("/")
    if not endpoint.startswith("http://127.0.0.1:"):
        raise CdpUnavailable("CDP_ENDPOINT_NOT_LOOPBACK")
    try:
        port = int(endpoint.rsplit(":", 1)[1])
    except (IndexError, ValueError) as error:
        raise CdpUnavailable("CDP_ENDPOINT_INVALID") from error
    if not 1 <= port <= 65535:
        raise CdpUnavailable("CDP_ENDPOINT_INVALID")
    request = Request(
        f"{endpoint}/json/new?{quote(str(target_url), safe='')}",
        method="PUT",
    )
    try:
        with _LOOPBACK_OPENER.open(
            request, timeout=timeout_seconds
        ) as response:
            document = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, ValueError, json.JSONDecodeError) as error:
        raise CdpUnavailable("CDP_PAGE_OPEN_FAILED") from error
    if not isinstance(document, Mapping) or document.get("type") != "page":
        raise CdpUnavailable("CDP_PAGE_OPEN_FAILED")
    return {
        "id": str(document.get("id", "")),
        "title": str(document.get("title", "")),
        "url": str(document.get("url", target_url)),
    }


def discover_browser_executable(
    environ: Mapping[str, str] | None = None,
) -> Path | None:
    """Find Chrome or Edge even when Windows did not add it to PATH."""

    discovered = shutil.which("chrome") or shutil.which("msedge")
    if discovered:
        return Path(discovered)
    env = os.environ if environ is None else environ
    for variable, relative in _WINDOWS_BROWSER_CANDIDATES:
        root = str(env.get(variable, "")).strip()
        if not root:
            continue
        candidate = Path(root) / Path(relative)
        if candidate.is_file():
            return candidate
    return None


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
        candidate = discover_browser_executable()
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
        start_new_session=False,
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
    deadline = time.monotonic() + max(0.1, startup_timeout_seconds)
    grace_deadline = time.monotonic() + (
        0.0 if launch.get("status") == "connected" else 0.5
    )
    target_requested = False
    saw_connected_endpoint = False
    initial_status = None
    if launch.get("status") == "connected":
        initial_status = CdpStatus(
            connected=True,
            endpoint=str(launch.get("endpoint", cdp_url)),
            pages=tuple(
                dict(page)
                for page in launch.get("pages", [])
                if isinstance(page, Mapping)
            ),
        )
    while time.monotonic() < deadline:
        status = initial_status or inspect_cdp_endpoint(
            cdp_url, timeout_seconds=0.25
        )
        initial_status = None
        if status.connected:
            saw_connected_endpoint = True
            if status.pages:
                return {
                    **launch,
                    "status": "connected",
                    "endpoint": status.endpoint,
                    "pages": list(status.pages),
                }
            if not target_requested and time.monotonic() >= grace_deadline:
                _open_cdp_target(cdp_url, material_center_url)
                target_requested = True
        time.sleep(0.1)
    if saw_connected_endpoint:
        raise CdpUnavailable(
            "CDP_PAGE_OPEN_TIMEOUT: endpoint connected but no page appeared"
        )
    raise CdpUnavailable(
        "CDP_START_TIMEOUT: browser launched but endpoint is unavailable"
    )


def assert_store_identity(page, selector: str, expected_store: str) -> str:
    locator = page.locator(selector)
    expected = expected_store.strip()
    observed: list[str] = []
    visible: list[str] = []
    for index in range(locator.count()):
        candidate = locator.nth(index) if locator.count() > 1 else locator
        text = candidate.inner_text().strip()
        if not text:
            continue
        observed.append(text)
        try:
            if candidate.is_visible():
                visible.append(text)
        except Exception:
            # Some lightweight page adapters do not expose visibility.  Their
            # text is still usable for the same exact-match check.
            visible.append(text)

    candidates = visible or observed
    if expected in candidates:
        return expected
    actual = " | ".join(dict.fromkeys(candidates))
    raise StoreIdentityError(
        f"目标店铺={expected}; 当前店铺={actual or '<missing>'}"
    )


def human_check_visible(page, selector: str) -> bool:
    """Return whether any configured human-verification surface is visible."""

    locator = page.locator(selector)
    try:
        count = locator.count()
    except (AttributeError, PlaywrightError):
        count = 1
    if count == 0:
        try:
            return bool(locator.is_visible())
        except PlaywrightError:
            return False
    for index in range(count):
        candidate = locator.nth(index) if count > 1 else locator
        try:
            if candidate.is_visible():
                return True
        except PlaywrightError:
            continue
    return False


def detect_human_check(page, selector: str) -> None:
    if human_check_visible(page, selector):
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
    field_results: dict[str, object] = {}
    for field in (
        "promotion_tab",
        "high_value_filter",
        "promotion_rows",
        "promotion_current_page",
        "promotion_first_page",
        "promotion_terminal_page",
        "promotion_next_page",
    ):
        selector = str(selectors.get(field, "")).strip()
        count = int(page.locator(selector).count()) if selector else 0
        field_results[field] = {
            "configured": bool(selector),
            "count": count,
        }
    pagination_state: dict[str, object]
    try:
        from .material_page import read_pagination_state

        observed_pagination = read_pagination_state(page, selectors)
    except RuntimeError as error:
        pagination_state = {
            "verified": False,
            "reason_code": str(error).split(":", 1)[0],
            "detail": str(error),
        }
    else:
        pagination_state = {
            "verified": True,
            **observed_pagination.as_dict(),
        }
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
        "pagination_state": pagination_state,
        "verified_at": checked_at,
    }


@contextmanager
def open_cdp_page(cdp_url: str, material_center_url: str | None = None):
    """Connect to a user-launched Chromium session without persisting credentials."""
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.connect_over_cdp(cdp_url)
        except PlaywrightError as error:
            raise CdpUnavailable(
                "CDP_UNAVAILABLE: open or restore the login browser"
            ) from error
        if not browser.contexts:
            raise RuntimeError("CDP 浏览器没有可用上下文")
        context = browser.contexts[0]
        pages = list(context.pages)
        page = None
        if material_center_url:
            page = next(
                (
                    candidate
                    for candidate in reversed(pages)
                    if "material-center" in str(candidate.url)
                    or "material-management" in str(candidate.url)
                ),
                None,
            )
        page = page or (pages[-1] if pages else context.new_page())
        current_url = str(page.url).strip()
        login_page = any(
            marker in current_url.casefold()
            for marker in ("login", "passport", "oauth", "authorize")
        )
        material_page = (
            "material-center" in current_url
            or "material-management" in current_url
        )
        if material_center_url and not material_page and not login_page:
            page.goto(material_center_url)
        yield page
