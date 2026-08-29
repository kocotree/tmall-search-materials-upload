"""Generate reviewable copy through Qianniu's built-in copy assistant.

The workflow intentionally stops before filling the generated copy into the
publish form and before any final publish action. It locally uploads the first
hash-verified final slot image as the minimum seed required by the Qianniu UI.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import uuid
from typing import Any, Mapping, Sequence

from ..collection_readiness import merge_default_safe_popup_selectors
from ..io_tables import sha256_file
from .material_page import _settle_safe_popups


MATERIAL_RECOMMEND_QUERY = "?tab=recommend"
PUBLISH_FRAME_FRAGMENT = "/publish-feeds/imagePreview"
MATERIAL_SELECTOR_FRAME_FRAGMENT = "sucai-selector-ng"
NAVIGATION_TIMEOUT_MS = 60_000
PRODUCT_SCOPE_ATTEMPTS = 300
PRODUCT_SCOPE_DELAY_MS = 300
PRODUCT_ROW_ATTEMPTS = 100
LIVE_SLOT_ATTEMPTS = 100
PUBLISH_FRAME_ATTEMPTS = 100
PUBLISH_BODY_ATTEMPTS = 120
MATERIAL_ROOT_ATTEMPTS = 120
MATERIAL_CARD_ATTEMPTS = 120
MATERIAL_SELECTOR_FRAME_ATTEMPTS = 100
COPY_UPLOAD_TIMEOUT_MS = 120_000
COPY_UPLOAD_CARD_ATTEMPTS = 100
AI_COPY_TIMEOUT_MS = 180_000
AI_COPY_POLL_MS = 500
PUBLISH_FORM_CLOSE_ATTEMPTS = 12
PUBLISH_FORM_CLOSE_DELAY_MS = 250
COPY_POPUP_SETTLE_DELAY_MS = 250
COPY_LATE_POPUP_QUIET_CHECKS = 8
COPY_BLOCKING_OVERLAY_SELECTOR = (
    ".next-overlay-wrapper.opened, "
    ".ant-modal-wrap:visible, "
    ".ant-drawer:visible, "
    '[role="dialog"]:visible'
)

COPY_LIST_SAFE_POPUP_SELECTORS = merge_default_safe_popup_selectors({})
_FORM_POPUP_UNSAFE_SELECTOR_PARTS = (
    ".next-overlay-wrapper",
    "button.ant-modal-close",
    'button:has-text("关闭")',
    '[aria-label*="关闭"]',
)


def _form_safe_popup_selectors() -> dict[str, str]:
    """Keep only controls that cannot close the active publish form."""

    def is_safe(part: str) -> bool:
        if any(
            marker in part
            for marker in _FORM_POPUP_UNSAFE_SELECTOR_PARTS
        ):
            return False
        if (
            '[aria-label="close"]' in part
            and "AiImageGenerationOfflinePushModal" not in part
        ):
            return False
        return True

    output: dict[str, str] = {}
    for field, selector in COPY_LIST_SAFE_POPUP_SELECTORS.items():
        parts = [part.strip() for part in str(selector).split(",")]
        safe_parts = [part for part in parts if part and is_safe(part)]
        output[field] = ", ".join(safe_parts)
    return output


COPY_FORM_SAFE_POPUP_SELECTORS = _form_safe_popup_selectors()


class QianniuCopyError(RuntimeError):
    """Stable failure raised by the Qianniu copy workflow."""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        self.reason_code = reason_code
        self.detail = detail
        message = reason_code if not detail else f"{reason_code}: {detail}"
        super().__init__(message)


def _popup_scopes(page) -> list[Any]:
    scopes = [page]
    for frame in list(getattr(page, "frames", ()) or ()):
        if frame is not None and all(frame is not scope for scope in scopes):
            scopes.append(frame)
    return scopes


def _visible_popup_control_count(
    page,
    selectors: Mapping[str, str],
) -> int:
    visible = 0
    for scope in _popup_scopes(page):
        for selector in selectors.values():
            value = str(selector).strip()
            if not value:
                continue
            try:
                candidates = scope.locator(value)
                for index in range(candidates.count()):
                    if candidates.nth(index).is_visible():
                        visible += 1
            except Exception:
                # Frames can disappear while Qianniu re-renders the SPA.
                continue
    return visible


def _visible_list_overlay_count(page) -> int:
    try:
        overlays = page.locator(COPY_BLOCKING_OVERLAY_SELECTOR)
        return sum(
            1
            for index in range(overlays.count())
            if overlays.nth(index).is_visible()
        )
    except Exception:
        return 0


def _settle_copy_popups(
    page,
    *,
    publish_form_open: bool = False,
    watch_for_late_popup: bool = False,
    require_clear_surface: bool = True,
) -> int:
    """Close only known-safe overlays before a copy workflow action."""

    selectors = (
        COPY_FORM_SAFE_POPUP_SELECTORS
        if publish_form_open
        else COPY_LIST_SAFE_POPUP_SELECTORS
    )
    closed = _settle_safe_popups(
        page,
        selectors,
        delay_ms=(
            COPY_POPUP_SETTLE_DELAY_MS if watch_for_late_popup else 0
        ),
        quiet_checks_required=(
            COPY_LATE_POPUP_QUIET_CHECKS if watch_for_late_popup else 1
        ),
    )
    remaining_controls = _visible_popup_control_count(page, selectors)
    remaining_overlays = (
        0
        if publish_form_open or not require_clear_surface
        else _visible_list_overlay_count(page)
    )
    if remaining_controls or remaining_overlays:
        raise QianniuCopyError(
            "QIANNIU_COPY_POPUP_BLOCKED",
            (
                "千牛页面仍有未关闭的引导或弹窗，文案抓取已安全停止；"
                "请关闭弹窗后重试"
            ),
        )
    return closed


@dataclass(frozen=True)
class QianniuCopyDraft:
    slot_id: str
    product_id: str
    remote_slot_position: int
    title: str
    description: str
    seed_image_name: str
    elapsed_seconds: float

    def as_response_item(self) -> dict[str, Any]:
        return {
            "slot_id": self.slot_id,
            "product_id": self.product_id,
            "title": self.title,
            "description": self.description,
            "evidence": [
                "千牛商品坑位内置 AI 生成",
                f"已绑定商品 {self.product_id}",
                (
                    f"使用本地上传图片 {self.seed_image_name} 触发生成；"
                    "未填充、未确认、未发布"
                ),
            ],
            "risks": ["千牛生成内容仍需人工核对商品事实与素材一致性"],
            "source": "qianniu_builtin_ai",
            "remote_slot_position": self.remote_slot_position,
            "seed_image_name": self.seed_image_name,
            "elapsed_seconds": self.elapsed_seconds,
        }


def parse_qianniu_ai_copy(text: str) -> tuple[str, str]:
    """Extract title and body from the visible Qianniu AI result panel."""

    normalized = (
        str(text)
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\u00a0", " ")
    )
    lines = [line.strip() for line in normalized.split("\n") if line.strip()]
    title_match = None
    description_match = None
    title_index = -1
    description_index = -1
    for index, line in enumerate(lines):
        match = re.fullmatch(r"标题(?:内容)?\s*[：:]?\s*(.*)", line)
        if match is not None:
            title_match = match
            title_index = index
            break
    if title_match is not None:
        for index in range(title_index + 1, len(lines)):
            match = re.fullmatch(
                r"(?:正文|描述)(?:内容)?\s*[：:]?\s*(.*)",
                lines[index],
            )
            if match is not None:
                description_match = match
                description_index = index
                break
    if title_match is None or description_match is None:
        raise QianniuCopyError(
            "QIANNIU_COPY_RESULT_INVALID",
            "未能从千牛 AI 结果面板读取标题和正文",
        )

    def is_action_line(value: str) -> bool:
        return re.fullmatch(
            r"(?:(?:确认|取消|填充文案|重新生成)\s*)+",
            value,
        ) is not None

    title_parts = [title_match.group(1).strip()]
    title_parts.extend(
        line
        for line in lines[title_index + 1 : description_index]
        if not is_action_line(line)
    )
    description_parts = [description_match.group(1).strip()]
    for line in lines[description_index + 1 :]:
        if is_action_line(line):
            break
        description_parts.append(line)
    title = " ".join(part for part in title_parts if part).strip()
    description = "\n".join(
        part for part in description_parts if part
    ).strip()
    if not title or not description:
        raise QianniuCopyError(
            "QIANNIU_COPY_RESULT_INVALID",
            "千牛 AI 返回了空标题或空正文",
        )
    return title, description


def _click_visible_image_text_action(
    page,
    *,
    attempts: int = 4,
) -> tuple[bool, bool]:
    """Re-resolve and click the portal action across React replacements."""

    saw_unique_action = False
    for _ in range(attempts):
        actions = []
        for scope in _page_scopes(page):
            candidate = scope.get_by_text("发图文", exact=True)
            for index in range(candidate.count()):
                item = candidate.nth(index)
                if item.is_visible():
                    actions.append(item)
        if len(actions) == 1:
            saw_unique_action = True
            try:
                actions[0].evaluate(
                    "element => element.click()",
                    timeout=1_500,
                )
                return True, saw_unique_action
            except Exception:
                # Qianniu renders this menu through a React portal. The item
                # can be replaced after is_visible(); re-resolve it instead
                # of waiting on the stale locator for Playwright's 30s default.
                pass
        page.wait_for_timeout(150)
    return False, saw_unique_action


def _read_ai_result_panel(publish_frame) -> tuple[str, str] | None:
    """Read the smallest visible AI panel, including labelled form controls."""

    actions = publish_frame.get_by_text("填充文案", exact=True)
    visible = []
    for index in range(actions.count()):
        action = actions.nth(index)
        if action.is_visible():
            visible.append(action)
    if len(visible) != 1:
        return None
    try:
        payload = visible[0].evaluate(
            """element => {
              let node = element;
              let root = null;
              for (let depth = 0; node && depth < 9; depth += 1) {
                const text = (node.innerText || '').trim();
                if (
                  text.includes('重新生成')
                  && text.includes('填充文案')
                  && /(^|\\n)\\s*标题(?:内容)?\\s*[：:]?/m.test(text)
                  && /(^|\\n)\\s*(?:正文|描述)(?:内容)?\\s*[：:]?/m.test(text)
                ) {
                  root = node;
                  break;
                }
                node = node.parentElement;
              }
              if (!root) return null;
              const controls = Array.from(
                root.querySelectorAll('input, textarea, [contenteditable="true"]')
              ).map(control => ({
                hint: [
                  control.getAttribute('aria-label') || '',
                  control.getAttribute('placeholder') || '',
                  control.getAttribute('name') || '',
                ].join(' '),
                context: (control.parentElement?.innerText || '').slice(0, 120),
                value: (
                  control.value
                  || control.innerText
                  || control.textContent
                  || ''
                ).trim(),
              }));
              return { text: (root.innerText || '').trim(), controls };
            }""",
            timeout=1_000,
        )
    except Exception:
        return None
    if not isinstance(payload, Mapping):
        return None
    title = ""
    description = ""
    controls = payload.get("controls")
    if isinstance(controls, Sequence) and not isinstance(controls, (str, bytes)):
        for raw_control in controls:
            if not isinstance(raw_control, Mapping):
                continue
            hint = str(raw_control.get("hint", "")).strip()
            context = str(raw_control.get("context", "")).strip()
            label = hint or context
            value = str(raw_control.get("value", "")).strip()
            if not value:
                continue
            if not title and "标题" in label and not re.search(
                r"正文|描述", label
            ):
                title = value
            if (
                not description
                and re.search(r"正文|描述", label)
                and "标题" not in label
            ):
                description = value
    if title and description:
        return title, description
    panel_text = str(payload.get("text", "")).strip()
    if panel_text:
        try:
            return parse_qianniu_ai_copy(panel_text)
        except QianniuCopyError:
            return None
    return None


def _wait_for_frame(
    page,
    fragment: str,
    *,
    attempts: int = 30,
    delay_ms: int = 300,
):
    for _ in range(attempts):
        matches = [
            candidate
            for candidate in page.frames
            if fragment in candidate.url
        ]
        for candidate in reversed(matches):
            try:
                if candidate.frame_element().is_visible():
                    return candidate
            except AttributeError:
                # Lightweight test doubles do not expose frame_element().
                return candidate
            except Exception:
                continue
        page.wait_for_timeout(delay_ms)
    raise QianniuCopyError(
        "QIANNIU_FRAME_NOT_READY",
        f"等待页面区域超时：{fragment}",
    )


def _recommend_url(material_center_url: str) -> str:
    base = str(material_center_url).split("?", 1)[0].rstrip("/")
    return f"{base}{MATERIAL_RECOMMEND_QUERY}"


def _open_recommend_list(page, material_center_url: str) -> None:
    page.goto(
        _recommend_url(material_center_url),
        wait_until="domcontentloaded",
        timeout=NAVIGATION_TIMEOUT_MS,
    )
    _settle_copy_popups(page, watch_for_late_popup=True)


def _recommend_list_is_current(page, material_center_url: str) -> bool:
    target = _recommend_url(material_center_url).rstrip("/")
    current = str(getattr(page, "url", "") or "").split("#", 1)[0].rstrip("/")
    if current != target:
        return False
    return not any(
        PUBLISH_FRAME_FRAGMENT in str(getattr(frame, "url", "") or "")
        for frame in list(getattr(page, "frames", []) or [])
    )


def _publish_form_frames(page) -> list[Any]:
    return [
        frame
        for frame in list(getattr(page, "frames", []) or [])
        if PUBLISH_FRAME_FRAGMENT
        in str(getattr(frame, "url", "") or "")
    ]


def _wait_for_publish_form_to_close(
    page,
    *,
    attempts: int = PUBLISH_FORM_CLOSE_ATTEMPTS,
) -> bool:
    for attempt in range(attempts + 1):
        if not _publish_form_frames(page):
            return True
        if attempt < attempts:
            page.wait_for_timeout(PUBLISH_FORM_CLOSE_DELAY_MS)
    return False


def _click_first_visible_close_control(scope, selectors: Sequence[str]) -> bool:
    for selector in selectors:
        try:
            candidates = scope.locator(selector)
            for index in range(candidates.count()):
                candidate = candidates.nth(index)
                if not candidate.is_visible():
                    continue
                candidate.click(force=True, timeout=1_500)
                return True
        except Exception:
            continue
    return False


def _close_publish_form_in_place(page, publish_frame=None) -> bool:
    """Discard one unconfirmed form without refreshing the product list."""

    if not _publish_form_frames(page):
        return True
    frame_close_selectors = (
        'button:has-text("确认取消")',
        'button:has-text("确认退出")',
        '[aria-label*="关闭"]',
        '[title*="关闭"]',
        ".ant-modal-close",
        ".ant-drawer-close",
        '[class*="CloseButton"]',
        '[class*="closeButton"]',
        'button:has-text("退出")',
        'button:has-text("关闭")',
        'button:has-text("取消")',
    )
    page_close_selectors = (
        ".next-overlay-wrapper.opened button:has(svg.next-icon-remote)",
        ".next-overlay-wrapper.opened a:has(svg.next-icon-remote)",
        ".next-overlay-wrapper.opened [role=\"button\"]:has(svg.next-icon-remote)",
        'button:has-text("确认取消")',
        'button:has-text("确认退出")',
        '[aria-label*="关闭"]',
        '[title*="关闭"]',
        ".ant-modal-close",
        ".ant-drawer-close",
    )
    for _ in range(3):
        try:
            backdrops = page.locator(
                ".next-overlay-wrapper.opened > .next-overlay-backdrop"
            )
            for index in range(backdrops.count()):
                backdrop = backdrops.nth(index)
                if not backdrop.is_visible():
                    continue
                backdrop.click(
                    position={"x": 8, "y": 8},
                    force=True,
                    timeout=1_500,
                )
                if _wait_for_publish_form_to_close(page, attempts=4):
                    return True
        except Exception:
            pass

        frames = _publish_form_frames(page)
        scopes = []
        if publish_frame is not None and any(
            publish_frame is frame for frame in frames
        ):
            scopes.append(publish_frame)
        scopes.extend(
            frame
            for frame in frames
            if all(frame is not scope for scope in scopes)
        )
        for scope in scopes:
            if _click_first_visible_close_control(
                scope,
                frame_close_selectors,
            ) and _wait_for_publish_form_to_close(page, attempts=4):
                return True
        if _click_first_visible_close_control(
            page,
            page_close_selectors,
        ) and _wait_for_publish_form_to_close(page, attempts=4):
            return True
        try:
            keyboard = getattr(page, "keyboard", None)
            if keyboard is not None:
                keyboard.press("Escape")
            if _wait_for_publish_form_to_close(page, attempts=4):
                return True
        except Exception:
            pass
    return not _publish_form_frames(page)


def _ensure_recommend_list(page, material_center_url: str) -> bool:
    """Open the list only when the preceding slot did not already return there."""

    if _recommend_list_is_current(page, material_center_url):
        _settle_copy_popups(page)
        return False
    _open_recommend_list(page, material_center_url)
    return True


def _page_scopes(page) -> list[Any]:
    """Return each current DOM scope once, including the main frame."""

    frames = list(getattr(page, "frames", []) or [])
    return frames or [page]


def _scope_url(scope: Any, index: int) -> str:
    value = str(getattr(scope, "url", "") or "").strip()
    return value or f"<blank-frame-{index}>"


def _product_search_candidate(scope):
    inputs = scope.locator("input")
    matching = []
    placeholders = []
    for index in range(inputs.count()):
        candidate = inputs.nth(index)
        placeholder = (
            candidate.get_attribute("placeholder") or ""
        ).strip()
        if placeholder:
            placeholders.append(placeholder)
        if (
            candidate.is_visible()
            and "商品" in placeholder
            and ("ID" in placeholder.upper() or "名称" in placeholder)
        ):
            matching.append(candidate)
    table_ready = scope.locator("tbody tr").count() > 0
    return (
        matching[0] if len(matching) == 1 and table_ready else None,
        placeholders,
        table_ready,
    )


def _wait_for_product_scope(page):
    observations: dict[str, dict[str, Any]] = {}
    for attempt in range(PRODUCT_SCOPE_ATTEMPTS):
        if attempt % 10 == 0:
            _settle_copy_popups(page, require_clear_surface=False)
        ready = []
        for index, scope in enumerate(_page_scopes(page)):
            label = _scope_url(scope, index)
            try:
                search, placeholders, table_ready = (
                    _product_search_candidate(scope)
                )
            except Exception:
                continue
            observations[label] = {
                "placeholders": placeholders[:12],
                "table_ready": table_ready,
            }
            if search is not None:
                ready.append((scope, search))
        if len(ready) == 1:
            return ready[0]
        if len(ready) > 1:
            raise QianniuCopyError(
                "QIANNIU_PRODUCT_SCOPE_AMBIGUOUS",
                "多个页面区域同时包含商品搜索框和商品表格",
            )
        page.wait_for_timeout(PRODUCT_SCOPE_DELAY_MS)
    raise QianniuCopyError(
        "QIANNIU_PRODUCT_SEARCH_NOT_FOUND",
        (
            "等待商品搜索区域超时；"
            f"页面区域={observations}"
        ),
    )


def _find_product_row(page, product_id: str):
    product_id = str(product_id).strip()
    _settle_copy_popups(page)
    scope, search = _wait_for_product_scope(page)
    _settle_copy_popups(page)
    search.fill(product_id)
    search.press("Enter")
    last_count = 0
    for attempt in range(PRODUCT_ROW_ATTEMPTS):
        if attempt and attempt % 10 == 0:
            _settle_copy_popups(page, require_clear_surface=False)
        try:
            rows = scope.locator("tbody tr").filter(
                has_text=product_id
            )
            last_count = rows.count()
            if (
                last_count == 1
                and rows.first.is_visible()
            ):
                return rows.first
        except Exception:
            break
        page.wait_for_timeout(300)
    raise QianniuCopyError(
        "QIANNIU_PRODUCT_IDENTITY_MISMATCH",
        (
            f"商品 {product_id} 命中 {last_count} 行；"
            f"scope={_scope_url(scope, 0)}"
        ),
    )


def _find_product_row_with_recovery(
    page,
    product_id: str,
    material_center_url: str,
):
    """Retry one slow list hydration without replaying completed slots."""

    try:
        return _find_product_row(page, product_id)
    except QianniuCopyError as error:
        if error.reason_code != "QIANNIU_PRODUCT_SEARCH_NOT_FOUND":
            raise
    _open_recommend_list(page, material_center_url)
    return _find_product_row(page, product_id)


def _slot_cells(row):
    cells = row.locator("td")
    if cells.count() == 0:
        raise QianniuCopyError(
            "QIANNIU_SLOT_TABLE_INVALID",
            "商品行没有可读取的素材列",
        )
    slots = cells.last.locator(
        '[class^="MaterialCell_container__"] '
        '> [class^="MaterialCell_materialCell__"]'
    )
    if slots.count() == 0:
        raise QianniuCopyError(
            "QIANNIU_SLOT_TABLE_INVALID",
            "没有识别到单品素材坑位",
        )
    return slots


def _empty_slot_positions(row) -> list[int]:
    slots = _slot_cells(row)
    output: list[int] = []
    for index in range(slots.count()):
        slot = slots.nth(index)
        publish = slot.locator(
            '[class*="PublishButton"], [class*="publishBtn"]'
        )
        if publish.count() and publish.first.is_visible():
            output.append(index + 1)
    return output


def _open_slot_publish_form(
    page,
    product_id: str,
    position: int,
    *,
    row=None,
):
    def resolve_live_slot():
        last_slot_count = 0
        for _ in range(LIVE_SLOT_ATTEMPTS):
            try:
                live_row = row
                if live_row is None:
                    scope, _search = _wait_for_product_scope(page)
                    rows = scope.locator("tbody tr").filter(
                        has_text=product_id
                    )
                    live_row = (
                        rows.first
                        if rows.count() == 1 and rows.first.is_visible()
                        else None
                    )
                if live_row is not None and live_row.is_visible():
                    slots = _slot_cells(live_row)
                    last_slot_count = slots.count()
                    if 1 <= position <= last_slot_count:
                        slot = slots.nth(position - 1)
                        publish = slot.locator(
                            '[class*="PublishButton"], '
                            '[class*="publishBtn"]'
                        ).first
                        if (
                            slot.is_visible()
                            and publish.count() == 1
                            and publish.is_visible()
                        ):
                            return slot, publish
            except Exception:
                pass
            page.wait_for_timeout(300)
        if position < 1 or position > last_slot_count:
            raise QianniuCopyError(
                "QIANNIU_SLOT_NOT_FOUND",
                f"商品 {product_id} 不存在第 {position} 个坑位",
            )
        raise QianniuCopyError(
            "QIANNIU_SLOT_OCCUPIED",
            f"商品 {product_id} 第 {position} 个坑位不是空坑位",
        )

    saw_unique_action = False
    for _ in range(4):
        _settle_copy_popups(page)
        slot, publish = resolve_live_slot()
        slot.hover(force=True)
        publish.click(force=True)
        page.wait_for_timeout(500)
        clicked, observed = _click_visible_image_text_action(page)
        saw_unique_action = saw_unique_action or observed
        if not clicked:
            slot, _publish = resolve_live_slot()
            slot.click(force=True)
            page.wait_for_timeout(500)
            clicked, observed = _click_visible_image_text_action(page)
            saw_unique_action = saw_unique_action or observed
        if not clicked:
            continue
        try:
            frame = _wait_for_frame(
                page,
                PUBLISH_FRAME_FRAGMENT,
                attempts=PUBLISH_FRAME_ATTEMPTS,
                delay_ms=300,
            )
            break
        except QianniuCopyError as error:
            if error.reason_code != "QIANNIU_FRAME_NOT_READY":
                raise
    else:
        reason_code = (
            "QIANNIU_IMAGE_TEXT_ACTION_UNSTABLE"
            if saw_unique_action
            else "QIANNIU_IMAGE_TEXT_ACTION_NOT_FOUND"
        )
        raise QianniuCopyError(
            reason_code,
            (
                f"商品 {product_id} 第 {position} 个坑位的发图文入口"
                "未能稳定打开"
            ),
        )
    body = ""
    for _ in range(PUBLISH_BODY_ATTEMPTS):
        body = frame.locator("body").inner_text().strip()
        if body:
            break
        page.wait_for_timeout(250)
    if "该内容暂不支持修改商品" not in body:
        raise QianniuCopyError(
            "QIANNIU_PRODUCT_NOT_BOUND",
            (
                f"商品 {product_id} 的坑位发布表单没有锁定商品；"
                f"表单可见文本={body[:800]!r}"
            ),
        )
    return frame


def _reset_material_selector_to_all_images(page, selector):
    """Select the material-library root and wait for its cards."""

    # Qianniu remembers the last material-library folder across forms and
    # sessions.  That folder can legitimately be empty even while the
    # merchant's root library contains many usable images.  Always reset to
    # the stable root entry before deciding that no seed image exists.
    visible_entries = []
    all_image_entries = selector.get_by_text("全部图片", exact=True)
    for _ in range(MATERIAL_ROOT_ATTEMPTS):
        all_image_entries = selector.get_by_text("全部图片", exact=True)
        visible_entries = []
        for index in range(all_image_entries.count()):
            entry = all_image_entries.nth(index)
            if entry.is_visible():
                visible_entries.append(entry)
        if len(visible_entries) == 1:
            break
        page.wait_for_timeout(250)
    if len(visible_entries) != 1:
        raise QianniuCopyError(
            "QIANNIU_MATERIAL_ROOT_NOT_FOUND",
            (
                "素材库没有唯一可见的“全部图片”入口；"
                f"visible={len(visible_entries)};"
                f"total={all_image_entries.count()}"
            ),
        )
    visible_entries[0].click(force=True)

    cards = selector.locator(
        '[class*="PicList_PicturesShow_main-show"]'
    )
    for _ in range(MATERIAL_CARD_ATTEMPTS):
        if cards.count() > 0:
            return cards
        page.wait_for_timeout(250)
        cards = selector.locator(
            '[class*="PicList_PicturesShow_main-show"]'
        )
    return cards


def _find_existing_seed_card(page, selector, filename: str):
    search = selector.locator('input[placeholder="搜索图片名称"]')
    if search.count() != 1 or not search.first.is_visible():
        raise QianniuCopyError(
            "QIANNIU_MATERIAL_SEARCH_NOT_FOUND",
            "素材库没有唯一可见的图片名称搜索框",
        )
    cards_selector = '[class*="PicList_PicturesShow_main-show"]'
    search.fill(filename)
    search.press("Enter")
    for _ in range(MATERIAL_CARD_ATTEMPTS):
        page.wait_for_timeout(250)
        matches = selector.locator(cards_selector).filter(
            has_text=filename
        )
        visible = []
        for index in range(matches.count()):
            card = matches.nth(index)
            try:
                title = card.locator('[class*="PicList_tip_title"]')
                material_name = (
                    title.first.inner_text(timeout=1_000).strip()
                    if title.count()
                    else ""
                )
                checkbox = card.locator('input[type="checkbox"]')
                if (
                    material_name.casefold() == filename.casefold()
                    and card.is_visible()
                    and checkbox.count() == 1
                    and checkbox.first.is_enabled()
                ):
                    visible.append(card)
            except Exception:
                # React can replace the result grid while the search request
                # settles. Re-resolve it on the next bounded poll.
                visible = []
                break
        if len(visible) == 1:
            return visible[0]
    # A failed exact-name lookup must not leave the material library in an
    # empty filtered state. The local-upload entry is rendered from the root
    # view in some Qianniu revisions.
    search.fill("")
    search.press("Enter")
    page.wait_for_timeout(500)
    return None


def _confirm_single_seed_card(page, selector, selected_card) -> None:
    selector.locator('input[type="checkbox"]:checked').evaluate_all(
        "checkboxes => checkboxes.forEach(checkbox => checkbox.click())"
    )
    selected_card.evaluate(
        """element => {
          const checkbox = element.querySelector('input[type="checkbox"]');
          if (checkbox && !checkbox.checked) checkbox.click();
        }"""
    )
    page.wait_for_timeout(300)
    confirm = selector.locator('button:has-text("确定"):visible').first
    confirm_text = confirm.inner_text() if confirm.count() == 1 else ""
    if (
        confirm.count() != 1
        or not confirm.is_enabled()
        or re.search(r"确定\s*[（(]1[）)]", confirm_text) is None
    ):
        raise QianniuCopyError(
            "QIANNIU_COPY_SEED_SELECTION_INVALID",
            "素材库没有形成唯一单图选择",
        )
    confirm.click()
    page.wait_for_timeout(1_500)


def _select_seed_image(
    page,
    publish_frame,
    *,
    seed_path: str,
    expected_sha256: str,
) -> str:
    _settle_copy_popups(page, publish_form_open=True)
    path = Path(seed_path)
    if (
        not path.is_file()
        or not expected_sha256
        or sha256_file(path) != expected_sha256
    ):
        raise QianniuCopyError(
            "QIANNIU_COPY_SEED_FILE_INVALID",
            f"种子图片不存在或哈希不一致：{path.name}",
        )
    upload = publish_frame.get_by_role(
        "button", name="上传图片", exact=True
    )
    if upload.count() != 1:
        raise QianniuCopyError(
            "QIANNIU_MATERIAL_SELECTOR_NOT_FOUND",
            "发布表单没有唯一的上传图片按钮",
        )
    upload.click(force=True)
    selector = _wait_for_frame(
        page,
        MATERIAL_SELECTOR_FRAME_FRAGMENT,
        attempts=MATERIAL_SELECTOR_FRAME_ATTEMPTS,
        delay_ms=300,
    )
    # Normalize only the selector's display folder. This does not search for
    # or reuse a cloud asset; it ensures the newly local-uploaded card is
    # visible after the upload panel returns.
    _reset_material_selector_to_all_images(page, selector)

    # Reuse the production upload primitives, but stop after the selector
    # confirmation. Always upload the exact, hash-verified local final slot
    # image; do not search or reuse a cloud-library image because the current
    # cloud folder and stale search state are not task-scoped.
    from .qianniu_upload import (
        QianniuUploadError,
        _select_uploaded_cards,
        _set_local_files,
        _wait_for_upload_completion,
    )

    extension = path.suffix.lower() or ".jpg"
    upload_name = (
        f"seed-{expected_sha256[:12]}-{uuid.uuid4().hex[:8]}"
        f"{extension}"
    )
    try:
        _set_local_files(
            page,
            selector,
            [str(path)],
            upload_names=[upload_name],
        )
        _wait_for_upload_completion(
            page,
            selector,
            1,
            timeout_ms=COPY_UPLOAD_TIMEOUT_MS,
        )
        selector = _wait_for_frame(
            page,
            MATERIAL_SELECTOR_FRAME_FRAGMENT,
            attempts=MATERIAL_SELECTOR_FRAME_ATTEMPTS,
            delay_ms=300,
        )
        _select_uploaded_cards(
            page,
            selector,
            [upload_name],
            attempts=COPY_UPLOAD_CARD_ATTEMPTS,
            delay_ms=300,
        )
    except QianniuUploadError as error:
        raise QianniuCopyError(
            error.reason_code,
            error.detail,
        ) from error
    return upload_name


def _visible_text_actions(publish_frame, text: str) -> list[Any]:
    try:
        actions = publish_frame.get_by_text(text, exact=True)
        return [
            actions.nth(index)
            for index in range(actions.count())
            if actions.nth(index).is_visible()
        ]
    except Exception:
        # A React revision can temporarily remove one of the two mutually
        # exclusive actions while the panel resets after replacing the image.
        return []


def _current_copy_result(publish_frame) -> tuple[str, str] | None:
    parsed = _read_ai_result_panel(publish_frame)
    if parsed is not None:
        return parsed
    try:
        body = publish_frame.locator("body").inner_text()
        return parse_qianniu_ai_copy(body)
    except Exception:
        return None


def _generate_copy(
    page,
    publish_frame,
    *,
    previous_copy: tuple[str, str] | None = None,
) -> tuple[str, str, float]:
    import time

    _settle_copy_popups(page, publish_form_open=True)
    initial_actions = _visible_text_actions(publish_frame, "AI生成文案")
    regenerate_actions = _visible_text_actions(publish_frame, "重新生成")
    baseline_copy: tuple[str, str] | None = None
    requires_generation_evidence = False
    if previous_copy is not None and len(regenerate_actions) == 1:
        action = regenerate_actions[0]
        baseline_copy = _current_copy_result(publish_frame) or previous_copy
        requires_generation_evidence = True
    elif len(initial_actions) == 1:
        action = initial_actions[0]
    elif len(regenerate_actions) == 1:
        action = regenerate_actions[0]
        baseline_copy = _current_copy_result(publish_frame) or previous_copy
        requires_generation_evidence = True
    else:
        raise QianniuCopyError(
            "QIANNIU_AI_COPY_ACTION_NOT_FOUND",
            "选择图片后没有出现唯一的 AI 生成或重新生成入口",
        )
    started = time.perf_counter()
    action.click(force=True)
    body = ""
    result_panel_ready = False
    observed_generation = False
    stale_result_observed = False
    for _ in range(AI_COPY_TIMEOUT_MS // AI_COPY_POLL_MS):
        page.wait_for_timeout(AI_COPY_POLL_MS)
        body = publish_frame.locator("body").inner_text()
        if "生成中" in body:
            observed_generation = True
            continue
        if (
            "重新生成" in body
            and "填充文案" in body
        ):
            result_panel_ready = True
            parsed = _read_ai_result_panel(publish_frame)
            if parsed is None:
                try:
                    parsed = parse_qianniu_ai_copy(body)
                except QianniuCopyError:
                    # The action buttons can become visible before React has
                    # committed both generated fields. Keep polling within the
                    # same bounded AI wait instead of failing the slot early.
                    continue
            title, description = parsed
            if (
                requires_generation_evidence
                and not observed_generation
                and (
                    baseline_copy is None
                    or (title, description) == baseline_copy
                )
            ):
                stale_result_observed = True
                continue
            return title, description, round(
                time.perf_counter() - started, 2
            )
    if stale_result_observed:
        raise QianniuCopyError(
            "QIANNIU_COPY_RESULT_STALE",
            "千牛没有返回可确认属于当前图片的新文案",
        )
    if result_panel_ready:
        raise QianniuCopyError(
            "QIANNIU_COPY_RESULT_INVALID",
            "千牛 AI 结果面板已完成，但未读取到完整标题和正文",
        )
    raise QianniuCopyError(
        "QIANNIU_AI_COPY_TIMEOUT",
        "千牛 AI 文案生成超时",
    )


def _prepare_slot_occurrences(
    slots: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    occurrences: dict[str, int] = {}
    prepared: list[dict[str, Any]] = []
    for raw_slot in slots:
        slot = dict(raw_slot)
        product_id = str(slot.get("product_id", "")).strip()
        occurrence = occurrences.get(product_id, 0)
        if slot.get("remote_slot_position") is None:
            slot.setdefault("remote_slot_occurrence", occurrence)
        occurrences[product_id] = occurrence + 1
        prepared.append(slot)
    return prepared


class QianniuProductCopySession:
    """Reuse one filtered product row while opening a fresh form per slot."""

    def __init__(
        self,
        page,
        slots: Sequence[Mapping[str, Any]],
        *,
        material_center_url: str,
    ) -> None:
        self.page = page
        self.material_center_url = material_center_url
        self.slots = [dict(slot) for slot in slots]
        product_ids = {
            str(slot.get("product_id", "")).strip()
            for slot in self.slots
        }
        if not self.slots or len(product_ids) != 1 or "" in product_ids:
            raise QianniuCopyError(
                "QIANNIU_COPY_SLOT_IDENTITY_INVALID",
                "商品级文案会话必须只包含一个有效商品",
            )
        self.product_id = next(iter(product_ids))
        self._slot_ids = {
            str(slot.get("slot_id", "")).strip() for slot in self.slots
        }
        if "" in self._slot_ids or len(self._slot_ids) != len(self.slots):
            raise QianniuCopyError(
                "QIANNIU_COPY_SLOT_IDENTITY_INVALID",
                "文案请求包含缺失或重复的 slot_id",
            )
        self._positions: dict[str, int] = {}
        self._row = None
        self._publish_frame = None

    def __enter__(self) -> "QianniuProductCopySession":
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        if exc_type is None:
            self.close()
        else:
            self._close_safely()
        return False

    def _resolve_positions(self, row) -> None:
        empty_positions = _empty_slot_positions(row)
        positions: dict[str, int] = {}
        seen_positions: set[int] = set()
        for local_occurrence, raw_slot in enumerate(self.slots):
            slot_id = str(raw_slot.get("slot_id", "")).strip()
            if not slot_id or slot_id in positions:
                raise QianniuCopyError(
                    "QIANNIU_COPY_SLOT_IDENTITY_INVALID",
                    "文案请求包含缺失或重复的 slot_id",
                )
            requested_position = raw_slot.get("remote_slot_position")
            if requested_position is not None:
                position = int(requested_position)
                if position not in empty_positions:
                    raise QianniuCopyError(
                        "QIANNIU_SLOT_OCCUPIED",
                        f"商品 {self.product_id} 第 {position} 个坑位不可用",
                    )
            else:
                occurrence = int(
                    raw_slot.get("remote_slot_occurrence", local_occurrence)
                )
                if occurrence < 0:
                    raise QianniuCopyError(
                        "QIANNIU_SLOT_OCCURRENCE_INVALID",
                        f"商品 {self.product_id} 的空坑位序号不可为负数",
                    )
                if occurrence >= len(empty_positions):
                    raise QianniuCopyError(
                        "QIANNIU_EMPTY_SLOT_SHORTAGE",
                        (
                            f"商品 {self.product_id} 需要第 {occurrence + 1} 个空坑位，"
                            f"当前只识别到 {len(empty_positions)} 个"
                        ),
                    )
                position = empty_positions[occurrence]
            if position in seen_positions:
                raise QianniuCopyError(
                    "QIANNIU_COPY_SLOT_IDENTITY_INVALID",
                    f"商品 {self.product_id} 的多个文案坑位指向同一位置 {position}",
                )
            seen_positions.add(position)
            positions[slot_id] = position
        self._positions = positions

    def _open(self) -> None:
        if self._row is not None and self._positions:
            return
        _ensure_recommend_list(self.page, self.material_center_url)
        row = _find_product_row_with_recovery(
            self.page,
            self.product_id,
            self.material_center_url,
        )
        self._resolve_positions(row)
        self._row = row

    def _close_current_form(self) -> None:
        if _close_publish_form_in_place(
            self.page,
            self._publish_frame,
        ):
            self._publish_frame = None
            return
        self._publish_frame = None
        raise QianniuCopyError(
            "QIANNIU_PUBLISH_FORM_CLOSE_FAILED",
            "上一坑位的未发布表单未能原地关闭",
        )

    def generate_slot(self, raw_slot: Mapping[str, Any]) -> dict[str, Any]:
        slot_id = str(raw_slot.get("slot_id", "")).strip()
        product_id = str(raw_slot.get("product_id", "")).strip()
        if product_id != self.product_id or slot_id not in self._slot_ids:
            raise QianniuCopyError(
                "QIANNIU_COPY_SLOT_IDENTITY_INVALID",
                "当前坑位不属于已打开的商品级文案会话",
            )
        ordered_outputs = raw_slot.get("ordered_outputs")
        if (
            not isinstance(ordered_outputs, Sequence)
            or isinstance(ordered_outputs, (str, bytes))
            or not ordered_outputs
            or not isinstance(ordered_outputs[0], Mapping)
        ):
            raise QianniuCopyError(
                "QIANNIU_COPY_SEED_OUTPUT_MISSING",
                f"商品 {product_id} 的坑位缺少最终图片输出",
            )
        seed_output = ordered_outputs[0]
        self._open()
        self._close_current_form()
        self._publish_frame = _open_slot_publish_form(
            self.page,
            self.product_id,
            self._positions[slot_id],
            row=self._row,
        )
        seed_image_name = _select_seed_image(
            self.page,
            self._publish_frame,
            seed_path=str(seed_output.get("output_path", "")),
            expected_sha256=str(seed_output.get("output_sha256", "")),
        )
        self._publish_frame = _wait_for_frame(
            self.page,
            PUBLISH_FRAME_FRAGMENT,
            attempts=PUBLISH_FRAME_ATTEMPTS,
            delay_ms=300,
        )
        title, description, elapsed = _generate_copy(
            self.page,
            self._publish_frame,
            previous_copy=None,
        )
        return QianniuCopyDraft(
            slot_id=slot_id,
            product_id=product_id,
            remote_slot_position=self._positions[slot_id],
            title=title,
            description=description,
            seed_image_name=seed_image_name,
            elapsed_seconds=elapsed,
        ).as_response_item()

    def recover_after_failure(self) -> None:
        """Close the failed slot form before retrying the same product row."""

        if _close_publish_form_in_place(
            self.page,
            self._publish_frame,
        ):
            self._publish_frame = None
            return
        self._publish_frame = None
        self._row = None
        self._positions = {}
        try:
            _ensure_recommend_list(self.page, self.material_center_url)
        except Exception:
            # The next attempt performs the normal bounded list recovery.
            pass

    def close(self) -> None:
        if _close_publish_form_in_place(
            self.page,
            self._publish_frame,
        ):
            self._publish_frame = None
            return
        self._publish_frame = None
        self._row = None
        self._positions = {}
        try:
            _open_recommend_list(self.page, self.material_center_url)
        except QianniuCopyError:
            raise
        except Exception as error:
            raise QianniuCopyError(
                "QIANNIU_COPY_RUNTIME_FAILED",
                str(error),
            ) from error

    def _close_safely(self) -> None:
        try:
            self.close()
        except Exception:
            pass


def open_qianniu_product_copy_session(
    page,
    slots: Sequence[Mapping[str, Any]],
    *,
    material_center_url: str,
) -> QianniuProductCopySession:
    return QianniuProductCopySession(
        page,
        slots,
        material_center_url=material_center_url,
    )


def generate_qianniu_copy_drafts(
    page,
    slots: Sequence[Mapping[str, Any]],
    *,
    material_center_url: str,
) -> list[dict[str, Any]]:
    """Generate one reviewable Qianniu draft for every final output slot."""

    prepared = _prepare_slot_occurrences(slots)
    grouped: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for index, slot in enumerate(prepared):
        product_id = str(slot.get("product_id", "")).strip()
        grouped.setdefault(product_id, []).append((index, slot))
    drafts_by_index: dict[int, dict[str, Any]] = {}
    try:
        for grouped_slots in grouped.values():
            product_slots = [slot for _index, slot in grouped_slots]
            with open_qianniu_product_copy_session(
                page,
                product_slots,
                material_center_url=material_center_url,
            ) as product_session:
                for index, raw_slot in grouped_slots:
                    drafts_by_index[index] = product_session.generate_slot(
                        raw_slot
                    )
    except QianniuCopyError:
        try:
            _ensure_recommend_list(page, material_center_url)
        except Exception:
            pass
        raise
    except Exception as error:
        try:
            _ensure_recommend_list(page, material_center_url)
        except Exception:
            pass
        raise QianniuCopyError(
            "QIANNIU_COPY_RUNTIME_FAILED",
            str(error),
        ) from error
    return [drafts_by_index[index] for index in range(len(prepared))]
