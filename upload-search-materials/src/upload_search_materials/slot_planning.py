"""Stage-five slot-board preparation and hard validation."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping

from PIL import Image, ImageOps, UnidentifiedImageError

from .image_compliance import KIB, MIB, _ratio_value, normalize_image_policy
from .image_compliance import PillowImageCompressionProvider, provider_contract_payload
from .interaction.session import InteractionConflict, SessionStore
from .io_tables import sha256_file


def slot_plan_sha256(assignments: Iterable[Mapping[str, Any]]) -> str:
    return hashlib.sha256(
        json.dumps(list(assignments), sort_keys=True).encode("utf-8")
    ).hexdigest()


def _display_ratio(width: Any, height: Any) -> str:
    try:
        width_value = int(width)
        height_value = int(height)
    except (TypeError, ValueError):
        return ""
    if width_value <= 0 or height_value <= 0:
        return ""
    divisor = math.gcd(width_value, height_value)
    return f"{width_value // divisor}:{height_value // divisor}"


def _size_display(size_bytes: Any) -> str:
    try:
        value = int(size_bytes)
    except (TypeError, ValueError):
        return ""
    if value < 0:
        return ""
    if value >= MIB:
        return f"{value / MIB:.2f}MB"
    return f"{value / KIB:.2f}KB"


def _backfill_source_metadata(asset: dict[str, Any]) -> None:
    """Inspect one referenced source only when its historical snapshot is incomplete."""

    missing_dimensions = not asset.get("width") or not asset.get("height")
    missing_size = asset.get("size_bytes") in (None, "")
    missing_format = not asset.get("format")
    if not (missing_dimensions or missing_size or missing_format):
        return
    source_path = Path(str(asset.get("source_path", "")))
    if not source_path.is_file():
        return
    if missing_size:
        try:
            asset["size_bytes"] = source_path.stat().st_size
        except OSError:
            pass
    if missing_dimensions or missing_format:
        try:
            with Image.open(source_path) as opened:
                image = ImageOps.exif_transpose(opened)
                if missing_dimensions:
                    asset["width"], asset["height"] = image.size
                if missing_format:
                    asset["format"] = str(opened.format or "").upper()
        except (OSError, UnidentifiedImageError):
            pass
    if not asset.get("original_ratio"):
        asset["original_ratio"] = _display_ratio(
            asset.get("width"), asset.get("height")
        )
    if not asset.get("size_display"):
        asset["size_display"] = _size_display(asset.get("size_bytes"))


def normalize_product_assets(
    candidates: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Collapse ratio-specific candidates into one immutable source record."""

    assets: dict[tuple[str, str], dict[str, Any]] = {}
    for source in candidates:
        candidate = dict(source)
        asset_id = str(candidate.get("asset_id", ""))
        source_sha256 = str(candidate.get("source_sha256", ""))
        if not asset_id:
            continue
        key = (asset_id, source_sha256)
        current = assets.setdefault(
            key,
            {
                "asset_id": asset_id,
                "product_id": str(candidate.get("product_id", "")),
                "product_title": str(candidate.get("product_title", "")),
                "source_path": str(candidate.get("source_path", "")),
                "source_sha256": source_sha256,
                "source_system": str(candidate.get("source_system", "")),
                "candidate_directory": str(
                    candidate.get("candidate_directory", "")
                ),
                "selection_order": int(
                    candidate.get("selection_order") or 0
                ),
                "width": candidate.get("width"),
                "height": candidate.get("height"),
                "original_ratio": str(
                    candidate.get("original_ratio")
                    or _display_ratio(
                        candidate.get("width"), candidate.get("height")
                    )
                ),
                "size_bytes": candidate.get("size_bytes"),
                "size_display": str(candidate.get("size_display", "")),
                "format": str(candidate.get("format", "")),
                "ratio_options": {},
            },
        )
        for field in (
            "product_id",
            "product_title",
            "source_path",
            "source_system",
            "candidate_directory",
            "selection_order",
            "width",
            "height",
            "original_ratio",
            "size_bytes",
            "size_display",
            "format",
        ):
            if current.get(field) in (None, "") and candidate.get(field) not in (
                None,
                "",
            ):
                current[field] = candidate[field]
        ratio = str(candidate.get("target_ratio", ""))
        if ratio:
            current["ratio_options"][ratio] = {
                "crop_box": candidate.get("crop_box"),
                "native_ratio": bool(candidate.get("native_ratio")),
                "requires_compression": bool(
                    candidate.get("requires_compression")
                ),
                "estimated_width": candidate.get("estimated_width"),
                "estimated_height": candidate.get("estimated_height"),
                "retained_fraction": candidate.get("retained_fraction"),
                "kind": str(candidate.get("kind", "")),
            }
    for asset in assets.values():
        _backfill_source_metadata(asset)
    return sorted(
        assets.values(),
        key=lambda item: (item["product_id"], item["asset_id"]),
    )


