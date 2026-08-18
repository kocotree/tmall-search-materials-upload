from dataclasses import asdict, dataclass
import hashlib
import re
from typing import Any, Callable, Iterable, Mapping

from playwright.sync_api import Error as PlaywrightError


class SelectorInvalidError(RuntimeError):
    pass


class ProductIdentityError(RuntimeError):
    pass


class PaginationStateError(SelectorInvalidError):
    """Stable fail-closed pagination contract failure."""


PAGINATION_RECOVERY_GUIDANCE = {
    "PAGINATION_ORIGIN_UNVERIFIED": (
        "无法确认当前分页位置，请重新验证本机选择器后重试。"
    ),
    "PAGINATION_ORIGIN_RESET_FAILED": (
        "无法返回并稳定在第 1 页，请保持素材中心页面打开后重试。"
    ),
    "PAGINATION_CHECKPOINT_MISMATCH": (
        "页面商品顺序与采集断点不一致，请保留证据并开始新的采集。"
    ),
    "PAGINATION_TRANSITION_MISMATCH": (
        "翻页后页码或商品没有按预期变化，请检查页面加载状态后重试。"
    ),
    "PAGINATION_TERMINAL_UNVERIFIED": (
        "下一页不可用，但无法证明当前确为末页，采集已安全停止。"
    ),
}
HIGH_VALUE_TRANSITION_TIMEOUT_MS = 15_000
PAGINATION_RECOVERY_GUIDANCE.update(
    {
        "HIGH_VALUE_REFRESH_FAILED": (
            "The material-center page could not be refreshed before collection."
        ),
        "HIGH_VALUE_TOTAL_UNVERIFIED": (
            "The total shown by the high-value filter could not be read."
        ),
        "HIGH_VALUE_TOTAL_MISMATCH": (
            "The collected unique-product count does not match the total shown "
            "by the high-value filter."
        ),
    }
)
PAGINATION_SELECTOR_FIELDS = (
    "promotion_current_page",
    "promotion_first_page",
    "promotion_terminal_page",
)


@dataclass(frozen=True)
class PaginationState:
    current_page: int
    terminal_page: int
    next_enabled: bool
    ordered_product_id_hash: str
    product_count: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _pagination_error(code: str, detail: str = "") -> PaginationStateError:
    suffix = f":{detail}" if detail else ""
    return PaginationStateError(f"{code}{suffix}")


def _refresh_high_value_collection_page(page, *, action_wait_ms: int) -> None:
    """Clear stale SPA search state before a full high-value scan."""

    try:
        page.reload(wait_until="domcontentloaded")
    except (AttributeError, PlaywrightError) as error:
        raise _pagination_error("HIGH_VALUE_REFRESH_FAILED") from error
    if action_wait_ms:
        page.wait_for_timeout(action_wait_ms)


def _wait_for_nonempty_promotion_rows(
    page,
    selector: str,
    *,
    timeout_ms: int,
    poll_ms: int = 250,
) -> list[str]:
    """Wait for the SPA table to hydrate after refresh or tab selection."""

    elapsed_ms = 0
    last_error: Exception | None = None
    while True:
        try:
            rows = [
                str(value).strip()
                for value in page.locator(selector).all_inner_texts()
                if str(value).strip()
            ]
        except (AttributeError, PlaywrightError) as error:
            rows = []
            last_error = error
        if rows:
            return rows
        if elapsed_ms >= timeout_ms:
            break
        delay_ms = min(poll_ms, timeout_ms - elapsed_ms)
        if delay_ms <= 0:
            break
        page.wait_for_timeout(delay_ms)
        elapsed_ms += delay_ms
    raise SelectorInvalidError("promotion_tab:outcome_unchanged") from last_error


def _high_value_total(category_filter) -> int:
    """Read the total embedded in labels such as ``搜推高价值 262``."""

    try:
        text = str(category_filter.inner_text() or "").strip()
    except (AttributeError, PlaywrightError) as error:
        raise _pagination_error("HIGH_VALUE_TOTAL_UNVERIFIED") from error
    numbers = re.findall(r"\d[\d,]*", text)
    if not numbers:
        raise _pagination_error(
            "HIGH_VALUE_TOTAL_UNVERIFIED", f"label={text!r}"
        )
    return int(numbers[-1].replace(",", ""))


