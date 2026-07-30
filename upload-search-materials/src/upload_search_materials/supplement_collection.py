"""Single maintained implementation for supplement material collection."""

from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .browser.material_page import (
    SelectorInvalidError,
    scan_recommended_material_status,
    supplement_material_status,
)
from .persistence import atomic_write_dict_csv, atomic_write_json, read_json


class CheckpointIdentityError(RuntimeError):
    """Raised when persisted collection state belongs to another input."""


BACKEND_STATUS_FIELDS = [
    "商品ID",
    "目标容量",
    "目标坑位",
    "现有素材数",
    "缺失数量",
    "空坑位",
    "精确坑位状态",
    "远端素材ID",
    "素材状态",
    "审核状态",
    "状态完整",
    "审核状态完整",
    "状态",
    "原因码",
    "采集时间",
    "证据",
]


def write_backend_status(
    path: Path, rows: Iterable[Mapping[str, str]]
) -> None:
    atomic_write_dict_csv(
        Path(path),
        rows,
        fieldnames=BACKEND_STATUS_FIELDS,
        extrasaction="ignore",
    )


def read_backend_status(path: Path) -> list[dict[str, str]]:
    source = Path(path)
    if not source.is_file():
        raise CheckpointIdentityError(
            "CHECKPOINT_OUTPUT_MISSING: status CSV is unavailable"
        )
    try:
        with source.open("r", encoding="utf-8-sig", newline="") as stream:
            return [dict(row) for row in csv.DictReader(stream)]
    except (OSError, csv.Error) as error:
        raise CheckpointIdentityError(
            "CHECKPOINT_OUTPUT_INVALID: status CSV is unreadable"
        ) from error


def _status_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _validate_unique_products(rows: Iterable[Mapping[str, str]]) -> None:
    seen: set[str] = set()
    for row in rows:
        product_id = str(row.get("商品ID", "")).strip()
        if not product_id:
            raise CheckpointIdentityError(
                "CHECKPOINT_OUTPUT_PRODUCT_ID_MISSING"
            )
        if product_id in seen:
            raise CheckpointIdentityError(
                f"CHECKPOINT_OUTPUT_DUPLICATE_PRODUCT:{product_id}"
            )
        seen.add(product_id)


def write_checkpoint(path: Path, document: Mapping[str, Any]) -> None:
    atomic_write_json(Path(path), document)


def validate_checkpoint_identity(
    path: Path,
    expected: Mapping[str, Any],
    *,
    compatible_missing_fields: frozenset[str] = frozenset(),
) -> dict[str, Any] | None:
    target = Path(path)
    if not target.is_file():
        return None
    try:
        document = read_json(target)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise CheckpointIdentityError(
            "CHECKPOINT_INVALID: checkpoint is unreadable"
        ) from error
    if not isinstance(document, dict):
        raise CheckpointIdentityError(
            "CHECKPOINT_INVALID: checkpoint must be an object"
        )
    for field, value in expected.items():
        if field not in document and field in compatible_missing_fields:
            continue
        if field not in document or document[field] != value:
            raise CheckpointIdentityError(
                f"CHECKPOINT_IDENTITY_MISMATCH:{field}"
            )
    return document


def collect_supplement_material_status(
    page,
    selectors: Mapping[str, str],
    *,
    scan_mode: str,
    product_ids: Iterable[str] = (),
    output: Path,
    checkpoint: Path,
    collected_at: str,
    max_pages: int | None = None,
    settle_delay_ms: int = 1000,
    action_wait_ms: int = 3000,
    checkpoint_context: Mapping[str, Any] | None = None,
    before_checkpoint: Callable[
        [int, list[dict[str, str]]], None
    ]
    | None = None,
    on_checkpoint: Callable[[int, list[dict[str, str]]], None] | None = None,
    on_phase: Callable[[str, int | None], None] | None = None,
) -> list[dict[str, str]]:
    """Collect through the one maintained supplement implementation."""

    context = dict(checkpoint_context or {})
    checkpoint_document = validate_checkpoint_identity(
        checkpoint,
        {"scan_mode": scan_mode, **context},
        compatible_missing_fields=frozenset({"attempt_id"}),
    )
    if (
        checkpoint_document
        and "attempt_id" in context
        and "attempt_id" not in checkpoint_document
    ):
        checkpoint_document["attempt_id"] = context["attempt_id"]
        checkpoint_document["identity_upgraded_at"] = collected_at
        write_checkpoint(checkpoint, checkpoint_document)
    last_completed_page = 0
    skip_completed_pages = 0
    initial_rows: list[dict[str, str]] = []
    if checkpoint_document:
        last_completed_page = int(
            checkpoint_document.get("last_completed_page") or 0
        )
        if checkpoint_document.get("status") in {"in_progress", "complete"}:
            skip_completed_pages = last_completed_page
            initial_rows = read_backend_status(output)
            expected_count = checkpoint_document.get("row_count")
            if (
                isinstance(expected_count, int)
                and len(initial_rows) != expected_count
            ):
                raise CheckpointIdentityError(
                    "CHECKPOINT_OUTPUT_ROW_COUNT_MISMATCH"
                )
            _validate_unique_products(initial_rows)
            expected_sha = str(
                checkpoint_document.get("output_sha256", "")
            ).strip()
            if expected_sha and _status_sha256(output) != expected_sha:
                raise CheckpointIdentityError(
                    "CHECKPOINT_OUTPUT_SHA256_MISMATCH"
                )
        if checkpoint_document.get("status") == "complete":
            return initial_rows

    def save_page(page_number: int, values: list[dict[str, str]]) -> None:
        nonlocal last_completed_page
        if before_checkpoint is not None:
            before_checkpoint(page_number, values)
        _validate_unique_products(values)
        last_completed_page = page_number
        write_backend_status(output, values)
        write_checkpoint(
            checkpoint,
            {
                "schema_version": 1,
                "status": "in_progress",
                "scan_mode": scan_mode,
                "last_completed_page": page_number,
                "row_count": len(values),
                "output_sha256": _status_sha256(output),
                "collected_at": collected_at,
                **context,
            },
        )
        if on_checkpoint is not None:
            on_checkpoint(page_number, values)

    try:
        if scan_mode in {"high-value", "recommended"}:
            rows = scan_recommended_material_status(
                page,
                dict(selectors),
                collected_at=collected_at,
                filter_selector_key=(
                    "high_value_filter"
                    if scan_mode == "high-value"
                    else "recommended_filter"
                ),
                on_page=save_page,
                max_pages=max_pages,
                settle_delay_ms=settle_delay_ms,
                action_wait_ms=action_wait_ms,
                initial_rows=initial_rows,
                skip_completed_pages=skip_completed_pages,
                on_phase=on_phase,
            )
        else:
            rows = supplement_material_status(
                page,
                dict(selectors),
                list(product_ids),
                collected_at=collected_at,
            )
    except SelectorInvalidError as error:
        write_checkpoint(
            checkpoint,
            {
                "schema_version": 1,
                "status": "needs_manual_review",
                "scan_mode": scan_mode,
                "last_completed_page": last_completed_page,
                "reason_code": "SELECTOR_INVALID",
                "failed_field": str(error),
                "collected_at": collected_at,
                **context,
            },
        )
        raise

    _validate_unique_products(rows)
    write_backend_status(output, rows)
    write_checkpoint(
        checkpoint,
        {
            "schema_version": 1,
            "status": "complete",
            "scan_mode": scan_mode,
            "last_completed_page": last_completed_page,
            "row_count": len(rows),
            "output_sha256": _status_sha256(output),
            "collected_at": collected_at,
            **context,
        },
    )
    return rows
