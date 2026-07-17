from collections import Counter
import csv
from dataclasses import asdict, is_dataclass
from enum import Enum
import json
from pathlib import Path
from typing import Iterable

from .models import AssetRecord, MaterialItem, MaterialStatus


def _plain(value):
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {key: _plain(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def write_json(path: Path, value) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(_plain(value), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_eligibility_csv(path: Path, products, decisions) -> Path:
    fieldnames = [
        "source_row",
        "商品ID",
        "商品名称",
        "品类",
        "产品等级",
        "status",
        "reason_codes",
        "evidence_json",
    ]
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for product, decision in zip(products, decisions, strict=True):
            writer.writerow(
                {
                    "source_row": decision.source_row,
                    "商品ID": product.product_id,
                    "商品名称": product.title,
                    "品类": product.category,
                    "产品等级": product.grade,
                    "status": decision.status,
                    "reason_codes": ";".join(decision.reason_codes),
                    "evidence_json": json.dumps(
                        decision.evidence,
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                }
            )
    return output


def write_supplement_candidates(path: Path, product_ids: Iterable[str]) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["商品ID", "reason"])
        writer.writeheader()
        for product_id in product_ids:
            writer.writerow({"商品ID": product_id, "reason": "BROWSER_SUPPLEMENT_REQUIRED"})
    return output


def material_item_from_dict(value: dict) -> MaterialItem:
    assets = [
        AssetRecord(
            product_id=asset.get("product_id", value.get("product_id", "")),
            source_path=asset.get("source_path", ""),
            asset_id=asset.get("asset_id", ""),
            sku=asset.get("sku", ""),
            asset_type=asset.get("asset_type", ""),
            source_system=asset.get("source_system", "local"),
            license_status=asset.get("license_status", "unknown"),
            sha256=asset.get("sha256", ""),
            width=asset.get("width"),
            height=asset.get("height"),
            duration=asset.get("duration"),
            validation_status=asset.get("validation_status", "pending_validation"),
            reason_codes=list(asset.get("reason_codes", [])),
        )
        for asset in value.get("assets", [])
    ]
    return MaterialItem(
        task_id=value["task_id"],
        product_id=value["product_id"],
        material_type=value["material_type"],
        slot_index=int(value["slot_index"]),
        status=MaterialStatus(value.get("status", "pending_validation")),
        assets=assets,
        title=value.get("title", ""),
        description=value.get("description", ""),
        remote_material_id=value.get("remote_material_id"),
        attempt_count=int(value.get("attempt_count", 0)),
        content_hash=value.get("content_hash", ""),
        reason_codes=list(value.get("reason_codes", [])),
    )


def write_summary(run_dir: Path) -> Path:
    directory = Path(run_dir)
    run = read_json(directory / "run.json")
    product_tasks = (
        read_json(directory / "product-tasks.json")
        if (directory / "product-tasks.json").exists()
        else []
    )
    material_items = (
        read_json(directory / "material-items.json")
        if (directory / "material-items.json").exists()
        else []
    )
    upload_results = (
        read_json(directory / "upload-results.json")
        if (directory / "upload-results.json").exists()
        else []
    )
    outcomes_by_task = {
        value.get("task_id", ""): value
        for value in upload_results
        if value.get("task_id")
    }
    effective_material_statuses = [
        outcomes_by_task.get(item.get("task_id", ""), item).get("status", "unknown")
        for item in material_items
    ]
    product_counts = Counter(item.get("status", "unknown") for item in product_tasks)
    material_counts = Counter(effective_material_statuses)
    lines = [
        f"# 批次报告 {run.get('run_id', '')}",
        "",
        f"- 目标店铺: {run.get('store', '')}",
        f"- 目标月份: {run.get('month', '')}",
        f"- 模式: {run.get('mode', '')}",
        "",
        "## 输入 SHA-256",
        "",
    ]
    source_hashes = run.get("source_sha256", {})
    if source_hashes:
        lines.extend(f"- {name}: {digest}" for name, digest in sorted(source_hashes.items()))
    else:
        lines.append("- 未记录")
    lines.extend([
        "",
        "## 商品任务",
        "",
    ])
    lines.extend(f"- {status}: {count}" for status, count in sorted(product_counts.items()))
    lines.extend(["", "## 坑位任务", ""])
    if material_counts:
        lines.extend(f"- {status}: {count}" for status, count in sorted(material_counts.items()))
    else:
        lines.append("- 无坑位任务")
    if upload_results:
        lines.extend(["", "## 远端结果", ""])
        for result in upload_results:
            lines.append(
                "- {task} / {remote} / {status}{reason}".format(
                    task=result.get("task_id", ""),
                    remote=result.get("remote_material_id") or "无远端ID",
                    status=result.get("status", "unknown"),
                    reason=(f" / {result['reason']}" if result.get("reason") else ""),
                )
            )
    output = directory / "summary.md"
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output