def _single_locator(page, selectors: Mapping[str, str], field: str):
    selector = str(selectors.get(field, "")).strip()
    if not selector:
        raise _pagination_error("PAGINATION_ORIGIN_UNVERIFIED", field)
    locator = page.locator(selector)
    try:
        count = int(locator.count())
    except (AttributeError, PlaywrightError) as error:
        raise _pagination_error(
            "PAGINATION_ORIGIN_UNVERIFIED", field
        ) from error
    if count != 1:
        raise _pagination_error(
            "PAGINATION_ORIGIN_UNVERIFIED", f"{field}:count={count}"
        )
    return locator


def _page_number(locator, field: str) -> int:
    candidates: list[str] = []
    try:
        candidates.append(str(locator.inner_text() or ""))
    except (AttributeError, PlaywrightError):
        pass
    for attribute in ("aria-label", "title", "data-page"):
        try:
            candidates.append(str(locator.get_attribute(attribute) or ""))
        except (AttributeError, PlaywrightError):
            continue
    for candidate in candidates:
        match = re.search(r"\d+", candidate)
        if match and int(match.group(0)) > 0:
            return int(match.group(0))
    raise _pagination_error(
        "PAGINATION_ORIGIN_UNVERIFIED", f"{field}:page_number"
    )


def _ordered_product_ids(row_texts: Iterable[str]) -> tuple[str, ...]:
    return tuple(
        match.group(1)
        for text in row_texts
        if (match := re.search(r"商品ID\s*(\d+)", str(text)))
    )


def _product_id_hash(product_ids: Iterable[str]) -> str:
    joined = "\0".join(str(value) for value in product_ids)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def read_pagination_state(
    page,
    selectors: Mapping[str, str],
) -> PaginationState:
    """Read one bounded, non-mutating pagination observation."""

    current = _single_locator(page, selectors, "promotion_current_page")
    _single_locator(page, selectors, "promotion_first_page")
    terminal = _single_locator(page, selectors, "promotion_terminal_page")
    next_page = _single_locator(page, selectors, "promotion_next_page")
    rows_selector = str(selectors.get("promotion_rows", "")).strip()
    if not rows_selector:
        raise _pagination_error(
            "PAGINATION_ORIGIN_UNVERIFIED", "promotion_rows"
        )
    rows = page.locator(rows_selector)
    try:
        row_count = int(rows.count())
    except (AttributeError, PlaywrightError) as error:
        raise _pagination_error(
            "PAGINATION_ORIGIN_UNVERIFIED", "promotion_rows"
        ) from error
    if row_count < 1:
        raise _pagination_error(
            "PAGINATION_ORIGIN_UNVERIFIED", "promotion_rows:count=0"
        )
    try:
        row_texts = [
            str(value).strip()
            for value in rows.all_inner_texts()
            if str(value).strip()
        ]
    except (AttributeError, PlaywrightError) as error:
        raise _pagination_error(
            "PAGINATION_ORIGIN_UNVERIFIED", "promotion_rows"
        ) from error
    product_ids = _ordered_product_ids(row_texts)
    if not product_ids:
        raise _pagination_error(
            "PAGINATION_ORIGIN_UNVERIFIED", "promotion_product_ids"
        )
    current_page = _page_number(current, "promotion_current_page")
    terminal_page = _page_number(terminal, "promotion_terminal_page")
    if current_page > terminal_page:
        raise _pagination_error(
            "PAGINATION_ORIGIN_UNVERIFIED",
            f"current={current_page}:terminal={terminal_page}",
        )
    try:
        next_enabled = bool(next_page.is_enabled())
    except (AttributeError, PlaywrightError) as error:
        raise _pagination_error(
            "PAGINATION_ORIGIN_UNVERIFIED", "promotion_next_page"
        ) from error
    return PaginationState(
        current_page=current_page,
        terminal_page=terminal_page,
        next_enabled=next_enabled,
        ordered_product_id_hash=_product_id_hash(product_ids),
        product_count=len(product_ids),
    )