def build_rule_slot_plan(
    suitability_data: Mapping[str, Any],
    suitability_decisions: Iterable[Mapping[str, Any]],
    *,
    image_review_revision: int,
) -> dict[str, Any]:
    """Build explainable slot drafts without requiring an Agent response."""

    records = {
        str(record.get("asset_id", "")): dict(record)
        for record in suitability_data.get(
            "suitability_records", suitability_data.get("assets", [])
        )
        if isinstance(record, Mapping) and record.get("asset_id")
    }
    decisions = {
        str(decision.get("asset_id", "")): dict(decision)
        for decision in suitability_decisions
        if isinstance(decision, Mapping) and decision.get("asset_id")
    }
    policy = normalize_image_policy(suitability_data.get("policy"))
    products: dict[str, list[dict[str, Any]]] = {}
    blocked: list[dict[str, str]] = []
    for asset_id, record in records.items():
        decision = decisions.get(asset_id)
        if (
            record.get("status") == "blocked"
            or not decision
            or decision.get("decision") == "excluded"
            or decision.get("action") == "excluded"
        ):
            blocked.append(
                {
                    "asset_id": asset_id,
                    "reason_code": (
                        "SUITABILITY_BLOCKED"
                        if record.get("status") == "blocked"
                        else "NOT_SELECTED_FOR_PLANNING"
                    ),
                }
            )
            continue
        candidate_ratios = decision.get("candidate_ratios")
        if not isinstance(candidate_ratios, list):
            candidate_ratios = list(record.get("crop_options", {}))
        for ratio in policy["allowed_aspect_ratios"]:
            if ratio not in candidate_ratios:
                continue
            assessment = record.get("ratio_options", {}).get(ratio, {})
            crop = decision.get("crop_candidates", {}).get(
                ratio, record.get("crop_options", {}).get(ratio)
            )
            if not crop:
                continue
            native = ratio in record.get("preflight", {}).get("matching_ratios", [])
            products.setdefault(str(record.get("product_id", "")), []).append(
                {
                    "asset_id": asset_id,
                    "product_id": str(record.get("product_id", "")),
                    "product_title": str(record.get("product_title", "")),
                    "target_ratio": ratio,
                    "source_path": str(record.get("source_path", "")),
                    "source_sha256": str(record.get("source_sha256", "")),
                    "source_system": str(record.get("source_system", "")),
                    "candidate_directory": str(
                        record.get("candidate_directory", "")
                    ),
                    "selection_order": int(
                        record.get("selection_order") or 0
                    ),
                    "width": record.get("width"),
                    "height": record.get("height"),
                    "size_bytes": record.get("size_bytes"),
                    "size_display": str(
                        record.get("preflight", {}).get("size_display", "")
                    ),
                    "original_ratio": str(
                        record.get("source_inspection", {}).get("ratio_display")
                        or record.get("preflight", {}).get("original_ratio")
                        or _display_ratio(
                            record.get("width"), record.get("height")
                        )
                    ),
                    "format": str(
                        record.get("source_inspection", {}).get("format", "")
                    ),
                    "crop_box": crop,
                    "native_ratio": native,
                    "requires_compression": bool(
                        decision.get("requires_compression")
                    ),
                    "estimated_width": assessment.get("assessment", {}).get(
                        "max_crop_width"
                    ),
                    "estimated_height": assessment.get("assessment", {}).get(
                        "max_crop_height"
                    ),
                    "retained_fraction": (
                        (
                            int(
                                assessment.get("assessment", {}).get(
                                    "max_crop_width", 0
                                )
                            )
                            * int(
                                assessment.get("assessment", {}).get(
                                    "max_crop_height", 0
                                )
                            )
                        )
                        / (int(record.get("width") or 1) * int(record.get("height") or 1))
                    ),
                    "kind": "direct_candidate" if native else "crop_candidate",
                }
            )
    rows: list[dict[str, Any]] = []
    rule_drafts: list[dict[str, Any]] = []
    for product_id, candidates in sorted(products.items()):
        available_by_ratio = {
            ratio: sum(item["target_ratio"] == ratio for item in candidates)
            for ratio in policy["allowed_aspect_ratios"]
        }
        rows.append(
            {
                "product_id": product_id,
                "product_title": str(
                    next(
                        (
                            item.get("product_title", "")
                            for item in candidates
                            if item.get("product_title")
                        ),
                        "",
                    )
                ),
                "outputs": sorted(
                    candidates,
                    key=lambda item: (
                        item["target_ratio"],
                        not item["native_ratio"],
                        item["requires_compression"],
                        item["asset_id"],
                    ),
                ),
                "assets": normalize_product_assets(candidates),
                "available_by_ratio": available_by_ratio,
            }
        )
        def _ratio_rank(ratio: str) -> tuple[Any, ...]:
            ratio_items = [
                item for item in candidates if item["target_ratio"] == ratio
            ]
            native_count = sum(item["native_ratio"] for item in ratio_items)
            compression_count = sum(
                item["requires_compression"] for item in ratio_items
            )
            retained = (
                sum(item["retained_fraction"] for item in ratio_items)
                / len(ratio_items)
                if ratio_items
                else 0
            )
            return (
                -available_by_ratio[ratio],
                -native_count,
                -retained,
                compression_count,
                ratio,
            )

        ranked_ratios = sorted(
            policy["allowed_aspect_ratios"],
            key=_ratio_rank,
        )
        for proposal_index, ratio in enumerate(ranked_ratios[:2], start=1):
            eligible = [
                item for item in candidates if item["target_ratio"] == ratio
            ]
            unique: dict[str, dict[str, Any]] = {}
            for item in eligible:
                unique.setdefault(
                    str(item.get("source_sha256") or item["asset_id"]), item
                )
            source_groups: dict[str, list[dict[str, Any]]] = {}
            for item in sorted(
                unique.values(),
                key=lambda value: (
                    not value["native_ratio"],
                    value["requires_compression"],
                    value["asset_id"],
                ),
            ):
                source_groups.setdefault(
                    str(item.get("source_system", "")), []
                ).append(item)
            eligible = []
            while any(source_groups.values()):
                for source in sorted(source_groups):
                    if source_groups[source]:
                        eligible.append(source_groups[source].pop(0))
            if len(eligible) < 3:
                continue
            ordered_ids = [item["asset_id"] for item in eligible[:9]]
            selected_items = eligible[:9]
            native_count = sum(item["native_ratio"] for item in selected_items)
            crop_count = sum(not item["native_ratio"] for item in selected_items)
            compression_count = sum(
                item["requires_compression"] for item in selected_items
            )
            retained_fraction = (
                sum(item["retained_fraction"] for item in selected_items)
                / len(selected_items)
            )
            rule_score = round(
                min(
                    1.0,
                    0.35
                    + min(len(selected_items), 9) / 18
                    + native_count / max(1, len(selected_items)) * 0.1
                    + retained_fraction * 0.05,
                ),
                2,
            )
            rule_drafts.append(
                {
                    "schema_version": 1,
                    "plan_source": "rules",
                    "proposal_id": f"rules-{product_id}-{proposal_index}",
                    "product_id": product_id,
                    "product_title": str(
                        selected_items[0].get("product_title", "")
                    ),
                    "slot_id": f"{product_id}-slot-1",
                    "target_ratio": ratio,
                    "ordered_asset_ids": ordered_ids,
                    "reason": (
                        f"{ratio} 候选 {len(selected_items)} 张；"
                        f"原生 {native_count} 张、需裁剪 {crop_count} 张、"
                        f"需压缩 {compression_count} 张，"
                        f"平均保留画面 {retained_fraction:.0%}"
                    ),
                    "rule_score": rule_score,
                    "score_components": {
                        "candidate_count": len(selected_items),
                        "native_count": native_count,
                        "retained_fraction": round(retained_fraction, 4),
                        "compression_count": compression_count,
                    },
                    "warnings": (
                        [] if len(ordered_ids) >= 3 else ["IMAGE_COUNT_INVALID"]
                    ),
                    "estimated_processing_count": sum(
                        not item["native_ratio"] or item["requires_compression"]
                        for item in selected_items
                    ),
                    "estimated_crop_count": crop_count,
                    "estimated_compression_count": compression_count,
                }
            )
    return {
        "schema_version": 2,
        "record_type": "slot_plan",
        "image_review_revision": image_review_revision,
        "asset_matching_revision": suitability_data.get(
            "asset_matching_revision"
        ),
        "policy": policy,
        "policy_sha256": suitability_data.get("policy_sha256"),
        "products": rows,
        "blocked_outputs": blocked,
        # Historical result files may still contain rule_drafts, but new boards
        # deliberately expose no rule plan or automatic fallback.
        "rule_drafts": [],
        "slot_image_min": 3,
        "slot_image_max": 9,
        "workflow_state": "planning",
    }


