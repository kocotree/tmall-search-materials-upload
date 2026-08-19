"""Side-effect-free random actions on the currently collected page."""

from __future__ import annotations

import random
import re
from typing import Any, Callable, Mapping

from .material_page import _settle_safe_popups

from .qianniu_copy import (
    PUBLISH_FRAME_FRAGMENT,
    QianniuCopyError,
    _empty_slot_positions,
    _page_scopes,
    _slot_cells,
)


RandomSource = random.Random | random.SystemRandom
HumanCheckWaiter = Callable[[Any, str], None]

PAGE_RESTORE_TIMEOUT_MS = 30_000
PAGE_RESTORE_POLL_INTERVAL_MS = 250
PAGE_RESTORE_STABLE_SAMPLES = 3
PAGE_RESTORE_CLOSE_RETRY_MS = 2_000
PAGE_RESTORE_GO_BACK_TIMEOUT_MS = 15_000
RANDOM_ACTION_INTERACTION_TIMEOUT_MS = 3_000
RANDOM_ACTION_POPUP_SETTLE_DELAY_MS = 250
ACTION_OVERLAY_SELECTOR = (
    ".next-overlay-wrapper.opened, "
    ".ant-modal-wrap:visible, "
    ".ant-drawer:visible, "
    '[role="dialog"]:visible'
)


def _row_product_id(row: Any) -> str:
    try:
        text = str(row.inner_text()).strip()
    except Exception:
        return ""
    match = re.search(r"商品ID\s*(\d+)", text)
    return match.group(1) if match else ""


def _visible_current_rows(page: Any, rows_selector: str) -> list[Any]:
    rows = page.locator(rows_selector)
    output: list[Any] = []
    for index in range(rows.count()):
        row = rows.nth(index)
        try:
            if row.is_visible() and _row_product_id(row):
                output.append(row)
        except Exception:
            continue
    return output


def _ordered_product_ids(page: Any, rows_selector: str) -> tuple[str, ...]:
    return tuple(
        product_id
        for row in _visible_current_rows(page, rows_selector)
        if (product_id := _row_product_id(row))
    )


def _context_pages(page: Any) -> tuple[Any, ...]:
    context = getattr(page, "context", None)
    try:
        return tuple(context.pages) if context is not None else ()
    except Exception:
        return ()


def _close_new_pages(page: Any, pages_before: tuple[Any, ...]) -> None:
    known = {id(candidate) for candidate in pages_before}
    for candidate in _context_pages(page):
        if candidate is page or id(candidate) in known:
            continue
        try:
            candidate.close(run_before_unload=False)
        except Exception:
            pass


def _publish_frame_visible(page: Any) -> bool:
    try:
        return any(
            PUBLISH_FRAME_FRAGMENT in str(getattr(frame, "url", ""))
            for frame in page.frames
        )
    except Exception:
        return False


def _visible_action_overlays(page: Any) -> list[Any]:
    try:
        overlays = page.locator(ACTION_OVERLAY_SELECTOR)
        return [
            overlays.nth(index)
            for index in range(overlays.count())
            if overlays.nth(index).is_visible()
        ]
    except Exception:
        return []


def _action_surface_restored(page: Any, baseline_overlay_count: int) -> bool:
    return (
        not _publish_frame_visible(page)
        and len(_visible_action_overlays(page)) <= baseline_overlay_count
    )


def _click_first_visible(scope: Any, selectors: tuple[str, ...]) -> bool:
    for selector in selectors:
        try:
            candidates = scope.locator(selector)
            for index in range(candidates.count()):
                candidate = candidates.nth(index)
                if candidate.is_visible():
                    candidate.click(force=True, timeout=1_500)
                    return True
        except Exception:
            continue
    return False


def _wait_for_publish_form_to_close(page: Any, delay_ms: int = 500) -> bool:
    try:
        page.wait_for_timeout(delay_ms)
    except Exception:
        pass
    return not _publish_frame_visible(page)