def _wait_for_stable_pagination(
    page,
    selectors: Mapping[str, str],
    *,
    expected_page: int,
    action_wait_ms: int,
    failure_code: str,
    different_from_hash: str | None = None,
) -> PaginationState:
    poll_ms = 250
    elapsed_ms = 0
    prior: PaginationState | None = None
    stable_checks = 0
    last_error: PaginationStateError | None = None
    while elapsed_ms <= max(action_wait_ms, poll_ms):
        try:
            observed = read_pagination_state(page, selectors)
        except PaginationStateError as error:
            last_error = error
            stable_checks = 0
        else:
            identity_changed = (
                not different_from_hash
                or observed.ordered_product_id_hash != different_from_hash
            )
            if (
                observed.current_page == expected_page
                and identity_changed
                and observed == prior
            ):
                stable_checks += 1
            elif observed.current_page == expected_page and identity_changed:
                stable_checks = 1
            else:
                stable_checks = 0
            prior = observed
            if stable_checks >= 2:
                return observed
        page.wait_for_timeout(poll_ms)
        elapsed_ms += poll_ms
    detail = str(last_error or f"expected_page={expected_page}")
    raise _pagination_error(failure_code, detail)


def normalize_pagination_origin(
    page,
    selectors: Mapping[str, str],
    *,
    action_wait_ms: int,
    settle_delay_ms: int,
) -> PaginationState:
    """Prove page 1 before any rows are emitted or checkpointed."""

    initial = read_pagination_state(page, selectors)
    reset_required = initial.current_page != 1
    if reset_required:
        first_page = _single_locator(
            page, selectors, "promotion_first_page"
        )
        _click_with_popup_retries(
            page,
            first_page,
            dict(selectors),
            field_name="promotion_first_page",
            delay_ms=settle_delay_ms,
        )
    return _wait_for_stable_pagination(
        page,
        selectors,
        expected_page=1,
        action_wait_ms=action_wait_ms,
        failure_code="PAGINATION_ORIGIN_RESET_FAILED",
        different_from_hash=(
            initial.ordered_product_id_hash if reset_required else None
        ),
    )


VISIBLE_MATERIAL_WARNINGS = (
    "重复或图片有删除",
    "素材获流风险",
    "标题无意义",
    "审核不通过",
)
READ_ONLY_FORCE_TARGETS = frozenset(
    {
        "promotion_tab",
        "high_value_filter",
        "promotion_first_page",
        "promotion_next_page",
    }
)
RECOGNIZED_GUIDE_KEYS = (
    "safe_popup_progress",
    "safe_popup_close_priority",
)
OVERLAY_INTERCEPTION_MARKERS = (
    "intercept",
    "overlay",
    "joyride",
    "guide",
    "pointer event",
)


