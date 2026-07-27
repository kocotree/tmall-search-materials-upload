from pathlib import Path

from PIL import Image

from upload_search_materials.confirmed_assets import (
    _proportional_allocations,
    build_confirmed_folder_gallery,
    extract_folder_decisions,
)
from upload_search_materials.models import ProductRecord


def _image(path: Path, color: tuple[int, int, int]) -> None:
    Image.new("RGB", (40, 30), color=color).save(path)


def test_proportional_allocations_build_a_one_hundred_image_pool():
    assert _proportional_allocations([370, 222, 148], 100) == [50, 30, 20]


def test_confirmed_gallery_samples_proportionally_across_folders(tmp_path):
    folders = [tmp_path / "五成", tmp_path / "三成", tmp_path / "二成"]
    counts = [50, 30, 20]
    color_index = 0
    for folder, count in zip(folders, counts, strict=True):
        folder.mkdir()
        for index in range(count):
            _image(
                folder / f"{index:03}.png",
                (
                    color_index % 251,
                    (color_index * 3) % 251,
                    (color_index * 7) % 251,
                ),
            )
            color_index += 1

    data = build_confirmed_folder_gallery(
        [ProductRecord("123", sku="SKU-123", title="测试商品")],
        [{"商品ID": "123", "缺失数量": "1"}],
        [
            {
                "decision": "confirmed",
                "folder_path": str(folder),
                "product_id": "123",
                "source_system": "model",
            }
            for folder in folders
        ],
        candidate_limit=10,
        page_size=30,
        sampling_seed="session-1",
    )

    assert data["candidate_strategy"] == "proportional_task_sample"
    assert data["requirements"][0]["candidate_count"] == 10
    assert "required_images" not in data["requirements"][0]
    assert len(data["asset_candidates"]) == 10
    assert data["scan_summary"]["discovered_images"] == 100
    assert data["scan_summary"]["inspected_candidates"] == 10
    allocations = {
        Path(item["folder_path"]).name: item["sampled_images"]
        for item in data["scan_summary"]["per_product"][0]["folder_allocations"]
    }
    assert allocations == {"五成": 5, "三成": 3, "二成": 2}
    assert data["candidate_limit"] == 10
    assert data["page_size"] == 30
    assert all(item["sha256"] for item in data["asset_candidates"])


def test_confirmed_gallery_shows_all_fourteen_without_declaring_a_shortage(tmp_path):
    folder = tmp_path / "分龄成长太阳镜"
    folder.mkdir()
    for index in range(14):
        _image(folder / f"solar-{index:02}.png", (index * 10, 20, 30))

    data = build_confirmed_folder_gallery(
        [ProductRecord("123", sku="SKU-123", title="测试太阳镜")],
        [{"商品ID": "123", "缺失数量": "6"}],
        [
            {
                "decision": "confirmed",
                "folder_path": str(folder),
                "product_id": "123",
                "source_system": "buyer",
            }
        ],
    )

    assert len(data["asset_candidates"]) == 14
    assert data["requirements"][0]["candidate_count"] == 14
    assert data["requirements"][0]["missing_materials"] == 6
    assert "required_images" not in data["requirements"][0]
    assert data["scan_summary"]["per_product"][0]["prepared_candidates"] == 14


def test_confirmed_gallery_writes_task_local_previews(tmp_path):
    folder = tmp_path / "素材"
    folder.mkdir()
    _image(folder / "a.png", (255, 0, 0))
    previews = tmp_path / "preview-cache"

    data = build_confirmed_folder_gallery(
        [ProductRecord("123", sku="SKU-123", title="测试商品")],
        [{"商品ID": "123", "缺失数量": "1"}],
        [
            {
                "decision": "confirmed",
                "folder_path": str(folder),
                "product_id": "123",
                "source_system": "model",
            }
        ],
        preview_dir=previews,
    )

    preview_path = Path(data["asset_candidates"][0]["preview_path"])
    assert preview_path.is_file()
    assert preview_path.parent == previews.resolve()
    assert preview_path.suffix == ".jpg"


def test_confirmed_gallery_preserves_stable_folder_identity_across_sources_and_nested_folders(
    tmp_path,
):
    source_a = tmp_path / "source-a" / "同名商品"
    nested = source_a / "精选"
    source_b = tmp_path / "source-b" / "同名商品"
    nested.mkdir(parents=True)
    source_b.mkdir(parents=True)
    _image(source_a / "parent.png", (1, 2, 3))
    _image(nested / "nested.png", (4, 5, 6))
    _image(source_b / "other.png", (7, 8, 9))

    data = build_confirmed_folder_gallery(
        [ProductRecord("123", sku="SKU-123", title="同名商品")],
        [{"商品ID": "123", "缺失数量": "1"}],
        [
            {
                "decision": "confirmed",
                "folder_id": "SOURCE-A-PARENT",
                "folder_path": str(source_a),
                "product_id": "123",
                "source_system": "model",
            },
            {
                "decision": "confirmed",
                "folder_id": "SOURCE-A-NESTED",
                "folder_path": str(nested),
                "product_id": "123",
                "source_system": "model",
            },
            {
                "decision": "confirmed",
                "folder_id": "SOURCE-B",
                "folder_path": str(source_b),
                "product_id": "123",
                "source_system": "buyer",
            },
        ],
    )

    by_name = {
        Path(candidate["source_path"]).name: candidate
        for candidate in data["asset_candidates"]
    }
    assert by_name["parent.png"]["folder_id"] == "SOURCE-A-PARENT"
    assert by_name["nested.png"]["folder_id"] == "SOURCE-A-NESTED"
    assert by_name["other.png"]["folder_id"] == "SOURCE-B"
    assert all(candidate["folder_path"] for candidate in by_name.values())
    assert {
        item["folder_id"]
        for item in data["scan_summary"]["per_product"][0]["folder_allocations"]
    } == {"SOURCE-A-PARENT", "SOURCE-A-NESTED", "SOURCE-B"}


def test_extract_folder_decisions_accepts_submitted_stage_input():
    assert extract_folder_decisions(
        {"values": {"folder_decisions": [{"decision": "confirmed"}]}}
    ) == [{"decision": "confirmed"}]
