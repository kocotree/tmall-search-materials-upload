"""Shared image policy, preflight, crop, and slot-validation primitives."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import math
from pathlib import Path
import time
from typing import Any, Mapping, Protocol

from PIL import Image, ImageOps, UnidentifiedImageError

from .io_tables import sha256_file


MIB = 1024 * 1024
KIB = 1024
IMAGE_POLICY_FIELDS = {
    "version",
    "formats",
    "allowed_aspect_ratios",
    "aspect_ratio_tolerance",
    "recommended_dimensions",
    "recommended_size_mode",
    "min_width",
    "min_height",
    "min_size_kb",
    "max_size_mb",
    "max_pixels",
    "output",
}
UNUSABLE_REASON_CODES = {
    "ASSET_NOT_FOUND",
    "ASSET_UNREADABLE",
    "ZERO_BYTE_ASSET",
    "ZERO_DIMENSION_IMAGE",
    "ASSET_FORMAT_UNSUPPORTED",
    "IMAGE_FORMAT_UNSUPPORTED",
    "SOURCE_UNREADABLE",
    "SOURCE_METADATA_MISSING",
    "IMAGE_SIZE_BELOW_MINIMUM",
    "IMAGE_SIZE_EXCEEDED",
    "OUTPUT_DIMENSIONS_BELOW_MINIMUM",
    "COMPRESSION_UNAVAILABLE",
}
REASON_MESSAGES = {
    "SOURCE_UNREADABLE": "原图无法读取，请检查文件是否存在、可访问且未损坏",
    "SOURCE_METADATA_MISSING": "原图预检信息不完整，需要重新检查",
    "IMAGE_SIZE_BELOW_MINIMUM": "原图小于 200KB，不可用于本次发布",
    "IMAGE_SIZE_EXCEEDED": "原图或预裁剪结果超过 20MB，不可用于本次发布",
    "OUTPUT_DIMENSIONS_BELOW_MINIMUM": "无法裁出宽高均不少于 720px 的合规图片",
    "COMPRESSION_UNAVAILABLE": "当前任务未启用图片压缩，超限图片不可采用",
    "COMPRESSION_TARGET_UNREACHABLE": "在允许质量和最小尺寸内无法压缩到目标大小",
    "OUTPUT_SIZE_BELOW_MINIMUM": "处理后文件小于允许的最小文件大小",
    "SOURCE_SHA_CHANGED": "图片在选择后发生了变化，需要重新确认",
    "OUTPUT_IDENTITY_MISMATCH": "处理后文件已变化，需要返回第四阶段重新确认",
    "OUTPUT_PATH_OUTSIDE_TASK": "处理后文件不在当前任务目录内",
}


class ImagePolicyError(ValueError):
    """Raised when a task cannot produce a trustworthy image-policy snapshot."""


@dataclass(frozen=True)
class SourceInspection:
    source_path: str
    format: str
    size_bytes: int
    width: int
    height: int
    ratio_value: float
    ratio_display: str
    sha256: str
    validation_status: str
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class TargetAssessment:
    target_ratio: str
    max_crop_width: int
    max_crop_height: int
    minimum_status: str
    recommendation_status: str


@dataclass(frozen=True)
class ProcessedOutput:
    output_path: str
    output_size_bytes: int
    output_width: int
    output_height: int
    output_sha256: str
    output_format: str
    target_ratio: str
    kind: str
    provider_id: str
    provider_version: str
    quality: int | None = None


@dataclass(frozen=True)
class CropSuggestion:
    box: dict[str, float]
    provider_id: str
    provider_version: str
    confidence: float | None = None


class CropSuggestionProvider(Protocol):
    def suggest(
        self,
        *,
        asset_id: str,
        source_sha256: str,
        source_path: str,
        width: int,
        height: int,
        target_ratio: str,
    ) -> CropSuggestion | None: ...


class NullCropSuggestionProvider:
    """Normal MVP provider: AI cropping is deliberately unavailable."""

    def suggest(self, **_: Any) -> None:
        return None


@dataclass(frozen=True)
class CompressionResult:
    output_path: str
    output_size_bytes: int
    output_sha256: str
    provider_id: str
    provider_version: str
    output_width: int | None = None
    output_height: int | None = None
    output_format: str = "JPEG"
    quality: int | None = None
    action: str = "compress"
    background_color: str | None = None


class ImageCompressionProvider(Protocol):
    def compress(
        self,
        *,
        asset_id: str,
        source_sha256: str,
        source_path: str,
        target_format: str,
        quality: int,
        max_output_bytes: int,
        derived_root: str,
    ) -> CompressionResult | None: ...


class NullImageCompressionProvider:
    """Normal MVP provider: independent compression is not implemented."""

    available = False

    def compress(self, **_: Any) -> None:
        return None


def default_image_policy() -> dict[str, Any]:
    return {
        "version": 2,
        "formats": ["jpg", "jpeg", "bmp", "gif", "heic", "png", "webp"],
        "allowed_aspect_ratios": ["3:4", "1:1"],
        "aspect_ratio_tolerance": 0.02,
        "recommended_dimensions": {
            "3:4": {"width": 1440, "height": 1920},
            "1:1": {"width": 1440, "height": 1440},
        },
        "recommended_size_mode": "warning",
        "min_width": 720,
        "min_height": 720,
        "min_size_kb": 200,
        "max_size_mb": 20,
        "max_pixels": 80_000_000,
        "output": {
            "format": "JPEG",
            "quality": 92,
            "quality_min": 55,
            "quality_max": 92,
            "optimize": True,
            "progressive": True,
            "background_color": "#FFFFFF",
            "resize_step": 0.9,
            "derived_directory": "derived",
        },
    }


def normalize_image_policy(policy: Mapping[str, Any] | None) -> dict[str, Any]:
    image = policy.get("image") if isinstance(policy, Mapping) else None
    if image is None and isinstance(policy, Mapping):
        image = policy
    if not isinstance(image, Mapping):
        raise ImagePolicyError("IMAGE_POLICY_INCOMPLETE: image policy is missing")
    missing = sorted(
        field for field in IMAGE_POLICY_FIELDS
        if field not in image or image[field] is None
    )
    if missing:
        raise ImagePolicyError(
            "IMAGE_POLICY_INCOMPLETE: missing " + ", ".join(missing)
        )
    normalized = dict(image)
    formats = [
        str(value).casefold().lstrip(".")
        for value in normalized["formats"]
        if str(value).strip()
    ]
    ratios = [str(value).strip() for value in normalized["allowed_aspect_ratios"]]
    dimensions = normalized["recommended_dimensions"]
    output = normalized["output"]
    if not formats or not ratios or not isinstance(dimensions, Mapping):
        raise ImagePolicyError("IMAGE_POLICY_INCOMPLETE: invalid formats or ratios")
    if not isinstance(output, Mapping):
        raise ImagePolicyError("IMAGE_POLICY_INCOMPLETE: output must be an object")
    for ratio in ratios:
        _ratio_value(ratio)
        recommendation = dimensions.get(ratio)
        if (
            not isinstance(recommendation, Mapping)
            or int(recommendation.get("width", 0)) <= 0
            or int(recommendation.get("height", 0)) <= 0
        ):
            raise ImagePolicyError(
                f"IMAGE_POLICY_INCOMPLETE: recommended_dimensions.{ratio}"
            )
    version = int(normalized["version"])
    tolerance = float(normalized["aspect_ratio_tolerance"])
    min_width = int(normalized["min_width"])
    min_height = int(normalized["min_height"])
    min_size_kb = float(normalized["min_size_kb"])
    max_size_mb = float(normalized["max_size_mb"])
    max_pixels = int(normalized["max_pixels"])
    mode = str(normalized["recommended_size_mode"])
    quality_min = int(output.get("quality_min", 55))
    quality_max = int(output.get("quality_max", output.get("quality", 92)))
    resize_step = float(output.get("resize_step", 0.9))
    if (
        version <= 0
        or tolerance < 0
        or min_width <= 0
        or min_height <= 0
        or min_size_kb <= 0
        or max_size_mb <= 0
        or min_size_kb * KIB > max_size_mb * MIB
        or max_pixels <= 0
        or mode not in {"warning", "blocking"}
        or not 1 <= quality_min <= quality_max <= 100
        or not 0.5 <= resize_step < 1
    ):
        raise ImagePolicyError("IMAGE_POLICY_INCOMPLETE: invalid policy values")
    normalized["version"] = version
    normalized["formats"] = formats
    normalized["allowed_aspect_ratios"] = ratios
    normalized["aspect_ratio_tolerance"] = tolerance
    normalized["min_width"] = min_width
    normalized["min_height"] = min_height
    normalized["min_size_kb"] = min_size_kb
    normalized["max_size_mb"] = max_size_mb
    normalized["max_pixels"] = max_pixels
    normalized["recommended_size_mode"] = mode
    normalized["recommended_dimensions"] = {
        ratio: {
            "width": int(dimensions[ratio]["width"]),
            "height": int(dimensions[ratio]["height"]),
        }
        for ratio in ratios
    }
    normalized["output"] = {
        "format": str(output.get("format", "JPEG")).upper(),
        "quality": quality_max,
        "quality_min": quality_min,
        "quality_max": quality_max,
        "optimize": bool(output.get("optimize", True)),
        "progressive": bool(output.get("progressive", True)),
        "background_color": str(output.get("background_color", "#FFFFFF")),
        "resize_step": resize_step,
        "derived_directory": str(output.get("derived_directory", "derived")),
    }
    return normalized


def snapshot_image_policy(
    policy: Mapping[str, Any],
    output_path: Path,
) -> tuple[dict[str, Any], str]:
    normalized = normalize_image_policy(policy)
    payload = json.dumps(
        normalized,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(payload, encoding="utf-8")
    return normalized, hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _ratio_value(value: str) -> float:
    try:
        left, right = str(value).split(":", 1)
        ratio = float(left) / float(right)
    except (TypeError, ValueError, ZeroDivisionError) as error:
        raise ImagePolicyError(f"invalid aspect ratio: {value}") from error
    if ratio <= 0:
        raise ImagePolicyError(f"invalid aspect ratio: {value}")
    return ratio


def maximum_inscribed_crop(
    width: int,
    height: int,
    target_ratio: str,
) -> dict[str, Any]:
    width = int(width)
    height = int(height)
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive")
    ratio = _ratio_value(target_ratio)
    if width / height > ratio:
        crop_height = height
        crop_width = min(width, max(1, int(height * ratio)))
        x = (width - crop_width) // 2
        y = 0
    else:
        crop_width = width
        crop_height = min(height, max(1, int(width / ratio)))
        x = 0
        y = (height - crop_height) // 2
    pixel = {
        "x": x,
        "y": y,
        "width": crop_width,
        "height": crop_height,
    }
    normalized = {
        "x": x / width,
        "y": y / height,
        "width": crop_width / width,
        "height": crop_height / height,
    }
    return {
        "target_ratio": target_ratio,
        "pixel": pixel,
        "normalized": normalized,
    }


def validate_normalized_crop(
    box: Mapping[str, Any],
    *,
    target_ratio: str,
    width: int,
    height: int,
    tolerance: float = 0.002,
) -> dict[str, Any]:
    try:
        normalized = {
            key: float(box[key]) for key in ("x", "y", "width", "height")
        }
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("crop box must contain numeric x, y, width, height") from error
    if (
        normalized["x"] < 0
        or normalized["y"] < 0
        or normalized["width"] <= 0
        or normalized["height"] <= 0
        or normalized["x"] + normalized["width"] > 1 + 1e-9
        or normalized["y"] + normalized["height"] > 1 + 1e-9
    ):
        raise ValueError("crop box must stay within source bounds")
    pixel = {
        "x": round(normalized["x"] * width),
        "y": round(normalized["y"] * height),
        "width": round(normalized["width"] * width),
        "height": round(normalized["height"] * height),
    }
    pixel["width"] = min(pixel["width"], width - pixel["x"])
    pixel["height"] = min(pixel["height"], height - pixel["y"])
    actual = pixel["width"] / pixel["height"] if pixel["height"] else 0
    if abs(actual - _ratio_value(target_ratio)) > tolerance:
        raise ValueError("crop box does not preserve target ratio")
    return {"normalized": normalized, "pixel": pixel}


def format_size(size_bytes: int | None) -> str:
    if size_bytes is None:
        return "未知"
    if int(size_bytes) < MIB:
        return f"{int(size_bytes) / KIB:.0f}KB"
    return f"{int(size_bytes) / MIB:.2f}MB"


def ratio_display(width: int | None, height: int | None) -> str | None:
    if not width or not height or int(width) <= 0 or int(height) <= 0:
        return None
    divisor = math.gcd(int(width), int(height))
    return f"{int(width) // divisor}:{int(height) // divisor}"


def inspect_image_source(
    source_path: Path,
    *,
    policy: Mapping[str, Any] | None = None,
    cache_path: Path | None = None,
) -> dict[str, Any]:
    """Read source facts without modifying the source, with an optional JSON cache."""

    image_policy = normalize_image_policy(policy or default_image_policy())
    source = Path(source_path).resolve()
    inspected_at = datetime.now(timezone.utc).isoformat()
    try:
        stat = source.stat()
    except OSError:
        return {
            "source_path": str(source),
            "readable": False,
            "validation_status": "blocked",
            "reason_codes": ["SOURCE_UNREADABLE"],
            "reason_messages": [REASON_MESSAGES["SOURCE_UNREADABLE"]],
            "inspected_at": inspected_at,
        }
    cache_key = hashlib.sha256(
        f"{str(source).casefold()}\0{stat.st_size}\0{stat.st_mtime_ns}".encode(
            "utf-8"
        )
    ).hexdigest()
    cache_data: dict[str, Any] = {}
    if cache_path is not None:
        cache_file = Path(cache_path)
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cached = {}
        if isinstance(cached, Mapping):
            cache_data = dict(cached)
        entry = cached.get(cache_key) if isinstance(cached, Mapping) else None
        if isinstance(entry, Mapping):
            return {**dict(entry), "cache_hit": True}
    reasons: list[str] = []
    width = height = None
    image_format = ""
    mode = ""
    try:
        with Image.open(source) as opened:
            image_format = str(opened.format or source.suffix.lstrip(".")).upper()
            transposed = ImageOps.exif_transpose(opened)
            width, height = transposed.size
            mode = str(transposed.mode)
            if int(width) * int(height) > image_policy["max_pixels"]:
                reasons.append("IMAGE_PIXEL_LIMIT_EXCEEDED")
            transposed.verify()
    except (OSError, UnidentifiedImageError, ValueError):
        reasons.append("SOURCE_UNREADABLE")
    if not width or not height:
        reasons.append("SOURCE_METADATA_MISSING")
    extension = source.suffix.casefold().lstrip(".")
    if extension not in image_policy["formats"]:
        reasons.append("IMAGE_FORMAT_UNSUPPORTED")
    sha256 = sha256_file(source) if stat.st_size else ""
    result = {
        "source_path": str(source),
        "format": image_format,
        "extension": extension,
        "mode": mode,
        "size_bytes": int(stat.st_size),
        "size_display": format_size(int(stat.st_size)),
        "width": int(width) if width else None,
        "height": int(height) if height else None,
        "ratio_value": (
            round(int(width) / int(height), 6) if width and height else None
        ),
        "ratio_display": ratio_display(width, height),
        "sha256": sha256,
        "readable": not reasons,
        "validation_status": "valid" if not reasons else "blocked",
        "reason_codes": list(dict.fromkeys(reasons)),
        "reason_messages": [
            REASON_MESSAGES.get(reason, reason)
            for reason in dict.fromkeys(reasons)
        ],
        "file_mtime_ns": int(stat.st_mtime_ns),
        "inspected_at": inspected_at,
        "cache_key": cache_key,
        "cache_hit": False,
    }
    if cache_path is not None:
        cache_file = Path(cache_path)
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_data[cache_key] = result
        cache_file.write_text(
            json.dumps(cache_data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return result


def load_image_preflight_source(
    source_path: Path,
    *,
    policy: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], Image.Image | None, str, dict[str, float]]:
    """Read, hash, and decode one source exactly once for ratio probes."""

    image_policy = normalize_image_policy(policy or default_image_policy())
    source = Path(source_path).resolve()
    started = time.perf_counter()
    inspected_at = datetime.now(timezone.utc).isoformat()
    try:
        stat = source.stat()
        read_started = time.perf_counter()
        payload = source.read_bytes()
        read_ms = (time.perf_counter() - read_started) * 1000
    except OSError:
        elapsed_ms = (time.perf_counter() - started) * 1000
        return (
            {
                "source_path": str(source),
                "readable": False,
                "validation_status": "blocked",
                "reason_codes": ["SOURCE_UNREADABLE"],
                "reason_messages": [REASON_MESSAGES["SOURCE_UNREADABLE"]],
                "inspected_at": inspected_at,
            },
            None,
            "",
            {"read_ms": elapsed_ms, "sha256_ms": 0.0, "decode_ms": 0.0},
        )

    sha_started = time.perf_counter()
    source_sha256 = hashlib.sha256(payload).hexdigest() if payload else ""
    sha256_ms = (time.perf_counter() - sha_started) * 1000
    reasons: list[str] = []
    width = height = None
    source_format = ""
    mode = ""
    decoded: Image.Image | None = None
    decode_started = time.perf_counter()
    try:
        with Image.open(BytesIO(payload)) as opened:
            source_format = str(
                opened.format or source.suffix.lstrip(".")
            ).upper()
            decoded = ImageOps.exif_transpose(opened)
            decoded.load()
            width, height = decoded.size
            mode = str(decoded.mode)
            if int(width) * int(height) > image_policy["max_pixels"]:
                reasons.append("IMAGE_PIXEL_LIMIT_EXCEEDED")
    except (OSError, UnidentifiedImageError, ValueError):
        reasons.append("SOURCE_UNREADABLE")
        decoded = None
    decode_ms = (time.perf_counter() - decode_started) * 1000
    if not width or not height:
        reasons.append("SOURCE_METADATA_MISSING")
    extension = source.suffix.casefold().lstrip(".")
    if extension not in image_policy["formats"]:
        reasons.append("IMAGE_FORMAT_UNSUPPORTED")
    unique_reasons = list(dict.fromkeys(reasons))
    inspection = {
        "source_path": str(source),
        "format": source_format,
        "extension": extension,
        "mode": mode,
        "size_bytes": int(stat.st_size),
        "size_display": format_size(int(stat.st_size)),
        "width": int(width) if width else None,
        "height": int(height) if height else None,
        "ratio_value": (
            round(int(width) / int(height), 6) if width and height else None
        ),
        "ratio_display": ratio_display(width, height),
        "sha256": source_sha256,
        "readable": not unique_reasons,
        "validation_status": "valid" if not unique_reasons else "blocked",
        "reason_codes": unique_reasons,
        "reason_messages": [
            REASON_MESSAGES.get(reason, reason) for reason in unique_reasons
        ],
        "file_mtime_ns": int(stat.st_mtime_ns),
        "inspected_at": inspected_at,
        "cache_hit": False,
    }
    return (
        inspection,
        decoded,
        source_format,
        {
            "read_ms": round(read_ms, 3),
            "sha256_ms": round(sha256_ms, 3),
            "decode_ms": round(decode_ms, 3),
        },
    )


def classify_image(
    *,
    width: int | None,
    height: int | None,
    size_bytes: int | None,
    extension: str,
    validation_status: str = "valid",
    reason_codes: list[str] | tuple[str, ...] = (),
    policy: Mapping[str, Any] | None = None,
    compression_available: bool = True,
) -> dict[str, Any]:
    image_policy = normalize_image_policy(policy or default_image_policy())
    reasons = list(dict.fromkeys(str(value) for value in reason_codes if str(value)))
    normalized_extension = str(extension).casefold().lstrip(".")
    if normalized_extension not in image_policy["formats"]:
        reasons.append("IMAGE_FORMAT_UNSUPPORTED")
    if not width or not height or int(width) <= 0 or int(height) <= 0:
        reasons.append("ZERO_DIMENSION_IMAGE")
    min_size_bytes = round(image_policy["min_size_kb"] * KIB)
    max_size_bytes = round(image_policy["max_size_mb"] * MIB)
    size_below_minimum = (
        size_bytes is not None and int(size_bytes) < min_size_bytes
    )
    size_exceeded = size_bytes is not None and int(size_bytes) > max_size_bytes
    if size_bytes is None:
        reasons.append("SOURCE_METADATA_MISSING")
    if size_below_minimum:
        reasons.append("IMAGE_SIZE_BELOW_MINIMUM")
    if size_exceeded and "IMAGE_SIZE_EXCEEDED" not in reasons:
        reasons.append("IMAGE_SIZE_EXCEEDED")
    checks: dict[str, Any] = {}
    matching_ratios: list[str] = []
    if validation_status == "valid" and width and height:
        actual_ratio = int(width) / int(height)
        for target_ratio in image_policy["allowed_aspect_ratios"]:
            crop = maximum_inscribed_crop(int(width), int(height), target_ratio)
            recommendation = image_policy["recommended_dimensions"][target_ratio]
            meets = (
                crop["pixel"]["width"] >= recommendation["width"]
                and crop["pixel"]["height"] >= recommendation["height"]
            )
            meets_minimum = (
                crop["pixel"]["width"] >= image_policy["min_width"]
                and crop["pixel"]["height"] >= image_policy["min_height"]
            )
            if abs(actual_ratio - _ratio_value(target_ratio)) <= image_policy[
                "aspect_ratio_tolerance"
            ]:
                matching_ratios.append(target_ratio)
            checks[target_ratio] = {
                "target_ratio": target_ratio,
                "max_crop_width": crop["pixel"]["width"],
                "max_crop_height": crop["pixel"]["height"],
                "recommended_width": recommendation["width"],
                "recommended_height": recommendation["height"],
                "minimum_width": image_policy["min_width"],
                "minimum_height": image_policy["min_height"],
                "minimum_status": (
                    "meets_minimum" if meets_minimum else "below_minimum"
                ),
                "status": "meets_or_exceeds" if meets else "below_recommended",
            }
    output_dimensions_valid = any(
        item["minimum_status"] == "meets_minimum" for item in checks.values()
    )
    if checks and not output_dimensions_valid:
        reasons.append("OUTPUT_DIMENSIONS_BELOW_MINIMUM")
    if size_exceeded and not compression_available:
        reasons.append("COMPRESSION_UNAVAILABLE")
    unusable = validation_status != "valid" or any(
        reason in UNUSABLE_REASON_CODES for reason in reasons
    )
    below_recommended = bool(checks) and not any(
        item["status"] == "meets_or_exceeds" for item in checks.values()
    )
    if unusable:
        status = "unusable"
    elif size_exceeded:
        status = "needs_compression" if matching_ratios else "crop_and_compress"
    elif matching_ratios:
        status = "direct"
    else:
        status = "croppable"
    if (
        below_recommended
        and image_policy["recommended_size_mode"] == "blocking"
        and "IMAGE_RESOLUTION_BELOW_RECOMMENDED" not in reasons
    ):
        reasons.append("IMAGE_RESOLUTION_BELOW_RECOMMENDED")
    return {
        "status": status,
        "selectable": not unusable,
        "width": width,
        "height": height,
        "original_ratio": (
            round(int(width) / int(height), 4) if width and height else None
        ),
        "matching_ratios": matching_ratios,
        "resolution_checks": checks,
        "size_bytes": size_bytes,
        "size_display": format_size(size_bytes),
        "min_size_bytes": min_size_bytes,
        "min_size_display": format_size(min_size_bytes),
        "max_size_bytes": max_size_bytes,
        "max_size_display": format_size(max_size_bytes),
        "size_below_minimum": size_below_minimum,
        "size_exceeded": size_exceeded,
        "compression_available": bool(compression_available),
        "reason_codes": list(dict.fromkeys(reasons)),
        "reason_messages": [
            REASON_MESSAGES.get(reason, reason)
            for reason in dict.fromkeys(reasons)
        ],
    }


def validate_crop_suggestion(
    suggestion: CropSuggestion,
    *,
    target_ratio: str,
    width: int,
    height: int,
) -> dict[str, Any]:
    if not suggestion.provider_id or not suggestion.provider_version:
        raise ValueError("crop suggestion provider identity is required")
    return validate_normalized_crop(
        suggestion.box,
        target_ratio=target_ratio,
        width=width,
        height=height,
    )


def validate_compression_result(
    result: CompressionResult,
    *,
    derived_root: Path,
    source_sha256: str,
) -> Path:
    if not result.provider_id or not result.provider_version:
        raise ValueError("compression provider identity is required")
    output = Path(result.output_path).resolve()
    root = Path(derived_root).resolve()
    if not output.is_relative_to(root) or not output.is_file():
        raise ValueError("compression output must exist inside the task directory")
    if output.stat().st_size != int(result.output_size_bytes):
        raise ValueError("compression output size does not match")
    if sha256_file(output) != result.output_sha256:
        raise ValueError("compression output SHA-256 does not match")
    if result.output_sha256 == source_sha256 and output.stat().st_size == 0:
        raise ValueError("compression output is invalid")
    return output


class PillowImageCompressionProvider:
    """Task-local JPEG compression without external executables or services."""

    available = True
    provider_id = "pillow"
    provider_version = "1"

    def __init__(self, policy: Mapping[str, Any] | None = None):
        self.policy = normalize_image_policy(policy or default_image_policy())

    def compress(
        self,
        *,
        asset_id: str,
        source_sha256: str,
        source_path: str,
        target_format: str = "JPEG",
        quality: int | None = None,
        max_output_bytes: int | None = None,
        derived_root: str,
        target_ratio: str | None = None,
        normalized_box: Mapping[str, Any] | None = None,
    ) -> CompressionResult:
        source = Path(source_path).resolve()
        root = Path(derived_root).resolve()
        root.mkdir(parents=True, exist_ok=True)
        if not source.is_file() or sha256_file(source) != source_sha256:
            raise ValueError("SOURCE_IDENTITY_MISMATCH: source image changed")
        before = source.stat()
        policy = self.policy
        output_policy = policy["output"]
        output_format = str(target_format or output_policy["format"]).upper()
        if output_format != "JPEG":
            raise ValueError("IMAGE_FORMAT_UNSUPPORTED: Pillow MVP outputs JPEG")
        maximum = int(
            max_output_bytes
            if max_output_bytes is not None
            else round(policy["max_size_mb"] * MIB)
        )
        minimum = round(policy["min_size_kb"] * KIB)
        quality_max = min(
            int(quality if quality is not None else output_policy["quality_max"]),
            int(output_policy["quality_max"]),
        )
        quality_min = int(output_policy["quality_min"])
        identity = hashlib.sha256(
            json.dumps(
                {
                    "asset_id": asset_id,
                    "source_sha256": source_sha256,
                    "target_ratio": target_ratio,
                    "normalized_box": normalized_box,
                    "policy": policy,
                    "quality_max": quality_max,
                    "maximum": maximum,
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()[:20]
        output = (root / f"{asset_id}-compressed-{identity}.jpg").resolve()
        if not output.is_relative_to(root) or output == source:
            raise ValueError("OUTPUT_PATH_OUTSIDE_TASK")
        temporary = output.with_name(f".{output.name}.tmp")
        chosen_quality: int | None = None
        background = output_policy["background_color"]
        action = "crop_and_compress" if normalized_box is not None else "compress"
        try:
            with Image.open(source) as opened:
                image = ImageOps.exif_transpose(opened)
                if image.width * image.height > policy["max_pixels"]:
                    raise ValueError("IMAGE_PIXEL_LIMIT_EXCEEDED")
                if normalized_box is not None:
                    if not target_ratio:
                        raise ValueError("crop compression requires target_ratio")
                    validated = validate_normalized_crop(
                        normalized_box,
                        target_ratio=target_ratio,
                        width=image.width,
                        height=image.height,
                    )
                    pixel = validated["pixel"]
                    image = image.crop(
                        (
                            pixel["x"],
                            pixel["y"],
                            pixel["x"] + pixel["width"],
                            pixel["y"] + pixel["height"],
                        )
                    )
                if image.mode in {"RGBA", "LA"} or (
                    image.mode == "P" and "transparency" in image.info
                ):
                    rgba = image.convert("RGBA")
                    canvas = Image.new("RGBA", rgba.size, background)
                    canvas.alpha_composite(rgba)
                    image = canvas.convert("RGB")
                else:
                    image = image.convert("RGB")
                while True:
                    chosen_quality = self._best_quality(
                        image,
                        temporary,
                        quality_min=quality_min,
                        quality_max=quality_max,
                        maximum=maximum,
                        minimum=minimum,
                    )
                    if chosen_quality is not None:
                        break
                    if temporary.exists() and temporary.stat().st_size < minimum:
                        raise ValueError("OUTPUT_SIZE_BELOW_MINIMUM")
                    next_width = int(image.width * output_policy["resize_step"])
                    next_height = int(image.height * output_policy["resize_step"])
                    if (
                        next_width >= image.width
                        or next_height >= image.height
                        or next_width < policy["min_width"]
                        or next_height < policy["min_height"]
                    ):
                        raise ValueError("COMPRESSION_TARGET_UNREACHABLE")
                    image = image.resize(
                        (next_width, next_height),
                        Image.Resampling.LANCZOS,
                    )
                temporary.replace(output)
            after = source.stat()
            if (
                before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns
                or sha256_file(source) != source_sha256
            ):
                output.unlink(missing_ok=True)
                raise ValueError("SOURCE_IDENTITY_MISMATCH: source changed during processing")
            with Image.open(output) as verified:
                output_width, output_height = verified.size
            result = CompressionResult(
                output_path=str(output),
                output_size_bytes=output.stat().st_size,
                output_sha256=sha256_file(output),
                provider_id=self.provider_id,
                provider_version=self.provider_version,
                output_width=output_width,
                output_height=output_height,
                output_format=output_format,
                quality=chosen_quality,
                action=action,
                background_color=background,
            )
            validate_compression_result(
                result,
                derived_root=root,
                source_sha256=source_sha256,
            )
            return result
        finally:
            temporary.unlink(missing_ok=True)

    def _best_quality(
        self,
        image: Image.Image,
        temporary: Path,
        *,
        quality_min: int,
        quality_max: int,
        maximum: int,
        minimum: int,
    ) -> int | None:
        best: int | None = None
        low, high = quality_min, quality_max
        last_size = 0
        while low <= high:
            candidate = (low + high) // 2
            image.save(
                temporary,
                format="JPEG",
                quality=candidate,
                optimize=bool(self.policy["output"]["optimize"]),
                progressive=bool(self.policy["output"]["progressive"]),
            )
            last_size = temporary.stat().st_size
            if minimum <= last_size <= maximum:
                best = candidate
                low = candidate + 1
            elif last_size > maximum:
                high = candidate - 1
            else:
                low = candidate + 1
        if best is not None:
            image.save(
                temporary,
                format="JPEG",
                quality=best,
                optimize=bool(self.policy["output"]["optimize"]),
                progressive=bool(self.policy["output"]["progressive"]),
            )
            return best
        if last_size < minimum:
            image.save(
                temporary,
                format="JPEG",
                quality=quality_max,
                optimize=bool(self.policy["output"]["optimize"]),
                progressive=bool(self.policy["output"]["progressive"]),
            )
        return None


def probe_loaded_crop_output_size(
    image: Image.Image,
    *,
    source_format: str,
    target_ratio: str,
    normalized_box: Mapping[str, Any],
    policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Encode one ratio from an already decoded source image."""

    started = time.perf_counter()
    image_policy = normalize_image_policy(policy or default_image_policy())
    minimum = round(float(image_policy["min_size_kb"]) * KIB)
    maximum = round(float(image_policy["max_size_mb"]) * MIB)
    output_policy = image_policy["output"]
    normalized_format = str(source_format or "").upper()
    validated = validate_normalized_crop(
        normalized_box,
        target_ratio=target_ratio,
        width=image.width,
        height=image.height,
    )
    pixel = validated["pixel"]
    cropped = image.crop(
        (
            pixel["x"],
            pixel["y"],
            pixel["x"] + pixel["width"],
            pixel["y"] + pixel["height"],
        )
    )
    rendered: Image.Image | None = None
    rgba: Image.Image | None = None
    canvas: Image.Image | None = None
    try:
        if cropped.mode in {"RGBA", "LA"} or (
            cropped.mode == "P" and "transparency" in cropped.info
        ):
            rgba = cropped.convert("RGBA")
            canvas = Image.new(
                "RGBA", rgba.size, output_policy["background_color"]
            )
            canvas.alpha_composite(rgba)
            rendered = canvas.convert("RGB")
        else:
            rendered = cropped.convert("RGB")
        buffer = BytesIO()
        save_options: dict[str, Any] = {}
        if normalized_format == "JPEG":
            save_options = {
                "quality": int(output_policy["quality_max"]),
                "optimize": bool(output_policy["optimize"]),
                "progressive": bool(output_policy["progressive"]),
            }
        elif normalized_format == "PNG":
            save_options = {"optimize": bool(output_policy["optimize"])}
        elif normalized_format == "WEBP":
            save_options = {"quality": int(output_policy["quality_max"])}
        elif normalized_format == "GIF":
            save_options = {"optimize": bool(output_policy["optimize"])}
        elif normalized_format not in {"BMP", "HEIC", "HEIF"}:
            raise ValueError("IMAGE_FORMAT_UNSUPPORTED")
        rendered.save(buffer, format=normalized_format, **save_options)
        size_bytes = buffer.tell()
    finally:
        if rendered is not None:
            rendered.close()
        if canvas is not None:
            canvas.close()
        if rgba is not None:
            rgba.close()
        cropped.close()
    return {
        "output_size_bytes": size_bytes,
        "minimum_size_bytes": minimum,
        "maximum_size_bytes": maximum,
        "meets_minimum": size_bytes >= minimum,
        "meets_maximum": size_bytes <= maximum,
        "meets_size_range": minimum <= size_bytes <= maximum,
        "probe_quality": int(output_policy["quality_max"]),
        "probe_format": normalized_format,
        "encode_ms": round((time.perf_counter() - started) * 1000, 3),
    }


