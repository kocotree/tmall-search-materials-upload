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
