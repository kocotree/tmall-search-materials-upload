import csv

from openpyxl import Workbook

from scripts.build_upload_tasks import main


PRODUCT_HEADERS = [
    "商品ID",
    "商品名称（查找引用）",
    "货号（查找引用）",
    "产品等级",
    "链接",
    "运营",
    "组别",
    "品类-公司维度划分",
]


def write_products(path):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=PRODUCT_HEADERS)
        writer.writeheader()
        for product_id, title in [("1", "普通水杯"), ("2", "活动水杯"), ("", "缺ID水杯")]:
            writer.writerow(
                {
                    "商品ID": product_id,
                    "商品名称（查找引用）": title,
                    "货号（查找引用）": "SKU-" + (product_id or "EMPTY"),
                    "产品等级": "A级",
                    "链接": "https://example.invalid/" + (product_id or "empty"),
                    "运营": "小王",
                    "组别": "一组",
                    "品类-公司维度划分": "水杯",
                }
            )


def write_rules(path):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["月份", "品类", "要推等级"])
        writer.writeheader()
        writer.writerow({"月份": "7月", "品类": "水杯", "要推等级": "A级"})


def write_xlsx(path, headers, rows):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    workbook.save(path)


def test_build_script_writes_eligible_excluded_and_blocked_rows(tmp_path):
    products = tmp_path / "products.csv"
    rules = tmp_path / "rules.csv"
    basic = tmp_path / "basic.xlsx"
    search = tmp_path / "search.xlsx"
    output = tmp_path / "eligibility.csv"
    write_products(products)
    write_rules(rules)
    write_xlsx(
        basic,
        ["商品ID", "商品标题", "商品白底图", "短标题"],
        [[1, "普通水杯", "已完成", "水杯"], [2, "积分兑换水杯", "已完成", "水杯"]],
    )
    write_xlsx(
        search,
        ["商品id", "商品名称", "素材id", "素材类型"],
        [[1, "普通水杯", 11, "图文"], [2, "积分兑换水杯", 12, "图文"]],
    )

    exit_code = main(
        [
            "--products", str(products),
            "--rules", str(rules),
            "--basic", str(basic),
            "--search", str(search),
            "--month", "7",
            "--output", str(output),
        ]
    )
    with output.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))

    assert exit_code == 1
    assert len(rows) == 3
    assert [row["eligibility_status"] for row in rows] == ["eligible", "excluded", "blocked"]
    assert rows[1]["reason_codes"] == "EXCLUDE_POINTS"
    assert rows[2]["reason_codes"] == "MISSING_PRODUCT_ID"
    assert rows[0]["task_id"].startswith("USR-07-")
    assert rows[1]["task_id"] == ""