def _settle_safe_popups(
    page,
    selectors: dict[str, str],
    *,
    delay_ms: int,
    quiet_checks_required: int = 3,
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
            priority_selector,
            progress_selector,
            fallback_selector,
        )
        if value
    ]
    if not popup_selectors:
        return 0
    events = getattr(page, "_tmall_collection_events", None)
    if not isinstance(events, list):
        events = []
        setattr(page, "_tmall_collection_events", events)
    closed = 0
    quiet_checks = 0
    required_quiet_checks = max(1, int(quiet_checks_required))
    no_change_by_control: dict[tuple[int, str], int] = {}
    for _ in range(20):
        found = False
        scopes = [page]
        for frame in list(getattr(page, "frames", ()) or ()):
            if frame is not None and all(frame is not scope for scope in scopes):
                scopes.append(frame)
        for scope_index, scope in enumerate(scopes):
            for popup_selector in popup_selectors:
                control_key = (id(scope), popup_selector)
                if no_change_by_control.get(control_key, 0) >= 2:
                    continue
                try:
                    locator = scope.locator(popup_selector)
                    count = int(locator.count())
                except (AttributeError, PlaywrightError):
                    continue
                for index in range(count):
                    candidate = locator.nth(index)
                    try:
                        visible = candidate.is_visible()
                    except PlaywrightError:
                        continue
                    if not visible:
                        continue
                    try:
                        before_text = candidate.inner_text().strip()
                    except (AttributeError, PlaywrightError):
                        before_text = ""
                    before = {
                        "selector": popup_selector,
                        "index": index,
                        "scope": "page" if scope_index == 0 else "frame",
                        "text": before_text[:120],
                    }
                    click_mode = "normal"
                    try:
                        candidate.click(timeout=1500)
                    except PlaywrightError:
                        try:
                            candidate.click(force=True, timeout=1500)
                            click_mode = "force"
                        except PlaywrightError:
                            try:
                                candidate.evaluate("element => element.click()")
                                click_mode = "dom"
                            except (AttributeError, PlaywrightError):
                                no_change_by_control[control_key] = (
                                    no_change_by_control.get(control_key, 0) + 1
                                )
                                found = True
                                break
                    if delay_ms:
                        page.wait_for_timeout(min(delay_ms, 300))
                    try:
                        after_locator = scope.locator(popup_selector)
                        after_count = int(after_locator.count())
                    except (AttributeError, PlaywrightError):
                        after_count = 0
                    after_visible = False
                    after_text = ""
                    if index < after_count:
                        after_candidate = after_locator.nth(index)
                        try:
                            after_visible = after_candidate.is_visible()
                            if after_visible:
                                after_text = after_candidate.inner_text().strip()
                        except (AttributeError, PlaywrightError):
                            after_visible = False
                    changed = (
                        not after_visible
                        or after_text[:120] != before["text"]
                    )
                    events.append(
                        {
                            "action": "close_safe_popup",
                            "target_field": popup_selector,
                            "before": before,
                            "after": {
                                "visible": after_visible,
                                "text": after_text[:120],
                            },
                            "changed": changed,
                            "click_mode": click_mode,
                        }
                    )
                    if changed:
                        no_change_by_control[control_key] = 0
                        closed += 1
                    else:
                        no_change_by_control[control_key] = (
                            no_change_by_control.get(control_key, 0) + 1
                        )
                    found = True
                    break
                if found:
                    break
            if found:
                break
        quiet_checks = 0 if found else quiet_checks + 1
        if quiet_checks >= required_quiet_checks:
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
    try:
        locator.click(timeout=3000)
        return
    except PlaywrightError as error:
        last_error = error
    error_summary = str(last_error).casefold()
    overlay_identified = any(
        marker in error_summary for marker in OVERLAY_INTERCEPTION_MARKERS
    )
    if overlay_identified:
        _settle_safe_popups(page, selectors, delay_ms=delay_ms)
        try:
            locator.click(timeout=3000)
            return
        except PlaywrightError as error:
            last_error = error
    recognized_guide_configured = any(
        str(selectors.get(key, "")).strip()
        for key in RECOGNIZED_GUIDE_KEYS
    )
    error_summary = str(last_error or "").casefold()
    overlay_identified = any(
        marker in error_summary for marker in OVERLAY_INTERCEPTION_MARKERS
    )
    if (
        field_name in READ_ONLY_FORCE_TARGETS
        and recognized_guide_configured
        and overlay_identified
    ):
        try:
            locator.evaluate("element => element.click()")
            events = getattr(page, "_tmall_collection_events", None)
            if not isinstance(events, list):
                events = []
                setattr(page, "_tmall_collection_events", events)
            events.append(
                {
                    "action": "dom_click",
                    "target_field": field_name,
                    "reason": "recognized_guide_interceptor",
                    "read_only": True,
                }
            )
            return
        except PlaywrightError as error:
            last_error = error
    raise SelectorInvalidError(f"{field_name}:popup_blocked") from last_error


