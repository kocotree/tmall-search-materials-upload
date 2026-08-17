from upload_search_materials.material_state import (
    build_completeness_matrix,
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


def test_completeness_matrix_reports_only_promotion_upload_gaps():
    matrix = build_completeness_matrix(
        promotion_rows=[
            {
                "商品ID": "1",
                "目标容量": "9",
                "现有素材数": "3",
                "缺失数量": "6",
                "远端素材ID": "a;b;c",
                "状态": "needs_manual_review",
                "原因码": "PROMOTION_MATERIALS_MISSING",
                "证据": "source=recommended_promotion_dom",
            }
        ],
        products=[
            {
                "商品ID": "1",
                "货号（查找引用）": "KQ001",
                "商品名称（查找引用）": "分龄成长太阳镜",
                "运营": "张三",
            }
        ],
        candidate_counts={"1": 18},
    )

    product = matrix["products"][0]
    assert product["product_title"] == "分龄成长太阳镜"
    assert product["owner"] == "张三"
    assert "basic" not in product
    assert product["promotion"]["target_slots"] == 9
    assert product["promotion"]["current_count"] == 3
    assert product["promotion"]["missing_count"] == 6
    assert product["promotion"]["remote_material_ids"] == ["a", "b", "c"]
    assert product["candidate_asset_count"] == 18
    assert product["status"] == "needs_supplement"
    assert product["selectable"] is True


def test_completeness_matrix_never_guesses_missing_backend_target():
    matrix = build_completeness_matrix(
        promotion_rows=[
            {
                "商品ID": "2",
                "状态": "needs_manual_review",
                "证据": "source=recommended_promotion_dom",
            }
        ],
        products=[
            {"商品ID": "2", "商品名称（查找引用）": "未知坑位商品"},
            {"商品ID": "3", "商品名称（查找引用）": "不在推荐补充列表"},
        ],
    )

    product = matrix["products"][0]
    assert [item["product_id"] for item in matrix["products"]] == ["2"]
    assert product["promotion"]["target_slots"] is None
    assert product["promotion"]["missing_count"] is None
    assert product["status"] == "needs_manual_review"
    assert matrix["summary"]["selectable_count"] == 1
    assert matrix["summary"]["excluded_count"] == 0
    assert matrix["summary"]["status_counts"] == {"needs_manual_review": 1}
    assert matrix["source_filter"] == "search_recommend_high_value"


def test_completeness_matrix_marks_zero_gap_product_complete():
    matrix = build_completeness_matrix(
        promotion_rows=[
            {
                "商品ID": "4",
                "目标容量": "9",
                "现有素材数": "9",
                "缺失数量": "0",
                "状态": "ready_for_review",
            }
        ]
    )

    assert matrix["products"][0]["status"] == "complete"
    assert matrix["summary"]["status_counts"] == {"complete": 1}


def test_completeness_matrix_marks_rule_matches_excluded_and_unselectable():
    products = [
        {"商品ID": "1", "商品名称（查找引用）": "UVNO 夏季款", "产品等级": "A级"},
        {"商品ID": "2", "商品名称（查找引用）": "积分兑换商品", "产品等级": "A级"},
        {"商品ID": "3", "商品名称（查找引用）": "普通商品", "产品等级": "清仓"},
        {"商品ID": "4", "商品名称（查找引用）": "好物体验专享", "产品等级": "A级"},
        {"商品ID": "5", "商品名称（查找引用）": "会员日新品", "产品等级": "A级"},
        {"商品ID": "6", "商品名称（查找引用）": "正常商品", "产品等级": "A级"},
    ]
    promotion_rows = [
        {
            "商品ID": str(product_id),
            "目标容量": "9",
            "现有素材数": "0",
            "缺失数量": "9",
            "状态": "ready_for_review",
        }
        for product_id in range(1, 7)
    ]

    matrix = build_completeness_matrix(
        promotion_rows=promotion_rows,
        products=products,
    )

    excluded = matrix["products"][:5]
    assert all(product["status"] == "excluded" for product in excluded)
    assert all(product["selectable"] is False for product in excluded)
    assert [product["eligibility"]["reason_codes"][0] for product in excluded] == [
        "EXCLUDE_UVNO",
        "EXCLUDE_POINTS",
        "EXCLUDE_CLEARANCE",
        "EXCLUDE_GOOD_EXPERIENCE",
        "EXCLUDE_MEMBER_DAY",
    ]
    assert matrix["products"][5]["selectable"] is True
    assert matrix["summary"]["excluded_count"] == 5
    assert matrix["summary"]["selectable_count"] == 1
    assert matrix["summary"]["status_counts"]["excluded"] == 5
