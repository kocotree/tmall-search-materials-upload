"""Qianniu search-recommendation image publishing workflow.

This module is adapted from the proven DOM flow in PlaywrightAuto's
``upload-recommend.js``.  It deliberately does not own approval or production
authorization; callers must complete those checks before invoking it.
"""

from __future__ import annotations

from dataclasses import dataclass
import mimetypes
from pathlib import Path
import re
import uuid
from typing import Callable, Sequence

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from ..models import MaterialItem
from .qianniu_copy import (
    MATERIAL_SELECTOR_FRAME_FRAGMENT,
    PUBLISH_FRAME_FRAGMENT,
    QianniuCopyError,
    _close_publish_form_in_place,
    _ensure_recommend_list,
    _find_product_row,
    _find_product_row_with_recovery,
    _open_recommend_list,
    _open_slot_publish_form,
    _recommend_list_is_current,
    _slot_cells,
    _wait_for_frame,
    _wait_for_publish_form_to_close,
)


DEFAULT_MATERIAL_CENTER_URL = (
    "https://myseller.taobao.com/home.htm/"
    "material-center/material-management"
)
REMOTE_ID_OBSERVE_ATTEMPTS = 20
REMOTE_ID_OBSERVE_DELAY_MS = 500


class QianniuUploadError(RuntimeError):
    """Stable pre-publish failure that is safe to retry."""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(
            reason_code if not detail else f"{reason_code}: {detail}"
        )


@dataclass(frozen=True)
class QianniuPublishObservation:
    status: str
    reason_code: str
    remote_material_id: str | None = None
    evidence: str = ""


def _first_visible(locator):
    for index in range(locator.count()):
        candidate = locator.nth(index)
        if candidate.is_visible():
            return candidate
    return None


def _visible_exact_text(scope, text: str):
    return _first_visible(scope.get_by_text(text, exact=True))


def _read_selection_count(selector_frame) -> tuple[int, str]:
    buttons = selector_frame.locator('button:has-text("确定")')
    confirm = _first_visible(buttons)
    if confirm is None:
        return -1, ""
    text = confirm.inner_text().strip()
    match = re.search(r"确定\s*[（(](\d+)[）)]", text)
    if match:
        return int(match.group(1)), text
    checked = selector_frame.locator(
        'input[type="checkbox"]:checked'
    ).count()
    return int(checked), text


