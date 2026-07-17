import csv

import pytest

from upload_search_materials.eligibility import (
    collect_titles_by_product,
    evaluate_all,
    evaluate_exclusions,
    load_monthly_rules,
)
from upload_search_materials.models import ProductRecord


@pytest.mark.parametrize(
    ("grade", "title", "code"),
    [
        ("清仓", "普通标题", "EXCLUDE_CLEARANCE"),
        ("A级", "uvno 夏季商品", "EXCLUDE_UVNO"),
        ("A级", "好物体验专享", "EXCLUDE_GOOD_EXPERIENCE"),
        ("A级", "会员日新品", "EXCLUDE_MEMBER_DAY"),
        ("A级", "积分兑换商品", "EXCLUDE_POINTS"),
    ],
)
def test_each_exclusion_rule(grade, title, code):
    decision = evaluate_exclusions(grade, [title])

    assert decision.status == "excluded"
    assert code in decision.reason_codes


def test_multiple_reasons_are_kept_in_one_record():
    decision = evaluate_exclusions("A级", ["UVNO会员日积分商品"])

    assert decision.reason_codes == [
        "EXCLUDE_UVNO",
        "EXCLUDE_MEMBER_DAY",
        "EXCLUDE_POINTS",
    ]


def test_titles_are_collected_from_basic_and_search_exports():
    titles = collect_titles_by_product(
        [{"商品ID": "123", "商品标题": "基础标题"}],
        [{"商品ID": "123", "商品名称": "商品名称", "素材标题": "素材标题"}],
    )

    assert titles == {"123": ["基础标题", "商品名称", "素材标题"]}


def test_all_input_rows_receive_auditable_decisions():
    products = [
        ProductRecord(product_id="1", title="普通商品", grade="A级", category="水杯", raw={"_source_row": 2}),
        ProductRecord(product_id="2", title="积分商品", grade="A级", category="水杯", raw={"_source_row": 3}),
        ProductRecord(product_id="", title="缺ID商品", grade="A级", category="水杯", raw={"_source_row": 4}),
        ProductRecord(product_id="1", title="重复商品", grade="A级", category="水杯", raw={"_source_row": 5}),
    ]

    decisions = evaluate_all(products, {"1": ["普通商品"], "2": ["积分商品"]}, {"水杯": {"A级"}})

    assert len(decisions) == 4
    assert decisions[0].status == "blocked"
    assert decisions[0].reason_codes == ["DUPLICATE_PRODUCT_ID"]
    assert decisions[1].status == "excluded"
    assert decisions[1].reason_codes == ["EXCLUDE_POINTS"]
    assert decisions[2].status == "blocked"
    assert decisions[2].reason_codes == ["MISSING_PRODUCT_ID"]
    assert decisions[3].status == "blocked"


def test_monthly_rules_are_normalized(tmp_path):
    path = tmp_path / "rules.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["月份", "品类", "要推等级"])
        writer.writeheader()
        writer.writerow({"月份": "7月", "品类": "水杯", "要推等级": "S, A级"})
        writer.writerow({"月份": "8月", "品类": "书包", "要推等级": "B级"})

    rules = load_monthly_rules(path, 7)

    assert rules == {"水杯": {"S级", "A级"}}


def test_non_selected_category_is_recorded_not_dropped():
    product = ProductRecord(product_id="1", title="普通商品", grade="A级", category="书包")

    decisions = evaluate_all([product], {"1": ["普通商品"]}, {"水杯": {"A级"}})

    assert decisions[0].status == "excluded"
    assert decisions[0].reason_codes == ["NOT_IN_MONTHLY_CATEGORY"]
