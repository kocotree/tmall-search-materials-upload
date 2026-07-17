from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Iterable

from jinja2 import Environment, FileSystemLoader

from .models import MaterialItem


@dataclass(frozen=True)
class ManifestVerification:
    valid: bool
    reason: str = ""


def canonical_hash(value: dict | list) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def item_payload(item: MaterialItem) -> dict:
    return {
        "task_id": item.task_id,
        "product_id": item.product_id,
        "material_type": item.material_type,
        "slot_index": item.slot_index,
        "asset_sha256": sorted(asset.sha256 for asset in item.assets),
        "title": item.title,
        "description": item.description,
        "action": "publish",
    }


def create_manifest(
    store: str,
    items: Iterable[MaterialItem],
    confirmed_by: str,
    confirmed_at: str,
    *,
    valid_until: str,
) -> dict:
    entries = [
        item_payload(item)
        for item in sorted(items, key=lambda candidate: candidate.task_id)
    ]
    return {
        "schema_version": 1,
        "store": store.strip(),
        "entries": entries,
        "confirmed_by": confirmed_by.strip(),
        "confirmed_at": confirmed_at,
        "valid_until": valid_until,
        "manifest_sha256": canonical_hash(entries),
    }


def verify_manifest(
    manifest: dict,
    items: Iterable[MaterialItem],
    *,
    expected_store: str,
    now: str,
) -> ManifestVerification:
    if manifest.get("schema_version") != 1:
        return ManifestVerification(False, "MANIFEST_SCHEMA_INVALID")
    entries = manifest.get("entries")
    if not isinstance(entries, list) or canonical_hash(entries) != manifest.get("manifest_sha256"):
        return ManifestVerification(False, "MANIFEST_HASH_INVALID")
    if manifest.get("store") != expected_store.strip():
        return ManifestVerification(False, "STORE_IDENTITY_MISMATCH")
    try:
        if datetime.fromisoformat(now) > datetime.fromisoformat(str(manifest["valid_until"])):
            return ManifestVerification(False, "APPROVAL_EXPIRED")
    except (KeyError, TypeError, ValueError):
        return ManifestVerification(False, "APPROVAL_EXPIRY_INVALID")
    entries_by_id = {entry.get("task_id"): entry for entry in entries}
    for item in items:
        approved = entries_by_id.get(item.task_id)
        if approved is None:
            return ManifestVerification(False, "TASK_NOT_APPROVED")
        if approved != item_payload(item):
            return ManifestVerification(False, "APPROVED_CONTENT_CHANGED")
    return ManifestVerification(True, "")


def render_review_html(
    items: Iterable[MaterialItem],
    output_path: Path,
    *,
    store: str,
    template_path: Path | None = None,
) -> Path:
    if template_path is None:
        template_path = Path(__file__).resolve().parents[2] / "assets" / "review-template.html.j2"
    environment = Environment(
        loader=FileSystemLoader(str(template_path.parent)),
        autoescape=True,
    )
    template = environment.get_template(template_path.name)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        template.render(store=store, items=list(items)),
        encoding="utf-8",
    )
    return output
