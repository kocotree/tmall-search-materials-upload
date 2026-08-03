from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image
import pytest

from upload_search_materials.image_compliance import (
    CompressionResult,
    CropSuggestion,
    ImagePolicyError,
    MIB,
    NullCropSuggestionProvider,
    NullImageCompressionProvider,
    PillowImageCompressionProvider,
    classify_image,
    default_image_policy,
    generate_crop_derivative,
    inspect_image_source,
    maximum_inscribed_crop,
    normalize_image_policy,
    probe_crop_output_size,
    snapshot_image_policy,
    validate_compression_result,
    validate_crop_suggestion,
    validate_normalized_crop,
)


def test_default_policy_is_complete_and_uses_twenty_mib():
    policy = normalize_image_policy(default_image_policy())
    assert policy["max_size_mb"] == 20
    assert policy["recommended_dimensions"]["3:4"] == {
        "width": 1440,
        "height": 1920,
    }


def test_incomplete_policy_is_rejected():
    with pytest.raises(ImagePolicyError, match="IMAGE_POLICY_INCOMPLETE"):
        normalize_image_policy({"image": {"formats": ["jpg"]}})


def test_policy_snapshot_is_deterministic(tmp_path):
    first, first_hash = snapshot_image_policy(
        {"image": default_image_policy()},
        tmp_path / "policy.json",
    )
    second, second_hash = snapshot_image_policy(
        first,
        tmp_path / "policy-2.json",
    )
    assert first == second
    assert first_hash == second_hash


@pytest.mark.parametrize(
    ("width", "height", "ratio", "pixel"),
    [
        (3000, 2000, "1:1", {"x": 500, "y": 0, "width": 2000, "height": 2000}),
        (2000, 3000, "1:1", {"x": 0, "y": 500, "width": 2000, "height": 2000}),
        (3000, 2000, "3:4", {"x": 750, "y": 0, "width": 1500, "height": 2000}),
        (2000, 3000, "3:4", {"x": 0, "y": 167, "width": 2000, "height": 2666}),
    ],
)
def test_maximum_inscribed_crop_is_centered(width, height, ratio, pixel):
    assert maximum_inscribed_crop(width, height, ratio)["pixel"] == pixel


def test_preflight_reports_resolution_ratio_and_size():
    result = classify_image(
        width=3000,
        height=4000,
        size_bytes=20 * MIB,
        extension=".jpg",
    )
    assert result["status"] == "direct"
    assert result["matching_ratios"] == ["3:4"]
    assert result["resolution_checks"]["3:4"]["status"] == "meets_or_exceeds"
    assert result["size_exceeded"] is False
    assert result["size_display"] == "20.00MB"


def test_preflight_twenty_mib_boundary_and_oversize_is_blocked():
    at_limit = classify_image(
        width=1000,
        height=1000,
        size_bytes=20 * MIB,
        extension=".png",
    )
    over_limit = classify_image(
        width=1000,
        height=1000,
        size_bytes=20 * MIB + 1,
        extension=".png",
    )
    assert at_limit["status"] == "direct"
    assert over_limit["status"] == "unusable"
    assert over_limit["selectable"] is False
    assert "IMAGE_SIZE_EXCEEDED" in over_limit["reason_codes"]
    assert over_limit["resolution_checks"]["1:1"]["status"] == "below_recommended"


def test_preflight_enforces_minimum_size_dimensions_and_compression_capability():
    too_small = classify_image(
        width=1440,
        height=1440,
        size_bytes=200 * 1024 - 1,
        extension=".jpg",
    )
    assert too_small["selectable"] is False
    assert "IMAGE_SIZE_BELOW_MINIMUM" in too_small["reason_codes"]
    too_short = classify_image(
        width=2000,
        height=719,
        size_bytes=200 * 1024,
        extension=".jpg",
    )
    assert too_short["selectable"] is False
    assert "OUTPUT_DIMENSIONS_BELOW_MINIMUM" in too_short["reason_codes"]
    no_provider = classify_image(
        width=1440,
        height=1920,
        size_bytes=20 * MIB + 1,
        extension=".jpg",
        compression_available=False,
    )
    assert no_provider["selectable"] is False
    assert "COMPRESSION_UNAVAILABLE" in no_provider["reason_codes"]


@pytest.mark.parametrize(
    ("width", "height", "size_bytes", "selectable"),
    [
        (720, 720, 200 * 1024, True),
        (719, 720, 200 * 1024, False),
        (720, 719, 200 * 1024, False),
        (720, 720, 200 * 1024 - 1, False),
        (720, 720, 20 * MIB, True),
    ],
)
def test_preflight_hard_rule_boundaries(width, height, size_bytes, selectable):
    result = classify_image(
        width=width,
        height=height,
        size_bytes=size_bytes,
        extension=".jpg",
    )
    assert result["selectable"] is selectable


@pytest.mark.parametrize(
    ("status", "reasons", "extension"),
    [
        ("blocked", ["ASSET_UNREADABLE"], ".jpg"),
        ("valid", ["ZERO_BYTE_ASSET"], ".jpg"),
        ("valid", [], ".gif"),
    ],
)
def test_unusable_image_is_not_selectable(status, reasons, extension):
    result = classify_image(
        width=100,
        height=100,
        size_bytes=100,
        extension=extension,
        validation_status=status,
        reason_codes=reasons,
    )
    assert result["status"] == "unusable"
    assert result["selectable"] is False


def test_crop_box_validation_rejects_ratio_and_bounds():
    valid = validate_normalized_crop(
        {"x": 0.25, "y": 0, "width": 0.5, "height": 1},
        target_ratio="1:1",
        width=2000,
        height=1000,
    )
    assert valid["pixel"]["width"] == valid["pixel"]["height"] == 1000
    with pytest.raises(ValueError, match="source bounds"):
        validate_normalized_crop(
            {"x": 0.8, "y": 0, "width": 0.5, "height": 1},
            target_ratio="1:1",
            width=2000,
            height=1000,
        )