def validate_final_output(
    output: Mapping[str, Any],
    *,
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    """Re-read the actual output instead of trusting browser-submitted metadata."""

    image_policy = normalize_image_policy(policy)
    path = Path(str(output.get("output_path", ""))).resolve()
    if not path.is_file():
        raise ValueError("OUTPUT_IDENTITY_MISMATCH: output file is unavailable")
    actual_sha256 = sha256_file(path)
    if actual_sha256 != str(output.get("output_sha256", "")):
        raise ValueError("OUTPUT_IDENTITY_MISMATCH")
    size_bytes = path.stat().st_size
    minimum = round(image_policy["min_size_kb"] * KIB)
    maximum = round(image_policy["max_size_mb"] * MIB)
    if size_bytes < minimum:
        raise ValueError("OUTPUT_SIZE_BELOW_MINIMUM")
    if size_bytes > maximum:
        raise ValueError("IMAGE_SIZE_EXCEEDED")
    try:
        with Image.open(path) as opened:
            image = ImageOps.exif_transpose(opened)
            width, height = image.size
    except (OSError, UnidentifiedImageError) as error:
        raise ValueError("SOURCE_UNREADABLE: output cannot be read") from error
    if width < image_policy["min_width"] or height < image_policy["min_height"]:
        raise ValueError("OUTPUT_DIMENSIONS_BELOW_MINIMUM")
    actual_ratio = width / height
    matching = [
        ratio
        for ratio in image_policy["allowed_aspect_ratios"]
        if abs(actual_ratio - _ratio_value(ratio))
        <= image_policy["aspect_ratio_tolerance"]
    ]
    expected_ratio = str(output.get("target_ratio", ""))
    if expected_ratio not in matching:
        raise ValueError("OUTPUT_ASPECT_RATIO_INVALID")
    return {
        **dict(output),
        "output_path": str(path),
        "output_sha256": actual_sha256,
        "output_width": width,
        "output_height": height,
        "output_size_bytes": size_bytes,
        "target_ratio": expected_ratio,
        "verified": True,
    }


def build_slot_board_data(
    decisions: Iterable[Mapping[str, Any]],
    *,
    image_review_revision: int,
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    image_policy = normalize_image_policy(policy)
    products: dict[str, list[dict[str, Any]]] = {}
    blocked: list[dict[str, Any]] = []
    for source in decisions:
        decision = dict(source)
        if decision.get("action") not in {
            "direct",
            "crop",
            "compress",
            "crop_and_compress",
        }:
            continue
        output = decision.get("output")
        if not isinstance(output, Mapping):
            blocked.append({
                "asset_id": str(decision.get("asset_id", "")),
                "reason_code": "IMAGE_REVIEW_OUTPUT_MISSING",
            })
            continue
        try:
            verified = validate_final_output(output, policy=image_policy)
        except ValueError as error:
            blocked.append({
                "asset_id": str(decision.get("asset_id", "")),
                "reason_code": str(error).split(":", 1)[0],
            })
            continue
        size_bytes = int(verified["output_size_bytes"])
        product_id = str(decision.get("product_id", ""))
        products.setdefault(product_id, []).append({
            "asset_id": str(decision.get("asset_id", "")),
            "product_id": product_id,
            "target_ratio": str(
                verified.get("target_ratio") or decision.get("target_ratio") or ""
            ),
            "output_path": str(verified.get("output_path", "")),
            "output_sha256": str(verified.get("output_sha256", "")),
            "output_width": verified.get("output_width"),
            "output_height": verified.get("output_height"),
            "output_size_bytes": size_bytes,
            "kind": str(verified.get("kind", decision.get("action", ""))),
            "image_review_revision": image_review_revision,
        })
    rows = [
        {
            "product_id": product_id,
            "outputs": sorted(
                outputs,
                key=lambda item: (
                    item["target_ratio"],
                    item["asset_id"],
                ),
            ),
            "available_by_ratio": {
                ratio: sum(
                    item["target_ratio"] == ratio for item in outputs
                )
                for ratio in image_policy["allowed_aspect_ratios"]
            },
        }
        for product_id, outputs in sorted(products.items())
    ]
    return {
        "schema_version": 1,
        "image_review_revision": image_review_revision,
        "policy": image_policy,
        "products": rows,
        "blocked_outputs": blocked,
        "slot_image_min": 3,
        "slot_image_max": 9,
    }


def validate_slot_assignments(
    assignments: Any,
    board_data: Mapping[str, Any],
    *,
    allow_incomplete: bool = False,
) -> list[dict[str, Any]]:
    if not isinstance(assignments, list):
        raise ValueError("slot_assignments must be a list")
    outputs = {
        (
            str(output.get("asset_id", "")),
            str(output.get("target_ratio", "")),
        ): output
        for product in board_data.get("products", [])
        if isinstance(product, Mapping)
        for output in product.get("outputs", [])
        if isinstance(output, Mapping) and output.get("asset_id")
    }
    outputs_by_asset: dict[str, list[Mapping[str, Any]]] = {}
    for (asset_id, _ratio), output in outputs.items():
        outputs_by_asset.setdefault(asset_id, []).append(output)
    minimum = int(board_data.get("slot_image_min", 3))
    maximum = int(board_data.get("slot_image_max", 9))
    normalized = []
    seen_slots: set[str] = set()
    seen_assets: dict[tuple[str, str], str] = {}
    for raw in assignments:
        if not isinstance(raw, Mapping):
            raise ValueError("each slot assignment must be an object")
        slot_id = str(raw.get("slot_id", "")).strip()
        product_id = str(raw.get("product_id", "")).strip()
        asset_ids = [
            str(value).strip()
            for value in raw.get("asset_ids", [])
            if str(value).strip()
        ] if isinstance(raw.get("asset_ids"), list) else []
        if not slot_id or slot_id in seen_slots:
            raise ValueError("slot_id is required and must be unique")
        seen_slots.add(slot_id)
        if (
            len(asset_ids) > maximum
            or (not allow_incomplete and len(asset_ids) < minimum)
        ):
            raise ValueError(
                f"{slot_id}: IMAGE_COUNT_INVALID ({len(asset_ids)}, expected {minimum}-{maximum})"
            )
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError(f"{slot_id}: DUPLICATE_ASSET")
        for asset_id in asset_ids:
            candidates = outputs_by_asset.get(asset_id, [])
            if not candidates:
                raise ValueError(f"{slot_id}: unconfirmed or stale asset {asset_id}")
            if not any(
                str(candidate.get("product_id", "")) == product_id
                for candidate in candidates
            ):
                raise ValueError(f"{slot_id}: asset belongs to another product")
            source_sha256 = str(candidates[0].get("source_sha256", ""))
            identity = (asset_id, source_sha256)
            previous_slot = seen_assets.get(identity)
            if previous_slot is not None and previous_slot != slot_id:
                raise ValueError(
                    f"{slot_id}: ASSET_ASSIGNED_TO_MULTIPLE_SLOTS "
                    f"({asset_id}, also in {previous_slot})"
                )
            seen_assets[identity] = slot_id
        target_ratio = str(raw.get("target_ratio", "")).strip()
        allowed_ratios = {
            str(value)
            for value in board_data.get("policy", {}).get(
                "allowed_aspect_ratios", ("3:4", "1:1")
            )
        }
        if target_ratio and target_ratio not in allowed_ratios:
            raise ValueError(f"{slot_id}: TARGET_RATIO_INVALID")
        if not target_ratio and asset_ids:
            common_ratios = {
                str(item.get("target_ratio", ""))
                for item in outputs_by_asset.get(asset_ids[0], [])
            }
            for asset_id in asset_ids[1:]:
                common_ratios &= {
                    str(item.get("target_ratio", ""))
                    for item in outputs_by_asset.get(asset_id, [])
                }
            if len(common_ratios) == 1:
                target_ratio = next(iter(common_ratios))
            elif not common_ratios:
                raise ValueError(f"{slot_id}: MIXED_ASPECT_RATIO")
        selected = []
        for asset_id in asset_ids:
            output = outputs.get((asset_id, target_ratio))
            if output is None:
                raise ValueError(f"{slot_id}: unconfirmed or stale asset {asset_id}")
            if str(output.get("product_id", "")) != product_id:
                raise ValueError(f"{slot_id}: asset belongs to another product")
            selected.append(output)
        ratios = {str(item.get("target_ratio", "")) for item in selected}
        if selected and len(ratios) != 1:
            raise ValueError(f"{slot_id}: MIXED_ASPECT_RATIO")
        if not target_ratio:
            if allow_incomplete and not selected:
                target_ratio = "3:4"
            else:
                raise ValueError(f"{slot_id}: TARGET_RATIO_REQUIRED")
        normalized.append({
            "schema_version": 1,
            "plan_source": str(raw.get("plan_source", "manual") or "manual"),
            "slot_id": slot_id,
            "product_id": product_id,
            "asset_ids": asset_ids,
            "ordered_asset_ids": asset_ids,
            "target_ratio": target_ratio,
            "image_review_revision": board_data.get("image_review_revision"),
            "policy_sha256": board_data.get("policy_sha256"),
        })
    if not normalized:
        raise ValueError("at least one slot assignment is required")
    return normalized


def materialize_confirmed_slot_plan(
    assignments: Iterable[Mapping[str, Any]],
    board_data: Mapping[str, Any],
    *,
    derived_root: Path,
    crop_parameters: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate and revalidate task-local outputs only after plan validation."""

    normalized = validate_slot_assignments(list(assignments), board_data)
    candidates = {
        (
            str(candidate.get("asset_id", "")),
            str(candidate.get("target_ratio", "")),
        ): candidate
        for product in board_data.get("products", [])
        if isinstance(product, Mapping)
        for candidate in product.get("outputs", [])
        if isinstance(candidate, Mapping)
    }
    policy = normalize_image_policy(board_data.get("policy"))
    provider = PillowImageCompressionProvider(policy)
    maximum = round(policy["max_size_mb"] * MIB)
    plan_sha256 = slot_plan_sha256(normalized)
    crop_values = dict(crop_parameters or {})
    processing_sha256 = hashlib.sha256(
        json.dumps(
            {
                "plan_sha256": plan_sha256,
                "crop_parameters": crop_values,
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    slots: list[dict[str, Any]] = []
    root = Path(derived_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    for assignment in normalized:
        outputs = []
        for order, asset_id in enumerate(
            assignment["ordered_asset_ids"], start=1
        ):
            candidate = candidates[(asset_id, assignment["target_ratio"])]
            crop_key = f"{assignment['slot_id']}:{asset_id}"
            crop_parameter = crop_values.get(crop_key, {})
            if crop_parameter and not isinstance(crop_parameter, Mapping):
                raise ValueError("CROP_PARAMETER_INVALID")
            submitted_box = (
                crop_parameter.get("normalized_box")
                if isinstance(crop_parameter, Mapping)
                else None
            )
            if (
                bool(candidate.get("requires_compression"))
                and (
                    not isinstance(crop_parameter, Mapping)
                    or crop_parameter.get("confirm_compression") is not True
                )
            ):
                raise ValueError("COMPRESSION_CONFIRMATION_REQUIRED")
            if submitted_box is not None:
                if (
                    not isinstance(submitted_box, list)
                    or len(submitted_box) != 4
                    or any(
                        isinstance(value, bool)
                        or not isinstance(value, (int, float))
                        or not 0 <= float(value) <= 1
                        for value in submitted_box
                    )
                    or float(submitted_box[0]) >= float(submitted_box[2])
                    or float(submitted_box[1]) >= float(submitted_box[3])
                ):
                    raise ValueError("CROP_BOX_INVALID")
                submitted_box = {
                    "x": float(submitted_box[0]),
                    "y": float(submitted_box[1]),
                    "width": float(submitted_box[2])
                    - float(submitted_box[0]),
                    "height": float(submitted_box[3])
                    - float(submitted_box[1]),
                }
            candidate_box = candidate.get("crop_box", {}).get(
                "normalized", candidate.get("crop_box")
            )
            if isinstance(candidate_box, list) and len(candidate_box) == 4:
                candidate_box = {
                    "x": float(candidate_box[0]),
                    "y": float(candidate_box[1]),
                    "width": float(candidate_box[2])
                    - float(candidate_box[0]),
                    "height": float(candidate_box[3])
                    - float(candidate_box[1]),
                }
            source_path = Path(str(candidate.get("source_path", ""))).resolve()
            expected_source_sha256 = str(candidate.get("source_sha256", ""))
            if (
                not source_path.is_file()
                or sha256_file(source_path) != expected_source_sha256
            ):
                raise ValueError("SOURCE_IDENTITY_MISMATCH")
            scoped_id = hashlib.sha256(
                (
                    f"{processing_sha256}|{assignment['slot_id']}|{asset_id}|"
                    f"{assignment['target_ratio']}|{order}"
                ).encode("utf-8")
            ).hexdigest()[:24]
            native = bool(candidate.get("native_ratio")) and submitted_box is None
            compressed = provider.compress(
                asset_id=scoped_id,
                source_sha256=expected_source_sha256,
                source_path=str(source_path),
                target_format=policy["output"]["format"],
                quality=policy["output"]["quality_max"],
                max_output_bytes=maximum,
                derived_root=str(root),
                target_ratio=assignment["target_ratio"],
                normalized_box=(
                    None
                    if native
                    else (
                        submitted_box or candidate_box
                    )
                ),
            )
            output = {
                **provider_contract_payload(compressed),
                "asset_id": asset_id,
                "product_id": assignment["product_id"],
                "slot_id": assignment["slot_id"],
                "order": order,
                "target_ratio": assignment["target_ratio"],
                "source_path": str(source_path),
                "source_sha256": expected_source_sha256,
                "policy_sha256": board_data.get("policy_sha256"),
                "plan_sha256": plan_sha256,
                "processing_sha256": processing_sha256,
                "crop_source": (
                    "manual"
                    if submitted_box is not None
                    else ("native" if native else "candidate")
                ),
            }
            if sha256_file(source_path) != expected_source_sha256:
                Path(output["output_path"]).unlink(missing_ok=True)
                raise ValueError("SOURCE_IDENTITY_MISMATCH")
            verified = validate_final_output(output, policy=policy)
            output_path = Path(verified["output_path"]).resolve()
            if not output_path.is_relative_to(root):
                raise ValueError("OUTPUT_PATH_OUTSIDE_TASK")
            outputs.append(verified)
        ratios = {output["target_ratio"] for output in outputs}
        if len(outputs) < 3 or len(outputs) > 9:
            raise ValueError("IMAGE_COUNT_INVALID")
        if ratios != {assignment["target_ratio"]}:
            raise ValueError("OUTPUT_ASPECT_RATIO_INVALID")
        slots.append(
            {
                **assignment,
                "status": "outputs_ready",
                "outputs": outputs,
            }
        )
    return {
        "schema_version": 1,
        "workflow_state": "outputs_ready",
        "image_review_revision": board_data.get("image_review_revision"),
        "policy_sha256": board_data.get("policy_sha256"),
        "plan_sha256": plan_sha256,
        "processing_sha256": processing_sha256,
        "slots": slots,
    }


def prepare_slot_board_session(
    store: SessionStore,
    session_id: str,
) -> dict[str, Any]:
    state = store.load_session(session_id)
    review_state = state["stages"]["image_review"]
    if review_state["status"] != "completed":
        raise InteractionConflict("image_review must be completed first")
    review_input = store.read_optional_stage_document(
        session_id, "image_review", "input"
    )
    if (
        not review_input
        or review_input.get("revision") != review_state["revision"]
        or not isinstance(review_input.get("values"), dict)
    ):
        raise InteractionConflict("current image_review input is unavailable")
    suitability_path = (
        store._stage_path(session_id, "image_review")
        / "suitability.snapshot.json"
    )
    try:
        suitability_snapshot = json.loads(
            suitability_path.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as error:
        raise InteractionConflict(
            "current image suitability data is unavailable; "
            "legacy stage-four outputs require explicit migration"
        ) from error
    suitability_data = suitability_snapshot.get("data")
    if not isinstance(suitability_data, dict):
        raise InteractionConflict("current image suitability data is unavailable")
    if int(suitability_snapshot.get("asset_matching_revision", -1)) != int(
        suitability_data.get("asset_matching_revision", -2)
    ):
        raise InteractionConflict("image suitability snapshot is stale")
    if int(suitability_snapshot.get("asset_matching_revision", -1)) != int(
        state["stages"]["asset_matching"]["revision"]
    ):
        raise InteractionConflict("image suitability snapshot is stale")
    board_data = build_rule_slot_plan(
        suitability_data,
        review_input["values"].get("decisions", []),
        image_review_revision=int(review_state["revision"]),
    )
    evidence_path = suitability_path
    context = {
        "schema_version": 1,
        "session_id": session_id,
        "stage_id": "slots_copy",
        "revision": int(state["stages"]["slots_copy"]["revision"]),
        "status": "needs_user_input",
        "summary": (
            f"已准备 {sum(len(row['outputs']) for row in board_data['products'])} "
            "个比例候选，并生成确定性规则草案"
        ),
        "blocking_reasons": (
            ["INSUFFICIENT_CONFIRMED_IMAGES"]
            if not any(
                count >= board_data["slot_image_min"]
                for row in board_data["products"]
                for count in row["available_by_ratio"].values()
            )
            else []
        ),
        "evidence": [str(evidence_path)],
        "next_action": "先确认每坑 3–9 张及唯一比例，再生成正式裁剪/压缩输出",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "data": board_data,
    }
    store.write_review_context(session_id, "slots_copy", context)
    return context


def slot_context_is_stale(
    context: Mapping[str, Any],
    *,
    current_image_review_revision: int,
) -> bool:
    data = context.get("data")
    return (
        not isinstance(data, Mapping)
        or int(data.get("image_review_revision", -1))
        != int(current_image_review_revision)
    )
