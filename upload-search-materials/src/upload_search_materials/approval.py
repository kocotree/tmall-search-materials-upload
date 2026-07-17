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


def _file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def item_payload(item: MaterialItem, *, rehash_assets: bool = False) -> dict:
    return {
        "task_id": item.task_id,
        "product_id": item.product_id,
        "material_type": item.material_type,
        "slot_index": item.slot_index,
        "asset_sha256": sorted(
            _file_sha256(asset.source_path) if rehash_assets else asset.sha256
            for asset in item.assets
        ),
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
    run_id: str = "",
    source_sha256: dict[str, str] | None = None,
) -> dict:
    entries = [
        item_payload(item)
        for item in sorted(items, key=lambda candidate: candidate.task_id)
    ]
    envelope = {
        "schema_version": 1,
        "run_id": run_id,
        "store": store.strip(),
        "source_sha256": dict(sorted((source_sha256 or {}).items())),
        "entries": entries,
        "confirmed_by": confirmed_by.strip(),
        "confirmed_at": confirmed_at,
        "valid_until": valid_until,
    }
    return {**envelope, "manifest_sha256": canonical_hash(envelope)}


def verify_manifest(
    manifest: dict,
    items: Iterable[MaterialItem],
    *,
    expected_store: str,
    now: str,
    expected_run_id: str | None = None,
    expected_source_sha256: dict[str, str] | None = None,
    rehash_assets: bool = False,
) -> ManifestVerification:
    if manifest.get("schema_version") != 1:
        return ManifestVerification(False, "MANIFEST_SCHEMA_INVALID")
    entries = manifest.get("entries")
    envelope = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    if not isinstance(entries, list) or canonical_hash(envelope) != manifest.get("manifest_sha256"):
        return ManifestVerification(False, "MANIFEST_HASH_INVALID")
    if manifest.get("store") != expected_store.strip():
        return ManifestVerification(False, "STORE_IDENTITY_MISMATCH")
    if expected_run_id is not None and manifest.get("run_id") != expected_run_id:
        return ManifestVerification(False, "RUN_ID_MISMATCH")
    if expected_source_sha256 is not None and manifest.get("source_sha256") != dict(
        sorted(expected_source_sha256.items())
    ):
        return ManifestVerification(False, "SOURCE_HASH_MISMATCH")
    try:
        trusted_now = datetime.fromisoformat(now)
        confirmed_at = datetime.fromisoformat(str(manifest["confirmed_at"]))
        valid_until = datetime.fromisoformat(str(manifest["valid_until"]))
        if trusted_now < confirmed_at:
            return ManifestVerification(False, "APPROVAL_NOT_YET_VALID")
        if trusted_now > valid_until:
            return ManifestVerification(False, "APPROVAL_EXPIRED")
    except (KeyError, TypeError, ValueError):
        return ManifestVerification(False, "APPROVAL_EXPIRY_INVALID")
    entries_by_id = {entry.get("task_id"): entry for entry in entries}
    for item in items:
        approved = entries_by_id.get(item.task_id)
        if approved is None:
            return ManifestVerification(False, "TASK_NOT_APPROVED")
        try:
            current_payload = item_payload(item, rehash_assets=rehash_assets)
        except OSError:
            return ManifestVerification(False, "APPROVED_CONTENT_CHANGED")
        if approved != current_payload:
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
