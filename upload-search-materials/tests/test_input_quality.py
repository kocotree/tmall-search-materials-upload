import csv

from upload_search_materials.interaction.web import _input_quality_summary
from upload_search_materials.io_tables import PRODUCT_REQUIRED_COLUMNS


def test_live_shape_611_rows_44_anomalies_keeps_valid_path(tmp_path):
    target = tmp_path / "products.csv"
    headers = sorted(PRODUCT_REQUIRED_COLUMNS)
    rows = []
    for index in range(567):
        rows.append({"商品ID": str(800000000000 + index)})
    duplicate_ids = [
        "900000000001",
        "900000000001",
        "900000000001",
        "900000000002",
        "900000000002",
        "900000000003",
        "900000000003",
        "900000000004",
        "900000000004",
        "900000000005",
        "900000000005",
    ]
    rows.extend({"商品ID": value} for value in duplicate_ids)
    rows.extend({"商品ID": ""} for _ in range(33))
    with target.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=headers)
        writer.writeheader()
        for index, row in enumerate(rows):
            writer.writerow(
                {
                    **{header: f"value-{index}" for header in headers},
                    **row,
                }
            )

    summary = _input_quality_summary(target)

    assert summary == {
        "status": "ready",
        "reason_code": "INPUT_QUALITY_READY",
        "total": 611,
        "valid": 567,
        "duplicate_id": 11,
        "missing_id": 33,
        "invalid_id": 0,
        "excluded": 44,
        "processable": 567,
        "reason_codes_by_row": summary["reason_codes_by_row"],
    }


def test_invalid_table_schema_blocks_before_collection(tmp_path):
    target = tmp_path / "products.csv"
    target.write_text("商品ID\n123\n", encoding="utf-8-sig")

    summary = _input_quality_summary(target)

    assert summary["status"] == "blocked"
    assert summary["reason_code"] == "PRODUCT_TABLE_BATCH_BLOCKED"
    assert summary["processable"] == 0
