from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from ..approval import verify_manifest
from ..models import MaterialItem, MaterialStatus
from .material_page import ProductIdentityError, SelectorInvalidError
from .session import (
    HumanCheckRequired,
    StoreIdentityError,
    assert_store_identity,
    detect_human_check,
)
from .qianniu_upload import (
    DEFAULT_MATERIAL_CENTER_URL,
    QianniuProductUploadSession,
    QianniuUploadError,
    prepare_qianniu_upload,
    publish_qianniu_once,
)


QIANNIU_NON_RETRYABLE_PRE_PUBLISH_REASONS = frozenset(
    {
        "QIANNIU_APPROVED_FILE_MISSING",
        "QIANNIU_PUBLISH_BUTTON_AMBIGUOUS",
        "QIANNIU_REMOTE_BASELINE_MISSING",
        "QIANNIU_REMOTE_ID_AMBIGUOUS",
        "QIANNIU_SLOT_NOT_FOUND",
        "QIANNIU_SLOT_OCCUPIED",
        "QIANNIU_UPLOAD_NAME_COUNT_MISMATCH",
        "QIANNIU_UPLOAD_SESSION_PRODUCT_MISMATCH",
        "QIANNIU_VIDEO_UPLOAD_UNSUPPORTED",
    }
)


def _qianniu_retry_allowed(reason_code: str) -> bool:
    return bool(reason_code) and (
        reason_code not in QIANNIU_NON_RETRYABLE_PRE_PUBLISH_REASONS
    )


@dataclass(frozen=True)
class UploadOutcome:
    status: str
    reason: str = ""
    retry_allowed: bool = False
    remote_material_id: str | None = None
    evidence: str = ""
    batch_stop: bool = False


def _prepare_exact_product(page, item: MaterialItem, selectors: dict[str, str]) -> None:
    search = page.locator(selectors["product_search"])
    search.fill(item.product_id)
    search.press("Enter")
    actual = page.locator(selectors["product_id"]).inner_text().strip()
    if actual != item.product_id:
        raise ProductIdentityError(f"目标商品={item.product_id}; 页面商品={actual or '<missing>'}")


def _assert_target_slot_empty(page, item: MaterialItem, selectors: dict[str, str]) -> None:
    table = page.locator(selectors["material_table"])
    if not table.is_visible():
        raise SelectorInvalidError("material_table")
    empty_indexes = {
        int(number)
        for text in page.locator(selectors["empty_slots"]).all_inner_texts()
        for number in re.findall(r"\d+", text)
    }
    if item.slot_index not in empty_indexes:
        raise SelectorInvalidError("REMOTE_SLOT_CONFLICT")


def _fill_approved_content(page, item: MaterialItem, selectors: dict[str, str]) -> None:
    action_key = "video_action" if item.material_type == "video" else "image_text_action"
    page.locator(selectors[action_key]).click()
    page.locator(selectors["file_input"]).set_input_files(
        [asset.source_path for asset in item.assets]
    )
    page.locator(selectors["title_input"]).fill(item.title)
    page.locator(selectors["description_input"]).fill(item.description)


def upload_approved_item(
    page,
    item: MaterialItem,
    manifest: dict,
    selectors: dict[str, str],
    *,
    expected_store: str,
    now: str,
    before_publish: Callable[[], None] | None = None,
    workflow: str = "legacy_selectors",
    material_center_url: str = DEFAULT_MATERIAL_CENTER_URL,
    product_session: QianniuProductUploadSession | None = None,
) -> UploadOutcome:
    approval = verify_manifest(
        manifest,
        [item],
        expected_store=expected_store,
        now=now,
        rehash_assets=True,
    )
    if not approval.valid:
        return UploadOutcome(
            "blocked",
            approval.reason,
            retry_allowed=False,
        )
    if item.status != MaterialStatus.APPROVED:
        return UploadOutcome(
            "blocked",
            "TASK_NOT_APPROVED_STATE",
            retry_allowed=False,
            batch_stop=True,
        )
    if workflow == "qianniu_recommend":
        try:
            assert_store_identity(
                page, selectors["store_name"], expected_store
            )
            detect_human_check(page, selectors["human_check"])
            if product_session is None:
                before_remote_ids = prepare_qianniu_upload(
                    page,
                    item,
                    material_center_url=material_center_url,
                )
            else:
                before_remote_ids = product_session.prepare(item)
        except (StoreIdentityError, HumanCheckRequired) as error:
            return UploadOutcome(
                "blocked",
                str(error),
                retry_allowed=False,
                evidence=str(error),
                batch_stop=True,
            )
        except QianniuUploadError as error:
            return UploadOutcome(
                "blocked",
                error.reason_code,
                retry_allowed=_qianniu_retry_allowed(error.reason_code),
                evidence=str(error),
            )
        try:
            publish_kwargs = {
                "material_center_url": material_center_url,
                "before_publish": before_publish,
                "before_remote_ids": before_remote_ids,
            }
            if product_session is not None:
                publish_kwargs["product_session"] = product_session
            observation = publish_qianniu_once(
                page,
                item,
                **publish_kwargs,
            )
        except QianniuUploadError as error:
            return UploadOutcome(
                "blocked",
                error.reason_code,
                retry_allowed=_qianniu_retry_allowed(error.reason_code),
                evidence=str(error),
            )
        return UploadOutcome(
            observation.status,
            observation.reason_code,
            retry_allowed=False,
            remote_material_id=observation.remote_material_id,
            evidence=observation.evidence,
        )
    try:
        assert_store_identity(page, selectors["store_name"], expected_store)
        detect_human_check(page, selectors["human_check"])
        _prepare_exact_product(page, item, selectors)
        _assert_target_slot_empty(page, item, selectors)
        _fill_approved_content(page, item, selectors)
    except (StoreIdentityError, HumanCheckRequired, ProductIdentityError, SelectorInvalidError) as error:
        return UploadOutcome("blocked", str(error), retry_allowed=False)

    if before_publish is not None:
        before_publish()
    try:
        page.locator(selectors["publish_button"]).click()
    except PlaywrightTimeoutError:
        return UploadOutcome(
            "publish_uncertain",
            "PUBLISH_UNCERTAIN",
            retry_allowed=False,
            evidence=f"task={item.task_id};attempt=1",
        )

    success = page.locator(selectors["success_signal"])
    if not success.is_visible():
        return UploadOutcome(
            "publish_uncertain",
            "PUBLISH_RESULT_NOT_OBSERVED",
            retry_allowed=False,
            evidence=f"task={item.task_id};attempt=1",
        )
    remote_id = page.locator(selectors["remote_material_id"]).inner_text().strip()
    if not remote_id:
        return UploadOutcome(
            "publish_uncertain",
            "REMOTE_MATERIAL_ID_MISSING",
            retry_allowed=False,
            evidence=f"task={item.task_id};attempt=1",
        )
    return UploadOutcome(
        "submitted",
        "",
        retry_allowed=False,
        remote_material_id=remote_id,
        evidence=f"task={item.task_id};remote_id={remote_id};attempt=1",
    )
