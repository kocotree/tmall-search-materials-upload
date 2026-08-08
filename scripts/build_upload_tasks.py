from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
from typing import Sequence

from upload_search_materials.eligibility import (
    collect_titles_by_product,
    evaluate_all,
    load_monthly_rules,
)
from upload_search_materials.io_tables import (
    read_basic_materials_xlsx,
    read_product_csv,
    read_search_materials_xlsx,
)


OUTPUT_COLUMNS = [
    "task_id",
    "商品ID",
    "货号",
    "商品名称",
    "月份",
    "品类",
    "产品等级",
    "运营",
    "组别",
    "链接",
    "eligibility_status",
    "reason_codes",
    "evidence_json",
    "desired_slots",
    "asset_root",
]


def _task_id(month: int, product_id: str, sku: str) -> str:
    raw = f"{month}|{product_id}|{sku}".encode("utf-8")
    return f"USR-{month:02d}-" + hashlib.sha256(raw).hexdigest()[:12]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build auditable Tmall eligibility records.")
    parser.add_argument("--products", required=True, help="Product source CSV")
    parser.add_argument("--rules", required=True, help="Merged monthly rules CSV")
    parser.add_argument("--basic", required=True, help="Basic material export XLSX")
    parser.add_argument("--search", required=True, help="Search material export XLSX")
    parser.add_argument("--month", required=True, type=int, choices=range(1, 13))
    parser.add_argument("--asset-root", default="", help="Configured asset root")
    parser.add_argument("--desired-slots", type=int, choices=(3, 9), default=3)
    parser.add_argument("--output", required=True, help="Output eligibility CSV")
    args = parser.parse_args(argv)

    products = read_product_csv(Path(args.products))
    basic_rows = read_basic_materials_xlsx(Path(args.basic))
    search_rows = read_search_materials_xlsx(Path(args.search))
    titles = collect_titles_by_product(basic_rows, search_rows)
    permitted = load_monthly_rules(Path(args.rules), args.month)
    decisions = evaluate_all(products, titles, permitted)

    output_rows = []
    for product, decision in zip(products, decisions, strict=True):
        output_rows.append(
            {
                "task_id": (
                    _task_id(args.month, product.product_id, product.sku)
                    if decision.status == "eligible"
                    else ""
                ),
                "商品ID": product.product_id,
                "货号": product.sku,
                "商品名称": product.title,
                "月份": f"{args.month}月",
                "品类": product.category,
                "产品等级": product.grade,
                "运营": product.owner,
                "组别": product.team,
                "链接": product.link,
                "eligibility_status": decision.status,
                "reason_codes": ";".join(decision.reason_codes),
                "evidence_json": json.dumps(decision.evidence, ensure_ascii=False, sort_keys=True),
                "desired_slots": args.desired_slots if decision.status == "eligible" else "",
                "asset_root": args.asset_root if decision.status == "eligible" else "",
            }
        )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(output_rows)

    counts = Counter(decision.status for decision in decisions)
    print(f"Wrote {len(output_rows)} eligibility records to {output_path}")
    for status, count in sorted(counts.items()):
        print(f"{status}: {count}")
    return 1 if counts["blocked"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
