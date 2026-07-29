"""Deterministic, explainable slot planning for selected source images."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping


STRATEGY_ID = "selected-assets-balanced-slots"
STRATEGY_VERSION = 1
STRATEGY_CONTRACT = {
    "strategy_id": STRATEGY_ID,
    "strategy_version": STRATEGY_VERSION,
    "minimum_images_per_slot": 3,
    "maximum_images_per_slot": 9,
    "source_order": "selection_order_then_source_round_robin",
    "ratio_order": [
        "common_feasibility",
        "native_count",
        "recommended_resolution_count",
        "retained_fraction",
        "compression_count",
        "prefer_3:4",
    ],
}
STRATEGY_SHA256 = hashlib.sha256(
    json.dumps(
        STRATEGY_CONTRACT,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()


def balanced_slot_sizes(image_count: int, missing_slots: int) -> list[int]:
    """Return balanced complete-slot sizes, never creating a 1–2 image slot."""

    count = max(0, int(image_count))
    capacity = max(0, int(missing_slots))
    slot_count = min(capacity, count // 3)
    if slot_count == 0:
        return []
    assigned_count = min(count, slot_count * 9)
    base, remainder = divmod(assigned_count, slot_count)
    return [
        base + (1 if index < remainder else 0)
        for index in range(slot_count)
    ]


def _asset_identity(asset: Mapping[str, Any]) -> str:
    return str(asset.get("source_sha256") or asset.get("asset_id") or "")


def _source_key(asset: Mapping[str, Any]) -> str:
    return str(
        asset.get("candidate_directory")
        or asset.get("folder_path")
        or asset.get("source_system")
        or ""
    )


def stable_source_interleave(
    assets: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Deduplicate and round-robin source folders without filesystem ordering."""

    prepared = sorted(
        (dict(raw) for raw in assets),
        key=lambda item: (
            int(item.get("selection_order") or 0),
            _source_key(item),
            str(item.get("source_path", "")),
            str(item.get("asset_id", "")),
        ),
    )
    unique: dict[str, dict[str, Any]] = {}
    for asset in prepared:
        identity = _asset_identity(asset)
        if identity:
            unique.setdefault(identity, asset)
    ordered = sorted(
        unique.values(),
        key=lambda item: (
            int(item.get("selection_order") or 0),
            _source_key(item),
            str(item.get("source_path", "")),
            str(item.get("asset_id", "")),
        ),
    )
    groups: dict[str, list[dict[str, Any]]] = {}
    for asset in ordered:
        groups.setdefault(_source_key(asset), []).append(asset)
    source_order = sorted(
        groups,
        key=lambda key: (
            min(int(item.get("selection_order") or 0) for item in groups[key]),
            key,
        ),
    )
    result: list[dict[str, Any]] = []
    while any(groups.values()):
        for key in source_order:
            if groups[key]:
                result.append(groups[key].pop(0))
    return result


def _ratio_option(asset: Mapping[str, Any], ratio: str) -> Mapping[str, Any]:
    value = asset.get("ratio_options", {}).get(ratio, {})
    return value if isinstance(value, Mapping) else {}


def _ratio_feasible(asset: Mapping[str, Any], ratio: str) -> bool:
    option = _ratio_option(asset, ratio)
    return bool(option) and option.get("feasible", True) is not False


def choose_slot_ratio(
    assets: Iterable[Mapping[str, Any]],
) -> tuple[str, dict[str, Any]]:
    """Choose one common ratio using the documented lexicographic score."""

    values = [dict(item) for item in assets]
    feasible = [
        ratio
        for ratio in ("3:4", "1:1")
        if values and all(_ratio_feasible(item, ratio) for item in values)
    ]
    if not feasible:
        raise ValueError("NO_COMMON_SLOT_RATIO")

    def score(ratio: str) -> tuple[Any, ...]:
        options = [_ratio_option(item, ratio) for item in values]
        native_count = sum(bool(item.get("native_ratio")) for item in options)
        recommended_count = sum(
            bool(
                item.get("recommended_resolution_pass")
                or item.get("assessment", {}).get("recommended_pass")
            )
            for item in options
        )
        retained = sum(
            float(item.get("retained_fraction") or 0) for item in options
        )
        compression_count = sum(
            bool(item.get("requires_compression")) for item in options
        )
        return (
            native_count,
            recommended_count,
            retained,
            -compression_count,
            1 if ratio == "3:4" else 0,
        )

    selected = max(feasible, key=score)
    selected_options = [_ratio_option(item, selected) for item in values]
    explanation = {
        "common_ratios": feasible,
        "native_count": sum(
            bool(item.get("native_ratio")) for item in selected_options
        ),
        "recommended_resolution_count": sum(
            bool(
                item.get("recommended_resolution_pass")
                or item.get("assessment", {}).get("recommended_pass")
            )
            for item in selected_options
        ),
        "average_retained_fraction": round(
            sum(
                float(item.get("retained_fraction") or 0)
                for item in selected_options
            )
            / len(selected_options),
            6,
        ),
        "compression_count": sum(
            bool(item.get("requires_compression")) for item in selected_options
        ),
    }
    return selected, explanation


