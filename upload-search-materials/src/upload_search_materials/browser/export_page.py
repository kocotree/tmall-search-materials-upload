import re
from pathlib import Path

from ..io_tables import (
    SchemaError,
    read_basic_materials_xlsx,
    read_search_materials_xlsx,
    sha256_file,
)
from ..models import SourceFile


REPORT_CONFIG = {
    "basic": {
        "selector_keys": ("export_basic",),
        "reader": read_basic_materials_xlsx,
        "contract_status": "confirmed",
        "schema_reason": "BASIC_XLSX_SCHEMA_INVALID",
        "read_reason": "BASIC_XLSX_READ_ERROR",
    },
    "promotion": {
        "selector_keys": ("export_promotion", "export_search"),
        "reader": read_search_materials_xlsx,
        "contract_status": "draft",
        "schema_reason": "PROMOTION_XLSX_SCHEMA_INVALID",
        "read_reason": "PROMOTION_XLSX_READ_ERROR",
    },
}


def _safe_component(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z._-]+", "_", str(value).strip()).strip("._")
    return cleaned or fallback


def _selector_for(selectors: dict[str, str], report_type: str) -> str:
    for key in REPORT_CONFIG[report_type]["selector_keys"]:
        value = str(selectors.get(key, "")).strip()
        if value:
            return value
    raise ValueError(f"missing selector for report type: {report_type}")


def _download_report(
    page,
    selector: str,
    output_dir: Path,
    run_id: str,
    report_type: str,
    downloaded_at: str,
) -> SourceFile:
    with page.expect_download() as download_info:
        page.locator(selector).click()
    download = download_info.value
    safe_name = Path(download.suggested_filename).name
    suggested = Path(safe_name)
    safe_stem = _safe_component(suggested.stem, report_type)
    safe_suffix = suggested.suffix.casefold() or ".xlsx"
    report_dir = output_dir / report_type
    report_dir.mkdir(parents=True, exist_ok=True)
    destination = report_dir / (
        f"{_safe_component(run_id, 'run')}-{report_type}-"
        f"{safe_stem}{safe_suffix}"
    )
    download.save_as(destination)
    config = REPORT_CONFIG[report_type]
    reason_codes: list[str] = []
    row_count = None
    schema_valid = False
    try:
        rows = config["reader"](destination)
        row_count = len(rows)
        schema_valid = True
    except SchemaError:
        reason_codes.append(str(config["schema_reason"]))
    except Exception:
        reason_codes.append(str(config["read_reason"]))
    return SourceFile(
        path=str(destination.resolve()),
        sha256=sha256_file(destination),
        downloaded_at=downloaded_at,
        original_name=safe_name,
        report_type=report_type,
        row_count=row_count,
        schema_valid=schema_valid,
        contract_status=str(config["contract_status"]),
        status="validated" if schema_valid else "needs_manual_review",
        reason_codes=reason_codes,
    )


def export_reports(
    page,
    selectors: dict[str, str],
    output_dir: Path,
    *,
    run_id: str,
    downloaded_at: str,
    report_types: tuple[str, ...] = ("basic", "promotion"),
) -> list[SourceFile]:
    requested = tuple(dict.fromkeys(report_types))
    if not requested or any(report_type not in REPORT_CONFIG for report_type in requested):
        raise ValueError("report_types must contain basic and/or promotion")
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    return [
        _download_report(
            page,
            _selector_for(selectors, report_type),
            target,
            run_id,
            report_type,
            downloaded_at,
        )
        for report_type in requested
    ]
