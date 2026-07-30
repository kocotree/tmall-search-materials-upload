"""Task-local image-review preparation and decision materialization."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Any, Iterable, Mapping

from .assets import load_media_policy
from .image_compliance import (
    KIB,
    MIB,
    ImageCompressionProvider,
    PillowImageCompressionProvider,
    classify_image,
    generate_crop_derivative,
    inspect_image_source,
    maximum_inscribed_crop,
    normalize_image_policy,
    provider_contract_payload,
    snapshot_image_policy,
    validate_normalized_crop,
)
from .interaction.session import InteractionConflict, SessionStore
from .interaction.session import CURRENT_WORKFLOW_PROFILE


def build_image_review_data(
    candidates: Iterable[Mapping[str, Any]],
    selected_decisions: Iterable[Mapping[str, Any]],
    *,
    policy: Mapping[str, Any],
    policy_sha256: str,
    asset_matching_revision: int,
    compression_provider: ImageCompressionProvider | None = None,
    inspection_cache_path: Path | None = None,
) -> dict[str, Any]:
    """Reinspect selected source images and build deterministic review records."""

    image_policy = normalize_image_policy(policy)
    by_id = {
        str(candidate.get("asset_id", "")): dict(candidate)
        for candidate in candidates
        if isinstance(candidate, Mapping) and candidate.get("asset_id")
    }
    selected = sorted(
        (
            dict(item)
            for item in selected_decisions
            if isinstance(item, Mapping)
            and item.get("decision") == "selected"
            and item.get("asset_id")
        ),
        key=lambda item: (
            str(item.get("product_id", "")),
            int(item.get("selection_order", 0)),
            str(item.get("asset_id", "")),
        ),
    )
    records: list[dict[str, Any]] = []
    seen_sha256: set[str] = set()
    duplicate_count = 0
    provider = compression_provider or PillowImageCompressionProvider(image_policy)
    compression_available = bool(getattr(provider, "available", False))
    for selection in selected:
        asset_id = str(selection["asset_id"])
        candidate = by_id.get(asset_id)
        if candidate is None:
            records.append(
                {
                    "asset_id": asset_id,
                    "product_id": str(selection.get("product_id", "")),
                    "status": "blocked",
                    "reason_codes": ["SELECTED_ASSET_NOT_IN_CURRENT_RESULT"],
                    "crop_options": {},
                }
            )
            continue
        source_path = Path(str(candidate.get("source_path", "")))
        inspection = inspect_image_source(
            source_path,
            policy=image_policy,
            cache_path=inspection_cache_path,
        )
        size_bytes = inspection.get("size_bytes")
        width = inspection.get("width")
        height = inspection.get("height")
        source_sha256 = str(inspection.get("sha256", ""))
        inspection_status = str(inspection.get("validation_status", "blocked"))
        reason_codes = list(inspection.get("reason_codes", []))
        preflight = classify_image(
            width=width,
            height=height,
            size_bytes=size_bytes,
            extension=source_path.suffix,
            validation_status=inspection_status,
            reason_codes=reason_codes,
            policy=image_policy,
            compression_available=compression_available,
        )
        crop_options = {}
        if preflight["selectable"]:
            crop_options = {
                ratio: maximum_inscribed_crop(int(width), int(height), ratio)
                for ratio in image_policy["allowed_aspect_ratios"]
            }
        duplicate = bool(source_sha256 and source_sha256 in seen_sha256)
        if duplicate:
            duplicate_count += 1
            preflight["reason_codes"] = [
                *preflight["reason_codes"],
                "DUPLICATE_ASSET",
            ]
        if source_sha256:
            seen_sha256.add(source_sha256)
        records.append(
            {
                "asset_id": asset_id,
                "product_id": str(candidate.get("product_id", "")),
                "product_title": str(candidate.get("product_title", "")),
                "source_system": str(candidate.get("source_system", "")),
                "folder_id": str(candidate.get("folder_id", "")),
                "folder_path": str(
                    candidate.get("folder_path")
                    or candidate.get("candidate_directory")
                    or ""
                ),
                "candidate_directory": str(
                    candidate.get("candidate_directory")
                    or candidate.get("folder_path")
                    or ""
                ),
                "selection_order": int(selection.get("selection_order") or 0),
                "source_path": str(source_path),
                "source_sha256": source_sha256,
                "source_inspection": inspection,
                "target_assessments": preflight["resolution_checks"],
                "width": width,
                "height": height,
                "size_bytes": size_bytes,
                "preflight": preflight,
                "status": "blocked" if not preflight["selectable"] else "pending",
                "reason_codes": preflight["reason_codes"],
                "crop_options": crop_options,
                "ratio_options": {
                    ratio: {
                        "ratio": ratio,
                        "crop_box": crop_options.get(ratio),
                        "assessment": preflight["resolution_checks"].get(ratio, {}),
                        "feasible": ratio in crop_options,
                        "requires_compression": bool(preflight["size_exceeded"]),
                    }
                    for ratio in image_policy["allowed_aspect_ratios"]
                },
                "duplicate": duplicate,
                "asset_matching_revision": asset_matching_revision,
            }
        )
    return {
        "schema_version": 3,
        "record_type": "suitability_record",
        "asset_matching_revision": asset_matching_revision,
        "policy": image_policy,
        "policy_sha256": policy_sha256,
        "selected_count": len(selected),
        "reviewable_count": sum(item["status"] != "blocked" for item in records),
        "blocked_count": sum(item["status"] == "blocked" for item in records),
        "duplicate_count": duplicate_count,
        "assets": records,
        "suitability_records": records,
        "capabilities": {
            "manual_crop": True,
            "ai_crop": False,
            "image_compression": compression_available,
            "compression_provider": (
                getattr(provider, "provider_id", None)
                if compression_available
                else None
            ),
            "compression_provider_version": (
                getattr(provider, "provider_version", None)
                if compression_available
                else None
            ),
        },
    }


def prepare_image_review_session(
    store: SessionStore,
    session_id: str,
    *,
    policy_path: Path,
) -> dict[str, Any]:
    """Prepare stage four from the current submitted stage-three revision."""

    state = store.load_session(session_id)
    matching_state = state["stages"]["asset_matching"]
    if matching_state["status"] != "completed":
        raise InteractionConflict("asset_matching must be completed first")
    matching_input = store.read_optional_stage_document(
        session_id, "asset_matching", "input"
    )
    matching_result = store.read_optional_stage_document(
        session_id, "asset_matching", "result"
    )
    if (
        not matching_input
        or not matching_result
        or matching_input.get("revision") != matching_state["revision"]
        or matching_result.get("revision") != matching_state["revision"]
    ):
        raise InteractionConflict("current asset_matching input/result is unavailable")
    values = matching_input.get("values")
    data = matching_result.get("data")
    if not isinstance(values, dict) or not isinstance(data, dict):
        raise InteractionConflict("asset_matching documents are malformed")
    stage_path = store._stage_path(session_id, "image_review")
    policy_source = load_media_policy(Path(policy_path))
    policy, policy_sha256 = snapshot_image_policy(
        policy_source,
        stage_path / "policy.snapshot.json",
    )
    review_data = build_image_review_data(
        data.get("asset_candidates", []),
        values.get("asset_decisions", []),
        policy=policy,
        policy_sha256=policy_sha256,
        asset_matching_revision=int(matching_state["revision"]),
        inspection_cache_path=stage_path / "source-inspection-cache.json",
    )
    review_context = {
        "schema_version": 1,
        "session_id": session_id,
        "stage_id": "image_review",
        "revision": int(state["stages"]["image_review"]["revision"]),
        "status": "needs_user_input",
        "summary": (
            f"已读取 {review_data['selected_count']} 张第三阶段已选图片，"
            f"{review_data['reviewable_count']} 张可审查"
        ),
        "blocking_reasons": (
            ["IMAGE_POLICY_INCOMPLETE"] if not review_data["reviewable_count"] else []
        ),
        "evidence": [
            str(stage_path / "policy.snapshot.json"),
        ],
        "next_action": "在第四阶段确认候选图片及 1:1、3:4 两种比例的可行性；第五阶段再确定坑位比例",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "data": review_data,
    }
    store._write_json_atomic(
        stage_path / "suitability.snapshot.json",
        {
            "schema_version": 1,
            "session_id": session_id,
            "stage_id": "image_review",
            "asset_matching_revision": int(matching_state["revision"]),
            "data": review_data,
        },
    )
    store.write_review_context(session_id, "image_review", review_context)
    return review_context


def normalize_review_decisions(
    decisions: Any,
    review_data: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if not isinstance(decisions, list):
        raise ValueError("decisions must be a list")
    assets = {
        str(item.get("asset_id", "")): item
        for item in review_data.get("assets", [])
        if isinstance(item, Mapping) and item.get("asset_id")
    }
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in decisions:
        if not isinstance(raw, Mapping):
            raise ValueError("each decision must be an object")
        asset_id = str(raw.get("asset_id", ""))
        if not asset_id or asset_id in seen or asset_id not in assets:
            raise ValueError("decision asset_id is missing, duplicated, or stale")
        seen.add(asset_id)
        action = str(raw.get("action", ""))
        if action not in {
            "direct",
            "crop",
            "compress",
            "crop_and_compress",
            "candidate_only",
            "excluded",
        }:
            raise ValueError(f"unsupported action for {asset_id}")
        asset = assets[asset_id]
        if asset.get("status") == "blocked" and action not in {
            "candidate_only",
            "excluded",
        }:
            raise ValueError(f"blocked asset cannot be used: {asset_id}")
        target_ratio = str(raw.get("target_ratio", ""))
        if action in {"direct", "compress"}:
            matching = asset.get("preflight", {}).get("matching_ratios", [])
            if target_ratio not in matching:
                raise ValueError(f"direct asset ratio is invalid: {asset_id}")
        if action in {"crop", "crop_and_compress"}:
            if target_ratio not in asset.get("crop_options", {}):
                raise ValueError(f"crop target ratio is invalid: {asset_id}")
            validated = validate_normalized_crop(
                raw.get("crop_box", {}),
                target_ratio=target_ratio,
                width=int(asset["width"]),
                height=int(asset["height"]),
            )
        else:
            validated = None
        crop_candidates: dict[str, Any] = {}
        supplied_candidates = raw.get("crop_candidates")
        if isinstance(supplied_candidates, Mapping):
            for ratio, crop in supplied_candidates.items():
                ratio = str(ratio)
                if ratio not in asset.get("crop_options", {}):
                    continue
                crop_candidates[ratio] = validate_normalized_crop(
                    crop,
                    target_ratio=ratio,
                    width=int(asset["width"]),
                    height=int(asset["height"]),
                )
        if validated is not None and target_ratio:
            crop_candidates[target_ratio] = validated
        for ratio, crop in asset.get("crop_options", {}).items():
            if ratio not in crop_candidates:
                crop_candidates[ratio] = crop
        candidate = action not in {"excluded"} and asset.get("status") != "blocked"
        normalized.append(
            {
                "schema_version": 1,
                "record_type": "suitability_decision",
                "asset_id": asset_id,
                "product_id": str(asset.get("product_id", "")),
                "decision": "candidate" if candidate else "excluded",
                "action": action if candidate else "excluded",
                "target_ratio": target_ratio or None,
                "crop_box": validated,
                "candidate_ratios": sorted(crop_candidates) if candidate else [],
                "crop_candidates": crop_candidates if candidate else {},
                "requires_compression": bool(
                    asset.get("preflight", {}).get("size_exceeded")
                ),
                "asset_matching_revision": review_data.get(
                    "asset_matching_revision"
                ),
            }
        )
    required = {
        asset_id
        for asset_id, asset in assets.items()
        if asset.get("status") != "blocked"
    }
    if not required.issubset(seen):
        missing = sorted(required - seen)
        raise ValueError("missing decisions for: " + ", ".join(missing))
    return normalized


def materialize_review_decisions(
    decisions: list[dict[str, Any]],
    review_data: Mapping[str, Any],
    *,
    derived_root: Path,
    compression_provider: ImageCompressionProvider | None = None,
) -> list[dict[str, Any]]:
    assets = {
        str(item["asset_id"]): item
        for item in review_data.get("assets", [])
        if isinstance(item, Mapping) and item.get("asset_id")
    }
    policy = review_data["policy"]
    provider = compression_provider or PillowImageCompressionProvider(policy)
    min_bytes = round(float(policy["min_size_kb"]) * KIB)
    max_bytes = round(float(policy["max_size_mb"]) * MIB)
    outputs: list[dict[str, Any]] = []
    for decision in decisions:
        result = dict(decision)
        asset = assets[decision["asset_id"]]
        if decision["action"] == "direct":
            result["output"] = {
                "output_path": asset["source_path"],
                "output_width": asset["width"],
                "output_height": asset["height"],
                "output_size_bytes": asset["size_bytes"],
                "output_sha256": asset["source_sha256"],
                "source_path": asset["source_path"],
                "source_sha256": asset["source_sha256"],
                "target_ratio": decision["target_ratio"],
                "kind": "direct",
                "output_format": str(
                    asset.get("source_inspection", {}).get("format", "")
                ),
                "quality": None,
                "provider_id": "source",
                "provider_version": "1",
                "policy_sha256": review_data.get("policy_sha256"),
            }
        elif decision["action"] == "crop":
            crop = generate_crop_derivative(
                source_path=Path(asset["source_path"]),
                expected_source_sha256=asset["source_sha256"],
                target_ratio=str(decision["target_ratio"]),
                normalized_box=decision["crop_box"]["normalized"],
                derived_root=derived_root,
                asset_id=decision["asset_id"],
                policy=policy,
            )
            if int(crop["output_size_bytes"]) > max_bytes:
                Path(crop["output_path"]).unlink(missing_ok=True)
                compressed = provider.compress(
                    asset_id=decision["asset_id"],
                    source_sha256=asset["source_sha256"],
                    source_path=asset["source_path"],
                    target_format=policy["output"]["format"],
                    quality=policy["output"]["quality_max"],
                    max_output_bytes=max_bytes,
                    derived_root=str(derived_root),
                    target_ratio=str(decision["target_ratio"]),
                    normalized_box=decision["crop_box"]["normalized"],
                )
                crop = {
                    **provider_contract_payload(compressed),
                    "source_path": asset["source_path"],
                    "source_sha256": asset["source_sha256"],
                    "target_ratio": decision["target_ratio"],
                    "crop_box": decision["crop_box"],
                    "kind": "crop_and_compress",
                }
            else:
                crop["kind"] = "crop"
            if int(crop["output_size_bytes"]) < min_bytes:
                raise ValueError("OUTPUT_SIZE_BELOW_MINIMUM")
            crop["policy_sha256"] = review_data.get("policy_sha256")
            result["output"] = crop
        elif decision["action"] in {"compress", "crop_and_compress"}:
            compressed = provider.compress(
                asset_id=decision["asset_id"],
                source_sha256=asset["source_sha256"],
                source_path=asset["source_path"],
                target_format=policy["output"]["format"],
                quality=policy["output"]["quality_max"],
                max_output_bytes=max_bytes,
                derived_root=str(derived_root),
                target_ratio=str(decision["target_ratio"]),
                normalized_box=(
                    decision["crop_box"]["normalized"]
                    if decision["action"] == "crop_and_compress"
                    else None
                ),
            )
            result["output"] = {
                **provider_contract_payload(compressed),
                "source_path": asset["source_path"],
                "source_sha256": asset["source_sha256"],
                "target_ratio": decision["target_ratio"],
                "crop_box": decision.get("crop_box"),
                "kind": compressed.action,
                "policy_sha256": review_data.get("policy_sha256"),
            }
        else:
            result["output"] = None
        result["source_inspection"] = asset.get("source_inspection")
        result["policy_sha256"] = review_data.get("policy_sha256")
        outputs.append(result)
    return outputs


def review_context_is_stale(
    context: Mapping[str, Any],
    *,
    current_asset_matching_revision: int,
) -> bool:
    data = context.get("data")
    return (
        not isinstance(data, Mapping)
        or int(data.get("asset_matching_revision", -1))
        != int(current_asset_matching_revision)
    )


def migrate_legacy_image_review_session(
    store: SessionStore,
    session_id: str,
    *,
    policy_path: Path,
) -> dict[str, Any]:
    """Rebuild suitability while retaining legacy outputs as read-only audit."""

    before = store.load_session(session_id)
    legacy_stage = store._stage_path(session_id, "image_review")
    legacy_outputs = sorted(
        str(path)
        for path in (legacy_stage / "derived").glob("**/*")
        if path.is_file()
    ) if (legacy_stage / "derived").is_dir() else []
    context = prepare_image_review_session(
        store,
        session_id,
        policy_path=policy_path,
    )
    with store._session_lock(session_id):
        state = store.load_session(session_id)
        state["stages"]["image_review"]["status"] = "needs_user_input"
        state["stages"]["slots_copy"]["status"] = "draft"
        state["current_stage"] = "image_review"
        store._write_session_state(session_id, state)
    report = {
        "schema_version": 1,
        "session_id": session_id,
        "migration": "slot-first-image-processing",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "obsolete_image_review_revision": before["stages"]["image_review"][
            "revision"
        ],
        "obsolete_slots_copy_revision": before["stages"]["slots_copy"][
            "revision"
        ],
        "legacy_outputs_retained_read_only": legacy_outputs,
        "rebuilt_asset_matching_revision": context["data"][
            "asset_matching_revision"
        ],
        "next_action": "review suitability and submit stage four again",
    }
    store._write_json_atomic(legacy_stage / "migration-slot-first.json", report)
    return report


def migrate_legacy_suitability_to_selected_preflight(
    store: SessionStore,
    session_id: str,
) -> dict[str, Any]:
    """Copy a compatible historical suitability snapshot into the new stage."""

    state = store.load_session(session_id)
    source_stage = store._stage_path(session_id, "image_review")
    source_path = source_stage / "suitability.snapshot.json"
    if not source_path.is_file():
        raise InteractionConflict("historical suitability snapshot is unavailable")
    snapshot = SessionStore._read_json(
        source_path, "historical-suitability"
    )
    data = snapshot.get("data")
    if not isinstance(data, dict):
        raise InteractionConflict("historical suitability snapshot is invalid")
    asset_revision = int(data.get("asset_matching_revision", -1))
    if asset_revision != int(state["stages"]["asset_matching"]["revision"]):
        raise InteractionConflict("historical suitability snapshot is stale")
    target_stage = store._stage_path(session_id, "asset_matching")
    target_path = target_stage / "selected-asset-preflight.json"
    migrated = {
        "schema_version": 1,
        "session_id": session_id,
        "stage_id": "asset_matching",
        "revision": asset_revision,
        "migration_source": str(source_path),
        "legacy_image_review_revision": int(
            state["stages"]["image_review"]["revision"]
        ),
        "policy_sha256": data.get("policy_sha256"),
        "data": data,
    }
    store._write_json_atomic(target_path, migrated)
    slots_context = store.read_optional_stage_document(
        session_id, "slots_copy", "review-context"
    )
    if isinstance(slots_context, dict) and isinstance(
        slots_context.get("data"), dict
    ):
        slots_context = dict(slots_context)
        slots_context["data"] = dict(slots_context["data"])
        slots_context["data"]["two_page_workflow"] = True
        slots_context["data"]["legacy_workflow_migrated"] = True
        store._write_json_atomic(
            store._stage_path(session_id, "slots_copy")
            / "review-context.json",
            slots_context,
        )
    with store._session_lock(session_id):
        current = store.load_session(session_id)
        current["workflow_profile"] = CURRENT_WORKFLOW_PROFILE
        current["current_stage"] = "asset_matching"
        current["stages"]["asset_matching"]["status"] = "needs_user_input"
        current["stages"]["slots_copy"]["status"] = "draft"
        store._write_session_state(session_id, current)
        store._append_event(
            store._session_path(session_id),
            "legacy_suitability_migrated",
            session_id=session_id,
            stage_id="asset_matching",
            revision=asset_revision,
            source=str(source_path),
            target=str(target_path),
        )
    return {
        "schema_version": 1,
        "session_id": session_id,
        "migration": "selected-asset-preflight",
        "selected_asset_preflight": str(target_path),
        "legacy_stage_retained": str(source_stage),
        "current_stage": "asset_matching",
    }
