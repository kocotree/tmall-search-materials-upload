import csv

from PIL import Image

from upload_search_materials.assets import (
    DirectoryAssetSource,
    ManifestAssetSource,
    inspect_asset,
    load_media_policy,
    validate_asset_group,
)


def make_image(path, size=(400, 400)):
    Image.new("RGB", size, color="white").save(path)


def test_mixed_image_ratios_are_blocked(tmp_path):
    paths = [tmp_path / "a.png", tmp_path / "b.png", tmp_path / "c.png"]
    make_image(paths[0], (300, 400))
    make_image(paths[1], (400, 400))
    make_image(paths[2], (300, 400))
    records = [inspect_asset(path, "123", "confirmed") for path in paths]

    result = validate_asset_group(records, material_type="image_text")

    assert result.status == "blocked"
    assert "MIXED_ASPECT_RATIO" in result.reason_codes


def test_unknown_license_blocks_asset(tmp_path):
    path = tmp_path / "a.png"
    make_image(path)

    record = inspect_asset(path, "123", "unknown")

    assert record.validation_status == "blocked"
    assert "LICENSE_UNKNOWN" in record.reason_codes


def test_zero_byte_asset_is_blocked(tmp_path):
    path = tmp_path / "empty.png"
    path.write_bytes(b"")

    record = inspect_asset(path, "123", "confirmed")

    assert record.validation_status == "blocked"
    assert "ZERO_BYTE_ASSET" in record.reason_codes


def test_duplicate_fingerprints_are_blocked(tmp_path):
    first = tmp_path / "a.png"
    second = tmp_path / "b.png"
    make_image(first)
    second.write_bytes(first.read_bytes())
    records = [inspect_asset(path, "123", "confirmed") for path in (first, second)]

    result = validate_asset_group(records, material_type="image_text")

    assert "DUPLICATE_ASSET" in result.reason_codes


def test_directory_source_prefers_product_id_then_sku(tmp_path):
    product_dir = tmp_path / "123"
    sku_dir = tmp_path / "SKU-1"
    product_dir.mkdir()
    sku_dir.mkdir()
    make_image(product_dir / "product.png")
    make_image(sku_dir / "sku.png")

    paths = DirectoryAssetSource(tmp_path).collect("123", "SKU-1")

    assert paths == [product_dir / "product.png"]


def test_directory_source_rejects_paths_outside_root(tmp_path):
    root = tmp_path / "assets"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.png").write_bytes(b"secret")

    paths = DirectoryAssetSource(root).collect("../outside", "")

    assert paths == []


def test_manifest_source_filters_by_product_and_keeps_license(tmp_path):
    manifest = tmp_path / "assets.csv"
    image_path = tmp_path / "a.png"
    make_image(image_path)
    with manifest.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=["product_id", "sku", "source_path", "license_status", "asset_type"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "product_id": "123",
                "sku": "SKU-1",
                "source_path": str(image_path),
                "license_status": "confirmed",
                "asset_type": "image",
            }
        )

    records = ManifestAssetSource(manifest).collect("123", "")

    assert len(records) == 1
    assert records[0].license_status == "confirmed"
    assert records[0].source_path == str(image_path.resolve())


def test_incomplete_video_policy_blocks_video(tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"not-empty")
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text("video:\n  formats: [mp4]\n", encoding="utf-8")
    policy = load_media_policy(policy_path)
    record = inspect_asset(video, "123", "confirmed", media_policy=policy)

    assert record.validation_status == "blocked"
    assert "VIDEO_POLICY_INCOMPLETE" in record.reason_codes


def complete_video_policy():
    return {
        "video": {
            "formats": ["mp4"],
            "codecs": ["h264"],
            "duration_min_seconds": 3,
            "duration_max_seconds": 60,
            "min_width": 720,
            "min_height": 720,
            "aspect_ratios": ["1:1"],
            "max_size_mb": 50,
            "cover_required": True,
            "watermark_allowed": False,
        }
    }


def test_complete_video_policy_accepts_matching_metadata(tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video-bytes")

    record = inspect_asset(
        video,
        "123",
        "confirmed",
        media_policy=complete_video_policy(),
        video_metadata={
            "codec": "h264",
            "duration": 15,
            "width": 1080,
            "height": 1080,
            "cover_present": True,
            "watermark_present": False,
        },
    )

    assert record.validation_status == "valid"
    assert record.duration == 15
    assert record.width == 1080


def test_video_codec_violation_is_blocked(tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video-bytes")

    record = inspect_asset(
        video,
        "123",
        "confirmed",
        media_policy=complete_video_policy(),
        video_metadata={
            "codec": "hevc",
            "duration": 15,
            "width": 1080,
            "height": 1080,
            "cover_present": True,
            "watermark_present": False,
        },
    )

    assert record.validation_status == "blocked"
    assert "VIDEO_CODEC_INVALID" in record.reason_codes


def test_video_container_violation_is_blocked(tmp_path):
    video = tmp_path / "video.mov"
    video.write_bytes(b"video-bytes")

    record = inspect_asset(
        video,
        "123",
        "confirmed",
        media_policy=complete_video_policy(),
        video_metadata={
            "codec": "h264",
            "duration": 15,
            "width": 1080,
            "height": 1080,
            "cover_present": True,
            "watermark_present": False,
        },
    )

    assert "VIDEO_FORMAT_INVALID" in record.reason_codes
