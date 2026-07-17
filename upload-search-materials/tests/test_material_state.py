from upload_search_materials.material_state import (
    calculate_gap,
    merge_material_state,
    products_requiring_supplement,
)


def test_three_slot_gap_with_one_video_and_one_image():
    gap = calculate_gap(
        3,
        [
            {"type": "video", "status": "approved"},
            {"type": "image_text", "status": "under_review"},
        ],
    )

    assert gap.missing_slots == 1
    assert gap.needs_page_supplement is False


def test_unknown_slot_requirement_requires_browser_supplement():
    gap = calculate_gap(None, [])

    assert gap.needs_page_supplement is True
    assert gap.missing_slots is None
    assert gap.reason_code == "SLOT_REQUIREMENT_UNKNOWN"


def test_rejected_and_deleted_materials_do_not_occupy_slots():
    gap = calculate_gap(
        3,
        [
            {"type": "image_text", "status": "approved"},
            {"type": "image_text", "status": "rejected"},
            {"type": "video", "status": "deleted"},
        ],
    )

    assert gap.missing_slots == 2


def test_merge_counts_only_rows_with_remote_material_id():
    snapshots = merge_material_state(
        basic_rows=[
            {
                "商品ID": "1",
                "卖点1": "便携",
                "卖点2": "",
                "商品白底图": "已完成",
                "商品透明图": "",
                "方版场景图": "已完成",
                "商品长图": "",
                "商家短标题": "水杯",
                "品牌LOGO透明图": "已完成",
                "利益点（短）": "轻巧",
            }
        ],
        search_rows=[
            {"商品ID": "1", "素材id": "11", "素材类型": "图文"},
            {"商品ID": "1", "素材id": "12", "素材类型": "视频"},
            {"商品ID": "1", "素材id": "", "素材类型": ""},
        ],
        backend_rows=[
            {
                "商品ID": "1",
                "目标坑位": "3",
                "空坑位": "3",
                "审核状态完整": "true",
            }
        ],
    )

    snapshot = snapshots[0]
    assert snapshot.image_text_count == 1
    assert snapshot.video_count == 1
    assert snapshot.desired_slots == 3
    assert snapshot.empty_slot_indexes == [3]
    assert snapshot.basic_missing_fields == ["卖点2", "商品透明图", "商品长图"]


def test_only_unknown_products_are_sent_to_browser_supplement():
    snapshots = merge_material_state(
        basic_rows=[{"商品ID": "1"}, {"商品ID": "2"}],
        search_rows=[],
        backend_rows=[
            {"商品ID": "1", "目标坑位": "3", "空坑位": "", "审核状态完整": "true"}
        ],
    )

    assert products_requiring_supplement(snapshots) == ["2"]