def _click_outside_publish_form(page: Any, opened_scope: Any | None) -> bool:
    """Click the publish form's backdrop without touching form controls."""

    # The parent drawer backdrop is actionable before its cross-origin iframe
    # is always discoverable, so this close path must not depend on the frame.
    try:
        parent_backdrops = page.locator(
            ".next-overlay-wrapper.opened > .next-overlay-backdrop"
        )
        for index in range(parent_backdrops.count()):
            backdrop = parent_backdrops.nth(index)
            if not backdrop.is_visible():
                continue
            backdrop.click(
                position={"x": 8, "y": 8},
                force=True,
                timeout=1_500,
            )
            if _wait_for_publish_form_to_close(page):
                return True
    except Exception:
        pass

    if (
        opened_scope is None
        or PUBLISH_FRAME_FRAGMENT
        not in str(getattr(opened_scope, "url", ""))
    ):
        return False

    # In some Qianniu revisions the backdrop is inside the cross-origin
    # publish iframe. A corner click targets the area outside the form card.
    backdrop_selectors = (
        ".ant-modal-wrap",
        ".ant-drawer-mask",
        '[class*="mask"]',
        '[class*="Mask"]',
        '[class*="overlay"]',
        '[class*="Overlay"]',
        "body",
    )
    for selector in backdrop_selectors:
        try:
            candidates = opened_scope.locator(selector)
            for index in range(candidates.count()):
                candidate = candidates.nth(index)
                if not candidate.is_visible():
                    continue
                candidate.click(
                    position={"x": 8, "y": 8},
                    force=True,
                    timeout=1_500,
                )
                if _wait_for_publish_form_to_close(page):
                    return True
        except Exception:
            continue

    # Other revisions size the iframe to the form card and keep the backdrop
    # in the parent page. Click the centre of the largest viewport band that
    # is provably outside the iframe element.
    try:
        box = opened_scope.frame_element().bounding_box()
        viewport = getattr(page, "viewport_size", None) or page.evaluate(
            "() => ({width: window.innerWidth, height: window.innerHeight})"
        )
        if not box or not isinstance(viewport, dict):
            return False
        width = float(viewport["width"])
        height = float(viewport["height"])
        left = max(float(box["x"]), 0.0)
        top = max(float(box["y"]), 0.0)
        right = min(float(box["x"]) + float(box["width"]), width)
        bottom = min(float(box["y"]) + float(box["height"]), height)
        bands = [
            (left * height, left / 2, height / 2),
            ((width - right) * height, (right + width) / 2, height / 2),
            ((right - left) * top, (left + right) / 2, top / 2),
            (
                (right - left) * (height - bottom),
                (left + right) / 2,
                (bottom + height) / 2,
            ),
        ]
        area, x, y = max(bands)
        if area <= 16:
            return False
        page.mouse.click(x, y)
        return _wait_for_publish_form_to_close(page)
    except Exception:
        return False


def _close_opened_action(
    page: Any,
    opened_scope: Any | None,
    *,
    baseline_overlay_count: int = 0,
) -> None:
    """Discard a slot detail or an unconfirmed publish form in-place."""

    if (
        _click_outside_publish_form(page, opened_scope)
        and _action_surface_restored(page, baseline_overlay_count)
    ):
        return

    close_selectors = (
        '[aria-label*="关闭"]',
        '[title*="关闭"]',
        '.ant-modal-close',
        '.ant-drawer-close',
        '.next-overlay-wrapper.opened button:has(svg.next-icon-remote)',
        '.next-overlay-wrapper.opened a:has(svg.next-icon-remote)',
        '.next-overlay-wrapper.opened [role="button"]:has(svg.next-icon-remote)',
        '.next-overlay-wrapper.opened svg.next-icon-remote',
        '[class*="CloseButton"]',
        '[class*="closeButton"]',
        'button:has-text("退出")',
        'button:has-text("关闭")',
        'button:has-text("取消")',
        'button:has-text("返回")',
    )
    scopes: list[Any] = []
    if opened_scope is not None:
        scopes.append(opened_scope)
    for overlay in reversed(_visible_action_overlays(page)):
        if all(overlay is not known for known in scopes):
            scopes.append(overlay)
    for scope in _page_scopes(page):
        if all(scope is not known for known in scopes):
            scopes.append(scope)

    # Compatibility fallback for details and Qianniu revisions without a
    # clickable publish backdrop.
    for _ in range(3):
        for scope in scopes:
            if _click_first_visible(scope, close_selectors):
                try:
                    page.wait_for_timeout(300)
                except Exception:
                    return
                if _action_surface_restored(page, baseline_overlay_count):
                    return
        try:
            keyboard = getattr(page, "keyboard", None)
            if keyboard is not None:
                keyboard.press("Escape")
            page.wait_for_timeout(300)
        except Exception:
            return
        if _action_surface_restored(page, baseline_overlay_count):
            return


