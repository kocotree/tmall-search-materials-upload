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