def probe_crop_output_size(
    source_path: Path,
    *,
    target_ratio: str,
    normalized_box: Mapping[str, Any],
    policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Render a same-format crop in memory and check the upload size range."""

    with Image.open(Path(source_path).resolve()) as opened:
        source_format = str(opened.format or "").upper()
        image = ImageOps.exif_transpose(opened)
        image.load()
    try:
        return probe_loaded_crop_output_size(
            image,
            source_format=source_format,
            target_ratio=target_ratio,
            normalized_box=normalized_box,
            policy=policy,
        )
    finally:
        image.close()


def generate_crop_derivative(
    *,
    source_path: Path,
    expected_source_sha256: str,
    target_ratio: str,
    normalized_box: Mapping[str, Any],
    derived_root: Path,
    asset_id: str,
    policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    image_policy = normalize_image_policy(policy or default_image_policy())
    source = Path(source_path).resolve()
    root = Path(derived_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if not source.is_file() or sha256_file(source) != expected_source_sha256:
        raise ValueError("source image changed or is unavailable")
    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened)
        width, height = image.size
        validated = validate_normalized_crop(
            normalized_box,
            target_ratio=target_ratio,
            width=width,
            height=height,
        )
        pixel = validated["pixel"]
        identity = hashlib.sha256(
            json.dumps(
                {
                    "source_sha256": expected_source_sha256,
                    "target_ratio": target_ratio,
                    "box": validated["normalized"],
                    "policy": image_policy,
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()[:16]
        output = (root / f"{asset_id}-{target_ratio.replace(':', 'x')}-{identity}.jpg").resolve()
        if not output.is_relative_to(root):
            raise ValueError("derived output escaped the task directory")
        cropped = image.crop(
            (
                pixel["x"],
                pixel["y"],
                pixel["x"] + pixel["width"],
                pixel["y"] + pixel["height"],
            )
        )
        if cropped.mode in {"RGBA", "LA"} or (
            cropped.mode == "P" and "transparency" in cropped.info
        ):
            rgba = cropped.convert("RGBA")
            canvas = Image.new(
                "RGBA",
                rgba.size,
                image_policy["output"]["background_color"],
            )
            canvas.alpha_composite(rgba)
            cropped = canvas.convert("RGB")
        else:
            cropped = cropped.convert("RGB")
        temporary = output.with_name(f".{output.name}.tmp")
        try:
            cropped.save(
                temporary,
                format=image_policy["output"]["format"],
                quality=image_policy["output"]["quality"],
                optimize=image_policy["output"]["optimize"],
                progressive=image_policy["output"]["progressive"],
            )
            temporary.replace(output)
        finally:
            temporary.unlink(missing_ok=True)
    return {
        "output_path": str(output),
        "output_width": pixel["width"],
        "output_height": pixel["height"],
        "output_size_bytes": output.stat().st_size,
        "output_sha256": sha256_file(output),
        "source_path": str(source),
        "source_sha256": expected_source_sha256,
        "target_ratio": target_ratio,
        "crop_box": validated,
        "output_format": image_policy["output"]["format"],
        "quality": image_policy["output"]["quality"],
        "provider_id": "pillow-crop",
        "provider_version": "1",
    }


def provider_contract_payload(value: Any) -> dict[str, Any]:
    """Return a JSON-friendly representation for audit/test fixtures."""

    return asdict(value) if hasattr(value, "__dataclass_fields__") else dict(value)
