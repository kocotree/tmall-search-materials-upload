import re
from typing import Callable, Iterable

from playwright.sync_api import Error as PlaywrightError


class SelectorInvalidError(RuntimeError):
    pass


class ProductIdentityError(RuntimeError):
    pass


VISIBLE_MATERIAL_WARNINGS = (
    "重复或图片有删除",
    "素材获流风险",
    "标题无意义",
    "审核不通过",
)


def _settle_safe_popups(
    page,
    selectors: dict[str, str],
    *,
    delay_ms: int,
) -> int:
    progress_selector = str(
        selectors.get("safe_popup_progress", "")
    ).strip()
    fallback_selector = str(selectors.get("safe_popup_close", "")).strip()
    priority_selector = str(
        selectors.get("safe_popup_close_priority", "")
    ).strip()
    popup_selectors = [
        value
        for value in (
            progress_selector,
            priority_selector,
            fallback_selector,
        )
        if value
    ]
    if not popup_selectors:
        return 0
    closed = 0
    quiet_checks = 0
    for _ in range(30):
        found = False
        for popup_selector in popup_selectors:
            locator = page.locator(popup_selector)
            for index in range(locator.count()):
                candidate = locator.nth(index)
                try:
                    visible = candidate.is_visible()
                except PlaywrightError:
                    continue
                if not visible:
                    continue
                try:
                    candidate.click(force=True, timeout=1500)
                except PlaywrightError:
                    continue
                closed += 1
                found = True
                if delay_ms:
                    page.wait_for_timeout(min(delay_ms, 300))
                break
            if found:
                break
        quiet_checks = 0 if found else quiet_checks + 1
        if quiet_checks >= 3:
            break
        if delay_ms:
            page.wait_for_timeout(delay_ms)
    return closed


def _click_with_popup_retries(
    page,
    locator,
    selectors: dict[str, str],
    *,
    field_name: str,
    delay_ms: int,
) -> None:
    last_error = None
    for _ in range(5):
        _settle_safe_popups(page, selectors, delay_ms=delay_ms)
        try:
            locator.click(timeout=3000)
            return
        except PlaywrightError as error:
            last_error = error
    raise SelectorInvalidError(f"{field_name}:popup_blocked") from last_error


def _parse_promotion_row(
    text: str,
    *,
    collected_at: str,
    evidence_source: str = "recommended_promotion_dom",
) -> dict[str, str]:
    normalized = re.sub(r"\s+", " ", str(text)).strip()
    product_match = re.search(r"商品ID\s*(\d+)", normalized)
    if not product_match:
        raise SelectorInvalidError("promotion_product_id")
    product_id = product_match.group(1)
    material_ids = [
        material_id
        for material_id in dict.fromkeys(re.findall(r"\bID\s+(\d{6,})", normalized))
        if material_id != product_id
    ]
    target_match = re.search(r"(?:上调)?发布坑位(?:到|为)\s*(3|9)\s*篇", normalized)
    current_match = re.search(r"当前发布\s*(\d+)\s*篇", normalized)
    target = int(target_match.group(1)) if target_match else None
    current = int(current_match.group(1)) if current_match else len(material_ids)
    missing = max(target - current, 0) if target is not None else None
    warnings = [value for value in VISIBLE_MATERIAL_WARNINGS if value in normalized]

    if warnings:
        reason_code = "REMOTE_MATERIAL_WARNING"
    elif target is None:
        reason_code = "TARGET_CAPACITY_NOT_EXPLICIT"
    elif missing:
        reason_code = "PROMOTION_MATERIALS_MISSING"
    else:
        reason_code = ""

    status = "needs_manual_review" if reason_code else "ready_for_review"
    target_text = "" if target is None else str(target)
    missing_text = "" if missing is None else str(missing)
    warning_text = ";".join(warnings)
    material_id_text = ";".join(material_ids)
    return {
        "商品ID": product_id,
        "目标容量": target_text,
        "目标坑位": target_text,
        "现有素材数": str(current),
        "缺失数量": missing_text,
        "空坑位": "",
        "远端素材ID": material_id_text,
        "素材状态": warning_text,
        "审核状态": warning_text,
        "状态完整": "false",
        "审核状态完整": "false",
        "状态": status,
        "原因码": reason_code,
        "采集时间": collected_at,
        "证据": (
            f"product={product_id};source={evidence_source};"
            f"target={target_text or 'unknown'};current={current};"
            f"remote_ids={material_id_text or 'none'}"
        ),
    }


