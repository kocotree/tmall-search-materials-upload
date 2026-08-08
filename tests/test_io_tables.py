import csv
from pathlib import Path

import pytest
from openpyxl import Workbook

from upload_search_materials.io_tables import (
    SchemaError,
    read_basic_materials_xlsx,
    read_product_csv,
    read_search_materials_xlsx,
    sha256_file,
    validate_product_records,
)


def save_workbook(path: Path, headers: list[str], rows: list[list[object]]) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    workbook.save(path)


def test_xlsx_keeps_product_id_as_text(tmp_path: Path):
    path = tmp_path / "basic.xlsx"
    save_workbook(
        path,
        ["商品ID", "商品标题", "商品白底图", "商家短标题"],
        [[1234567890123, "KK树儿童水杯", "已完成", "儿童水杯"]],
    )

    rows = read_basic_materials_xlsx(path)

    assert rows[0]["商品ID"] == "1234567890123"


def test_basic_xlsx_accepts_current_export_short_title_header(tmp_path: Path):
    path = tmp_path / "basic-current.xlsx"
    save_workbook(
        path,
        ["商品ID", "商品标题", "商品白底图", "短标题"],
        [[123, "KK树儿童水杯", "已完成", "儿童水杯"]],
    )

    rows = read_basic_materials_xlsx(path)

    assert rows[0]["商家短标题"] == "儿童水杯"


def test_search_xlsx_normalizes_lowercase_product_id_header(tmp_path: Path):
    path = tmp_path / "search.xlsx"
    save_workbook(
        path,
        ["商品id", "商品标题", "素材id", "素材类型"],
        [[123, "水杯", 456, "图文"]],
    )

    rows = read_search_materials_xlsx(path)

    assert rows == [{"商品ID": "123", "商品标题": "水杯", "素材id": "456", "素材类型": "图文"}]


def test_missing_required_xlsx_header_blocks_batch(tmp_path: Path):
    path = tmp_path / "bad.xlsx"
    save_workbook(path, ["错误字段"], [["value"]])

    with pytest.raises(SchemaError, match="商品ID"):
        read_basic_materials_xlsx(path)


def test_product_csv_maps_business_fields(tmp_path: Path):
    path = tmp_path / "products.csv"
    headers = [
        "商品ID",
        "商品名称（查找引用）",
        "货号（查找引用）",
        "产品等级",
        "链接",
        "运营",
        "组别",
        "品类-公司维度划分",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=headers)
        writer.writeheader()
        writer.writerow(
            {
                "商品ID": "00123",
                "商品名称（查找引用）": "水杯",
                "货号（查找引用）": "SKU-1",
                "产品等级": "A级",
                "链接": "https://example.invalid/00123",
                "运营": "小王",
                "组别": "一组",
                "品类-公司维度划分": "水杯",
            }
        )

    records = read_product_csv(path)

    assert records[0].product_id == "00123"
    assert records[0].title == "水杯"
    assert records[0].category == "水杯"


def test_missing_and_duplicate_product_ids_block_only_affected_rows(tmp_path: Path):
    path = tmp_path / "products.csv"
    headers = [
        "商品ID",
        "商品名称（查找引用）",
        "货号（查找引用）",
        "产品等级",
        "链接",
        "运营",
        "组别",
        "品类-公司维度划分",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=headers)
        writer.writeheader()
        for product_id in ["123", "123", ""]:
            writer.writerow({key: product_id if key == "商品ID" else "value" for key in headers})

    report = validate_product_records(read_product_csv(path))

    assert report.batch_blocking is False
    assert report.reason_codes_by_row == {
        2: ["DUPLICATE_PRODUCT_ID"],
        3: ["DUPLICATE_PRODUCT_ID"],
        4: ["MISSING_PRODUCT_ID"],
    }
    assert report.blocking is True


def test_sha256_file_reads_bytes_without_modifying_source(tmp_path: Path):
    path = tmp_path / "source.bin"
    path.write_bytes(b"abc")

    digest = sha256_file(path)

    assert digest == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert path.read_bytes() == b"abc"
