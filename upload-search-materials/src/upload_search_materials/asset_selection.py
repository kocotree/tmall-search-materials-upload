"""Deterministic, review-first image selection from indexed candidates."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import hashlib
from typing import Any


MATCH_RANK = {
    "exact_product_id": 0,
    "exact_sku": 1,
    "confirmed_alias": 2,
    "name_candidate": 3,
}
CONFIRMED_MATCH_STATUSES = {
    "matched_unlicensed",
    "confirmed",
    "confirmed_alias",
}


def _candidate_key(candidate: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        MATCH_RANK.get(str(candidate.get("match_type", "")), 99),
        str(candidate.get("source_system", "")).casefold(),
        str(candidate.get("source_path", "")).casefold(),
        str(candidate.get("sha256", "")),
        str(candidate.get("asset_id", "")),
    )


def _eligible(candidate: Mapping[str, Any]) -> bool:
    return (
        str(candidate.get("validation_status", "")) == "valid"
        and str(candidate.get("license_status", "")) == "confirmed"
        and str(candidate.get("match_status", "")) in CONFIRMED_MATCH_STATUSES
        and bool(str(candidate.get("sha256", "")).strip())
    )


def build_selection_batch(
    candidates: Iterable[Mapping[str, Any]],
    *,
    material_count: int,
    images_per_material: int = 3,
    batch_index: int = 0,
    already_selected_sha256: Iterable[str] = (),
    remote_sha256: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Return a stable candidate window grouped into image-text materials.

    A batch never samples randomly. Exact product matches rank before SKU and
    confirmed aliases, then source and path provide stable ordering.
    """

    if material_count < 0:
        raise ValueError("material_count must be non-negative")
    if not 3 <= images_per_material <= 9:
        raise ValueError("images_per_material must be between 3 and 9")
    if batch_index < 0:
        raise ValueError("batch_index must be non-negative")

    required_images = material_count * images_per_material
    selected_hashes = {str(value) for value in already_selected_sha256 if str(value)}
    known_remote = (
        {str(value) for value in remote_sha256 if str(value)}
        if remote_sha256 is not None
        else None
    )
    seen_hashes: set[str] = set()
    duplicate_sha256_count = 0
    remote_duplicate_count = 0
    eligible: list[dict[str, Any]] = []

    for source in sorted(candidates, key=_candidate_key):
        candidate = dict(source)
        sha256 = str(candidate.get("sha256", "")).strip()
        if not _eligible(candidate):
            continue
        if sha256 in seen_hashes:
            duplicate_sha256_count += 1
            continue
        seen_hashes.add(sha256)
        if sha256 in selected_hashes:
            continue
        if known_remote is not None and sha256 in known_remote:
            remote_duplicate_count += 1
            continue
        eligible.append(candidate)

    start = batch_index * required_images if required_images else 0
    selected = eligible[start : start + required_images]
    groups = [
        {
            "group_index": group_index + 1,
            "assets": selected[offset : offset + images_per_material],
        }
        for group_index, offset in enumerate(
            range(0, len(selected), images_per_material)
        )
    ]
    reason_codes = []
    if remote_sha256 is None:
        reason_codes.append("REMOTE_FINGERPRINTS_UNAVAILABLE")
    if len(selected) < required_images:
        reason_codes.append("INSUFFICIENT_ELIGIBLE_IMAGES")

    return {
        "status": (
            "ready_for_review"
            if len(selected) == required_images
            else "insufficient_candidates"
        ),
        "batch_index": batch_index,
        "material_count": material_count,
        "images_per_material": images_per_material,
        "required_images": required_images,
        "available_images": len(eligible),
        "selected": selected,
        "selected_sha256": [str(item["sha256"]) for item in selected],
        "groups": groups,
        "duplicate_sha256_count": duplicate_sha256_count,
        "remote_duplicate_count": remote_duplicate_count,
        "remote_dedupe_status": (
            "checked" if remote_sha256 is not None else "not_available"
        ),
        "reason_codes": reason_codes,
    }


