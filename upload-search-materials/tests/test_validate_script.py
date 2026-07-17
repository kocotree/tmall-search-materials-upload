import csv
import json

from scripts.validate_product_data import main


HEADERS = [
    "商品ID",
    "商品名称（查找引用）",
    "货号（查找引用）",
    "产品等级",
    "链接",
    "运营",
    "组别",
    "品类-公司维度划分",
]


def write_csv(path, headers, product_ids):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=headers)
        writer.writeheader()
        for product_id in product_ids:
            writer.writerow({key: product_id if key == "商品ID" else "value" for key in headers})


def test_validator_returns_one_for_product_level_blocks(tmp_path, capsys):
    path = tmp_path / "products.csv"
    write_csv(path, HEADERS, ["123", "123", ""])

    exit_code = main(["--input", str(path)])
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert report["blocking"] is True
    assert report["batch_blocking"] is False


def test_validator_returns_two_for_schema_error(tmp_path, capsys):
    path = tmp_path / "products.csv"
    write_csv(path, ["商品ID"], ["123"])

    exit_code = main(["--input", str(path)])
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert report["batch_blocking"] is True
    assert report["error_code"] == "DATA_SCHEMA"
