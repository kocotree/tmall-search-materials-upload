from collections import Counter, defaultdict
import csv
from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Iterable, Mapping

from .models import ProductRecord


@dataclass(frozen=True)
class EligibilityDecision:
    product_id: str = ""
    source_row: int | None = None
    status: str = "eligible_candidate"
    reason_codes: list[str] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)


EXCLUSION_RULES = (
    ("EXCLUDE_UVNO", lambda text: "uvno" in text.casefold()),
    ("EXCLUDE_GOOD_EXPERIENCE", lambda text: "好物体验" in text),
    ("EXCLUDE_MEMBER_DAY", lambda text: "会员日" in text),
    ("EXCLUDE_POINTS", lambda text: "积分" in text),
)


def normalize_grade(value: str) -> str:
    text = re.sub(r"\s+", "", value or "").upper()
    aliases = {"S": "S级", "A": "A级", "B": "B级"}
    return aliases.get(text, text)


def evaluate_exclusions(grade: str, titles: Iterable[str]) -> EligibilityDecision:
    normalized_titles = [title.strip() for title in titles if title and title.strip()]
    combined = "\n".join(normalized_titles)
    reasons = []
    if normalize_grade(grade) == "清仓":
        reasons.append("EXCLUDE_CLEARANCE")
    reasons.extend(code for code, predicate in EXCLUSION_RULES if predicate(combined))
    return EligibilityDecision(
        status="excluded" if reasons else "eligible_candidate",
        reason_codes=reasons,
        evidence={"grade": grade, "titles": normalized_titles},
    )


def collect_titles_by_product(*datasets: Iterable[Mapping[str, str]]) -> dict[str, list[str]]:
    collected: dict[str, list[str]] = defaultdict(list)
    for dataset in datasets:
        for row in dataset:
            product_id = str(row.get("商品ID", "")).strip()
            if not product_id:
                continue
            for key in ("商品标题", "商品名称", "素材标题"):
                title = str(row.get(key, "")).strip()
                if title and title not in collected[product_id]:
                    collected[product_id].append(title)
    return dict(collected)


def load_monthly_rules(path: Path, month: int) -> dict[str, set[str]]:
    target = f"{month}月"
    permitted: dict[str, set[str]] = defaultdict(set)
    with Path(path).open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"月份", "品类", "要推等级"}
        missing = sorted(required - set(reader.fieldnames or []))
        if missing:
            raise ValueError("缺少月度规则表头: " + ", ".join(missing))
        for row in reader:
            if str(row.get("月份", "")).strip() != target:
                continue
            category = str(row.get("品类", "")).strip()
            raw_grades = str(row.get("要推等级", ""))
            grades = {
                normalize_grade(value)
                for value in re.split(r"[,，、;/\s]+", raw_grades)
                if value.strip()
            }
            if category:
                permitted[category].update(grades)
    return dict(permitted)


def evaluate_all(
    products: Iterable[ProductRecord],
    titles_by_product: Mapping[str, list[str]],
    permitted: Mapping[str, set[str]],
) -> list[EligibilityDecision]:
    records = list(products)
    id_counts = Counter(record.product_id for record in records if record.product_id)
    decisions = []
    for fallback_row, record in enumerate(records, 2):
        source_row = int(record.raw.get("_source_row", fallback_row))
        evidence = {
            "source_row": source_row,
            "grade": record.grade,
            "category": record.category,
            "titles": list(titles_by_product.get(record.product_id, [])),
        }
        if not record.product_id:
            decisions.append(
                EligibilityDecision("", source_row, "blocked", ["MISSING_PRODUCT_ID"], evidence)
            )
            continue
        if not record.product_id.isdigit():
            decisions.append(
                EligibilityDecision(
                    record.product_id,
                    source_row,
                    "blocked",
                    ["INVALID_PRODUCT_ID"],
                    evidence,
                )
            )
            continue
        if id_counts[record.product_id] > 1:
            decisions.append(
                EligibilityDecision(
                    record.product_id,
                    source_row,
                    "blocked",
                    ["DUPLICATE_PRODUCT_ID"],
                    evidence,
                )
            )
            continue

        titles = list(titles_by_product.get(record.product_id, []))
        if record.title and record.title not in titles:
            titles.append(record.title)
        excluded = evaluate_exclusions(record.grade, titles)
        evidence["titles"] = titles
        if excluded.reason_codes:
            decisions.append(
                EligibilityDecision(
                    record.product_id,
                    source_row,
                    "excluded",
                    excluded.reason_codes,
                    evidence,
                )
            )
            continue

        if record.category not in permitted:
            decisions.append(
                EligibilityDecision(
                    record.product_id,
                    source_row,
                    "excluded",
                    ["NOT_IN_MONTHLY_CATEGORY"],
                    evidence,
                )
            )
            continue
        if normalize_grade(record.grade) not in permitted[record.category]:
            decisions.append(
                EligibilityDecision(
                    record.product_id,
                    source_row,
                    "excluded",
                    ["GRADE_NOT_SELECTED"],
                    evidence,
                )
            )
            continue
        decisions.append(
            EligibilityDecision(record.product_id, source_row, "eligible", [], evidence)
        )
    return decisions