def _restore_current_page(
    page: Any,
    *,
    rows_selector: str,
    product_ids_before: tuple[str, ...],
    url_before: str,
    opened_scope: Any | None,
    baseline_overlay_count: int,
) -> dict[str, Any]:
    _close_opened_action(
        page,
        opened_scope,
        baseline_overlay_count=baseline_overlay_count,
    )

    if str(getattr(page, "url", "")) != url_before:
        try:
            page.go_back(
                wait_until="domcontentloaded",
                timeout=PAGE_RESTORE_GO_BACK_TIMEOUT_MS,
            )
        except Exception:
            pass

    stable_samples = 0
    waited_ms = 0
    poll_count = max(
        1,
        PAGE_RESTORE_TIMEOUT_MS // PAGE_RESTORE_POLL_INTERVAL_MS,
    )
    last_state: dict[str, Any] = {}
    for poll_index in range(poll_count + 1):
        observed_product_ids = _ordered_product_ids(page, rows_selector)
        last_state = {
            "url_matches": str(getattr(page, "url", "")) == url_before,
            "product_order_matches": observed_product_ids == product_ids_before,
            "publish_frame_visible": _publish_frame_visible(page),
            "action_overlay_count": len(_visible_action_overlays(page)),
            "baseline_overlay_count": baseline_overlay_count,
            "expected_product_count": len(product_ids_before),
            "observed_product_count": len(observed_product_ids),
        }
        all_restored = (
            last_state["url_matches"]
            and last_state["product_order_matches"]
            and not last_state["publish_frame_visible"]
            and last_state["action_overlay_count"] <= baseline_overlay_count
        )
        stable_samples = stable_samples + 1 if all_restored else 0
        if stable_samples >= PAGE_RESTORE_STABLE_SAMPLES:
            return {
                "restored": True,
                "elapsed_ms": waited_ms,
                "stable_samples": stable_samples,
                **last_state,
            }
        if poll_index >= poll_count:
            break
        if (
            poll_index > 0
            and waited_ms % PAGE_RESTORE_CLOSE_RETRY_MS == 0
            and (
                last_state["publish_frame_visible"]
                or last_state["action_overlay_count"] > baseline_overlay_count
            )
        ):
            _close_opened_action(
                page,
                opened_scope,
                baseline_overlay_count=baseline_overlay_count,
            )
        try:
            page.wait_for_timeout(PAGE_RESTORE_POLL_INTERVAL_MS)
            waited_ms += PAGE_RESTORE_POLL_INTERVAL_MS
        except Exception:
            break
    return {
        "restored": False,
        "elapsed_ms": waited_ms,
        "stable_samples": stable_samples,
        **last_state,
    }


