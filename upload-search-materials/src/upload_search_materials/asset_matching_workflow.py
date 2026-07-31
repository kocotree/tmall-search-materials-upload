"""State and identity helpers for the two-step asset-matching workflow."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable


FOLDER_REVIEW = "folder_review"
GALLERY_PREPARING = "gallery_preparing"
IMAGE_SELECTION = "image_selection"
WORKFLOW_STEPS = frozenset({FOLDER_REVIEW, GALLERY_PREPARING, IMAGE_SELECTION})

ALL_FOLDERS_REJECTED = "ALL_FOLDERS_REJECTED"
CONFIRMED_FOLDER_UNREADABLE = "CONFIRMED_FOLDER_UNREADABLE"
CONFIRMED_FOLDER_EMPTY = "CONFIRMED_FOLDER_EMPTY"
GALLERY_IDENTITY_STALE = "GALLERY_IDENTITY_STALE"
GALLERY_PREPARATION_FAILED = "GALLERY_PREPARATION_FAILED"
PRODUCT_IMAGE_SHORTAGE = "PRODUCT_IMAGE_SHORTAGE"
LEGACY_FOLDER_HANDOFF = "legacy_folder_handoff"
FINAL_MATERIAL_HANDOFF = "final_material_handoff"
UNKNOWN_ASSET_HANDOFF = "unknown_asset_handoff"


def infer_workflow_step(data: Any, *, status: str = "") -> str:
    """Return an explicit step, with a safe migration rule for old sessions."""

    if not isinstance(data, dict):
        return GALLERY_PREPARING if status == "processing" else FOLDER_REVIEW
    if status == "processing":
        return GALLERY_PREPARING
    explicit = str(data.get("workflow_step", ""))
    if explicit in WORKFLOW_STEPS:
        return explicit
    if (
        isinstance(data.get("asset_candidates"), list)
        and data["asset_candidates"]
    ) or (
        isinstance(data.get("requirements"), list)
        and data["requirements"]
    ):
        return IMAGE_SELECTION
    return FOLDER_REVIEW


def classify_asset_handoff(
    handoff: Any, input_document: Any
) -> str:
    if not isinstance(handoff, dict) or not isinstance(
        input_document, dict
    ):
        return UNKNOWN_ASSET_HANDOFF
    if handoff.get("handoff_kind") == "final_material_selection":
        return FINAL_MATERIAL_HANDOFF
    values = input_document.get("values")
    if not isinstance(values, dict):
        return UNKNOWN_ASSET_HANDOFF
    selected = [
        item
        for item in values.get("asset_decisions", [])
        if isinstance(item, dict) and item.get("decision") == "selected"
    ]
    if selected:
        return FINAL_MATERIAL_HANDOFF
    confirmed = [
        item
        for item in values.get("folder_decisions", [])
        if isinstance(item, dict) and item.get("decision") == "confirmed"
    ]
    return (
        LEGACY_FOLDER_HANDOFF
        if confirmed
        else UNKNOWN_ASSET_HANDOFF
    )


def canonical_folder_decisions(
    decisions: Iterable[dict[str, Any]],
) -> list[dict[str, str]]:
    rows = [
        {
            "product_id": str(item.get("product_id", "")),
            "folder_id": str(item.get("folder_id", "")),
            "folder_path": (
                ""
                if str(item.get("relative_path", "")).strip()
                else str(item.get("folder_path", ""))
            ),
            "source_system": str(item.get("source_system", "")),
            "source_id": str(
                item.get("source_id") or item.get("source_system", "")
            ),
            "relative_path": str(item.get("relative_path", "")),
            "decision": (
                "rejected"
                if str(item.get("decision", "")) == "rejected"
                else "confirmed"
            ),
        }
        for item in decisions
        if isinstance(item, dict)
        and item.get("product_id")
        and item.get("folder_id")
    ]
    return sorted(
        rows,
        key=lambda item: (
            item["product_id"],
            item["folder_id"],
            item["source_system"],
            item["source_id"],
            item["relative_path"],
            item["folder_path"],
        ),
    )


def folder_decisions_sha256(decisions: Iterable[dict[str, Any]]) -> str:
    payload = json.dumps(
        canonical_folder_decisions(decisions),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def confirmed_folder_keys(
    decisions: Iterable[dict[str, Any]],
) -> set[tuple[str, str]]:
    return {
        (item["product_id"], item["folder_id"])
        for item in canonical_folder_decisions(decisions)
        if item["decision"] == "confirmed"
    }


def build_gallery_identity(
    *,
    session_id: str,
    revision: int,
    input_sha256: str,
    folder_decisions: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    confirmed = sorted(confirmed_folder_keys(folder_decisions))
    return {
        "session_id": session_id,
        "stage_id": "asset_matching",
        "prepared_from_revision": int(revision),
        "prepared_from_input_sha256": str(input_sha256),
        "folder_decisions_sha256": folder_decisions_sha256(folder_decisions),
        "selected_product_ids": sorted(
            {product_id for product_id, _folder_id in confirmed}
        ),
        "prepared_folder_keys": [
            {"product_id": product_id, "folder_id": folder_id}
            for product_id, folder_id in confirmed
        ],
    }


def gallery_covers_folder_decisions(
    identity: Any,
    decisions: Iterable[dict[str, Any]],
    *,
    session_id: str,
) -> bool:
    """Allow exclusions, but reject folders not present in the prepared gallery."""

    if not isinstance(identity, dict):
        return False
    if (
        identity.get("session_id") != session_id
        or identity.get("stage_id") != "asset_matching"
    ):
        return False
    prepared = {
        (str(item.get("product_id", "")), str(item.get("folder_id", "")))
        for item in identity.get("prepared_folder_keys", [])
        if isinstance(item, dict)
    }
    return confirmed_folder_keys(decisions).issubset(prepared)
