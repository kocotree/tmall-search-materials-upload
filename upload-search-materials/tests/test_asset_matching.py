from importlib import import_module, util
from pathlib import Path

from upload_search_materials.io_tables import ProductValidationReport
from upload_search_materials.models import ProductRecord


def asset_matching():
    spec = util.find_spec("upload_search_materials.asset_matching")
    assert spec is not None, "asset_matching module must exist"
    return import_module("upload_search_materials.asset_matching")


def report_for(records, blocked=None):
    return ProductValidationReport(
        row_count=len(records),
        reason_codes_by_row=blocked or {},
    )


def matcher_for(records, blocked=None):
    return asset_matching().ProductPathMatcher.from_products(
        records,
        report_for(records, blocked),
    )


def product(product_id, *, sku="", title="", source_row=2):
    return ProductRecord(
        product_id=product_id,
        sku=sku,
        title=title,
        raw={"_source_row": source_row},
    )


def test_normalize_match_text_applies_the_required_canonicalization():
    normalize = asset_matching().normalize_match_text

    assert normalize("  ＫＫ树 花 仙-子—帽_+· ") == "花仙子帽"


def test_exact_product_id_component_wins_over_sku_and_name_components():
    matcher = matcher_for(
        [product("123456", sku="SKU-HAT", title="花仙子翻翻帽")]
    )

    matches = matcher.match(Path("花仙子翻翻帽/SKU-HAT/123456/photo.jpg"))

    assert matches == (
        asset_matching().PathMatch(
            product_id="123456",
            sku="SKU-HAT",
            product_title="花仙子翻翻帽",
            match_type="exact_product_id",
            match_status="matched_unlicensed",
            reason_codes=(),
        ),
    )


def test_sku_is_case_insensitive_but_requires_a_complete_directory_component():
    matcher = matcher_for([product("123456", sku="SKU-HAT", title="完全不相关商品")])

    exact = matcher.match(Path("campaign/sku-hat/photo.jpg"))
    partial = matcher.match(Path("campaign/prefix-sku-hat/photo.jpg"))

    assert [match.match_type for match in exact] == ["exact_sku"]
    assert exact[0].match_status == "matched_unlicensed"
    assert partial == ()


def test_creator_folder_with_full_product_name_is_only_a_manual_candidate():
    matcher = matcher_for([product("123456", title="花仙子翻翻帽")])

    matches = matcher.match(Path("达人 - 花仙子翻翻帽-椰蓉/photo.jpg"))

    assert [(match.match_type, match.match_status) for match in matches] == [
        ("name_candidate", "needs_manual_confirmation")
    ]


def test_creator_folder_matches_a_title_after_removing_leading_brand():
    matcher = matcher_for([product("123456", title="KK树小萌宠滑雪服")])

    matches = matcher.match(Path("宣小七 - 小萌宠滑雪服/photo.jpg"))

    assert [(match.match_type, match.match_status) for match in matches] == [
        ("name_candidate", "needs_manual_confirmation")
    ]


def test_product_name_in_filename_does_not_create_a_candidate():
    matcher = matcher_for([product("123456", title="花仙子翻翻帽")])

    assert matcher.match(Path("unrelated/花仙子翻翻帽.jpg")) == ()


def test_short_non_contiguous_and_blocked_products_do_not_match():
    records = [
        product("100001", sku="SHORT-SKU", title="雨衣", source_row=10),
        product("100002", sku="OTHER-SKU", title="花仙子翻翻帽", source_row=11),
        product("100003", sku="BLOCK-SKU", title="阻断商品名称", source_row=12),
        product("", sku="MISSING-SKU", title="缺少编号商品", source_row=13),
        product("100004", sku="DUP-SKU", title="重复商品名称", source_row=14),
    ]
    blocked = {
        12: ["BLOCKED"],
        13: ["MISSING_PRODUCT_ID"],
        14: ["DUPLICATE_PRODUCT_ID"],
    }
    matcher = matcher_for(records, blocked)

    assert matcher.match(Path("雨衣/photo.jpg")) == ()
    assert matcher.match(Path("花仙子春翻翻帽/photo.jpg")) == ()
    assert matcher.match(Path("100003/BLOCK-SKU/阻断商品名称/photo.jpg")) == ()
    assert matcher.match(Path("MISSING-SKU/缺少编号商品/photo.jpg")) == ()
    assert matcher.match(Path("100004/DUP-SKU/重复商品名称/photo.jpg")) == ()


def test_multiple_name_candidates_are_stably_sorted_and_deduplicated():
    records = [
        product("20", sku="SKU-20", title="花仙子翻翻帽", source_row=20),
        product("10", sku="SKU-10", title="花仙子翻翻帽", source_row=21),
        product("10", sku="SKU-10", title="花仙子翻翻帽", source_row=22),
    ]
    matcher = matcher_for(records)

    matches = matcher.match(Path("达人 - 花仙子翻翻帽-椰蓉/photo.jpg"))

    assert [(match.product_id, match.match_type) for match in matches] == [
        ("10", "name_candidate"),
        ("20", "name_candidate"),
    ]