def perform_random_collection_action(
    page: Any,
    *,
    rows_selector: str,
    popup_selectors: Mapping[str, str] | None = None,
    rng: RandomSource | None = None,
    wait_for_human_check: HumanCheckWaiter | None = None,
) -> dict[str, Any]:
    """View one filled slot from the current page and restore that exact page.

    The function never enters a product id in a search field.  Candidate rows
    and filled slots are taken only from the collector's currently visible
    page. Empty slots are never opened by this read-only random action.
    """

    selector = str(rows_selector).strip()
    if not selector:
        return {"status": "skipped", "reason_code": "RANDOM_ACTION_ROWS_UNAVAILABLE"}
    safe_popup_selectors = dict(popup_selectors or {})
    if safe_popup_selectors:
        _settle_safe_popups(
            page,
            safe_popup_selectors,
            delay_ms=RANDOM_ACTION_POPUP_SETTLE_DELAY_MS,
        )
    if _visible_action_overlays(page):
        return {
            "status": "skipped",
            "reason_code": "RANDOM_ACTION_PREEXISTING_OVERLAY",
        }
    source = rng or random.SystemRandom()
    rows = _visible_current_rows(page, selector)
    if not rows:
        return {"status": "skipped", "reason_code": "RANDOM_ACTION_NO_CURRENT_ROW"}

    product_ids_before = tuple(_row_product_id(row) for row in rows)
    candidates: list[tuple[Any, str, int]] = []
    for row in rows:
        product_id = _row_product_id(row)
        try:
            slots = _slot_cells(row)
            empty_positions = _empty_slot_positions(row)
        except QianniuCopyError:
            continue
        empty_set = set(empty_positions)
        candidates.extend(
            (row, product_id, position)
            for position in range(1, slots.count() + 1)
            if position not in empty_set
        )
    if not candidates:
        return {
            "status": "skipped",
            "reason_code": "RANDOM_ACTION_NO_FILLED_SLOT",
        }

    action = "view_filled_slot"
    row, product_id, position = source.choice(candidates)
    pages_before = _context_pages(page)
    url_before = str(getattr(page, "url", ""))
    baseline_overlay_count = 0
    opened_scope = None
    result: dict[str, Any]
    action_started = False
    try:
        slots = _slot_cells(row)
        action_started = True
        slot = slots.nth(position - 1)
        slot.hover(
            force=True,
            timeout=RANDOM_ACTION_INTERACTION_TIMEOUT_MS,
        )
        slot.click(
            force=True,
            timeout=RANDOM_ACTION_INTERACTION_TIMEOUT_MS,
        )
        page.wait_for_timeout(source.randint(600, 1_200))
        if wait_for_human_check is not None:
            wait_for_human_check(page, "random_action_after_open")
        result = {
            "status": "completed",
            "action": action,
            "product_id": product_id,
            "slot_position": position,
            "read_only": True,
            "source": "current_page",
        }
    except QianniuCopyError as error:
        result = {
            "status": "skipped",
            "reason_code": error.reason_code,
            "detail": error.detail,
        }
    except Exception as error:
        result = {
            "status": "skipped",
            "reason_code": "RANDOM_ACTION_RUNTIME_FAILED",
            "detail": str(error),
        }
    finally:
        if safe_popup_selectors:
            _settle_safe_popups(
                page,
                safe_popup_selectors,
                delay_ms=RANDOM_ACTION_POPUP_SETTLE_DELAY_MS,
            )
        _close_new_pages(page, pages_before)
        if action_started:
            restore = _restore_current_page(
                page,
                rows_selector=selector,
                product_ids_before=product_ids_before,
                url_before=url_before,
                opened_scope=opened_scope,
                baseline_overlay_count=baseline_overlay_count,
            )
            if restore["restored"]:
                result["page_state_restored"] = True
            else:
                detail = (
                    f"elapsed_ms={restore['elapsed_ms']};"
                    f"url_matches={str(restore['url_matches']).lower()};"
                    "product_order_matches="
                    f"{str(restore['product_order_matches']).lower()};"
                    "publish_frame_visible="
                    f"{str(restore['publish_frame_visible']).lower()};"
                    "action_overlay_count="
                    f"{restore['action_overlay_count']};"
                    "baseline_overlay_count="
                    f"{restore['baseline_overlay_count']};"
                    "observed_product_count="
                    f"{restore['observed_product_count']};"
                    "expected_product_count="
                    f"{restore['expected_product_count']}"
                )
                result = {
                    "status": "skipped",
                    "reason_code": "RANDOM_ACTION_PAGE_RESTORE_FAILED",
                    "action": action,
                    "product_id": product_id,
                    "slot_position": position,
                    "read_only": True,
                    "source": "current_page",
                    "page_state_restored": False,
                    "detail": detail,
                    "restore_diagnostics": restore,
                }
    return result


def random_page_interval(rng: RandomSource | None = None) -> int:
    """Choose the confirmed one-or-two-page interval."""

    return int((rng or random.SystemRandom()).randint(1, 2))
