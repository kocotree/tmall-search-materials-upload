from collections import defaultdict
import hashlib
import json
from typing import Iterable, Mapping

from .copywriting import CopyResult
from .models import (
    AssetRecord,
    MaterialItem,
    MaterialStatus,
    ProductRecord,
    ProductStatus,
    ProductTask,
    make_task_id,
)


def _canonical_hash(value: dict) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _item_content_hash(
    product_id: str,
    material_type: str,
    slot_index: int,
    assets: list[AssetRecord],
    copy: CopyResult,
) -> str:
    return _canonical_hash(
        {
            "product_id": product_id,
            "material_type": material_type,
            "slot_index": slot_index,
            "asset_sha256": sorted(asset.sha256 for asset in assets),
            "title": copy.title,
            "description": copy.description,
        }
    )


def build_material_items(
    *,
    run_id: str,
    product_id: str,
    target_slot_indexes: list[int],
    asset_groups: list[tuple[str, list[AssetRecord]]],
    copy_results: list[CopyResult],
) -> list[MaterialItem]:
    if not (len(target_slot_indexes) == len(asset_groups) == len(copy_results)):
        raise ValueError("坑位、素材组和文案数量必须一致")
    items = []
    for slot_index, (material_type, assets), copy in zip(
        target_slot_indexes,
        asset_groups,
        copy_results,
        strict=True,
    ):
        reasons = list(copy.reason_codes)
        for asset in assets:
            for reason in asset.reason_codes:
                if reason not in reasons:
                    reasons.append(reason)
        valid = copy.status == "valid" and assets and all(
            asset.validation_status == "valid" for asset in assets
        )
        if not assets:
            reasons.append("ASSET_NOT_FOUND")
        items.append(
            MaterialItem(
                task_id=make_task_id(run_id, product_id, material_type, slot_index),
                product_id=product_id,
                material_type=material_type,
                slot_index=slot_index,
                status=(
                    MaterialStatus.READY_FOR_REVIEW
                    if valid
                    else MaterialStatus.NEEDS_MANUAL_REVIEW
                ),
                assets=list(assets),
                title=copy.title,
                description=copy.description,
                content_hash=_item_content_hash(
                    product_id,
                    material_type,
                    slot_index,
                    assets,
                    copy,
                ),
                reason_codes=reasons,
            )
        )
    return items


def _product_task_id(run_id: str, product_id: str, source_row: object) -> str:
    raw = f"{run_id}|{product_id}|{source_row}".encode("utf-8")
    return "PRD-" + hashlib.sha256(raw).hexdigest()[:16]


def build_product_tasks(
    *,
    run_id: str,
    products: Iterable[ProductRecord],
    decisions_by_product: Mapping[str, tuple[str, list[str]]],
    desired_slots_by_product: Mapping[str, int],
    material_items_by_product: Mapping[str, list[MaterialItem]],
) -> list[ProductTask]:
    tasks = []
    for fallback_row, product in enumerate(products, 2):
        status_name, reasons = decisions_by_product.get(
            product.product_id,
            ("blocked", ["ELIGIBILITY_DECISION_MISSING"]),
        )
        desired_slots = desired_slots_by_product.get(product.product_id)
        items = material_items_by_product.get(product.product_id, [])
        if status_name == "excluded":
            status = ProductStatus.EXCLUDED
        elif status_name == "blocked":
            status = ProductStatus.BLOCKED
        elif desired_slots not in (3, 9):
            status = ProductStatus.BLOCKED
            reasons = [*reasons, "SLOT_REQUIREMENT_UNKNOWN"]
        elif not items:
            status = ProductStatus.BLOCKED
            reasons = [*reasons, "MATERIAL_ITEMS_MISSING"]
        elif all(item.status == MaterialStatus.READY_FOR_REVIEW for item in items):
            status = ProductStatus.READY_FOR_REVIEW
        else:
            status = ProductStatus.BLOCKED
            for item in items:
                for reason in item.reason_codes:
                    if reason not in reasons:
                        reasons.append(reason)
        source_row = product.raw.get("_source_row", fallback_row)
        tasks.append(
            ProductTask(
                task_id=_product_task_id(run_id, product.product_id, source_row),
                run_id=run_id,
                product_id=product.product_id,
                status=status,
                desired_slots=desired_slots,
                owner=product.owner,
                reason_codes=list(reasons),
            )
        )
    return tasks
