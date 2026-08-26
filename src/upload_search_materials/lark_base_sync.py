"""Optional Feishu Base integration for the Windows material workflow.

The plugin's core upload flow must keep working when Feishu is not configured,
not authorized, or temporarily unavailable.  This module therefore exposes
small soft-fail helpers: they return structured summaries and never raise for
ordinary Base/auth/network failures.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .io_tables import read_product_csv
from .models import ProductRecord
from .persistence import atomic_write_json, read_json
from .reporting import material_item_from_dict
from .runtime_config import LarkBaseConfig
from .time_utils import iso_timestamp


PRODUCT_ID_FIELDS = (
    "商品ID",
    "商品 ID",
    "商品id",
    "商品编号",
    "Product ID",
    "product_id",
)
SKU_FIELDS = (
    "货号（查找引用）",
    "货号",
    "SKU",
    "sku",
)
TITLE_FIELDS = (
    "商品名称（查找引用）",
    "商品名称",
    "商品标题",
    "标题",
    "Product Name",
    "product_title",
)
OWNER_FIELDS = (
    "运营",
    "负责人",
    "责任人",
    "上传负责人",
    "Owner",
    "owner",
)
UPLOAD_LOG_FIELDS = {
    "upload_owner": "上传负责人",
    "product_id": "商品 ID",
    "sku": "货号",
    "product_title": "商品名称",
    "uploaded_at": "上传时间",
    "asset_filenames": "上传图片文件名",
    "title": "标题",
}
SUCCESS_UPLOAD_STATUSES = {"submitted", "under_review", "success"}
MAX_RECORD_LIST_PAGES = 200


@dataclass(frozen=True)
class LarkCliResult:
    ok: bool
    payload: Any = None
    stdout: str = ""
    stderr: str = ""
    reason_code: str = ""
    message: str = ""


@dataclass(frozen=True)
class ProductMetadata:
    product_id: str
    owner: str = ""
    sku: str = ""
    title: str = ""


@dataclass(frozen=True)
class ProductSyncResult:
    status: str
    reason_code: str = ""
    message: str = ""
    base_token: str = ""
    table_id: str = ""
    fetched_count: int = 0
    matched_count: int = 0
    updated_owner_count: int = 0
    records: tuple[ProductRecord, ...] = ()

    def evidence(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "integration": "lark_base_product_metadata",
            "status": self.status,
            "reason_code": self.reason_code,
            "message": self.message,
            "base_token": self.base_token,
            "table_id": self.table_id,
            "fetched_count": self.fetched_count,
            "matched_count": self.matched_count,
            "updated_owner_count": self.updated_owner_count,
            "recorded_at": iso_timestamp(),
        }


@dataclass(frozen=True)
class UploadLogResult:
    status: str
    reason_code: str = ""
    message: str = ""
    base_token: str = ""
    table_id: str = ""
    attempted_count: int = 0
    created_count: int = 0
    rows: tuple[dict[str, Any], ...] = ()
    response: Mapping[str, Any] = field(default_factory=dict)

    def evidence(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "integration": "lark_base_upload_log",
            "status": self.status,
            "reason_code": self.reason_code,
            "message": self.message,
            "base_token": self.base_token,
            "table_id": self.table_id,
            "attempted_count": self.attempted_count,
            "created_count": self.created_count,
            "recorded_at": iso_timestamp(),
        }


@dataclass(frozen=True)
class LarkTarget:
    base_token: str
    table_id: str


Runner = Callable[[Sequence[str], float], LarkCliResult]


def default_lark_cli_runner(
    args: Sequence[str], timeout_seconds: float
) -> LarkCliResult:
    executable = shutil.which("lark-cli.cmd") or shutil.which("lark-cli")
    if not executable:
        return LarkCliResult(
            ok=False,
            reason_code="LARK_CLI_NOT_FOUND",
            message="未找到 lark-cli，跳过飞书多维表格同步。",
        )
    try:
        environment = os.environ.copy()
        environment["LARKSUITE_CLI_NO_UPDATE_NOTIFIER"] = "1"
        environment["LARKSUITE_CLI_NO_SKILLS_NOTIFIER"] = "1"
        completed = subprocess.run(
            [executable, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
            env=environment,
            creationflags=(
                subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            ),
        )
    except subprocess.TimeoutExpired:
        return LarkCliResult(
            ok=False,
            reason_code="LARK_CLI_TIMEOUT",
            message="飞书多维表格响应超时，已跳过本次同步。",
        )
    except OSError as error:
        return LarkCliResult(
            ok=False,
            reason_code="LARK_CLI_UNAVAILABLE",
            message=str(error),
        )
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    if completed.returncode != 0:
        return LarkCliResult(
            ok=False,
            stdout=stdout,
            stderr=stderr,
            reason_code=_classify_lark_error(stdout + "\n" + stderr),
            message=_compact_message(stdout, stderr),
        )
    try:
        payload = json.loads(stdout) if stdout.strip() else {}
    except json.JSONDecodeError:
        return LarkCliResult(
            ok=False,
            stdout=stdout,
            stderr=stderr,
            reason_code="LARK_JSON_INVALID",
            message="飞书多维表格返回内容无法解析。",
        )
    return LarkCliResult(ok=True, payload=payload, stdout=stdout, stderr=stderr)


def inspect_lark_base_config(
    config: LarkBaseConfig,
    *,
    runner: Runner | None = None,
) -> dict[str, Any]:
    """Return a compact readiness summary for the setup page."""

    if not config.enabled:
        return {
            "status": "disabled",
            "message": "未启用飞书多维表格同步。",
            "product_sync": _target_status("disabled", "未启用负责人同步。"),
            "upload_log": _target_status("disabled", "未启用上传记录。"),
        }
    active_runner = runner or default_lark_cli_runner
    product = _inspect_target(
        config,
        base_url=config.product_base_url,
        base_token=config.product_base_token,
        table_id=config.product_table_id,
        runner=active_runner,
        label="负责人同步",
        require_records=True,
    )
    upload_log = _inspect_target(
        config,
        base_url=config.upload_log_base_url,
        base_token=config.upload_log_base_token,
        table_id=config.upload_log_table_id,
        runner=active_runner,
        label="上传记录",
    )
    ready_count = sum(
        1 for item in (product, upload_log) if item["status"] == "available"
    )
    return {
        "status": "available" if ready_count else "unavailable",
        "message": (
            f"飞书多维表格已配置，{ready_count} / 2 项可用。"
            if ready_count
            else "飞书多维表格尚不可用，请检查授权、链接和表名。"
        ),
        "product_sync": product,
        "upload_log": upload_log,
    }


def sync_product_metadata(
    products: Sequence[ProductRecord],
    config: LarkBaseConfig,
    *,
    evidence_path: Path | None = None,
    runner: Runner | None = None,
) -> ProductSyncResult:
    """Overlay product owners from Feishu Base for the current session."""

    if not config.product_sync_configured:
        result = ProductSyncResult(
            status="skipped",
            reason_code=(
                "LARK_BASE_DISABLED"
                if not config.enabled
                else "LARK_PRODUCT_TABLE_NOT_CONFIGURED"
            ),
            message="未配置飞书商品信息表，使用商品快照中的负责人。",
            records=tuple(products),
        )
        _write_optional_evidence(evidence_path, result.evidence())
        return result

    active_runner = runner or default_lark_cli_runner
    target_result = _resolve_target(
        config.product_base_url,
        config.product_base_token,
        config.product_table_id,
        config.command_timeout_seconds,
        active_runner,
    )
    if not target_result.ok:
        result = ProductSyncResult(
            status="skipped",
            reason_code=target_result.reason_code,
            message=target_result.message,
            table_id=config.product_table_id,
            records=tuple(products),
        )
        _write_optional_evidence(evidence_path, result.evidence())
        return result
    target = target_result.payload
    records_result = _list_all_records(
        target.base_token,
        target.table_id,
        config.command_timeout_seconds,
        active_runner,
    )
    if not records_result.ok:
        result = ProductSyncResult(
            status="skipped",
            reason_code=records_result.reason_code,
            message=records_result.message,
            base_token=target.base_token,
            table_id=target.table_id,
            records=tuple(products),
        )
        _write_optional_evidence(evidence_path, result.evidence())
        return result

    if not records_result.payload:
        result = ProductSyncResult(
            status="skipped",
            reason_code="LARK_PRODUCT_TABLE_EMPTY_OR_INVISIBLE",
            message=(
                "飞书商品信息表可访问，但当前账号未读取到任何记录，"
                "已保留商品快照中的负责人。"
            ),
            base_token=target.base_token,
            table_id=target.table_id,
            records=tuple(products),
        )
        _write_optional_evidence(evidence_path, result.evidence())
        return result

    metadata = _metadata_by_product_id(records_result.payload)
    updated_owner_count = 0
    matched_count = 0
    enriched: list[ProductRecord] = []
    for product in products:
        meta = metadata.get(_normalize_product_id(product.product_id))
        if meta is None:
            enriched.append(product)
            continue
        matched_count += 1
        owner = meta.owner.strip()
        if owner and owner != product.owner.strip():
            product.owner = owner
            product.raw["运营"] = owner
            updated_owner_count += 1
        if not product.sku and meta.sku:
            product.sku = meta.sku
            product.raw["货号（查找引用）"] = meta.sku
        if not product.title and meta.title:
            product.title = meta.title
            product.raw["商品名称（查找引用）"] = meta.title
        enriched.append(product)

    result = ProductSyncResult(
        status="completed",
        message="已按飞书多维表格同步商品负责人。",
        base_token=target.base_token,
        table_id=target.table_id,
        fetched_count=len(records_result.payload),
        matched_count=matched_count,
        updated_owner_count=updated_owner_count,
        records=tuple(enriched),
    )
    _write_optional_evidence(evidence_path, result.evidence())
    return result


def write_successful_upload_log(
    *,
    run_dir: Path,
    session_inputs_dir: Path,
    task_records: Sequence[Mapping[str, Any] | None],
    config: LarkBaseConfig,
    confirmed_by: str = "",
    evidence_path: Path | None = None,
    runner: Runner | None = None,
) -> UploadLogResult:
    """Append successful upload rows to the configured Feishu Base table."""

    try:
        rows = build_upload_log_rows(
            run_dir=run_dir,
            session_inputs_dir=session_inputs_dir,
            task_records=task_records,
            confirmed_by=confirmed_by,
        )
    except (OSError, ValueError, KeyError) as error:
        result = UploadLogResult(
            status="failed",
            reason_code="UPLOAD_LOG_ARTIFACT_INVALID",
            message=str(error),
        )
        _write_optional_evidence(evidence_path, result.evidence())
        return result
    if not rows:
        result = UploadLogResult(
            status="skipped",
            reason_code="NO_SUCCESSFUL_UPLOADS",
            message="没有成功上传的任务需要记录。",
            rows=(),
        )
        _write_optional_evidence(evidence_path, result.evidence())
        return result
    rows_sha256 = _stable_json_sha256(rows)
    existing = _read_object_json(evidence_path) if evidence_path else {}
    if (
        existing.get("status") == "completed"
        and existing.get("rows_sha256") == rows_sha256
    ):
        return UploadLogResult(
            status="completed",
            reason_code="LARK_UPLOAD_LOG_ALREADY_RECORDED",
            message="本批成功上传记录已写入飞书，本次跳过重复追加。",
            attempted_count=len(rows),
            created_count=int(existing.get("created_count") or len(rows)),
            rows=tuple(rows),
        )
    if not config.upload_log_configured:
        result = UploadLogResult(
            status="skipped",
            reason_code=(
                "LARK_BASE_DISABLED"
                if not config.enabled
                else "LARK_UPLOAD_LOG_TABLE_NOT_CONFIGURED"
            ),
            message="未配置飞书上传记录表，已跳过记录写入。",
            attempted_count=len(rows),
            rows=tuple(rows),
        )
        _write_optional_evidence(
            evidence_path,
            {**result.evidence(), "rows_sha256": rows_sha256, "rows": rows},
        )
        return result

    active_runner = runner or default_lark_cli_runner
    target_result = _resolve_target(
        config.upload_log_base_url,
        config.upload_log_base_token,
        config.upload_log_table_id,
        config.command_timeout_seconds,
        active_runner,
    )
    if not target_result.ok:
        result = UploadLogResult(
            status="failed",
            reason_code=target_result.reason_code,
            message=target_result.message,
            attempted_count=len(rows),
            rows=tuple(rows),
        )
        _write_optional_evidence(
            evidence_path,
            {**result.evidence(), "rows_sha256": rows_sha256, "rows": rows},
        )
        return result

    target = target_result.payload
    payload = {"create_records": rows}
    command = [
        "base",
        "+record-batch-create",
        "--base-token",
        target.base_token,
        "--table-id",
        target.table_id,
        "--json",
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        "--as",
        "user",
    ]
    create_result = active_runner(command, config.command_timeout_seconds)
    if not create_result.ok:
        result = UploadLogResult(
            status="failed",
            reason_code=create_result.reason_code,
            message=create_result.message,
            base_token=target.base_token,
            table_id=target.table_id,
            attempted_count=len(rows),
            rows=tuple(rows),
        )
        _write_optional_evidence(
            evidence_path,
            {**result.evidence(), "rows_sha256": rows_sha256, "rows": rows},
        )
        return result

    created_count = _created_record_count(create_result.payload, len(rows))
    result = UploadLogResult(
        status="completed",
        message="已记录成功上传的素材信息。",
        base_token=target.base_token,
        table_id=target.table_id,
        attempted_count=len(rows),
        created_count=created_count,
        rows=tuple(rows),
        response=create_result.payload if isinstance(create_result.payload, Mapping) else {},
    )
    _write_optional_evidence(
        evidence_path,
        {**result.evidence(), "rows_sha256": rows_sha256, "rows": rows},
    )
    return result


def build_upload_log_rows(
    *,
    run_dir: Path,
    session_inputs_dir: Path,
    task_records: Sequence[Mapping[str, Any] | None],
    confirmed_by: str = "",
) -> list[dict[str, Any]]:
    """Build Feishu Base rows from persisted upload artifacts."""

    directory = Path(run_dir)
    items = {
        item.task_id: item
        for item in (
            material_item_from_dict(value)
            for value in _read_list_json(directory / "material-items.json")
            if isinstance(value, dict)
        )
    }
    manifest = _read_object_json(directory / "approval-manifest.json")
    entries = {
        str(entry.get("task_id", "")): entry
        for entry in manifest.get("entries", [])
        if isinstance(entry, Mapping)
    }
    products = {
        product.product_id: product
        for product in read_product_csv(Path(session_inputs_dir) / "products.csv")
    }
    rows: list[dict[str, Any]] = []
    for record in task_records:
        if not record:
            continue
        status = str(record.get("status", "")).strip()
        remote_material_id = str(record.get("remote_material_id") or "").strip()
        if status not in SUCCESS_UPLOAD_STATUSES or not remote_material_id:
            continue
        task_id = str(record.get("task_id", "")).strip()
        item = items.get(task_id)
        entry = entries.get(task_id, {})
        product_id = str(
            getattr(item, "product_id", "")
            or entry.get("product_id", "")
            or record.get("product_id", "")
        ).strip()
        product = products.get(product_id)
        fallback_owner = product.owner if product else ""
        upload_owner = str(confirmed_by or fallback_owner).strip()
        filenames = _asset_filenames(item)
        uploaded_at = _display_timestamp(record.get("updated_at"))
        rows.append(
            {
                UPLOAD_LOG_FIELDS["upload_owner"]: upload_owner,
                UPLOAD_LOG_FIELDS["product_id"]: product_id,
                UPLOAD_LOG_FIELDS["sku"]: product.sku if product else "",
                UPLOAD_LOG_FIELDS["product_title"]: product.title if product else "",
                UPLOAD_LOG_FIELDS["uploaded_at"]: uploaded_at,
                UPLOAD_LOG_FIELDS["asset_filenames"]: filenames,
                UPLOAD_LOG_FIELDS["title"]: item.title if item else str(entry.get("title", "")),
            }
        )
    return rows


def _inspect_target(
    config: LarkBaseConfig,
    *,
    base_url: str,
    base_token: str,
    table_id: str,
    runner: Runner,
    label: str,
    require_records: bool = False,
) -> dict[str, Any]:
    if not (base_url or base_token):
        return _target_status(
            "not_configured",
            f"{label}未配置链接。",
            reason_code="LARK_TARGET_NOT_CONFIGURED",
        )
    target = _resolve_target(
        base_url,
        base_token,
        table_id,
        config.command_timeout_seconds,
        runner,
    )
    if not target.ok:
        return _target_status(
            "unavailable",
            target.message,
            reason_code=target.reason_code,
        )
    coordinates = target.payload
    result = runner(
        [
            "base",
            "+field-list",
            "--base-token",
            coordinates.base_token,
            "--table-id",
            coordinates.table_id,
            "--limit",
            "200",
            "--json",
            "--as",
            "user",
        ],
        config.command_timeout_seconds,
    )
    if not result.ok:
        return _target_status(
            "unavailable",
            result.message,
            reason_code=result.reason_code,
            base_token=coordinates.base_token,
            table_id=coordinates.table_id,
        )
    fields = _extract_items(result.payload)
    if require_records:
        record_probe = runner(
            [
                "base",
                "+record-list",
                "--base-token",
                coordinates.base_token,
                "--table-id",
                coordinates.table_id,
                "--offset",
                "0",
                "--limit",
                "1",
                "--json",
                "--as",
                "user",
            ],
            config.command_timeout_seconds,
        )
        if not record_probe.ok:
            return _target_status(
                "unavailable",
                record_probe.message,
                reason_code=record_probe.reason_code,
                base_token=coordinates.base_token,
                table_id=coordinates.table_id,
                field_count=len(fields),
            )
        if not _extract_items(record_probe.payload):
            return _target_status(
                "unavailable",
                "商品信息表可访问，但当前账号没有读取到任何记录。",
                reason_code="LARK_PRODUCT_TABLE_EMPTY_OR_INVISIBLE",
                base_token=coordinates.base_token,
                table_id=coordinates.table_id,
                field_count=len(fields),
                record_probe_count=0,
            )
    return _target_status(
        "available",
        f"{label}表可访问，已读取 {len(fields)} 个字段。",
        base_token=coordinates.base_token,
        table_id=coordinates.table_id,
        field_count=len(fields),
        record_probe_count=(1 if require_records else None),
    )


def _target_status(
    status: str,
    message: str,
    *,
    reason_code: str = "",
    base_token: str = "",
    table_id: str = "",
    field_count: int | None = None,
    record_probe_count: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": status,
        "message": message,
        "reason_code": reason_code,
        "base_token": base_token,
        "table_id": table_id,
    }
    if field_count is not None:
        payload["field_count"] = field_count
    if record_probe_count is not None:
        payload["record_probe_count"] = record_probe_count
    return payload


def _resolve_target(
    base_url: str,
    base_token: str,
    table_id: str,
    timeout_seconds: float,
    runner: Runner,
) -> LarkCliResult:
    token = base_token
    resolved_table = ""
    if base_url and (not token or not table_id):
        result = runner(
            ["base", "+url-resolve", "--url", base_url, "--json", "--as", "user"],
            timeout_seconds,
        )
        if not result.ok:
            return result
        if not token:
            token = _find_first_text(
                result.payload,
                ("base_token", "app_token", "bitable_token"),
            )
        resolved_table = _find_first_text(
            result.payload,
            ("table_id", "table", "block_id"),
        )
    if not token:
        return LarkCliResult(
            ok=False,
            reason_code="LARK_BASE_URL_REQUIRED",
            message="缺少飞书多维表格链接。",
        )
    selected_table = table_id or resolved_table
    if not selected_table:
        return LarkCliResult(
            ok=False,
            reason_code="LARK_TABLE_ID_REQUIRED",
            message="缺少飞书数据表名或表 ID。",
        )
    return LarkCliResult(
        ok=True,
        payload=LarkTarget(base_token=token, table_id=selected_table),
    )


def _list_all_records(
    base_token: str,
    table_id: str,
    timeout_seconds: float,
    runner: Runner,
) -> LarkCliResult:
    offset = 0
    limit = 200
    records: list[dict[str, Any]] = []
    for _page in range(MAX_RECORD_LIST_PAGES):
        result = runner(
            [
                "base",
                "+record-list",
                "--base-token",
                base_token,
                "--table-id",
                table_id,
                "--offset",
                str(offset),
                "--limit",
                str(limit),
                "--json",
                "--as",
                "user",
            ],
            timeout_seconds,
        )
        if not result.ok:
            return result
        items = _extract_items(result.payload)
        records.extend(item for item in items if isinstance(item, dict))
        has_more = _has_more(result.payload)
        if has_more is False:
            break
        if has_more is None and len(items) < limit:
            break
        if not items:
            break
        offset += len(items)
    return LarkCliResult(ok=True, payload=records)


def _metadata_by_product_id(records: Sequence[Mapping[str, Any]]) -> dict[str, ProductMetadata]:
    values: dict[str, ProductMetadata] = {}
    for record in records:
        fields = record.get("fields") if isinstance(record.get("fields"), Mapping) else record
        if not isinstance(fields, Mapping):
            continue
        product_id = _normalize_product_id(_first_field_text(fields, PRODUCT_ID_FIELDS))
        if not product_id:
            continue
        values[product_id] = ProductMetadata(
            product_id=product_id,
            owner=_first_field_text(fields, OWNER_FIELDS),
            sku=_first_field_text(fields, SKU_FIELDS),
            title=_first_field_text(fields, TITLE_FIELDS),
        )
    return values


def _first_field_text(fields: Mapping[str, Any], names: Sequence[str]) -> str:
    lookup = {str(key).casefold(): key for key in fields}
    for name in names:
        key = lookup.get(name.casefold())
        if key is None:
            continue
        text = _cell_text(fields[key])
        if text:
            return text
    return ""


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        parts = [_cell_text(item) for item in value]
        return "；".join(part for part in parts if part)
    if isinstance(value, Mapping):
        for key in (
            "text",
            "name",
            "display_name",
            "en_name",
            "title",
            "value",
            "url",
            "link",
            "id",
        ):
            if key in value:
                text = _cell_text(value[key])
                if text:
                    return text
        parts = [
            _cell_text(item)
            for key, item in value.items()
            if str(key) not in {"type", "field_id"}
        ]
        return "；".join(part for part in parts if part)
    return str(value).strip()


def _normalize_product_id(value: Any) -> str:
    text = _cell_text(value).strip()
    if re.fullmatch(r"\d+\.0", text):
        return text[:-2]
    return text


def _extract_items(payload: Any) -> list[Any]:
    if isinstance(payload, Mapping):
        for key in ("items", "records", "fields"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        for key in ("data", "result"):
            value = payload.get(key)
            nested = _extract_items(value)
            if nested:
                return nested
    return []


def _has_more(payload: Any) -> bool | None:
    if isinstance(payload, Mapping):
        value = payload.get("has_more")
        if isinstance(value, bool):
            return value
        for key in ("data", "result"):
            if key not in payload:
                continue
            nested = _has_more(payload[key])
            if nested is not None:
                return nested
    return None


def _find_first_text(payload: Any, names: Sequence[str]) -> str:
    if isinstance(payload, Mapping):
        for name in names:
            value = payload.get(name)
            text = _cell_text(value)
            if text:
                return text
        for value in payload.values():
            text = _find_first_text(value, names)
            if text:
                return text
    if isinstance(payload, Sequence) and not isinstance(payload, (bytes, bytearray, str)):
        for value in payload:
            text = _find_first_text(value, names)
            if text:
                return text
    return ""


def _read_list_json(path: Path) -> list[Any]:
    try:
        value = read_json(Path(path))
    except (OSError, ValueError, json.JSONDecodeError):
        return []
    return value if isinstance(value, list) else []


def _read_object_json(path: Path) -> dict[str, Any]:
    try:
        value = read_json(Path(path))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _asset_filenames(item: Any) -> str:
    if item is None:
        return ""
    names = []
    for asset in item.assets:
        name = Path(str(asset.source_path)).name
        if name and name not in names:
            names.append(name)
    return "；".join(names)


def _display_timestamp(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return iso_timestamp()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def _created_record_count(payload: Any, fallback: int) -> int:
    if isinstance(payload, Mapping):
        for key in ("record_id_list", "record_ids", "records"):
            value = payload.get(key)
            if isinstance(value, list):
                return len(value)
        for key in ("data", "result"):
            nested = _created_record_count(payload.get(key), -1)
            if nested >= 0:
                return nested
    return fallback


def _stable_json_sha256(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _classify_lark_error(text: str) -> str:
    lower = text.casefold()
    if "base:block:read" in lower or "scope" in lower or "permission" in lower or "无权限" in lower:
        return "LARK_PERMISSION_REQUIRED"
    if "unauthorized" in lower or "auth" in lower or "登录" in lower:
        return "LARK_AUTH_REQUIRED"
    if "table" in lower or "数据表" in lower:
        return "LARK_TABLE_UNAVAILABLE"
    return "LARK_COMMAND_FAILED"


def _compact_message(stdout: str, stderr: str) -> str:
    text = (stderr.strip() or stdout.strip() or "飞书多维表格命令执行失败。")
    return " ".join(text.split())[:500]


def _write_optional_evidence(path: Path | None, document: Mapping[str, Any]) -> None:
    if path is None:
        return
    try:
        atomic_write_json(Path(path), dict(document))
    except OSError:
        return
