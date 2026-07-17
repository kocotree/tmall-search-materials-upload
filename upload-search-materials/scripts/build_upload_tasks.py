from __future__ import print_function

import argparse
import csv
import hashlib
import os
import re
from collections import Counter


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm"}
OUTPUT_COLUMNS = [
    "task_id", "商品ID", "货号", "商品名称", "月份", "品类", "产品等级", "运营", "组别",
    "链接", "asset_dir", "image_count", "video_count", "desired_slots", "task_status", "reason"
]


def clean(value):
    return (value or "").strip()


def split_values(value):
    return {part.strip() for part in re.split(r"[,，、]", clean(value)) if part.strip()}


def normalize_grade(value):
    text = re.sub(r"\s+", "", clean(value)).upper()
    aliases = {"S": "S级", "A": "A级", "B": "B级"}
    return aliases.get(text, text)


def count_assets(directory):
    counts = Counter()
    if not directory or not os.path.isdir(directory):
        return counts
    for root, _, files in os.walk(directory):
        for name in files:
            extension = os.path.splitext(name)[1].lower()
            if extension in IMAGE_EXTENSIONS:
                counts["images"] += 1
            elif extension in VIDEO_EXTENSIONS:
                counts["videos"] += 1
    return counts


def task_id(month, product_id, sku):
    raw = "%s|%s|%s" % (month, product_id, sku)
    return "USR-%02d-%s" % (month, hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12])


def main():
    parser = argparse.ArgumentParser(description="Build reviewable Tmall material upload tasks.")
    parser.add_argument("--products", required=True, help="Product source CSV")
    parser.add_argument("--rules", required=True, help="Merged monthly rules CSV")
    parser.add_argument("--month", required=True, type=int, choices=range(1, 13))
    parser.add_argument("--asset-root", help="Asset root containing product-ID or SKU directories")
    parser.add_argument("--desired-slots", type=int, choices=(3, 9), default=3)
    parser.add_argument("--output", required=True, help="Output task CSV")
    args = parser.parse_args()

    month_label = "%d月" % args.month
    with open(args.rules, "r", encoding="utf-8-sig", newline="") as stream:
        rules = list(csv.DictReader(stream))
    permitted = {}
    for rule in rules:
        if clean(rule.get("月份")) == month_label:
            permitted[clean(rule.get("品类"))] = {
                normalize_grade(value) for value in split_values(rule.get("要推等级"))
            }

    with open(args.products, "r", encoding="utf-8-sig", newline="") as stream:
        products = list(csv.DictReader(stream))
    id_counts = Counter(clean(row.get("商品ID")) for row in products if clean(row.get("商品ID")))

    tasks = []
    for row in products:
        product_id = clean(row.get("商品ID"))
        sku = clean(row.get("货号（查找引用）"))
        category = clean(row.get("品类-公司维度划分"))
        grade = normalize_grade(row.get("产品等级"))
        if grade == "清仓" or category not in permitted or grade not in permitted[category]:
            continue

        candidates = []
        if args.asset_root:
            if product_id:
                candidates.append(os.path.join(args.asset_root, product_id))
            if sku:
                candidates.append(os.path.join(args.asset_root, sku))
        asset_dir = next((path for path in candidates if os.path.isdir(path)), "")
        counts = count_assets(asset_dir)

        status = "ready_for_review"
        reasons = []
        if not product_id:
            status = "needs_manual_review"
            reasons.append("MISSING_PRODUCT_ID")
        elif id_counts[product_id] > 1:
            status = "needs_manual_review"
            reasons.append("DUPLICATE_PRODUCT_ID")
        if not asset_dir:
            status = "needs_manual_review"
            reasons.append("ASSET_NOT_FOUND")

        tasks.append({
            "task_id": task_id(args.month, product_id, sku),
            "商品ID": product_id,
            "货号": sku,
            "商品名称": clean(row.get("商品名称（查找引用）")),
            "月份": month_label,
            "品类": category,
            "产品等级": grade,
            "运营": clean(row.get("运营")),
            "组别": clean(row.get("组别")),
            "链接": clean(row.get("链接")),
            "asset_dir": asset_dir,
            "image_count": counts["images"],
            "video_count": counts["videos"],
            "desired_slots": args.desired_slots,
            "task_status": status,
            "reason": ";".join(reasons),
        })

    with open(args.output, "w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(tasks)

    status_counts = Counter(task["task_status"] for task in tasks)
    print("Wrote %d tasks to %s" % (len(tasks), args.output))
    for status, count in sorted(status_counts.items()):
        print("%s: %d" % (status, count))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