def build_deterministic_slot_plan(
    board_data: Mapping[str, Any],
    *,
    missing_slots_by_product: Mapping[str, int],
) -> dict[str, Any]:
    """Create the sole initial slot draft for all products in a board."""

    assignments: list[dict[str, Any]] = []
    unused_assets: list[dict[str, Any]] = []
    products_summary: list[dict[str, Any]] = []
    for product in sorted(
        (
            item
            for item in board_data.get("products", [])
            if isinstance(item, Mapping)
        ),
        key=lambda item: str(item.get("product_id", "")),
    ):
        product_id = str(product.get("product_id", ""))
        ordered = stable_source_interleave(product.get("assets", []))
        missing_slots = max(
            0, int(missing_slots_by_product.get(product_id, 0))
        )
        sizes = balanced_slot_sizes(len(ordered), missing_slots)
        available = list(ordered)
        created = 0
        for requested_size in sizes:
            seed = available[:requested_size]
            try:
                ratio, ratio_score = choose_slot_ratio(seed)
            except ValueError:
                ratio = max(
                    ("3:4", "1:1"),
                    key=lambda value: (
                        sum(_ratio_feasible(item, value) for item in available),
                        value == "3:4",
                    ),
                )
                seed = [
                    item for item in available if _ratio_feasible(item, ratio)
                ][:requested_size]
                if len(seed) < 3:
                    break
                ratio, ratio_score = choose_slot_ratio(seed)
            selected_identities = {_asset_identity(item) for item in seed}
            available = [
                item
                for item in available
                if _asset_identity(item) not in selected_identities
            ]
            created += 1
            assignments.append(
                {
                    "slot_id": f"{product_id}-slot-{created}",
                    "product_id": product_id,
                    "target_ratio": ratio,
                    "asset_ids": [str(item["asset_id"]) for item in seed],
                    "plan_source": "deterministic",
                    "theme": "",
                    "quantity_reason": (
                        f"已选 {len(ordered)} 张、后台缺 {missing_slots} 个坑位；"
                        f"优先创建 {len(sizes)} 个完整坑位并均衡分配"
                    ),
                    "ratio_reason": ratio_score,
                }
            )
        unused_assets.extend(
            {
                "product_id": product_id,
                "asset_id": str(item.get("asset_id", "")),
                "source_sha256": str(item.get("source_sha256", "")),
                "source_path": str(item.get("source_path", "")),
                "reason_code": (
                    "SLOT_CAPACITY_EXCEEDED"
                    if len(ordered) > sum(sizes)
                    else "NO_COMPLETE_COMPATIBLE_SLOT"
                ),
            }
            for item in available
        )
        products_summary.append(
            {
                "product_id": product_id,
                "missing_slots": missing_slots,
                "usable_unique_images": len(ordered),
                "complete_slots": created,
                "slot_sizes": [
                    len(item["asset_ids"])
                    for item in assignments
                    if item["product_id"] == product_id
                ],
                "unused_count": len(available),
                "needs_more_images": (
                    max(0, 3 - len(ordered)) if missing_slots else 0
                ),
            }
        )
    return {
        "schema_version": 1,
        "record_type": "deterministic_slot_plan",
        "strategy_id": STRATEGY_ID,
        "strategy_version": STRATEGY_VERSION,
        "strategy_sha256": STRATEGY_SHA256,
        "asset_matching_revision": board_data.get("asset_matching_revision"),
        "image_review_revision": board_data.get("image_review_revision"),
        "assignments": assignments,
        "unused_assets": unused_assets,
        "products": products_summary,
    }
