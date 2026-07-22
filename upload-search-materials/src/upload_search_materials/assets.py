from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from PIL import Image, UnidentifiedImageError
import yaml

from .io_tables import sha256_file
from .models import AssetRecord


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm"}
VIDEO_POLICY_FIELDS = {
    "formats",
    "codecs",
    "duration_min_seconds",
    "duration_max_seconds",
    "min_width",
    "min_height",
    "aspect_ratios",
    "max_size_mb",
    "cover_required",
    "watermark_allowed",
}


class AssetSource(Protocol):
    def collect(self, product_id: str, sku: str): ...


@dataclass(frozen=True)
class AssetValidationResult:
    status: str
    reason_codes: list[str] = field(default_factory=list)


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def load_media_policy(path: Path) -> dict:
    loaded = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def _video_policy_complete(policy: dict | None) -> bool:
    video = (policy or {}).get("video")
    return isinstance(video, dict) and all(
        key in video and video[key] is not None for key in VIDEO_POLICY_FIELDS
    )


def inspect_asset(
    path: Path,
    product_id: str,
    license_status: str,
    *,
    media_policy: dict | None = None,
    asset_type: str = "",
    source_system: str = "local",
    sku: str = "",
    video_metadata: dict | None = None,
) -> AssetRecord:
    resolved = Path(path).resolve()
    reasons: list[str] = []
    if not resolved.is_file():
        return AssetRecord(
            product_id=product_id,
            sku=sku,
            source_path=str(resolved),
            source_system=source_system,
            license_status=license_status,
            validation_status="blocked",
            reason_codes=["ASSET_NOT_FOUND"],
        )
    size_bytes = resolved.stat().st_size
    if size_bytes == 0:
        reasons.append("ZERO_BYTE_ASSET")
    if license_status != "confirmed":
        reasons.append("LICENSE_UNKNOWN")

    extension = resolved.suffix.casefold()
    detected_type = asset_type or (
        "image" if extension in IMAGE_EXTENSIONS else "video" if extension in VIDEO_EXTENSIONS else "unknown"
    )
    width = height = None
    duration = None
    if detected_type == "image" and size_bytes:
        try:
            with Image.open(resolved) as image:
                image.verify()
            with Image.open(resolved) as image:
                width, height = image.size
        except (OSError, UnidentifiedImageError):
            reasons.append("ASSET_UNREADABLE")
    elif detected_type == "video":
        if not _video_policy_complete(media_policy):
            reasons.append("VIDEO_POLICY_INCOMPLETE")
        elif video_metadata is None:
            reasons.append("VIDEO_METADATA_UNAVAILABLE")
        else:
            video_policy = media_policy["video"]
            allowed_formats = {
                "." + str(value).casefold().lstrip(".") for value in video_policy["formats"]
            }
            if extension not in allowed_formats:
                reasons.append("VIDEO_FORMAT_INVALID")
            codec = str(video_metadata.get("codec", "")).casefold()
            duration = float(video_metadata.get("duration", 0))
            width = int(video_metadata.get("width", 0))
            height = int(video_metadata.get("height", 0))
            allowed_codecs = {str(value).casefold() for value in video_policy["codecs"]}
            if codec not in allowed_codecs:
                reasons.append("VIDEO_CODEC_INVALID")
            if not (
                float(video_policy["duration_min_seconds"])
                <= duration
                <= float(video_policy["duration_max_seconds"])
            ):
                reasons.append("VIDEO_DURATION_INVALID")
            if width < int(video_policy["min_width"]) or height < int(video_policy["min_height"]):
                reasons.append("VIDEO_RESOLUTION_INVALID")
            allowed_ratios = {
                round(float(left) / float(right), 3)
                for left, right in (
                    str(value).split(":", 1) for value in video_policy["aspect_ratios"]
                )
            }
            actual_ratio = round(width / height, 3) if height else 0
            if not any(abs(actual_ratio - ratio) <= 0.02 for ratio in allowed_ratios):
                reasons.append("VIDEO_ASPECT_RATIO_INVALID")
            if size_bytes > float(video_policy["max_size_mb"]) * 1024 * 1024:
                reasons.append("VIDEO_SIZE_INVALID")
            if video_policy["cover_required"] and not video_metadata.get("cover_present", False):
                reasons.append("VIDEO_COVER_MISSING")
            if not video_policy["watermark_allowed"] and video_metadata.get("watermark_present", False):
                reasons.append("VIDEO_WATERMARK_NOT_ALLOWED")
    else:
        reasons.append("ASSET_FORMAT_UNSUPPORTED")

    return AssetRecord(
        asset_id=sha256_file(resolved)[:16] if size_bytes else "",
        product_id=product_id,
        sku=sku,
        asset_type=detected_type,
        source_system=source_system,
        source_path=str(resolved),
        license_status=license_status,
        sha256=sha256_file(resolved) if size_bytes else "",
        width=width,
        height=height,
        duration=duration,
        validation_status="blocked" if reasons else "valid",
        reason_codes=reasons,
    )


