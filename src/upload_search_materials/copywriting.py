from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
from typing import Mapping, Protocol


ALLOWED_SOURCE_FIELDS = {
    "商品标题",
    "品类",
    "卖点1",
    "卖点2",
    "短标题",
    "商家短标题",
    "利益点",
    "利益点（短）",
    "品牌",
}

SENSITIVE_CLAIM_TOKENS = {
    "女童",
    "男童",
    "女孩",
    "男孩",
    "女性",
    "男性",
    "防晒",
    "UPF",
    "纯棉",
    "真皮",
    "食品级",
    "抗菌",
    "抑菌",
    "医用",
    "认证",
}


@dataclass(frozen=True)
class CopyRequest:
    source_fields: dict[str, str]
    instructions: str = (
        "只能使用来源字段中的事实；标题不超过30字符，描述10至1000字符；"
        "不得补写年龄、性别、材质、认证、功效或防晒等级。"
    )


@dataclass(frozen=True)
class CopyResult:
    title: str
    description: str
    source_fields: dict[str, str]
    raw_output: dict[str, str]
    generated_at: str
    status: str
    reason_codes: list[str] = field(default_factory=list)


class CopyProvider(Protocol):
    def generate(self, request: CopyRequest) -> Mapping[str, str]: ...


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _has_unsupported_claim(output_text: str, source_text: str) -> bool:
    for token in SENSITIVE_CLAIM_TOKENS:
        if token.casefold() in output_text.casefold() and token.casefold() not in source_text.casefold():
            return True
    for age_claim in re.findall(r"\d+\s*(?:岁|个月)", output_text):
        if age_claim not in source_text:
            return True
    return False


def validate_copy(
    title: str,
    description: str,
    source_fields: Mapping[str, str],
    prohibited_terms: list[str],
) -> list[str]:
    reasons: list[str] = []
    if not title:
        reasons.append("TITLE_MISSING")
    elif len(title) > 30:
        reasons.append("TITLE_TOO_LONG")
    if not 10 <= len(description) <= 1000:
        reasons.append("DESCRIPTION_LENGTH_INVALID")
    combined_output = title + "\n" + description
    if any(term and term.casefold() in combined_output.casefold() for term in prohibited_terms):
        reasons.append("PROHIBITED_TERM")
    source_text = "\n".join(source_fields.values())
    if _has_unsupported_claim(combined_output, source_text):
        _append_unique(reasons, "UNSUPPORTED_CLAIM")
    return reasons


def generate_and_validate_copy(
    source: Mapping[str, object],
    provider: CopyProvider,
    prohibited_terms: list[str],
    *,
    generated_at: str | None = None,
) -> CopyResult:
    safe_source = {
        key: str(value).strip()
        for key, value in source.items()
        if key in ALLOWED_SOURCE_FIELDS and value is not None and str(value).strip()
    }
    request = CopyRequest(source_fields=safe_source)
    raw = dict(provider.generate(request))
    title = str(raw.get("title", "")).strip()
    description = str(raw.get("description", "")).strip()
    reasons = validate_copy(title, description, safe_source, prohibited_terms)
    timestamp = generated_at or datetime.now(timezone.utc).isoformat()
    return CopyResult(
        title=title,
        description=description,
        source_fields=safe_source,
        raw_output=raw,
        generated_at=timestamp,
        status="needs_manual_review" if reasons else "valid",
        reason_codes=reasons,
    )