def scan_recommended_material_status(
    page,
    selectors: dict[str, str],
    *,
    collected_at: str,
    filter_selector_key: str = "recommended_filter",
    on_page: Callable[[int, list[dict[str, str]]], None] | None = None,
    max_pages: int | None = None,
    settle_delay_ms: int = 1000,
    action_wait_ms: int = 3000,
) -> list[dict[str, str]]:
    required = (
        "promotion_tab",
        filter_selector_key,
        "promotion_rows",
        "promotion_next_page",
    )
    missing = [key for key in required if not str(selectors.get(key, "")).strip()]
    if missing:
        raise SelectorInvalidError(",".join(missing))

    _click_with_popup_retries(
        page,
        page.locator(selectors["promotion_tab"]),
        selectors,
        field_name="promotion_tab",
        delay_ms=settle_delay_ms,
    )
    if action_wait_ms:
        page.wait_for_timeout(action_wait_ms)
    _settle_safe_popups(page, selectors, delay_ms=settle_delay_ms)

    category_filter = page.locator(selectors[filter_selector_key])
    checked = category_filter.get_attribute("aria-checked")
    class_name = category_filter.get_attribute("class") or ""
    if checked != "true" and "checked" not in class_name.split():
        _click_with_popup_retries(
            page,
            category_filter,
            selectors,
            field_name=filter_selector_key,
            delay_ms=settle_delay_ms,
        )
        if action_wait_ms:
            page.wait_for_timeout(action_wait_ms)
        _settle_safe_popups(page, selectors, delay_ms=settle_delay_ms)
        checked = category_filter.get_attribute("aria-checked")
        class_name = category_filter.get_attribute("class") or ""
    if checked != "true" and "checked" not in class_name.split():
        raise SelectorInvalidError(filter_selector_key)

    output_by_product: dict[str, dict[str, str]] = {}
    page_number = 0
    while True:
        page_number += 1
        row_texts = [
            text.strip()
            for text in page.locator(selectors["promotion_rows"]).all_inner_texts()
            if text.strip()
        ]
        if not row_texts:
            raise SelectorInvalidError("promotion_rows")
        for row_text in row_texts:
            if re.search(r"商品ID\s*x{4,}", row_text, flags=re.IGNORECASE):
                continue
            row = _parse_promotion_row(
                row_text,
                collected_at=collected_at,
                evidence_source=(
                    "search_recommend_high_value_dom"
                    if filter_selector_key == "high_value_filter"
                    else "recommended_promotion_dom"
                ),
            )
            output_by_product.setdefault(row["商品ID"], row)
        values = list(output_by_product.values())
        if on_page:
            on_page(page_number, values)
        if max_pages is not None and page_number >= max_pages:
            break
        next_page = page.locator(selectors["promotion_next_page"])
        if not next_page.is_enabled():
            break
        _click_with_popup_retries(
            page,
            next_page,
            selectors,
            field_name="promotion_next_page",
            delay_ms=settle_delay_ms,
        )
        _settle_safe_popups(page, selectors, delay_ms=settle_delay_ms)
        previous_ids = tuple(
            match.group(1)
            for text in row_texts
            if (match := re.search(r"商品ID\s*(\d+)", text))
        )
        poll_ms = 250
        elapsed_ms = 0
        while True:
            next_row_texts = [
                text.strip()
                for text in page.locator(selectors["promotion_rows"]).all_inner_texts()
                if text.strip()
            ]
            next_ids = tuple(
                match.group(1)
                for text in next_row_texts
                if (match := re.search(r"商品ID\s*(\d+)", text))
            )
            if next_ids and next_ids != previous_ids:
                break
            if elapsed_ms >= action_wait_ms:
                raise SelectorInvalidError("promotion_next_page:rows_unchanged")
            page.wait_for_timeout(poll_ms)
            elapsed_ms += poll_ms
    return list(output_by_product.values())


def _required_text(page, selector: str, field_name: str) -> str:
    locator = page.locator(selector)
    if not locator.count():
        raise SelectorInvalidError(field_name)
    value = locator.inner_text().strip()
    if not value:
        raise SelectorInvalidError(field_name)
    return value


def supplement_material_status(
    page,
    selectors: dict[str, str],
    product_ids: Iterable[str],
    *,
    collected_at: str,
) -> list[dict[str, str]]:
    output = []
    for product_id in product_ids:
        try:
            search = page.locator(selectors["product_search"])
            search.fill(product_id)
            search.press("Enter")
            actual_product_id = _required_text(page, selectors["product_id"], "product_id")
            if actual_product_id != product_id:
                raise ProductIdentityError(f"目标商品={product_id}; 页面商品={actual_product_id}")
            desired_text = _required_text(page, selectors["desired_slots"], "desired_slots")
            match = re.search(r"(?:^|\D)(3|9)(?:\D|$)", desired_text)
            if not match:
                raise SelectorInvalidError("desired_slots")
            desired_slots = int(match.group(1))
            table = page.locator(selectors["material_table"])
            if not table.count() or not table.is_visible():
                raise SelectorInvalidError("material_table")
            material_count = page.locator(selectors["material_rows"]).count()
            empty_texts = page.locator(selectors["empty_slots"]).all_inner_texts()
            empty_indexes = [
                int(number)
                for text in empty_texts
                for number in re.findall(r"\d+", text)
            ]
            if material_count < desired_slots and not empty_indexes:
                raise SelectorInvalidError("empty_slots")
            review_states = [
                value.strip()
                for value in page.locator(selectors["review_status"]).all_inner_texts()
                if value.strip()
            ]
            if len(review_states) != material_count:
                raise SelectorInvalidError("review_status")
            output.append(
                {
                    "商品ID": product_id,
                    "目标坑位": str(desired_slots),
                    "现有素材数": str(material_count),
                    "空坑位": ";".join(str(index) for index in empty_indexes),
                    "审核状态": ";".join(review_states),
                    "审核状态完整": "true",
                    "状态": "ready_for_review",
                    "原因码": "",
                    "采集时间": collected_at,
                    "证据": f"product={product_id};rows={material_count};selector_version=runtime",
                }
            )
        except SelectorInvalidError as error:
            output.append(
                {
                    "商品ID": product_id,
                    "目标坑位": "",
                    "现有素材数": "",
                    "空坑位": "",
                    "审核状态": "",
                    "审核状态完整": "false",
                    "状态": "needs_manual_review",
                    "原因码": "SELECTOR_INVALID",
                    "采集时间": collected_at,
                    "证据": (
                        f"product={product_id};failed_field={error};"
                        "selector_version=runtime"
                    ),
                }
            )
    return output
