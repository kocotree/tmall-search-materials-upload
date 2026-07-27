"""Stage-five slot-board preparation and hard validation."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from PIL import Image, ImageOps, UnidentifiedImageError

from .image_compliance import KIB, MIB, _ratio_value, normalize_image_policy
from .interaction.session import InteractionConflict, SessionStore
from .io_tables import sha256_file


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
) -> list[dict[str, Any]]:
    if not isinstance(assignments, list):
        raise ValueError("slot_assignments must be a list")
    outputs = {
        str(output.get("asset_id", "")): output
        for product in board_data.get("products", [])
        if isinstance(product, Mapping)
        for output in product.get("outputs", [])
        if isinstance(output, Mapping) and output.get("asset_id")
    }
    minimum = int(board_data.get("slot_image_min", 3))
    maximum = int(board_data.get("slot_image_max", 9))
    normalized = []
    seen_slots: set[str] = set()
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
        if not minimum <= len(asset_ids) <= maximum:
            raise ValueError(
                f"{slot_id}: IMAGE_COUNT_INVALID ({len(asset_ids)}, expected {minimum}-{maximum})"
            )
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError(f"{slot_id}: DUPLICATE_ASSET")
        selected = []
        for asset_id in asset_ids:
            output = outputs.get(asset_id)
            if output is None:
                raise ValueError(f"{slot_id}: unconfirmed or stale asset {asset_id}")
            if str(output.get("product_id", "")) != product_id:
                raise ValueError(f"{slot_id}: asset belongs to another product")
            selected.append(output)
        ratios = {str(item.get("target_ratio", "")) for item in selected}
        if len(ratios) != 1:
            raise ValueError(f"{slot_id}: MIXED_ASPECT_RATIO")
        normalized.append({
            "slot_id": slot_id,
            "product_id": product_id,
            "asset_ids": asset_ids,
            "target_ratio": next(iter(ratios)),
            "image_review_revision": board_data.get("image_review_revision"),
        })
    if not normalized:
        raise ValueError("at least one slot assignment is required")
    return normalized


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
    policy_path = (
        store._stage_path(session_id, "image_review") / "policy.snapshot.json"
    )
    try:
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise InteractionConflict("image policy snapshot is unavailable") from error
    board_data = build_slot_board_data(
        review_input["values"].get("decisions", []),
        image_review_revision=int(review_state["revision"]),
        policy=policy,
    )
    context = {
        "schema_version": 1,
        "session_id": session_id,
        "stage_id": "slots_copy",
        "revision": int(state["stages"]["slots_copy"]["revision"]),
        "status": "needs_user_input",
        "summary": (
            f"已准备 {sum(len(row['outputs']) for row in board_data['products'])} "
            "张第四阶段确认输出"
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
        "evidence": [str(policy_path)],
        "next_action": "按每坑 3–9 张且比例一致完成坑位编排",
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
