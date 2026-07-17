from __future__ import print_function

import argparse
import csv
import json
from collections import Counter, defaultdict


REQUIRED_COLUMNS = [
    "商品ID",
    "商品名称（查找引用）",
    "货号（查找引用）",
    "产品等级",
    "链接",
    "运营",
    "组别",
    "品类-公司维度划分",
]


def clean(value):
    return (value or "").strip()


def main():
    parser = argparse.ArgumentParser(description="Validate the Tmall product source CSV.")
    parser.add_argument("--input", required=True, help="Product CSV path")
    parser.add_argument("--output", help="Optional JSON report path")
    args = parser.parse_args()

    with open(args.input, "r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        headers = reader.fieldnames or []
        missing_headers = [name for name in REQUIRED_COLUMNS if name not in headers]
        rows = list(reader)

    issues = []
    ids = defaultdict(list)
    grades = Counter()
    missing_counts = Counter()

    for number, row in enumerate(rows, 2):
        product_id = clean(row.get("商品ID"))
        grade = clean(row.get("产品等级"))
        if product_id:
            ids[product_id].append(number)
            if not product_id.isdigit():
                issues.append({"row": number, "code": "INVALID_PRODUCT_ID", "value": product_id})
        else:
            missing_counts["商品ID"] += 1

        grades[grade or "<blank>"] += 1
        for column in REQUIRED_COLUMNS[1:]:
            if not clean(row.get(column)):
                missing_counts[column] += 1

    duplicates = [
        {"product_id": product_id, "rows": line_numbers}
        for product_id, line_numbers in sorted(ids.items())
        if len(line_numbers) > 1
    ]
    report = {
        "input": args.input,
        "row_count": len(rows),
        "missing_headers": missing_headers,
        "missing_value_counts": dict(sorted(missing_counts.items())),
        "grade_counts": dict(sorted(grades.items())),
        "duplicate_product_ids": duplicates,
        "issues": issues,
        "blocking": bool(missing_headers),
    }

    output = json.dumps(report, ensure_ascii=False, indent=2)
    print(output)
    if args.output:
        with open(args.output, "w", encoding="utf-8", newline="") as stream:
            stream.write(output)
            stream.write("\n")
    return 2 if missing_headers else 0


if __name__ == "__main__":
    raise SystemExit(main())