def _promotion_tab_is_already_open(page, selectors: dict[str, str]) -> bool:
    """Avoid a redundant tab click after refresh restored the target route."""

    tab = page.locator(selectors["promotion_tab"])
    try:
        selected = tab.get_attribute("aria-selected") == "true"
        class_names = set((tab.get_attribute("class") or "").split())
        route_selected = "tab=recommend" in str(getattr(page, "url", ""))
        has_rows = any(
            str(value).strip()
            for value in page.locator(selectors["promotion_rows"]).all_inner_texts()
        )
    except (AttributeError, PlaywrightError):
        return False
    return has_rows and (
        selected
        or route_selected
        or bool(class_names.intersection({"active", "selected", "checked"}))
    )


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
        "精确坑位状态": "not_collected",
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
    initial_rows: Iterable[dict[str, str]] = (),
    skip_completed_pages: int = 0,
    on_phase: Callable[[str, int | None], None] | None = None,
    on_pagination_event: Callable[[dict[str, Any]], None] | None = None,
    expected_page_hashes: Mapping[int, str] | None = None,
    human_check_waiter: Callable[[int, str], None] | None = None,
    random_action: Callable[[int], Mapping[str, Any] | None] | None = None,
    random_interval_picker: Callable[[], int] | None = None,
) -> list[dict[str, str]]:
    required = (
        "promotion_tab",
        filter_selector_key,
        "promotion_rows",
        "promotion_next_page",
    )
    if filter_selector_key == "high_value_filter":
        required = (*required, *PAGINATION_SELECTOR_FIELDS)
    missing = [key for key in required if not str(selectors.get(key, "")).strip()]
    if missing:
        raise SelectorInvalidError(",".join(missing))

    expected_high_value_total: int | None = None
    if filter_selector_key == "high_value_filter":
        _refresh_high_value_collection_page(
            page,
            action_wait_ms=action_wait_ms,
        )

    _settle_safe_popups(page, selectors, delay_ms=settle_delay_ms)

    if on_phase is not None:
        on_phase("opening_promotion", None)
    if not _promotion_tab_is_already_open(page, selectors):
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
    _wait_for_nonempty_promotion_rows(
        page,
        selectors["promotion_rows"],
        timeout_ms=max(action_wait_ms, 15_000) if action_wait_ms else 0,
    )

    if on_phase is not None:
        on_phase("selecting_high_value", None)
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
    if filter_selector_key == "high_value_filter":
        expected_high_value_total = _high_value_total(category_filter)

    pagination_state: PaginationState | None = None
    if filter_selector_key == "high_value_filter":
        try:
            pagination_state = normalize_pagination_origin(
                page,
                selectors,
                action_wait_ms=action_wait_ms,
                settle_delay_ms=settle_delay_ms,
            )
        except PaginationStateError as error:
            if on_pagination_event is not None:
                on_pagination_event(
                    {
                        "event_type": "failure",
                        "reason_code": str(error).split(":", 1)[0],
                        "detail": str(error),
                    }
                )
            raise
        if on_pagination_event is not None:
            on_pagination_event(
                {
                    "event_type": "origin",
                    "reason_code": "PAGINATION_ORIGIN_VERIFIED",
                    **pagination_state.as_dict(),
                    **(
                        {
                            "expected_product_count": (
                                expected_high_value_total
                            )
                        }
                        if expected_high_value_total is not None
                        else {}
                    ),
                }
            )

    output_by_product: dict[str, dict[str, str]] = {
        str(row.get("商品ID", "")): dict(row)
        for row in initial_rows
        if str(row.get("商品ID", "")).strip()
    }
    page_number = 0
    next_random_action_page = (
        skip_completed_pages + max(1, int(random_interval_picker()))
        if random_action is not None and random_interval_picker is not None
        else None
    )
    while True:
        page_number += 1
        _settle_safe_popups(page, selectors, delay_ms=0)
        if pagination_state is not None:
            if pagination_state.current_page != page_number:
                raise _pagination_error(
                    "PAGINATION_TRANSITION_MISMATCH",
                    (
                        f"internal={page_number}:"
                        f"observed={pagination_state.current_page}"
                    ),
                )
            expected_hash = str(
                (expected_page_hashes or {}).get(page_number, "")
            ).strip()
            if (
                page_number <= skip_completed_pages
                and (
                    not expected_hash
                    or expected_hash
                    != pagination_state.ordered_product_id_hash
                )
            ):
                raise _pagination_error(
                    "PAGINATION_CHECKPOINT_MISMATCH",
                    f"page={page_number}",
                )
        if human_check_waiter is not None:
            human_check_waiter(page_number, "before_page")
        if on_phase is not None:
            on_phase("collecting_page", page_number)
        row_texts = [
            text.strip()
            for text in page.locator(selectors["promotion_rows"]).all_inner_texts()
            if text.strip()
        ]
        if not row_texts:
            raise SelectorInvalidError("promotion_rows")
        if page_number > skip_completed_pages:
            for row_text in row_texts:
                if re.search(
                    r"商品ID\s*x{4,}", row_text, flags=re.IGNORECASE
                ):
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
            if human_check_waiter is not None and on_page is not None:
                human_check_waiter(page_number, "after_checkpoint")
            if (
                random_action is not None
                and on_page is not None
                and next_random_action_page is not None
                and page_number >= next_random_action_page
            ):
                event = dict(random_action(page_number) or {})
                events = getattr(page, "_tmall_collection_events", None)
                if not isinstance(events, list):
                    events = []
                    setattr(page, "_tmall_collection_events", events)
                events.append(
                    {
                        "action": "random_collection_action",
                        "page_number": page_number,
                        **event,
                    }
                )
                interval = (
                    max(1, int(random_interval_picker()))
                    if random_interval_picker is not None
                    else 1
                )
                next_random_action_page = page_number + interval
                if event.get("page_state_restored") is False:
                    detail = str(event.get("detail", "")).strip()
                    raise _pagination_error(
                        "RANDOM_ACTION_PAGE_RESTORE_FAILED",
                        ";".join(
                            value
                            for value in (
                                f"page={page_number}",
                                detail,
                            )
                            if value
                        ),
                    )
                if human_check_waiter is not None:
                    human_check_waiter(page_number, "after_random_action")
        if max_pages is not None and page_number >= max_pages:
            break
        if human_check_waiter is not None:
            human_check_waiter(page_number, "before_pagination")
        _settle_safe_popups(page, selectors, delay_ms=0)
        next_page = page.locator(selectors["promotion_next_page"])
        if not next_page.is_enabled():
            if pagination_state is not None:
                try:
                    terminal_state = _wait_for_stable_pagination(
                        page,
                        selectors,
                        expected_page=page_number,
                        action_wait_ms=action_wait_ms,
                        failure_code="PAGINATION_TERMINAL_UNVERIFIED",
                    )
                except PaginationStateError as error:
                    if on_pagination_event is not None:
                        on_pagination_event(
                            {
                                "event_type": "failure",
                                "reason_code": str(error).split(":", 1)[0],
                                "detail": str(error),
                            }
                        )
                    raise
                if terminal_state.next_enabled:
                    pagination_state = terminal_state
                elif (
                    terminal_state.current_page
                    != terminal_state.terminal_page
                ):
                    raise _pagination_error(
                        "PAGINATION_TERMINAL_UNVERIFIED",
                        (
                            f"current={terminal_state.current_page}:"
                            f"terminal={terminal_state.terminal_page}:"
                            f"next={terminal_state.next_enabled}"
                        ),
                    )
                elif on_pagination_event is not None:
                    on_pagination_event(
                        {
                            "event_type": "terminal",
                            "reason_code": "PAGINATION_TERMINAL_VERIFIED",
                            **terminal_state.as_dict(),
                        }
                    )
                if not terminal_state.next_enabled:
                    break
            else:
                break
        previous_state = pagination_state
        _click_with_popup_retries(
            page,
            next_page,
            selectors,
            field_name="promotion_next_page",
            delay_ms=settle_delay_ms,
        )
        previous_ids = tuple(
            match.group(1)
            for text in row_texts
            if (match := re.search(r"商品ID\s*(\d+)", text))
        )
        poll_ms = 250
        elapsed_ms = 0
        transition_timeout_ms = (
            max(action_wait_ms, HIGH_VALUE_TRANSITION_TIMEOUT_MS)
            if pagination_state is not None
            else action_wait_ms
        )
        while True:
            _settle_safe_popups(page, selectors, delay_ms=0)
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
            if elapsed_ms >= transition_timeout_ms:
                raise _pagination_error(
                    "PAGINATION_TRANSITION_MISMATCH",
                    "promotion_next_page:rows_unchanged",
                )
            page.wait_for_timeout(poll_ms)
            elapsed_ms += poll_ms
        if previous_state is not None:
            pagination_state = _wait_for_stable_pagination(
                page,
                selectors,
                expected_page=page_number + 1,
                action_wait_ms=action_wait_ms,
                failure_code="PAGINATION_TRANSITION_MISMATCH",
            )
            if (
                pagination_state.ordered_product_id_hash
                == previous_state.ordered_product_id_hash
            ):
                raise _pagination_error(
                    "PAGINATION_TRANSITION_MISMATCH",
                    f"page={page_number + 1}:identity_unchanged",
                )
            if on_pagination_event is not None:
                on_pagination_event(
                    {
                        "event_type": "transition",
                        "reason_code": "PAGINATION_TRANSITION_VERIFIED",
                        "from_page": page_number,
                        **pagination_state.as_dict(),
                    }
                )
    output = list(output_by_product.values())
    if (
        expected_high_value_total is not None
        and max_pages is None
        and len(output) != expected_high_value_total
    ):
        error = _pagination_error(
            "HIGH_VALUE_TOTAL_MISMATCH",
            (
                f"expected={expected_high_value_total}:"
                f"collected={len(output)}"
            ),
        )
        if on_pagination_event is not None:
            on_pagination_event(
                {
                    "event_type": "failure",
                    "reason_code": "HIGH_VALUE_TOTAL_MISMATCH",
                    "detail": str(error),
                    "expected_product_count": expected_high_value_total,
                    "collected_product_count": len(output),
                }
            )
        raise error
    return output


