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

from ..io_tables import sha256_file


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


class QianniuCopyError(RuntimeError):
    """Stable failure raised by the Qianniu copy workflow."""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        self.reason_code = reason_code
        self.detail = detail
        message = reason_code if not detail else f"{reason_code}: {detail}"
        super().__init__(message)


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


def _dismiss_guides(page) -> None:
    for _ in range(8):
        clicked = False
        for label in ("下一步", "完成", "知道了"):
            candidates = page.get_by_text(label, exact=True)
            for index in range(candidates.count() - 1, -1, -1):
                item = candidates.nth(index)
                if item.is_visible():
                    item.click(force=True)
                    page.wait_for_timeout(400)
                    clicked = True
                    break
            if clicked:
                break
        if not clicked:
            break


def _recommend_url(material_center_url: str) -> str:
    base = str(material_center_url).split("?", 1)[0].rstrip("/")
    return f"{base}{MATERIAL_RECOMMEND_QUERY}"


def _open_recommend_list(page, material_center_url: str) -> None:
    page.goto(
        _recommend_url(material_center_url),
        wait_until="domcontentloaded",
        timeout=NAVIGATION_TIMEOUT_MS,
    )
    _dismiss_guides(page)


def _recommend_list_is_current(page, material_center_url: str) -> bool:
    target = _recommend_url(material_center_url).rstrip("/")
    current = str(getattr(page, "url", "") or "").split("#", 1)[0].rstrip("/")
    if current != target:
        return False
    return not any(
        PUBLISH_FRAME_FRAGMENT in str(getattr(frame, "url", "") or "")
        for frame in list(getattr(page, "frames", []) or [])
    )


def _ensure_recommend_list(page, material_center_url: str) -> bool:
    """Open the list only when the preceding slot did not already return there."""

    if _recommend_list_is_current(page, material_center_url):
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
    for _ in range(PRODUCT_SCOPE_ATTEMPTS):
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
    scope, search = _wait_for_product_scope(page)
    search.fill(product_id)
    search.press("Enter")
    last_count = 0
    for _ in range(PRODUCT_ROW_ATTEMPTS):
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
        _select_uploaded_cards,
        _set_local_files,
        _wait_for_upload_completion,
    )

    extension = path.suffix.lower() or ".jpg"
    upload_name = (
        f"seed-{expected_sha256[:12]}-{uuid.uuid4().hex[:8]}"
        f"{extension}"
    )
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
    return upload_name


def _generate_copy(page, publish_frame) -> tuple[str, str, float]:
    import time

    assistant = publish_frame.get_by_text("AI生成文案", exact=True)
    if assistant.count() != 1 or not assistant.is_visible():
        raise QianniuCopyError(
            "QIANNIU_AI_COPY_ACTION_NOT_FOUND",
            "选择图片后没有出现 AI生成文案",
        )
    started = time.perf_counter()
    assistant.click(force=True)
    body = ""
    result_panel_ready = False
    for _ in range(AI_COPY_TIMEOUT_MS // AI_COPY_POLL_MS):
        page.wait_for_timeout(AI_COPY_POLL_MS)
        body = publish_frame.locator("body").inner_text()
        if (
            "生成中" not in body
            and "重新生成" in body
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
            return title, description, round(
                time.perf_counter() - started, 2
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


def generate_qianniu_copy_drafts(
    page,
    slots: Sequence[Mapping[str, Any]],
    *,
    material_center_url: str,
) -> list[dict[str, Any]]:
    """Generate one reviewable Qianniu draft for every final output slot."""

    occurrence_by_product: dict[str, int] = {}
    drafts: list[dict[str, Any]] = []
    _ensure_recommend_list(page, material_center_url)
    try:
        for raw_slot in slots:
            slot_id = str(raw_slot.get("slot_id", "")).strip()
            product_id = str(raw_slot.get("product_id", "")).strip()
            if not slot_id or not product_id:
                raise QianniuCopyError(
                    "QIANNIU_COPY_SLOT_IDENTITY_INVALID",
                    "文案请求缺少 slot_id 或 product_id",
                )
            occurrence = int(
                raw_slot.get(
                    "remote_slot_occurrence",
                    occurrence_by_product.get(product_id, 0),
                )
            )
            if occurrence < 0:
                raise QianniuCopyError(
                    "QIANNIU_SLOT_OCCURRENCE_INVALID",
                    f"商品 {product_id} 的空坑位序号不可为负数",
                )
            row = _find_product_row_with_recovery(
                page,
                product_id,
                material_center_url,
            )
            empty_positions = _empty_slot_positions(row)
            requested_position = raw_slot.get("remote_slot_position")
            if requested_position is not None:
                position = int(requested_position)
                if position not in empty_positions:
                    raise QianniuCopyError(
                        "QIANNIU_SLOT_OCCUPIED",
                        f"商品 {product_id} 第 {position} 个坑位不可用",
                    )
            else:
                if occurrence >= len(empty_positions):
                    raise QianniuCopyError(
                        "QIANNIU_EMPTY_SLOT_SHORTAGE",
                        (
                            f"商品 {product_id} 需要第 {occurrence + 1} 个空坑位，"
                            f"当前只识别到 {len(empty_positions)} 个"
                        ),
                    )
                position = empty_positions[occurrence]
            occurrence_by_product[product_id] = occurrence + 1
            publish_frame = _open_slot_publish_form(
                page,
                product_id,
                position,
                row=row,
            )
            ordered_outputs = raw_slot.get("ordered_outputs")
            if (
                not isinstance(ordered_outputs, Sequence)
                or not ordered_outputs
                or not isinstance(ordered_outputs[0], Mapping)
            ):
                raise QianniuCopyError(
                    "QIANNIU_COPY_SEED_OUTPUT_MISSING",
                    f"商品 {product_id} 的坑位缺少最终图片输出",
                )
            seed_output = ordered_outputs[0]
            seed_image_name = _select_seed_image(
                page,
                publish_frame,
                seed_path=str(seed_output.get("output_path", "")),
                expected_sha256=str(
                    seed_output.get("output_sha256", "")
                ),
            )
            publish_frame = _wait_for_frame(
                page,
                PUBLISH_FRAME_FRAGMENT,
                attempts=PUBLISH_FRAME_ATTEMPTS,
                delay_ms=300,
            )
            title, description, elapsed = _generate_copy(
                page, publish_frame
            )
            draft = QianniuCopyDraft(
                slot_id=slot_id,
                product_id=product_id,
                remote_slot_position=position,
                title=title,
                description=description,
                seed_image_name=seed_image_name,
                elapsed_seconds=elapsed,
            )
            drafts.append(draft.as_response_item())
            # Returning to the list discards the unconfirmed form.  Do not
            # click "填充文案", the form's final "确认", or publish.
            _open_recommend_list(page, material_center_url)
    except QianniuCopyError:
        try:
            _open_recommend_list(page, material_center_url)
        except Exception:
            pass
        raise
    except Exception as error:
        try:
            _open_recommend_list(page, material_center_url)
        except Exception:
            pass
        raise QianniuCopyError(
            "QIANNIU_COPY_RUNTIME_FAILED",
            str(error),
        ) from error
    return drafts