def _set_local_files(
    page,
    selector_frame,
    paths: Sequence[str],
    *,
    upload_names: Sequence[str] | None = None,
) -> None:
    if upload_names is not None and len(upload_names) != len(paths):
        raise QianniuUploadError("QIANNIU_UPLOAD_NAME_COUNT_MISMATCH")
    file_values: list[str | dict[str, object]]
    if upload_names is None:
        file_values = list(paths)
    else:
        file_values = []
        for path, upload_name in zip(paths, upload_names):
            mime_type = (
                mimetypes.guess_type(upload_name)[0]
                or "application/octet-stream"
            )
            file_values.append(
                {
                    "name": upload_name,
                    "mimeType": mime_type,
                    "buffer": Path(path).read_bytes(),
                }
            )

    def current_scopes():
        # PlaywrightAuto re-resolves every frame after each UI transition.
        # Keep the known selector frame first, but do not assume Qianniu keeps
        # the upload panel in that same frame revision.
        scopes = [selector_frame]
        scopes.extend(
            frame for frame in page.frames if frame != selector_frame
        )
        return scopes

    def set_first_attached_input(timeout_ms: int) -> bool:
        attempts = max(1, timeout_ms // 200)
        for _ in range(attempts):
            for scope in current_scopes():
                try:
                    inputs = scope.locator('input[type="file"]')
                    if inputs.count():
                        inputs.first.set_input_files(file_values)
                        return True
                except PlaywrightError:
                    continue
            page.wait_for_timeout(200)
        return False

    if set_first_attached_input(200):
        return

    def wait_for_visible_action(kind: str, timeout_ms: int):
        attempts = max(1, timeout_ms // 250)
        for _ in range(attempts):
            for scope in current_scopes():
                try:
                    if kind == "local":
                        candidate = _first_visible(
                            scope.get_by_role(
                                "button",
                                name="本地上传",
                                exact=True,
                            )
                        )
                        if candidate is None:
                            candidate = _visible_exact_text(
                                scope, "本地上传"
                            )
                    else:
                        candidates = scope.locator(
                            "button#sucai-tu-upload"
                        )
                        if not candidates.count():
                            candidates = scope.get_by_role(
                                "button",
                                name="点击/拖拽，批量导入文件",
                                exact=False,
                            )
                        candidate = _first_visible(candidates)
                except PlaywrightError:
                    candidate = None
                if candidate is not None:
                    return candidate
            page.wait_for_timeout(250)
        return None

    local_upload = wait_for_visible_action("local", 30_000)
    if local_upload is not None:
        # Current Qianniu expands an in-frame "上传素材" panel instead of
        # opening a native chooser from this first button. Wait for the
        # upload panel to hydrate, then pass absolute paths straight to its
        # hidden HTML file input. This intentionally never opens an OS dialog.
        local_upload.evaluate("element => element.click()")
        if set_first_attached_input(10_000):
            return

    # Some page revisions render the upload panel before its input is
    # attached. Its real button is useful as a hydration trigger, but file
    # assignment still goes directly through the HTML input.
    batch_button = wait_for_visible_action("batch", 10_000)
    if batch_button is not None:
        batch_button.evaluate("element => element.click()")
        if set_first_attached_input(10_000):
            return
    observations = []
    for scope in current_scopes():
        try:
            texts = [
                text.strip()
                for text in scope.locator("button").all_inner_texts()
                if "上传" in text or "导入" in text
            ]
            observations.append(
                (
                    f"url={scope.url};"
                    f'file_inputs={scope.locator("input[type=file]").count()};'
                    f"buttons={texts[:8]}"
                )
            )
        except PlaywrightError:
            continue
    raise QianniuUploadError(
        "QIANNIU_LOCAL_UPLOAD_ENTRY_MISSING",
        " | ".join(observations[:8]),
    )


def _wait_for_upload_completion(
    page,
    selector_frame,
    expected_count: int,
    *,
    timeout_ms: int = 60_000,
) -> None:
    success = selector_frame.get_by_text(
        f"{expected_count} 个文件上传成功",
        exact=False,
    )
    try:
        success.wait_for(state="visible", timeout=timeout_ms)
    except PlaywrightTimeoutError:
        try:
            body = selector_frame.locator("body").inner_text()
        except PlaywrightError as exc:
            if "frame was detached" in str(exc).lower():
                return
            raise QianniuUploadError(
                "QIANNIU_LOCAL_UPLOAD_RUNTIME_FAILED",
                str(exc),
            ) from exc
        if "上传失败" in body:
            raise QianniuUploadError(
                "QIANNIU_LOCAL_UPLOAD_FAILED",
                body[-300:],
            )
        raise QianniuUploadError(
            "QIANNIU_LOCAL_UPLOAD_TIMEOUT",
            f"expected={expected_count}",
        )
    except PlaywrightError as exc:
        # The material selector replaces its iframe after a successful local
        # upload in some Qianniu revisions.  The caller immediately reacquires
        # that frame and verifies the exact uploaded filename, so detachment is
        # a safe transition signal rather than evidence of failure.
        if "frame was detached" in str(exc).lower():
            return
        raise QianniuUploadError(
            "QIANNIU_LOCAL_UPLOAD_RUNTIME_FAILED",
            str(exc),
        ) from exc

    done = _visible_exact_text(selector_frame, "完成")
    if done is not None:
        try:
            done.click(force=True)
        except PlaywrightError as exc:
            if "frame was detached" in str(exc).lower():
                return
            raise QianniuUploadError(
                "QIANNIU_LOCAL_UPLOAD_RUNTIME_FAILED",
                str(exc),
            ) from exc
        page.wait_for_timeout(1_000)


def _select_uploaded_cards(
    page,
    selector_frame,
    paths: Sequence[str],
    *,
    attempts: int = 30,
    delay_ms: int = 300,
) -> None:
    candidates_by_path = []
    selected_cards = []
    use_existing_selection = False
    for _ in range(attempts):
        cards = selector_frame.locator(
            '[class*="PicList_PicturesShow_main-show"]'
        )
        candidates_by_path = []
        selected_cards = []
        for path in paths:
            filename = Path(path).name
            matches = cards.filter(has_text=filename)
            visible = [
                matches.nth(index)
                for index in range(matches.count())
                if matches.nth(index).is_visible()
            ]
            candidates_by_path.append((filename, visible))
            checked = [
                card
                for card in visible
                if card.locator(
                    'input[type="checkbox"]:checked'
                ).count()
                == 1
            ]
            selected_cards.append(
                checked[0] if len(checked) == 1 else None
            )
        existing_count, _ = _read_selection_count(selector_frame)
        # This is the proven PlaywrightAuto path: a successful local upload
        # can return to the selector with exactly the uploaded files already
        # selected before their cards finish rendering.
        if existing_count == len(paths):
            use_existing_selection = True
            break
        if all(len(visible) == 1 for _, visible in candidates_by_path):
            break
        page.wait_for_timeout(delay_ms)

    if not use_existing_selection:
        selector_frame.locator(
            'input[type="checkbox"]:checked'
        ).evaluate_all(
            "checkboxes => checkboxes.forEach(checkbox => checkbox.click())"
        )
        page.wait_for_timeout(300)
        selected_cards = []
        for filename, visible in candidates_by_path:
            if len(visible) != 1:
                raise QianniuUploadError(
                    "QIANNIU_MATERIAL_IDENTITY_AMBIGUOUS",
                    f"{filename}:matches={len(visible)}",
                )
            selected_cards.append(visible[0])

    if not use_existing_selection:
        for (filename, _visible), card in zip(
            candidates_by_path, selected_cards
        ):
            if card is None:
                raise QianniuUploadError(
                    "QIANNIU_MATERIAL_IDENTITY_AMBIGUOUS",
                    filename,
                )
            checkbox = card.locator('input[type="checkbox"]')
            if checkbox.count() != 1 or not checkbox.is_enabled():
                raise QianniuUploadError(
                    "QIANNIU_MATERIAL_NOT_SELECTABLE",
                    filename,
                )
            card.evaluate(
                """element => {
                  const checkbox = element.querySelector(
                    'input[type="checkbox"]'
                  );
                  if (checkbox && !checkbox.checked) checkbox.click();
                }"""
            )
            page.wait_for_timeout(200)

    selected_count, confirm_text = _read_selection_count(selector_frame)
    if selected_count != len(paths):
        raise QianniuUploadError(
            "QIANNIU_MATERIAL_SELECTION_COUNT_MISMATCH",
            f"expected={len(paths)};actual={selected_count}",
        )
    confirm = _first_visible(
        selector_frame.locator('button:has-text("确定")')
    )
    if confirm is None or not confirm.is_enabled():
        raise QianniuUploadError(
            "QIANNIU_MATERIAL_CONFIRM_NOT_READY",
            confirm_text,
        )
    for _ in range(3):
        confirm.click()
        page.wait_for_timeout(1_500)
        if not any(
            MATERIAL_SELECTOR_FRAME_FRAGMENT in frame.url
            for frame in page.frames
        ):
            return
    raise QianniuUploadError("QIANNIU_MATERIAL_CONFIRM_FAILED")


def _fill_first_visible(
    scope,
    selectors: Sequence[str],
    value: str,
    *,
    field_name: str,
) -> None:
    for selector in selectors:
        locator = scope.locator(selector)
        candidate = _first_visible(locator)
        if candidate is None:
            continue
        candidate.fill(value)
        try:
            actual = candidate.input_value()
        except PlaywrightError:
            actual = candidate.inner_text()
        if str(actual).strip() != value:
            raise QianniuUploadError(
                "QIANNIU_CONTENT_FILL_MISMATCH",
                field_name,
            )
        return
    raise QianniuUploadError(
        "QIANNIU_CONTENT_FIELD_MISSING",
        field_name,
    )


def _remote_ids_from_row(row) -> tuple[set[str], dict[str, tuple[int, str]]]:
    remote_ids: set[str] = set()
    evidence: dict[str, tuple[int, str]] = {}
    slots = _slot_cells(row)
    for index in range(slots.count()):
        slot = slots.nth(index)
        values = [
            value.strip()
            for value in slot.locator(
                '[class*="CopyId_value"]'
            ).all_inner_texts()
            if value.strip()
        ]
        if len(values) > 1:
            raise QianniuUploadError(
                "QIANNIU_REMOTE_ID_AMBIGUOUS",
                f"position={index + 1};count={len(values)}",
            )
        if values:
            remote_id = values[0]
            if remote_id in remote_ids:
                raise QianniuUploadError(
                    "QIANNIU_REMOTE_ID_AMBIGUOUS",
                    f"duplicate={remote_id}",
                )
            remote_ids.add(remote_id)
            evidence[remote_id] = (index + 1, slot.inner_text().strip())
    return remote_ids, evidence


def _approved_upload_paths(item: MaterialItem) -> list[str]:
    if item.material_type != "image_text":
        raise QianniuUploadError("QIANNIU_VIDEO_UPLOAD_UNSUPPORTED")
    paths = [str(asset.source_path) for asset in item.assets]
    if not paths or any(not Path(path).is_file() for path in paths):
        raise QianniuUploadError("QIANNIU_APPROVED_FILE_MISSING")
    return paths


def _prepare_qianniu_upload_from_row(
    page,
    item: MaterialItem,
    *,
    row,
    paths: Sequence[str],
) -> set[str]:
    before_remote_ids, _ = _remote_ids_from_row(row)
    frame = _open_slot_publish_form(
        page,
        str(item.product_id),
        int(item.slot_index),
        row=row,
    )
    upload = frame.get_by_role("button", name="上传图片", exact=True)
    if upload.count() != 1 or not upload.is_visible():
        raise QianniuUploadError("QIANNIU_MATERIAL_SELECTOR_NOT_FOUND")
    upload.click(force=True)
    selector_frame = _wait_for_frame(
        page,
        MATERIAL_SELECTOR_FRAME_FRAGMENT,
        attempts=40,
        delay_ms=300,
    )
    upload_names = []
    for asset, path in zip(item.assets, paths, strict=True):
        extension = Path(path).suffix.lower() or ".jpg"
        upload_names.append(
            f"publish-{asset.sha256[:12]}-{uuid.uuid4().hex[:8]}"
            f"{extension}"
        )
    _set_local_files(
        page,
        selector_frame,
        paths,
        upload_names=upload_names,
    )
    _wait_for_upload_completion(page, selector_frame, len(paths))
    selector_frame = _wait_for_frame(
        page,
        MATERIAL_SELECTOR_FRAME_FRAGMENT,
        attempts=20,
        delay_ms=300,
    )
    _select_uploaded_cards(page, selector_frame, upload_names)
    frame = _wait_for_frame(
        page,
        PUBLISH_FRAME_FRAGMENT,
        attempts=30,
        delay_ms=300,
    )
    _fill_first_visible(
        frame,
        (
            'input[placeholder*="标题"]',
            'textarea[placeholder*="标题"]',
            'input[maxlength="20"]',
            'input[placeholder*="8-10个中文字"]',
        ),
        item.title,
        field_name="title",
    )
    _fill_first_visible(
        frame,
        (
            'textarea[placeholder*="描述"]',
            'textarea[placeholder*="内容"]',
            'textarea[placeholder*="词"]',
            'textarea[maxlength="1000"]',
            'textarea[placeholder*="10-1000个中文字"]',
            '[contenteditable="true"]',
            'textarea[data-cangjie-dockey]',
        ),
        item.description,
        field_name="description",
    )
    return before_remote_ids


def _as_upload_error(error: Exception) -> QianniuUploadError:
    if isinstance(error, QianniuUploadError):
        return error
    if isinstance(error, QianniuCopyError):
        return QianniuUploadError(error.reason_code, error.detail)
    return QianniuUploadError(
        "QIANNIU_UPLOAD_PREPARE_FAILED",
        str(error),
    )


def prepare_qianniu_upload(
    page,
    item: MaterialItem,
    *,
    material_center_url: str = DEFAULT_MATERIAL_CENTER_URL,
) -> set[str]:
    """Upload exact approved files and fill copy, stopping before publish."""

    paths = _approved_upload_paths(item)

    try:
        _open_recommend_list(page, material_center_url)
        row = _find_product_row(page, str(item.product_id))
        return _prepare_qianniu_upload_from_row(
            page,
            item,
            row=row,
            paths=paths,
        )
    except QianniuUploadError:
        raise
    except Exception as error:
        raise _as_upload_error(error) from error


class QianniuProductUploadSession:
    """Reuse one filtered product row across its formal upload slots."""

    def __init__(
        self,
        page,
        product_id: str,
        *,
        material_center_url: str,
    ) -> None:
        self.page = page
        self.product_id = str(product_id).strip()
        self.material_center_url = material_center_url
        self._row = None
        if not self.product_id:
            raise QianniuUploadError("QIANNIU_PRODUCT_IDENTITY_INVALID")

    def _cached_row_is_live(self) -> bool:
        if self._row is None or not _recommend_list_is_current(
            self.page,
            self.material_center_url,
        ):
            return False
        try:
            return bool(self._row.is_visible())
        except Exception:
            return False

    def _resolve_row(self):
        if self._cached_row_is_live():
            return self._row
        self._row = None
        try:
            _ensure_recommend_list(self.page, self.material_center_url)
            self._row = _find_product_row_with_recovery(
                self.page,
                self.product_id,
                self.material_center_url,
            )
            return self._row
        except Exception as error:
            raise _as_upload_error(error) from error

    def prepare(self, item: MaterialItem) -> set[str]:
        if str(item.product_id).strip() != self.product_id:
            raise QianniuUploadError(
                "QIANNIU_UPLOAD_SESSION_PRODUCT_MISMATCH",
                f"session={self.product_id};item={item.product_id}",
            )
        paths = _approved_upload_paths(item)
        try:
            return _prepare_qianniu_upload_from_row(
                self.page,
                item,
                row=self._resolve_row(),
                paths=paths,
            )
        except Exception as error:
            raise _as_upload_error(error) from error

    def observe_new_remote_item(
        self,
        item: MaterialItem,
        *,
        before_remote_ids: set[str],
    ) -> QianniuPublishObservation:
        _wait_for_publish_form_to_close(self.page)
        row = self._resolve_row()
        for attempt in range(REMOTE_ID_OBSERVE_ATTEMPTS + 1):
            current_remote_ids, _ = _remote_ids_from_row(row)
            missing_ids = before_remote_ids - current_remote_ids
            new_ids = current_remote_ids - before_remote_ids
            if missing_ids or new_ids:
                break
            if attempt < REMOTE_ID_OBSERVE_ATTEMPTS:
                self.page.wait_for_timeout(REMOTE_ID_OBSERVE_DELAY_MS)
        return _observe_new_remote_item_from_row(
            item,
            row=row,
            before_remote_ids=before_remote_ids,
        )

    def recover_after_failure(self) -> None:
        """Discard an unsubmitted form and retain the filtered row if possible."""

        closed = _close_publish_form_in_place(self.page)
        if closed and self._cached_row_is_live():
            return
        self._row = None
        try:
            _ensure_recommend_list(self.page, self.material_center_url)
        except Exception:
            # The bounded resolver retries navigation on the next attempt.
            pass

    def finish(self) -> None:
        """Leave no unsubmitted form behind after a safely completed group."""

        if not _close_publish_form_in_place(self.page):
            self._row = None


def open_qianniu_product_upload_session(
    page,
    product_id: str,
    *,
    material_center_url: str,
) -> QianniuProductUploadSession:
    return QianniuProductUploadSession(
        page,
        product_id,
        material_center_url=material_center_url,
    )


def _publish_button(page, frame):
    semantic = [
        candidate
        for candidate in (
            frame.locator('button[data-autolog*="publisher_ok_clk"]'),
            page.locator('button[data-autolog*="publisher_ok_clk"]'),
        )
        if candidate.count() == 1 and candidate.first.is_visible()
    ]
    if len(semantic) == 1:
        return semantic[0].first
    if len(semantic) > 1:
        raise QianniuUploadError(
            "QIANNIU_PUBLISH_BUTTON_AMBIGUOUS",
            f"semantic_count={len(semantic)}",
        )
    matches = []
    for scope in (frame, page):
        for label in ("提交发布", "发布"):
            candidate = _first_visible(
                scope.get_by_role("button", name=label, exact=True)
            )
            if candidate is not None:
                matches.append(candidate)
    if len(matches) != 1:
        raise QianniuUploadError(
            "QIANNIU_PUBLISH_BUTTON_AMBIGUOUS",
            f"count={len(matches)}",
        )
    return matches[0]


def _secondary_confirmation_visible(page) -> bool:
    for scope in (page, *page.frames):
        dialogs = scope.locator(
            '[role="dialog"], .next-dialog'
        )
        for label in ("确认", "确定"):
            buttons = dialogs.get_by_role(
                "button", name=label, exact=True
            )
            if _first_visible(buttons) is not None:
                return True
    return False


def _observe_target_slot(
    page,
    item: MaterialItem,
    *,
    material_center_url: str,
) -> QianniuPublishObservation:
    _open_recommend_list(page, material_center_url)
    row = _find_product_row(page, str(item.product_id))
    slots = _slot_cells(row)
    position = int(item.slot_index)
    if position < 1 or position > slots.count():
        return QianniuPublishObservation(
            "publish_uncertain",
            "QIANNIU_REMOTE_SLOT_NOT_FOUND",
            evidence=f"product={item.product_id};slot={position}",
        )
    slot = slots.nth(position - 1)
    remote_ids = [
        value.strip()
        for value in slot.locator(
            '[class*="CopyId_value"]'
        ).all_inner_texts()
        if value.strip()
    ]
    if len(remote_ids) != 1:
        return QianniuPublishObservation(
            "publish_uncertain",
            "QIANNIU_REMOTE_ID_NOT_OBSERVED",
            evidence=(
                f"product={item.product_id};slot={position};"
                f"remote_id_count={len(remote_ids)}"
            ),
        )
    slot_text = slot.inner_text().strip()
    status = "submitted"
    if "审核中" in slot_text:
        status = "under_review"
    elif any(token in slot_text for token in ("失败", "驳回", "拒绝")):
        status = "failed"
    elif any(token in slot_text for token in ("通过", "成功")):
        status = "success"
    return QianniuPublishObservation(
        status,
        "",
        remote_material_id=remote_ids[0],
        evidence=(
            f"product={item.product_id};slot={position};"
            f"remote_id={remote_ids[0]};dom=CopyId_value"
        ),
    )


def _observe_new_remote_item_from_row(
    item: MaterialItem,
    *,
    row,
    before_remote_ids: set[str],
) -> QianniuPublishObservation:
    current_remote_ids, current_evidence = _remote_ids_from_row(row)
    missing_ids = before_remote_ids - current_remote_ids
    new_ids = current_remote_ids - before_remote_ids
    if missing_ids or len(new_ids) != 1:
        return QianniuPublishObservation(
            "publish_uncertain",
            "QIANNIU_REMOTE_ID_DELTA_AMBIGUOUS",
            evidence=(
                f"product={item.product_id};"
                f"before={len(before_remote_ids)};"
                f"current={len(current_remote_ids)};"
                f"new={sorted(new_ids)};"
                f"missing={sorted(missing_ids)}"
            ),
        )
    remote_id = next(iter(new_ids))
    position, slot_text = current_evidence[remote_id]
    status = "submitted"
    if "审核中" in slot_text:
        status = "under_review"
    elif any(token in slot_text for token in ("失败", "驳回", "拒绝")):
        status = "failed"
    elif any(token in slot_text for token in ("通过", "成功")):
        status = "success"
    return QianniuPublishObservation(
        status,
        "",
        remote_material_id=remote_id,
        evidence=(
            f"product={item.product_id};requested_slot={item.slot_index};"
            f"current_position={position};remote_id={remote_id};"
            "method=remote_id_set_delta"
        ),
    )


def _observe_new_remote_item(
    page,
    item: MaterialItem,
    *,
    before_remote_ids: set[str],
    material_center_url: str,
) -> QianniuPublishObservation:
    _open_recommend_list(page, material_center_url)
    row = _find_product_row(page, str(item.product_id))
    return _observe_new_remote_item_from_row(
        item,
        row=row,
        before_remote_ids=before_remote_ids,
    )


def publish_qianniu_once(
    page,
    item: MaterialItem,
    *,
    material_center_url: str = DEFAULT_MATERIAL_CENTER_URL,
    before_publish: Callable[[], None] | None = None,
    before_remote_ids: set[str] | None = None,
    product_session: QianniuProductUploadSession | None = None,
) -> QianniuPublishObservation:
    """Click once and identify the new item without assuming stable positions."""

    if before_remote_ids is None:
        raise QianniuUploadError("QIANNIU_REMOTE_BASELINE_MISSING")

    try:
        frame = _wait_for_frame(
            page,
            PUBLISH_FRAME_FRAGMENT,
            attempts=10,
            delay_ms=200,
        )
        button = _publish_button(page, frame)
    except QianniuUploadError:
        raise
    except Exception as error:
        raise _as_upload_error(error) from error
    if before_publish is not None:
        before_publish()
    try:
        button.click(timeout=10_000)
    except PlaywrightTimeoutError:
        return QianniuPublishObservation(
            "publish_uncertain",
            "PUBLISH_UNCERTAIN",
            evidence=f"task={item.task_id};click_timeout=true",
        )
    except PlaywrightError as error:
        return QianniuPublishObservation(
            "publish_uncertain",
            "PUBLISH_UNCERTAIN",
            evidence=f"task={item.task_id};click_error={error}",
        )
    page.wait_for_timeout(1_000)
    if _secondary_confirmation_visible(page):
        return QianniuPublishObservation(
            "publish_uncertain",
            "QIANNIU_SECOND_CONFIRMATION_REQUIRED",
            evidence=(
                f"task={item.task_id};"
                "secondary_confirmation_not_clicked=true"
            ),
        )
    page.wait_for_timeout(4_000)
    try:
        if product_session is not None:
            return product_session.observe_new_remote_item(
                item,
                before_remote_ids=before_remote_ids,
            )
        return _observe_new_remote_item(
            page,
            item,
            before_remote_ids=before_remote_ids,
            material_center_url=material_center_url,
        )
    except Exception as error:
        return QianniuPublishObservation(
            "publish_uncertain",
            "QIANNIU_REMOTE_VERIFICATION_FAILED",
            evidence=f"task={item.task_id};detail={error}",
        )


def verify_qianniu_remote_item(
    page,
    item: MaterialItem,
    *,
    material_center_url: str = DEFAULT_MATERIAL_CENTER_URL,
) -> QianniuPublishObservation:
    """Read the exact product and slot without clicking any publish action."""

    try:
        return _observe_target_slot(
            page,
            item,
            material_center_url=material_center_url,
        )
    except Exception as error:
        return QianniuPublishObservation(
            "publish_uncertain",
            "QIANNIU_REMOTE_VERIFICATION_FAILED",
            evidence=f"task={item.task_id};detail={error}",
        )