def build_gallery_data(
    records: Iterable[Any],
    status_rows: Iterable[Mapping[str, Any]],
    *,
    images_per_material: int = 3,
    license_decisions: Iterable[Mapping[str, Any]] = (),
    alias_decisions: Iterable[Mapping[str, Any]] = (),
    remote_sha256_by_product: Mapping[str, Iterable[str]] | None = None,
) -> dict[str, Any]:
    """Build the structured result consumed by the asset review gallery."""

    if not 3 <= images_per_material <= 9:
        raise ValueError("images_per_material must be between 3 and 9")
    confirmed_licenses = {
        str(item.get("asset_id", ""))
        for item in license_decisions
        if item.get("status") == "confirmed"
    }
    confirmed_aliases = {
        (
            str(item.get("product_id", "")),
            str(item.get("candidate_directory", "")),
        )
        for item in alias_decisions
        if item.get("status") == "confirmed"
    }
    requirements = []
    product_ids: set[str] = set()
    for row in status_rows:
        product_id = str(row.get("商品ID", "")).strip()
        try:
            missing = int(str(row.get("缺失数量", "")).strip())
        except ValueError:
            continue
        if not product_id or missing <= 0:
            continue
        product_ids.add(product_id)
        requirements.append(
            {
                "product_id": product_id,
                "product_title": "",
                "missing_materials": missing,
                "images_per_material": images_per_material,
                "required_images": missing * images_per_material,
            }
        )

    candidates = []
    titles: dict[str, str] = {}
    seen_asset_keys: set[tuple[str, str]] = set()
    for record in records:
        product_id = str(record.product_id)
        if product_id not in product_ids:
            continue
        titles.setdefault(product_id, str(record.product_title))
        stable_source = (
            f"{record.source_system}\0{record.relative_path}"
        ).encode("utf-8")
        asset_id = (
            str(record.sha256)[:16]
            if str(record.sha256)
            else hashlib.sha256(stable_source).hexdigest()[:16]
        )
        asset_key = (product_id, asset_id)
        if asset_key in seen_asset_keys:
            continue
        seen_asset_keys.add(asset_key)
        alias_confirmed = (
            product_id,
            str(record.candidate_directory),
        ) in confirmed_aliases
        match_type = (
            "confirmed_alias"
            if record.match_type == "name_candidate" and alias_confirmed
            else str(record.match_type)
        )
        match_status = (
            "confirmed_alias"
            if match_type == "confirmed_alias"
            else str(record.match_status)
        )
        remote_hashes = (
            {
                str(value)
                for value in remote_sha256_by_product.get(product_id, ())
            }
            if remote_sha256_by_product is not None
            else set()
        )
        candidates.append(
            {
                "asset_id": asset_id,
                "product_id": product_id,
                "product_title": str(record.product_title),
                "source_system": str(record.source_system),
                "candidate_directory": str(record.candidate_directory),
                "source_path": str(record.absolute_path),
                "relative_path": str(record.relative_path),
                "sha256": str(record.sha256),
                "width": record.width,
                "height": record.height,
                "validation_status": str(record.validation_status),
                "reason_codes": sorted(
                    {
                        *record.file_reason_codes,
                        *record.match_reason_codes,
                    }
                ),
                "match_type": match_type,
                "match_status": match_status,
                "license_status": (
                    "confirmed"
                    if asset_id in confirmed_licenses
                    else "unknown"
                ),
                "remote_duplicate": bool(
                    str(record.sha256)
                    and str(record.sha256) in remote_hashes
                ),
            }
        )

    for requirement in requirements:
        requirement["product_title"] = titles.get(
            requirement["product_id"],
            "",
        )
    return {
        "schema_version": 1,
        "selection_mode": "deterministic",
        "requirements": requirements,
        "asset_candidates": sorted(
            candidates,
            key=lambda value: (
                value["product_id"],
                _candidate_key(value),
            ),
        ),
        "remote_dedupe_status": (
            "checked"
            if remote_sha256_by_product is not None
            else "not_available"
        ),
    }
