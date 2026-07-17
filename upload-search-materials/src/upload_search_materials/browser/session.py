from contextlib import contextmanager

from playwright.sync_api import sync_playwright


class StoreIdentityError(RuntimeError):
    pass


class HumanCheckRequired(RuntimeError):
    pass


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


@contextmanager
def open_cdp_page(cdp_url: str):
    """Connect to a user-launched Chromium session without persisting credentials."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(cdp_url)
        if not browser.contexts:
            raise RuntimeError("CDP 浏览器没有可用上下文")
        context = browser.contexts[0]
        page = context.pages[-1] if context.pages else context.new_page()
        yield page
