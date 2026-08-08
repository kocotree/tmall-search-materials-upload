from datetime import datetime, timedelta

from ..models import MaterialItem
from .session import assert_store_identity, detect_human_check
from .upload_page import UploadOutcome, _prepare_exact_product
from .qianniu_upload import (
    DEFAULT_MATERIAL_CENTER_URL,
    verify_qianniu_remote_item,
)


def _status_name(value: str) -> str:
    if any(token in value for token in ("通过", "成功")):
        return "success"
    if any(token in value for token in ("失败", "驳回", "拒绝")):
        return "failed"
    if "审核中" in value:
        return "under_review"
    return "submitted"


def verify_remote_item(
    page,
    item: MaterialItem,
    selectors: dict[str, str],
    *,
    expected_store: str,
    submitted_at: str,
    workflow: str = "legacy_selectors",
    material_center_url: str = DEFAULT_MATERIAL_CENTER_URL,
) -> UploadOutcome:
    assert_store_identity(page, selectors["store_name"], expected_store)
    detect_human_check(page, selectors["human_check"])
    if workflow == "qianniu_recommend":
        observation = verify_qianniu_remote_item(
            page,
            item,
            material_center_url=material_center_url,
        )
        return UploadOutcome(
            observation.status,
            observation.reason_code,
            retry_allowed=False,
            remote_material_id=observation.remote_material_id,
            evidence=observation.evidence,
        )
    _prepare_exact_product(page, item, selectors)
    table = page.locator(selectors["remote_material_table"])
    if not table.is_visible():
        return UploadOutcome("publish_uncertain", "REMOTE_TABLE_NOT_VISIBLE", False)
    remote_id = page.locator(selectors["remote_material_id"]).inner_text().strip()
    if not remote_id:
        return UploadOutcome(
            "not_found",
            "REMOTE_ABSENCE_CONFIRMED",
            retry_allowed=False,
            evidence=f"product={item.product_id};slot={item.slot_index};remote_rows=0",
        )
    fingerprint = page.locator(selectors["remote_material_fingerprint"]).inner_text().strip()
    slot_text = page.locator(selectors["remote_material_slot"]).inner_text().strip()
    remote_status = page.locator(selectors["remote_material_status"]).inner_text().strip()
    remote_time = page.locator(selectors["remote_material_time"]).inner_text().strip()
    try:
        slot_matches = int(slot_text) == item.slot_index
        time_matches = datetime.fromisoformat(remote_time) >= (
            datetime.fromisoformat(submitted_at) - timedelta(minutes=5)
        )
    except (TypeError, ValueError):
        return UploadOutcome("publish_uncertain", "REMOTE_EVIDENCE_INVALID", False)
    if fingerprint != item.content_hash or not slot_matches or not time_matches:
        return UploadOutcome("publish_uncertain", "REMOTE_EVIDENCE_MISMATCH", False)
    return UploadOutcome(
        _status_name(remote_status),
        "",
        retry_allowed=False,
        remote_material_id=remote_id,
        evidence=(
            f"product={item.product_id};slot={item.slot_index};"
            f"remote_id={remote_id};status={remote_status};time={remote_time}"
        ),
    )