def prepare_high_value_validation_page(
    page,
    selectors: Mapping[str, str],
    *,
    settle_delay_ms: int = 500,
    action_wait_ms: int = 3000,
) -> PaginationState:
    """Navigate the read-only material view to high-value page 1 for validation."""

    required = (
        "promotion_tab",
        "high_value_filter",
        "promotion_rows",
        "promotion_next_page",
        *PAGINATION_SELECTOR_FIELDS,
    )
    missing = [
        key for key in required if not str(selectors.get(key, "")).strip()
    ]
    if missing:
        raise SelectorInvalidError(",".join(missing))
    selected = dict(selectors)
    _settle_safe_popups(page, selected, delay_ms=settle_delay_ms)
    category_filter = page.locator(selected["high_value_filter"])
    filter_count = category_filter.count()
    checked = (
        category_filter.get_attribute("aria-checked")
        if filter_count == 1
        else None
    )
    class_name = (
        category_filter.get_attribute("class") or ""
        if filter_count == 1
        else ""
    )
    if filter_count != 1 or (
        checked != "true" and "checked" not in class_name.split()
    ):
        if not _promotion_tab_is_already_open(page, selected):
            _click_with_popup_retries(
                page,
                page.locator(selected["promotion_tab"]),
                selected,
                field_name="promotion_tab",
                delay_ms=settle_delay_ms,
            )
            if action_wait_ms:
                page.wait_for_timeout(action_wait_ms)
        category_filter = page.locator(selected["high_value_filter"])
        filter_count = category_filter.count()
    if filter_count != 1:
        raise SelectorInvalidError(
            f"high_value_filter:count={filter_count}"
        )
    checked = category_filter.get_attribute("aria-checked")
    class_name = category_filter.get_attribute("class") or ""
    if checked != "true" and "checked" not in class_name.split():
        try:
            category_filter.click(timeout=3000)
        except PlaywrightError:
            _click_with_popup_retries(
                page,
                category_filter,
                selected,
                field_name="high_value_filter",
                delay_ms=settle_delay_ms,
            )
        if action_wait_ms:
            page.wait_for_timeout(action_wait_ms)
    return normalize_pagination_origin(
        page,
        selected,
        action_wait_ms=action_wait_ms,
        settle_delay_ms=settle_delay_ms,
    )


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
                    "精确坑位状态": "collected",
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
                    "精确坑位状态": "unknown",
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
