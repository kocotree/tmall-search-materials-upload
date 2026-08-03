"""Process one submitted product selection into folder-review context."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import traceback
from typing import Any

from .folder_index import build_folder_review_data, snapshot_folder_candidates
from .interaction.session import InteractionConflict, SessionStore
from .reporting import read_json


PROCESSOR_NAME = "product-selection-to-folder-review"
PROCESSOR_VERSION = 1


class ProductSelectionProcessingError(RuntimeError):
    """A failed processor invocation with a durable Codex diagnostic."""

    def __init__(
        self,
        reason_code: str,
        message: str,
        diagnostic_path: Path,
    ) -> None:
        super().__init__(f"{reason_code}: {message}")
        self.reason_code = reason_code
        self.diagnostic_path = diagnostic_path


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _selected_product_ids(input_document: dict[str, Any]) -> list[str]:
    values = input_document.get("values")
    if not isinstance(values, dict):
        raise ValueError("PRODUCT_SELECTION_INPUT_INVALID: values must be an object")
    raw_ids = values.get("selected_product_ids")
    if not isinstance(raw_ids, list):
        raise ValueError(
            "PRODUCT_SELECTION_INPUT_INVALID: selected_product_ids must be a list"
        )
    selected = sorted(
        {
            str(product_id).strip()
            for product_id in raw_ids
            if str(product_id).strip()
        }
    )
    if not selected:
        raise ValueError(
            "PRODUCT_SELECTION_INPUT_INVALID: selected_product_ids is empty"
        )
    return selected


def _reason_code(error: Exception, phase: str) -> str:
    message = str(error)
    stable_prefix = message.split(":", 1)[0].strip()
    if (
        stable_prefix.isupper()
        and "_" in stable_prefix
        and " " not in stable_prefix
    ):
        return stable_prefix
    if isinstance(error, FileNotFoundError):
        return "FOLDER_INDEX_NOT_READY"
    if isinstance(error, PermissionError) or getattr(error, "winerror", None) == 5:
        return "FOLDER_INDEX_ACCESS_DENIED"
    if isinstance(error, UnicodeError):
        return "FOLDER_INDEX_ENCODING_INVALID"
    if isinstance(error, ValueError):
        if phase in {"validate_input", "claim_handoff"}:
            return "PRODUCT_SELECTION_INPUT_INVALID"
        return "FOLDER_INDEX_INVALID"
    if isinstance(error, InteractionConflict):
        return stable_prefix or "PRODUCT_SELECTION_IDENTITY_CONFLICT"
    return "PRODUCT_SELECTION_PROCESSOR_FAILED"


def _recovery_action(reason_code: str) -> str:
    if reason_code == "FOLDER_INDEX_NOT_READY":
        return "建立或刷新当前机器配置的共享文件夹索引，然后重跑同一处理入口。"
    if reason_code == "FOLDER_INDEX_ACCESS_DENIED":
        return "检查共享索引目录的本机读取权限，然后重跑同一处理入口。"
    if reason_code.startswith("PRODUCT_SELECTION_INPUT"):
        return "核对当前 completeness handoff、revision 与 input_sha256，不要改写既有证据。"
    if reason_code in {
        "FOLDER_INDEX_INVALID",
        "FOLDER_INDEX_ENCODING_INVALID",
    }:
        return "检查共享 folder-candidates.csv 与 folder-scan-summary.json，修复索引后重跑同一入口。"
    return "Codex 读取诊断文件定位失败阶段，修复后对同一 session 重跑同一入口。"


def _artifact_state(path: Path) -> dict[str, Any]:
    state: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "is_file": path.is_file(),
    }
    if path.is_file():
        try:
            state["size_bytes"] = path.stat().st_size
            state["sha256"] = _sha256_file(path)
        except OSError as error:
            state["inspection_error"] = str(error)
    return state


def _advance_to_asset_matching(store: SessionStore, session_id: str) -> None:
    with store._session_lock(session_id):
        state = store.load_session(session_id)
        if state.get("current_stage") not in {
            "completeness",
            "asset_matching",
        }:
            raise InteractionConflict("PRODUCT_SELECTION_STAGE_CHANGED")
        state["current_stage"] = "asset_matching"
        store._write_session_state(session_id, state)


def _completed_transition(
    store: SessionStore,
    session_id: str,
) -> dict[str, Any] | None:
    """Finish or report a transition that completed before the caller returned."""

    result = store.read_optional_stage_document(
        session_id, "completeness", "result"
    )
    context = store.read_optional_stage_document(
        session_id, "asset_matching", "review-context"
    )
    if not (
        isinstance(result, dict)
        and result.get("status") == "completed"
        and isinstance(context, dict)
    ):
        return None
    data = context.get("data")
    identity = data.get("product_selection_identity") if isinstance(data, dict) else None
    if not isinstance(identity, dict):
        return None
    if (
        int(identity.get("revision", -1)) != int(result.get("revision", -2))
        or identity.get("input_sha256") != result.get("input_sha256")
    ):
        return None
    _advance_to_asset_matching(store, session_id)
    snapshot = data.get("folder_snapshot") if isinstance(data, dict) else {}
    return {
        "status": "completed",
        "idempotent": True,
        "session_id": session_id,
        "completeness_revision": int(result["revision"]),
        "requested_products": int(snapshot.get("requested_products", 0)),
        "matched_products": int(snapshot.get("matched_products", 0)),
        "candidate_rows": int(snapshot.get("candidate_rows", 0)),
        "next_stage": "asset_matching",
    }


def process_product_selection_handoff(
    store: SessionStore,
    session_id: str,
    *,
    folder_index_root: Path,
    claimant_id: str = "codex-agent",
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    """Claim one completeness handoff and publish folder-review UI data.

    The shared folder index is read only. This processor never enumerates or
    opens source images; image access remains behind the user's gallery button.
    """

    completed = _completed_transition(store, session_id)
    if completed is not None:
        return completed

    phase = "claim_handoff"
    handoff: dict[str, Any] | None = None
    selected: list[str] = []
    stage_path = store._stage_path(session_id, "completeness")
    asset_stage_path = store._stage_path(session_id, "asset_matching")
    shared_candidates = Path(folder_index_root) / "folder-candidates.csv"
    shared_summary = Path(folder_index_root) / "folder-scan-summary.json"
    task_candidates = asset_stage_path / "folder-candidates.csv"
    task_summary = asset_stage_path / "folder-scan-summary.json"
    folder_review_path = asset_stage_path / "folder-review.json"
    diagnostic_path = stage_path / "product-selection-diagnostic.json"
    progress_path = stage_path / "product-selection-processing.json"
    temp_candidates: Path | None = None

    try:
        handoff = store.wait_for_handoff(
            session_id,
            "completeness",
            timeout_seconds=0.5,
            claimant_id=claimant_id,
            reclaim_expired=True,
            resume_blocked=True,
        )
        state = store.load_session(session_id)
        claim = state.get("processing_claim")
        claim_id = str(claim.get("claim_id", "")) if isinstance(claim, dict) else ""
        attempt_id = str(claim.get("attempt_id", "")) if isinstance(claim, dict) else ""
        store._write_json_atomic(
            progress_path,
            {
                "schema_version": 1,
                "processor": PROCESSOR_NAME,
                "processor_version": PROCESSOR_VERSION,
                "status": "processing",
                "phase": phase,
                "session_id": session_id,
                "stage_id": "completeness",
                "revision": int(handoff["revision"]),
                "input_sha256": str(handoff["input_sha256"]),
                "claim_id": claim_id,
                "attempt_id": attempt_id,
                "updated_at": _now_iso(),
            },
        )

        phase = "validate_input"
        input_path = stage_path / "input.json"
        actual_input_sha256 = _sha256_file(input_path)
        if actual_input_sha256 != handoff.get("input_sha256"):
            raise InteractionConflict("PRODUCT_SELECTION_INPUT_HASH_MISMATCH")
        input_document = store._read_json(input_path, "input")
        selected = _selected_product_ids(input_document)

        phase = "snapshot_candidates"
        if not shared_candidates.is_file():
            raise FileNotFoundError(str(shared_candidates))
        temp_candidates = asset_stage_path / (
            f".folder-candidates.{attempt_id or 'pending'}.tmp"
        )
        snapshot = snapshot_folder_candidates(
            shared_candidates,
            temp_candidates,
            selected,
        )
        os.replace(temp_candidates, task_candidates)
        temp_candidates = None
        shared_summary_data: dict[str, Any] | None = None
        if shared_summary.is_file():
            loaded_summary = read_json(shared_summary)
            if not isinstance(loaded_summary, dict):
                raise ValueError("FOLDER_INDEX_INVALID: summary must be an object")
            shared_summary_data = loaded_summary
        snapshot_document = {
            "schema_version": 1,
            "processor": PROCESSOR_NAME,
            "processor_version": PROCESSOR_VERSION,
            "session_id": session_id,
            "source_candidates": _artifact_state(shared_candidates),
            "source_summary": _artifact_state(shared_summary),
            "shared_scan_summary": shared_summary_data,
            "selected_product_ids": selected,
            "snapshot": snapshot,
            "created_at": _now_iso(),
        }
        store._write_json_atomic(task_summary, snapshot_document)

        phase = "prepare_folder_review"
        review_data = build_folder_review_data(task_candidates)
        review_data["workflow_step"] = "folder_review"
        review_data["folder_snapshot"] = snapshot
        review_data["product_selection_identity"] = {
            "revision": int(handoff["revision"]),
            "input_sha256": str(handoff["input_sha256"]),
            "selected_product_ids": selected,
            "shared_candidates_sha256": snapshot_document["source_candidates"].get(
                "sha256"
            ),
        }
        store._write_json_atomic(folder_review_path, review_data)

        phase = "publish_review_context"
        asset_state = store.load_session(session_id)
        asset_revision = int(
            asset_state["stages"]["asset_matching"]["revision"]
        )
        unmatched = int(snapshot["requested_products"]) - int(
            snapshot["matched_products"]
        )
        review_context = {
            "schema_version": 1,
            "session_id": session_id,
            "stage_id": "asset_matching",
            "revision": asset_revision,
            "status": "needs_user_input",
            "summary": (
                f"已为 {snapshot['requested_products']} 个商品准备 "
                f"{snapshot['candidate_rows']} 个候选文件夹"
            ),
            "blocking_reasons": [],
            "evidence": [
                str(task_candidates),
                str(task_summary),
                str(folder_review_path),
            ],
            "next_action": (
                "核对候选文件夹；确认文件夹后再由本机素材执行器读取图片。"
                if not unmatched
                else f"有 {unmatched} 个商品未命中文件夹；可核对索引或人工补充精确文件夹。"
            ),
            "created_at": _now_iso(),
            "data": review_data,
        }
        store.write_review_context(
            session_id, "asset_matching", review_context
        )

        phase = "complete_source_stage"
        store.write_result(
            session_id,
            "completeness",
            int(handoff["revision"]),
            str(handoff["input_sha256"]),
            status="completed",
            summary=(
                f"已处理 {snapshot['requested_products']} 个所选商品，"
                f"生成 {snapshot['candidate_rows']} 个候选文件夹"
            ),
            evidence=[
                str(task_candidates),
                str(task_summary),
                str(folder_review_path),
            ],
            next_action="进入素材匹配并核对候选文件夹",
            data={
                "processor": PROCESSOR_NAME,
                "processor_version": PROCESSOR_VERSION,
                "selected_product_ids": selected,
                "folder_snapshot": snapshot,
                "next_stage": "asset_matching",
            },
            claim_id=claim_id or None,
            attempt_id=attempt_id or None,
        )

        phase = "advance_stage"
        _advance_to_asset_matching(store, session_id)
        completed_at = _now_iso()
        store._write_json_atomic(
            progress_path,
            {
                "schema_version": 1,
                "processor": PROCESSOR_NAME,
                "processor_version": PROCESSOR_VERSION,
                "status": "completed",
                "phase": "completed",
                "session_id": session_id,
                "stage_id": "completeness",
                "revision": int(handoff["revision"]),
                "input_sha256": str(handoff["input_sha256"]),
                "claim_id": claim_id,
                "attempt_id": attempt_id,
                "selected_product_ids": selected,
                "folder_snapshot": snapshot,
                "completed_at": completed_at,
                "updated_at": completed_at,
            },
        )
        if diagnostic_path.is_file():
            prior_diagnostic = store._read_json(
                diagnostic_path, "product-selection-diagnostic"
            )
            prior_diagnostic["status"] = "resolved"
            prior_diagnostic["resolved_at"] = completed_at
            prior_diagnostic["resolved_by_attempt_id"] = attempt_id
            store._write_json_atomic(diagnostic_path, prior_diagnostic)
        return {
            "status": "completed",
            "idempotent": False,
            "session_id": session_id,
            "completeness_revision": int(handoff["revision"]),
            **snapshot,
            "unmatched_products": unmatched,
            "next_stage": "asset_matching",
            "folder_review": str(folder_review_path),
        }
    except Exception as error:
        if temp_candidates is not None and temp_candidates.is_file():
            temp_candidates.unlink(missing_ok=True)
        reason_code = _reason_code(error, phase)
        recovery_action = _recovery_action(reason_code)
        retry_command = [
            "tmall-materials",
            "process-product-selection",
            "--runs-root",
            str(store.runs_root),
            "--session",
            session_id,
        ]
        if config_path:
            retry_command.extend(["--config", str(config_path)])
        diagnostic = {
            "schema_version": 1,
            "processor": PROCESSOR_NAME,
            "processor_version": PROCESSOR_VERSION,
            "status": "failed",
            "phase": phase,
            "reason_code": reason_code,
            "error_type": type(error).__name__,
            "message": str(error),
            "session_id": session_id,
            "stage_id": "completeness",
            "revision": int(handoff["revision"]) if handoff else None,
            "input_sha256": str(handoff["input_sha256"]) if handoff else None,
            "selected_product_ids": selected,
            "artifacts": {
                "shared_candidates": _artifact_state(shared_candidates),
                "shared_summary": _artifact_state(shared_summary),
                "task_candidates": _artifact_state(task_candidates),
                "task_summary": _artifact_state(task_summary),
                "folder_review": _artifact_state(folder_review_path),
            },
            "codex_recovery": {
                "action": recovery_action,
                "retry_same_handoff": handoff is not None,
                "command": retry_command,
                "checks": [
                    "核对 session/stage/revision/input_sha256",
                    "核对共享 folder-candidates.csv 的存在性、编码和表头",
                    "修复后重跑同一入口，不手工拼接 result.json",
                ],
            },
            "traceback": traceback.format_exc(),
            "created_at": _now_iso(),
        }
        store._write_json_atomic(diagnostic_path, diagnostic)
        if handoff is not None:
            try:
                current = store.load_session(session_id)
                active_claim = current.get("processing_claim")
                store.write_result(
                    session_id,
                    "completeness",
                    int(handoff["revision"]),
                    str(handoff["input_sha256"]),
                    status="blocked",
                    summary=f"候选文件夹准备失败：{reason_code}",
                    blocking_reasons=[reason_code],
                    evidence=[str(diagnostic_path)],
                    next_action=recovery_action,
                    data={
                        "processor": PROCESSOR_NAME,
                        "processor_version": PROCESSOR_VERSION,
                        "failed_phase": phase,
                        "reason_code": reason_code,
                        "diagnostic_path": str(diagnostic_path),
                    },
                    claim_id=(
                        str(active_claim.get("claim_id", "")) or None
                        if isinstance(active_claim, dict)
                        else None
                    ),
                    attempt_id=(
                        str(active_claim.get("attempt_id", "")) or None
                        if isinstance(active_claim, dict)
                        else None
                    ),
                )
            except Exception as result_error:
                diagnostic["result_write_error"] = {
                    "error_type": type(result_error).__name__,
                    "message": str(result_error),
                }
                store._write_json_atomic(diagnostic_path, diagnostic)
        raise ProductSelectionProcessingError(
            reason_code, str(error), diagnostic_path
        ) from error