def validate_asset_group(
    records: list[AssetRecord],
    *,
    material_type: str,
) -> AssetValidationResult:
    reasons: list[str] = []
    for record in records:
        for reason in record.reason_codes:
            _append_unique(reasons, reason)
    fingerprints = [record.sha256 for record in records if record.sha256]
    if len(fingerprints) != len(set(fingerprints)):
        _append_unique(reasons, "DUPLICATE_ASSET")
    if material_type == "image_text":
        if not 3 <= len(records) <= 9:
            _append_unique(reasons, "IMAGE_COUNT_INVALID")
        ratios = {
            round(record.width / record.height, 3)
            for record in records
            if record.width and record.height
        }
        if len(ratios) > 1:
            _append_unique(reasons, "MIXED_ASPECT_RATIO")
        for ratio in ratios:
            if min(abs(ratio - 0.75), abs(ratio - 1.0)) > 0.02:
                _append_unique(reasons, "ASPECT_RATIO_INVALID")
    elif material_type == "video" and len(records) != 1:
        _append_unique(reasons, "VIDEO_COUNT_INVALID")
    return AssetValidationResult(
        status="blocked" if reasons else "valid",
        reason_codes=reasons,
    )


class DirectoryAssetSource:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    def collect(self, product_id: str, sku: str) -> list[Path]:
        candidates = [(self.root / product_id).resolve()] if product_id else []
        if sku:
            candidates.append((self.root / sku).resolve())
        candidates = [candidate for candidate in candidates if candidate.is_relative_to(self.root)]
        directory = next((candidate for candidate in candidates if candidate.is_dir()), None)
        if directory is None:
            return []
        return sorted(
            path
            for path in directory.rglob("*")
            if path.is_file() and path.suffix.casefold() in IMAGE_EXTENSIONS | VIDEO_EXTENSIONS
        )


class ManifestAssetSource:
    def __init__(self, manifest_path: Path):
        self.manifest_path = Path(manifest_path).resolve()

    def collect(self, product_id: str, sku: str) -> list[AssetRecord]:
        records = []
        with self.manifest_path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            for row in reader:
                row_product_id = str(row.get("product_id", "")).strip()
                row_sku = str(row.get("sku", "")).strip()
                if row_product_id:
                    if row_product_id != product_id:
                        continue
                elif not sku or row_sku != sku:
                    continue
                source_path = Path(str(row.get("source_path", "")).strip())
                if not source_path.is_absolute():
                    source_path = self.manifest_path.parent / source_path
                records.append(
                    inspect_asset(
                        source_path,
                        row_product_id or product_id,
                        str(row.get("license_status", "unknown")).strip(),
                        asset_type=str(row.get("asset_type", "")).strip(),
                        source_system="manifest",
                        sku=row_sku,
                    )
                )
        return records
