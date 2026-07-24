from collections import defaultdict
from dataclasses import dataclass, field
import re
from typing import Iterable, Mapping


BASIC_MATERIAL_FIELDS = (
    "卖点1",
    "卖点2",
    "商品白底图",
    "商品透明图",
    "方版场景图",
    "商品长图",
    "商家短标题",
    "品牌LOGO透明图",
    "利益点（短）",
)


@dataclass(frozen=True)
class MaterialGap:
    missing_slots: int | None
    needs_page_supplement: bool
    reason_code: str = ""


@dataclass
class MaterialSnapshot:
    product_id: str
    basic_missing_fields: list[str] = field(default_factory=list)
    material_items: list[dict] = field(default_factory=list)
    image_text_count: int = 0
    video_count: int = 0
    desired_slots: int | None = None
    empty_slot_indexes: list[int] | None = None
    review_states_complete: bool = False
    collected_at: str = ""
    evidence: str = ""


def calculate_gap(desired_slots: int | None, items: Iterable[Mapping[str, str]]) -> MaterialGap:
    records = list(items)
    if desired_slots not in (3, 9):
        return MaterialGap(None, True, "SLOT_REQUIREMENT_UNKNOWN")
    occupied = sum(
        1
        for item in records
        if str(item.get("status", "")).casefold() not in {"rejected", "deleted"}
    )
    return MaterialGap(max(desired_slots - occupied, 0), False, "")


def _parse_int(value: object) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    return int(float(text))


def _parse_indexes(value: object) -> list[int]:
    text = str(value or "").strip()
    if not text:
        return []
    return [int(part) for part in re.findall(r"\d+", text)]


def _parse_bool(value: object) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "是", "完整"}


def merge_material_state(
    basic_rows: Iterable[Mapping[str, str]],
    search_rows: Iterable[Mapping[str, str]],
    backend_rows: Iterable[Mapping[str, str]] | None = None,
) -> list[MaterialSnapshot]:
    basic = list(basic_rows)
    search = list(search_rows)
    backend = list(backend_rows or [])
    product_ids = []
    for row in [*basic, *search, *backend]:
        product_id = str(row.get("商品ID", "")).strip()
        if product_id and product_id not in product_ids:
            product_ids.append(product_id)

    basic_by_id = {str(row.get("商品ID", "")).strip(): row for row in basic}
    search_by_id: dict[str, list[dict]] = defaultdict(list)
    for row in search:
        product_id = str(row.get("商品ID", "")).strip()
        material_id = str(row.get("素材id", row.get("素材ID", ""))).strip()
        if not product_id or not material_id:
            continue
        material_type = str(row.get("素材类型", "")).strip()
        search_by_id[product_id].append(
            {
                "remote_material_id": material_id,
                "type": "video" if "视频" in material_type else "image_text",
                "status": str(row.get("审核状态", "existing")).strip() or "existing",
            }
        )
    backend_by_id = {str(row.get("商品ID", "")).strip(): row for row in backend}

    snapshots = []
    for product_id in product_ids:
        basic_row = basic_by_id.get(product_id, {})
        items = search_by_id.get(product_id, [])
        backend_row = backend_by_id.get(product_id)
        desired_slots = _parse_int(backend_row.get("目标坑位")) if backend_row else None
        empty_slots = _parse_indexes(backend_row.get("空坑位")) if backend_row else None
        review_complete = _parse_bool(backend_row.get("审核状态完整")) if backend_row else False
        snapshots.append(
            MaterialSnapshot(
                product_id=product_id,
                basic_missing_fields=[
                    field_name
                    for field_name in BASIC_MATERIAL_FIELDS
                    if not str(basic_row.get(field_name, "")).strip()
                ],
                material_items=items,
                image_text_count=sum(item["type"] == "image_text" for item in items),
                video_count=sum(item["type"] == "video" for item in items),
                desired_slots=desired_slots,
                empty_slot_indexes=empty_slots,
                review_states_complete=review_complete,
                collected_at=str(backend_row.get("采集时间", "")) if backend_row else "",
                evidence=str(backend_row.get("证据", "")) if backend_row else "",
            )
        )
    return snapshots