def test_null_providers_do_not_claim_capabilities():
    assert NullCropSuggestionProvider().suggest() is None
    assert NullImageCompressionProvider().compress() is None


def test_crop_suggestion_contract_requires_provider_identity():
    with pytest.raises(ValueError, match="provider identity"):
        validate_crop_suggestion(
            CropSuggestion(
                box={"x": 0, "y": 0, "width": 1, "height": 1},
                provider_id="",
                provider_version="",
            ),
            target_ratio="1:1",
            width=100,
            height=100,
        )


def test_generate_crop_derivative_is_task_local_and_source_unchanged(tmp_path):
    source = tmp_path / "source.png"
    Image.new("RGB", (1200, 1600), "red").save(source)
    source_bytes = source.read_bytes()
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    result = generate_crop_derivative(
        source_path=source,
        expected_source_sha256=source_hash,
        target_ratio="1:1",
        normalized_box={"x": 0, "y": 0.125, "width": 1, "height": 0.75},
        derived_root=tmp_path / "run" / "04-image-review" / "derived",
        asset_id="asset-a",
    )
    output = Path(result["output_path"])
    assert output.is_file()
    assert output.is_relative_to(tmp_path / "run" / "04-image-review" / "derived")
    assert source.read_bytes() == source_bytes
    repeated = generate_crop_derivative(
        source_path=source,
        expected_source_sha256=source_hash,
        target_ratio="1:1",
        normalized_box={"x": 0, "y": 0.125, "width": 1, "height": 0.75},
        derived_root=tmp_path / "run" / "04-image-review" / "derived",
        asset_id="asset-a",
    )
    assert repeated["output_path"] == result["output_path"]
    assert repeated["output_sha256"] == result["output_sha256"]


def test_crop_probe_preserves_png_format(tmp_path):
    source = tmp_path / "source.png"
    Image.effect_noise((1600, 1600), 80).convert("RGB").save(source)
    result = probe_crop_output_size(
        source,
        target_ratio="3:4",
        normalized_box=maximum_inscribed_crop(1600, 1600, "3:4")["normalized"],
    )
    assert result["probe_format"] == "PNG"
    assert result["meets_size_range"] is True


def test_generate_crop_rejects_changed_source(tmp_path):
    source = tmp_path / "source.jpg"
    Image.new("RGB", (100, 100), "blue").save(source)
    with pytest.raises(ValueError, match="source image changed"):
        generate_crop_derivative(
            source_path=source,
            expected_source_sha256="0" * 64,
            target_ratio="1:1",
            normalized_box={"x": 0, "y": 0, "width": 1, "height": 1},
            derived_root=tmp_path / "derived",
            asset_id="a",
        )


def test_compression_contract_rejects_path_escape(tmp_path):
    output = tmp_path / "outside.jpg"
    output.write_bytes(b"output")
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="task directory"):
        validate_compression_result(
            CompressionResult(
                output_path=str(output),
                output_size_bytes=output.stat().st_size,
                output_sha256=digest,
                provider_id="test",
                provider_version="1",
            ),
            derived_root=tmp_path / "derived",
            source_sha256="source",
        )


def test_source_inspection_records_original_facts_and_uses_cache(tmp_path):
    source = tmp_path / "source.png"
    Image.new("RGB", (1440, 1920), "orange").save(source)
    cache = tmp_path / "cache.json"
    first = inspect_image_source(source, cache_path=cache)
    second = inspect_image_source(source, cache_path=cache)
    assert first["width"] == 1440
    assert first["height"] == 1920
    assert first["ratio_display"] == "3:4"
    assert first["size_bytes"] == source.stat().st_size
    assert len(first["sha256"]) == 64
    assert second["cache_hit"] is True


def test_pillow_provider_compresses_to_task_directory_and_preserves_source(tmp_path):
    source = tmp_path / "source.jpg"
    Image.effect_noise((1600, 1600), 100).convert("RGB").save(
        source,
        format="JPEG",
        quality=100,
    )
    original = source.read_bytes()
    source_hash = hashlib.sha256(original).hexdigest()
    policy = {
        **default_image_policy(),
        "min_size_kb": 10,
        "max_size_mb": 0.5,
    }
    provider = PillowImageCompressionProvider(policy)
    result = provider.compress(
        asset_id="asset-noise",
        source_sha256=source_hash,
        source_path=str(source),
        target_format="JPEG",
        quality=92,
        max_output_bytes=round(0.5 * MIB),
        derived_root=str(tmp_path / "run" / "04-image-review" / "derived"),
        target_ratio="1:1",
    )
    output = Path(result.output_path)
    assert output.is_file()
    assert output.stat().st_size <= round(0.5 * MIB)
    assert output.stat().st_size >= 10 * 1024
    assert result.quality is not None
    assert source.read_bytes() == original


def test_pillow_provider_flattens_transparency_with_policy_background(tmp_path):
    source = tmp_path / "transparent.png"
    Image.new("RGBA", (800, 800), (255, 0, 0, 100)).save(source)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    policy = {
        **default_image_policy(),
        "min_size_kb": 0.001,
    }
    result = PillowImageCompressionProvider(policy).compress(
        asset_id="transparent",
        source_sha256=source_hash,
        source_path=str(source),
        derived_root=str(tmp_path / "derived"),
        target_ratio="1:1",
    )
    with Image.open(result.output_path) as output:
        assert output.mode == "RGB"
    assert result.background_color == "#FFFFFF"