def products_requiring_supplement(snapshots: Iterable[MaterialSnapshot]) -> list[str]:
    return [
        snapshot.product_id
        for snapshot in snapshots
        if snapshot.desired_slots not in (3, 9)
        or snapshot.empty_slot_indexes is None
        or snapshot.review_states_complete is False
    ]


def build_completeness_matrix(
    basic_rows: Iterable[Mapping[str, str]],
    promotion_rows: Iterable[Mapping[str, str]],
    *,
    products: Iterable[Mapping[str, str]] = (),
    candidate_counts: Mapping[str, int] | None = None,
) -> dict:
    """Build the stable data contract rendered by interaction stage 02.

    Missing source rows remain explicit ``unknown``/``needs_backend_collection``
    states.  The matrix never infers a 3/9 target from a category label.
    """

    basic_by_id = {
        str(row.get("商品ID", "")).strip(): row
        for row in basic_rows
        if str(row.get("商品ID", "")).strip()
    }
    promotion_by_id = {
        str(row.get("商品ID", "")).strip(): row
        for row in promotion_rows
        if str(row.get("商品ID", "")).strip()
    }
    product_by_id = {}
    for row in products:
        product_id = str(row.get("商品ID", row.get("product_id", ""))).strip()
        if product_id:
            product_by_id[product_id] = row

    product_ids = []
    for product_id in [*basic_by_id, *promotion_by_id]:
        if product_id not in product_ids:
            product_ids.append(product_id)

    counts = candidate_counts or {}
    records = []
    for product_id in product_ids:
        product = product_by_id.get(product_id, {})
        basic = basic_by_id.get(product_id)
        promotion = promotion_by_id.get(product_id)

        target_slots = _promotion_int(promotion, "目标容量", "目标坑位")
        current_count = _promotion_int(promotion, "现有素材数")
        missing_count = _promotion_int(promotion, "缺失数量")
        if missing_count is None and target_slots is not None and current_count is not None:
            missing_count = max(target_slots - current_count, 0)
        reason_codes = _split_tokens(promotion.get("原因码", "")) if promotion else []
        remote_ids = _split_tokens(promotion.get("远端素材ID", "")) if promotion else []
        promotion_status = (
            str(promotion.get("状态", "")).strip() or "needs_manual_review"
            if promotion
            else "needs_backend_collection"
        )

        if promotion is None:
            overall_status = "needs_backend_collection"
        elif promotion_status in {"error", "blocked", "abnormal"}:
            overall_status = "abnormal"
        elif (missing_count or 0) > 0:
            overall_status = "needs_supplement"
        elif missing_count == 0:
            overall_status = "complete"
        else:
            overall_status = "needs_manual_review"

        records.append(
            {
                "product_id": product_id,
                "sku": str(
                    product.get("货号（查找引用）", product.get("sku", ""))
                ).strip(),
                "product_title": str(
                    product.get("商品名称（查找引用）", product.get("product_title", ""))
                    or (basic or {}).get("商品标题", "")
                ).strip(),
                "status": overall_status,
                "promotion": {
                    "status": promotion_status,
                    "target_slots": target_slots,
                    "current_count": current_count,
                    "missing_count": missing_count,
                    "empty_slot_indexes": _parse_indexes(
                        promotion.get("空坑位", "") if promotion else ""
                    ),
                    "remote_material_ids": remote_ids,
                    "reason_codes": reason_codes,
                    "collected_at": str(
                        promotion.get("采集时间", "") if promotion else ""
                    ).strip(),
                    "evidence": str(promotion.get("证据", "") if promotion else "").strip(),
                },
                "candidate_asset_count": (
                    int(counts[product_id]) if product_id in counts else None
                ),
            }
        )

    status_counts: dict[str, int] = defaultdict(int)
    for record in records:
        status_counts[record["status"]] += 1
    return {
        "contract_version": 1,
        "products": records,
        "summary": {
            "product_count": len(records),
            "status_counts": dict(sorted(status_counts.items())),
        },
    }


def _promotion_int(row: Mapping[str, str] | None, *names: str) -> int | None:
    if row is None:
        return None
    for name in names:
        value = str(row.get(name, "")).strip()
        if value:
            try:
                return int(float(value))
            except ValueError:
                return None
    return None


def _split_tokens(value: object) -> list[str]:
    return [item.strip() for item in re.split(r"[;,，；|]+", str(value or "")) if item.strip()]
