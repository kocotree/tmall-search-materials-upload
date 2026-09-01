"""Durable Qianniu copy requests executed by the active Codex task.

The browser automation itself remains in ``browser.qianniu_copy``.  This
module only binds that maintained Playwright flow to one durable Agent request,
checkpoints completed slots, and exposes an idempotent processor for Codex.
"""

from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .agent_diagnostics import (
    resolve_agent_diagnostic,
    write_exception_diagnostic,
)
from .agent_handoff import (
    claim_agent_request,
    complete_agent_request,
    create_agent_request,
    fail_agent_request,
    find_equivalent_agent_request,
    read_agent_request,
    recovery_prompt,
    resume_agent_request,
    retry_agent_request,
)
from .browser.qianniu_copy import (
    QianniuCopyError,
    open_qianniu_product_copy_session,
)
from .browser.session import CdpUnavailable, open_cdp_page
from .interaction.session import InteractionConflict, SessionStore
from .slot_workflow import (
    final_outputs_sha256,
    read_current_slot_plan,
    save_current_slot_plan,
)


PROCESSOR = "process-copy-request"
COPY_AUTHORIZATION_SCHEMA_VERSION = 1
COPY_AUTHORIZATION_SCOPE = (
    "upload_slot_seed_image",
    "invoke_qianniu_builtin_ai",
    "read_generated_copy",
    "exit_unpublished_form",
)
COPY_AUTHORIZATION_SOURCES = {
    "workbench_image_completion",
    "workbench_copy_regeneration",
    "legacy_workbench_copy_request",
}
COPY_SLOT_MAX_RETRIES = 3
COPY_FAILED_ONLY_MAX_PASSES = 1
COPY_SLOT_IMMEDIATE_SKIP_REASON_CODES = frozenset(
    {
        # The product session has already closed residual dialogs, reloaded the
        # live row, and searched the product again before raising these.  More
        # identical outer retries would only repeat a deterministic miss.
        "QIANNIU_SLOT_NOT_FOUND",
        "QIANNIU_SLOT_STATE_CHANGED",
    }
)
COPY_SLOT_RETRYABLE_REASON_CODES = frozenset(
    {
        "QIANNIU_AI_COPY_ACTION_NOT_FOUND",
        "QIANNIU_AI_COPY_TIMEOUT",
        "QIANNIU_COPY_POPUP_BLOCKED",
        "QIANNIU_COPY_RESPONSE_INVALID",
        "QIANNIU_COPY_RESULT_INVALID",
        "QIANNIU_COPY_RESULT_STALE",
        "QIANNIU_COPY_RUNTIME_FAILED",
        "QIANNIU_COPY_SEED_SELECTION_INVALID",
        "QIANNIU_EMPTY_SLOT_SHORTAGE",
        "QIANNIU_FRAME_NOT_READY",
        "QIANNIU_IMAGE_TEXT_ACTION_NOT_FOUND",
        "QIANNIU_IMAGE_TEXT_ACTION_UNSTABLE",
        "QIANNIU_MATERIAL_CONFIRM_NOT_READY",
        "QIANNIU_MATERIAL_ROOT_NOT_FOUND",
        "QIANNIU_MATERIAL_SEARCH_NOT_FOUND",
        "QIANNIU_MATERIAL_SELECTOR_NOT_FOUND",
        "QIANNIU_PRODUCT_IDENTITY_MISMATCH",
        "QIANNIU_PRODUCT_NOT_BOUND",
        "QIANNIU_PRODUCT_SEARCH_NOT_FOUND",
        "QIANNIU_PUBLISH_FORM_CLOSE_FAILED",
        "QIANNIU_SLOT_NOT_FOUND",
        "QIANNIU_SLOT_OCCUPIED",
        "QIANNIU_SLOT_STATE_CHANGED",
        "QIANNIU_SLOT_TABLE_INVALID",
    }
)
COPY_SLOT_FAILURE_CATEGORIES = {
    "QIANNIU_EMPTY_SLOT_SHORTAGE": ("slot_state_changed", "坑位状态已变化"),
    "QIANNIU_SLOT_OCCUPIED": ("slot_state_changed", "坑位状态已变化"),
    "QIANNIU_SLOT_STATE_CHANGED": ("slot_state_changed", "坑位状态已变化"),
    "QIANNIU_SLOT_NOT_FOUND": ("slot_not_found", "未找到坑位"),
    "QIANNIU_SLOT_TABLE_INVALID": ("slot_not_found", "未找到坑位"),
    "QIANNIU_PRODUCT_IDENTITY_MISMATCH": ("slot_not_found", "未找到坑位"),
    "QIANNIU_PRODUCT_NOT_BOUND": ("slot_not_found", "未找到坑位"),
    "QIANNIU_PRODUCT_SEARCH_NOT_FOUND": ("slot_not_found", "未找到坑位"),
    "QIANNIU_COPY_SEED_SELECTION_INVALID": ("image_upload_failed", "上传图片失败"),
    "QIANNIU_MATERIAL_CONFIRM_NOT_READY": ("image_upload_failed", "上传图片失败"),
    "QIANNIU_MATERIAL_ROOT_NOT_FOUND": ("image_upload_failed", "上传图片失败"),
    "QIANNIU_MATERIAL_SEARCH_NOT_FOUND": ("image_upload_failed", "上传图片失败"),
    "QIANNIU_MATERIAL_SELECTOR_NOT_FOUND": ("image_upload_failed", "上传图片失败"),
    "QIANNIU_IMAGE_TEXT_ACTION_NOT_FOUND": ("image_upload_failed", "上传图片失败"),
    "QIANNIU_IMAGE_TEXT_ACTION_UNSTABLE": ("image_upload_failed", "上传图片失败"),
    "QIANNIU_AI_COPY_ACTION_NOT_FOUND": ("ai_timeout", "AI 生成超时"),
    "QIANNIU_AI_COPY_TIMEOUT": ("ai_timeout", "AI 生成超时"),
    "QIANNIU_COPY_RESPONSE_INVALID": ("copy_incomplete", "标题描述读取不完整"),
    "QIANNIU_COPY_RESULT_INVALID": ("copy_incomplete", "标题描述读取不完整"),
    "QIANNIU_COPY_RESULT_STALE": ("copy_incomplete", "标题描述读取不完整"),
}


class CopyDraftProcessingError(RuntimeError):
    def __init__(self, reason_code: str, message: str, diagnostic_path: Path):
        super().__init__(f"{reason_code}: {message}")
        self.reason_code = reason_code
        self.diagnostic_path = Path(diagnostic_path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _skipped_copy_draft(
    slot: Mapping[str, Any],
    error: QianniuCopyError,
    *,
    attempt_count: int,
) -> dict[str, Any]:
    """Represent one exhausted slot without blocking later copy generation."""

    slot_id = str(slot.get("slot_id", "")).strip()
    product_id = str(slot.get("product_id", "")).strip()
    reason_code = str(error.reason_code)
    failure_category, message = COPY_SLOT_FAILURE_CATEGORIES.get(
        reason_code,
        ("copy_generation_failed", "千牛自动获取文案未完成"),
    )
    skipped = {
        "slot_id": slot_id,
        "product_id": product_id,
        "title": "",
        "description": "",
        "evidence": ["千牛自动获取未完成，已保留该坑位供人工填写"],
        "risks": [f"{message}，请人工填写并核对标题和描述"],
        "source": "manual_required",
        "generation_status": "skipped",
        "skip_reason_code": reason_code,
        "skip_message": message,
        "failure_category": failure_category,
        "attempt_count": attempt_count,
        "retry_count": max(0, attempt_count - 1),
    }
    if slot.get("remote_slot_position") is not None:
        skipped["remote_slot_position"] = int(slot["remote_slot_position"])
    return skipped


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _copy_seed_images(slots: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return the exact first verified output authorized for every slot."""

    seeds: list[dict[str, Any]] = []
    for slot in slots:
        ordered_outputs = slot.get("ordered_outputs", [])
        if not isinstance(ordered_outputs, list) or not ordered_outputs:
            raise InteractionConflict("COPY_DRAFT_AUTHORIZATION_INVALID")
        outputs = sorted(
            (dict(item) for item in ordered_outputs if isinstance(item, Mapping)),
            key=lambda item: int(item.get("order", 0)),
        )
        if not outputs:
            raise InteractionConflict("COPY_DRAFT_AUTHORIZATION_INVALID")
        seed = outputs[0]
        seeds.append(
            {
                "slot_id": str(slot.get("slot_id", "")),
                "product_id": str(slot.get("product_id", "")),
                "asset_id": str(seed.get("asset_id", "")),
                "output_sha256": str(seed.get("output_sha256", "")),
                "output_path": str(seed.get("output_path", "")),
                "order": int(seed.get("order", 0)),
            }
        )
    if any(
        not seed["slot_id"]
        or not seed["asset_id"]
        or len(seed["output_sha256"]) != 64
        or not seed["output_path"]
        for seed in seeds
    ):
        raise InteractionConflict("COPY_DRAFT_AUTHORIZATION_INVALID")
    return seeds


def _build_copy_authorization(
    *,
    session_id: str,
    context_revision: int,
    slot_plan_revision: int,
    outputs_identity: str,
    slots: list[Mapping[str, Any]],
    source: str,
    granted_at: str | None = None,
) -> dict[str, Any]:
    authorization = {
        "schema_version": COPY_AUTHORIZATION_SCHEMA_VERSION,
        "status": "granted",
        "source": source,
        "granted_at": granted_at or _now(),
        "session_id": session_id,
        "stage_id": "slots_copy",
        "context_revision": int(context_revision),
        "slot_plan_revision": int(slot_plan_revision),
        "final_outputs_sha256": outputs_identity,
        "seed_images": _copy_seed_images(slots),
        "scope": list(COPY_AUTHORIZATION_SCOPE),
        "requires_chat_confirmation": False,
        "publish_allowed": False,
    }
    authorization["authorization_sha256"] = _canonical_sha256(authorization)
    return authorization


def _validate_or_migrate_copy_authorization(
    store: SessionStore,
    session_id: str,
    request: dict[str, Any],
) -> dict[str, Any]:
    """Fail closed on tampering, while upgrading requests from older Plugins."""

    context = request.get("request_context", {})
    slots = context.get("slots", []) if isinstance(context, Mapping) else []
    if not isinstance(slots, list) or not slots:
        raise InteractionConflict("AGENT_COPY_REQUEST_HAS_NO_SLOTS")
    stage_path = store._stage_path(session_id, "slots_copy")
    processed_path = stage_path / "processed-outputs.json"
    if not processed_path.is_file():
        raise InteractionConflict("COPY_DRAFT_AUTHORIZATION_INVALID")
    processed = SessionStore._read_json(processed_path, "processed-outputs")
    outputs_identity = final_outputs_sha256(processed)
    if outputs_identity != str(context.get("final_outputs_sha256", "")):
        raise InteractionConflict("COPY_DRAFT_AUTHORIZATION_INVALID")

    authorization = context.get("authorization")
    if authorization is None:
        # Before this contract existed, copy_draft could only be created by the
        # workbench completion/regeneration actions.  Reconstructing the grant
        # is safe only while the immutable final-output fingerprint still
        # matches the legacy request exactly.
        authorization = _build_copy_authorization(
            session_id=session_id,
            context_revision=int(request.get("context_revision", -1)),
            slot_plan_revision=int(context.get("slot_plan_revision", -1)),
            outputs_identity=outputs_identity,
            slots=slots,
            source="legacy_workbench_copy_request",
            granted_at=str(request.get("created_at") or _now()),
        )
        upgraded = dict(request)
        upgraded_context = dict(context)
        upgraded_context["authorization"] = authorization
        upgraded["request_context"] = upgraded_context
        upgraded["recovery_prompt"] = recovery_prompt(
            store,
            session_id,
            str(request.get("request_id", "")),
            kind="copy_draft",
        )
        store._write_json_atomic(
            _request_root(
                store, session_id, str(request.get("request_id", ""))
            )
            / "request.json",
            upgraded,
        )
        request = upgraded
        context = upgraded_context
    if not isinstance(authorization, Mapping):
        raise InteractionConflict("COPY_DRAFT_AUTHORIZATION_INVALID")

    supplied_hash = str(authorization.get("authorization_sha256", ""))
    unsigned = dict(authorization)
    unsigned.pop("authorization_sha256", None)
    valid = (
        int(authorization.get("schema_version", -1))
        == COPY_AUTHORIZATION_SCHEMA_VERSION
        and authorization.get("status") == "granted"
        and authorization.get("source") in COPY_AUTHORIZATION_SOURCES
        and authorization.get("session_id") == session_id
        and authorization.get("stage_id") == "slots_copy"
        and int(authorization.get("context_revision", -1))
        == int(request.get("context_revision", -2))
        and int(authorization.get("slot_plan_revision", -1))
        == int(context.get("slot_plan_revision", -2))
        and authorization.get("final_outputs_sha256") == outputs_identity
        and authorization.get("seed_images") == _copy_seed_images(slots)
        and authorization.get("scope") == list(COPY_AUTHORIZATION_SCOPE)
        and authorization.get("requires_chat_confirmation") is False
        and authorization.get("publish_allowed") is False
        and supplied_hash == _canonical_sha256(unsigned)
    )
    if not valid:
        raise InteractionConflict("COPY_DRAFT_AUTHORIZATION_INVALID")
    return request


def _request_root(
    store: SessionStore, session_id: str, request_id: str
) -> Path:
    return (
        store._stage_path(session_id, "slots_copy")
        / "agent-requests"
        / request_id
    )


def _with_remote_slot_occurrences(
    slots: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Keep per-product empty-slot selection stable across one-slot retries."""

    remote_occurrences: dict[str, int] = {}
    prepared: list[dict[str, Any]] = []
    for raw_slot in slots:
        slot = dict(raw_slot)
        product_id = str(slot.get("product_id", "")).strip()
        occurrence = remote_occurrences.get(product_id, 0)
        if slot.get("remote_slot_position") is None:
            slot["remote_slot_occurrence"] = occurrence
        remote_occurrences[product_id] = occurrence + 1
        prepared.append(slot)
    return prepared


def _unfinished_slots_by_product(
    slots: list[Mapping[str, Any]],
    completed: Mapping[str, Mapping[str, Any]],
) -> list[list[Mapping[str, Any]]]:
    """Group unfinished slots by first product appearance."""

    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for slot in slots:
        slot_id = str(slot.get("slot_id", "")).strip()
        if slot_id in completed:
            continue
        product_id = str(slot.get("product_id", "")).strip()
        grouped.setdefault(product_id, []).append(slot)
    return list(grouped.values())


def create_copy_draft_request(
    store: SessionStore,
    session_id: str,
    *,
    copy_provider: str = "qianniu_builtin_ai",
    regenerate: bool = False,
    board_data: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create or reuse the exact copy request for current final outputs."""

    if copy_provider not in {"codex_agent", "qianniu_builtin_ai"}:
        raise ValueError("unsupported copy provider")
    state = store.load_session(session_id)
    current = read_current_slot_plan(store, session_id)
    stage_path = store._stage_path(session_id, "slots_copy")
    processed_path = stage_path / "processed-outputs.json"
    ready_states = {"outputs_ready", "copy_generating", "copy_review"}
    if regenerate:
        # A blocked downstream dry-run may send the user back after copy was
        # already confirmed.  The immutable processed outputs are still the
        # source of truth, so a new evidence version is safe and necessary.
        ready_states.add("completed")
    if (
        current is None
        or current.get("workflow_state") not in ready_states
        or not processed_path.is_file()
    ):
        raise ValueError("final image outputs are not ready")
    processed = SessionStore._read_json(processed_path, "processed-outputs")
    outputs_identity = final_outputs_sha256(processed)
    context_revision = int(state["stages"]["slots_copy"]["revision"])
    context_fingerprint = hashlib.sha256(
        f"{outputs_identity}|{copy_provider}".encode("utf-8")
    ).hexdigest()
    if not regenerate:
        existing = find_equivalent_agent_request(
            store,
            session_id,
            kind="copy_draft",
            context_revision=context_revision,
            context_fingerprint=context_fingerprint,
        )
        if existing is not None:
            return existing

    if board_data is None:
        context = store.read_optional_stage_document(
            session_id, "slots_copy", "review-context"
        )
        board_data = context.get("data") if isinstance(context, dict) else {}
    if not isinstance(board_data, Mapping):
        board_data = {}
    product_titles = {
        str(item.get("product_id", "")): str(item.get("product_title", ""))
        for item in board_data.get("products", [])
        if isinstance(item, dict)
    }
    slots: list[dict[str, Any]] = []
    for slot in processed.get("slots", []):
        if not isinstance(slot, Mapping):
            continue
        product_id = str(slot.get("product_id", ""))
        ordered_outputs = sorted(
            (
                {
                    "asset_id": str(output.get("asset_id", "")),
                    "output_sha256": str(output.get("output_sha256", "")),
                    "output_path": str(output.get("output_path", "")),
                    "order": int(output.get("order", 0)),
                }
                for output in slot.get("outputs", [])
                if isinstance(output, Mapping)
            ),
            key=lambda output: output["order"],
        )
        slots.append(
            {
                "slot_id": str(slot.get("slot_id", "")),
                "product_id": product_id,
                "target_ratio": str(slot.get("target_ratio", "")),
                "theme": str(slot.get("theme", "")),
                "ordered_outputs": ordered_outputs,
                "trusted_source_fields": {
                    "商品标题": product_titles.get(product_id, "")
                },
            }
        )
    if not slots:
        raise ValueError("copy request has no slots")

    authorization = _build_copy_authorization(
        session_id=session_id,
        context_revision=context_revision,
        slot_plan_revision=int(current["plan_revision"]),
        outputs_identity=outputs_identity,
        slots=slots,
        source=(
            "workbench_copy_regeneration"
            if regenerate
            else "workbench_image_completion"
        ),
    )

    created = create_agent_request(
        store,
        session_id,
        kind="copy_draft",
        candidates=[],
        context_revision=context_revision,
        request_context={
            "slot_plan_revision": int(current["plan_revision"]),
            "final_outputs_sha256": outputs_identity,
            "context_fingerprint": context_fingerprint,
            "copy_provider": copy_provider,
            "processor": PROCESSOR,
            "slots": slots,
            "prohibited_terms": [],
            "authorization": authorization,
        },
    )
    if current.get("workflow_state") != "completed":
        save_current_slot_plan(
            store,
            session_id,
            board_data,
            current["slot_assignments"],
            decision_source=str(current["decision_source"]),
            context_revision=int(current["context_revision"]),
            expected_plan_revision=int(current["plan_revision"]),
            workflow_state="copy_generating",
            confirmed=True,
            request_id=current.get("agent_request_id"),
            response_sha256=current.get("agent_response_sha256"),
            fallback_reason=current.get("fallback_reason"),
            actor="system",
        )
    return created


def resume_copy_draft_request(
    store: SessionStore,
    session_id: str,
    request_id: str,
    *,
    current_copy_edits: list[Mapping[str, Any]] | None = None,
    actor: str = "user",
) -> dict[str, Any]:
    """Retry every empty draft while preserving completed drafts and history."""

    request = read_agent_request(store, session_id, request_id)
    if request.get("kind") != "copy_draft":
        raise InteractionConflict("AGENT_REQUEST_KIND_MISMATCH")
    status = str(request.get("status", ""))
    if status in {"pending_agent", "processing"}:
        return request
    if status not in {"failed", "completed"}:
        raise InteractionConflict("COPY_DRAFT_REQUEST_NOT_RESUMABLE")
    # Validate the original bounded authorization before making the request
    # claimable again. The processor will independently repeat this check.
    request = _validate_or_migrate_copy_authorization(
        store, session_id, request
    )
    context = request.get("request_context", {})
    slots = context.get("slots", []) if isinstance(context, Mapping) else []
    slot_by_id = {
        str(slot.get("slot_id", "")): dict(slot)
        for slot in slots
        if isinstance(slot, Mapping) and slot.get("slot_id")
    }
    if not slot_by_id:
        raise InteractionConflict("AGENT_COPY_REQUEST_HAS_NO_SLOTS")

    request_root = _request_root(store, session_id, request_id)
    progress_path = request_root / "progress.json"
    response_path = request_root / "response.json"
    prior_progress = (
        SessionStore._read_json(progress_path, "copy-progress")
        if progress_path.is_file()
        else {}
    )
    prior_response = (
        SessionStore._read_json(response_path, "copy-response")
        if response_path.is_file()
        else {}
    )

    def complete_draft(item: Mapping[str, Any]) -> bool:
        return bool(
            str(item.get("title", "")).strip()
            and str(item.get("description", "")).strip()
        )

    preserved: dict[str, dict[str, Any]] = {}

    def preserve_complete_draft(item: Mapping[str, Any]) -> None:
        slot_id = str(item.get("slot_id", ""))
        slot = slot_by_id.get(slot_id)
        if slot is None or not complete_draft(item):
            return
        supplied_request_id = str(item.get("request_id", "")).strip()
        if supplied_request_id and supplied_request_id != request_id:
            return
        output_sha256 = [
            str(output.get("output_sha256", ""))
            for output in slot.get("ordered_outputs", [])
            if isinstance(output, Mapping) and output.get("output_sha256")
        ]
        draft = {
            **dict(item),
            "slot_id": slot_id,
            "product_id": str(slot.get("product_id", "")),
            "title": str(item.get("title", "")).strip(),
            "description": str(item.get("description", "")).strip(),
            "evidence": [
                str(value).strip()
                for value in item.get("evidence", [])
                if str(value).strip()
            ] or ["已完成文案，继续获取时予以保留"],
            "risks": [
                str(value).strip()
                for value in item.get("risks", [])
                if str(value).strip()
            ],
            "source": str(item.get("source") or "manual_override"),
            "generation_status": "generated",
            "confirmed": False,
            "request_id": request_id,
            "output_sha256": output_sha256,
        }
        for key in (
            "skip_reason_code",
            "skip_message",
            "failure_category",
            "final_retry_exhausted",
            "initial_skip_reason_code",
            "recovered_after_batch",
        ):
            draft.pop(key, None)
        preserved[slot_id] = draft

    prior_drafts = prior_response.get("result", {}).get("copy_drafts", [])
    prior_progress_drafts = prior_progress.get("copy_drafts", [])
    for collection in (
        prior_drafts if isinstance(prior_drafts, list) else [],
        prior_progress_drafts
        if isinstance(prior_progress_drafts, list)
        else [],
    ):
        for item in collection:
            if isinstance(item, Mapping):
                preserve_complete_draft(item)

    # The visible editor is authoritative. Clearing either field means that
    # slot must be fetched again even when an older generated value exists.
    for item in current_copy_edits or []:
        if not isinstance(item, Mapping):
            continue
        slot_id = str(item.get("slot_id", ""))
        if slot_id not in slot_by_id:
            continue
        supplied_request_id = str(item.get("request_id", "")).strip()
        if supplied_request_id and supplied_request_id != request_id:
            continue
        preserved.pop(slot_id, None)
        preserve_complete_draft(item)

    if len(preserved) == len(slot_by_id):
        raise InteractionConflict("COPY_DRAFT_REQUEST_HAS_NO_EMPTY_SLOTS")

    next_round = int(request.get("manual_resume_count", 0) or 0) + 1
    history_root = request_root / "resume-history" / f"round-{next_round:03d}"
    if prior_progress:
        store._write_json_atomic(
            history_root / "previous-progress.json", prior_progress
        )
    if prior_response:
        store._write_json_atomic(
            history_root / "previous-response.json", prior_response
        )
    ordered_preserved = [
        preserved[slot_id] for slot_id in slot_by_id if slot_id in preserved
    ]
    store._write_json_atomic(
        progress_path,
        {
            "schema_version": 1,
            "session_id": session_id,
            "stage_id": "slots_copy",
            "request_id": request_id,
            "context_fingerprint": str(context.get("context_fingerprint", "")),
            "status": "pending_agent",
            "current_slot_id": "",
            "completed_count": len(ordered_preserved),
            "generated_count": len(ordered_preserved),
            "skipped_count": 0,
            "total_count": len(slot_by_id),
            "copy_drafts": ordered_preserved,
            "skipped_slots": [],
            "reason_code": "",
            "current_retry_count": 0,
            "max_retries": COPY_SLOT_MAX_RETRIES,
            "repair_total_count": 0,
            "repair_processed_count": 0,
            "current_repair_pass": 0,
            "max_repair_passes": COPY_FAILED_ONLY_MAX_PASSES,
            "manual_resume_round": next_round,
            "updated_at": _now(),
        },
    )
    return resume_agent_request(
        store, session_id, request_id, actor=actor
    )


def process_copy_draft_request(
    store: SessionStore,
    session_id: str,
    request_id: str,
    *,
    runtime: Any,
    page: Any = None,
    page_factory: Any = None,
    actor: str = "codex-agent",
) -> dict[str, Any]:
    """Run the existing Qianniu Playwright flow and checkpoint every slot."""

    request = read_agent_request(store, session_id, request_id)
    if request.get("kind") != "copy_draft":
        raise InteractionConflict("AGENT_REQUEST_KIND_MISMATCH")
    if request.get("status") == "completed":
        response_path = _request_root(store, session_id, request_id) / "response.json"
        return SessionStore._read_json(response_path, "response")
    request = _validate_or_migrate_copy_authorization(
        store, session_id, request
    )
    if request.get("status") == "failed":
        request = retry_agent_request(
            store, session_id, request_id, actor=actor
        )
    request = claim_agent_request(
        store, session_id, request_id, actor=actor
    )
    context = request.get("request_context", {})
    slots = context.get("slots", []) if isinstance(context, Mapping) else []
    if not isinstance(slots, list) or not slots:
        raise InteractionConflict("AGENT_COPY_REQUEST_HAS_NO_SLOTS")
    slots = _with_remote_slot_occurrences(slots)
    request_root = _request_root(store, session_id, request_id)
    progress_path = request_root / "progress.json"
    prior: dict[str, Any] = {}
    if progress_path.is_file():
        prior = SessionStore._read_json(progress_path, "copy-progress")
        if (
            prior.get("request_id") != request_id
            or prior.get("context_fingerprint")
            != context.get("context_fingerprint")
        ):
            prior = {}
    completed = {
        str(item.get("slot_id", "")): dict(item)
        for item in prior.get("copy_drafts", [])
        if isinstance(item, Mapping) and item.get("slot_id")
    }
    repair_total_count = int(prior.get("repair_total_count", 0) or 0)
    repair_processed_count = int(
        prior.get("repair_processed_count", 0) or 0
    )
    current_repair_pass = int(prior.get("current_repair_pass", 0) or 0)

    def ordered_completed() -> list[dict[str, Any]]:
        return [
            completed[str(slot.get("slot_id", ""))]
            for slot in slots
            if str(slot.get("slot_id", "")) in completed
        ]

    def write_progress(
        status: str,
        *,
        current_slot_id: str = "",
        reason_code: str = "",
        current_retry_count: int = 0,
    ) -> dict[str, Any]:
        ordered = ordered_completed()
        skipped = [
            item
            for item in ordered
            if item.get("generation_status") == "skipped"
        ]
        document = {
            "schema_version": 1,
            "session_id": session_id,
            "stage_id": "slots_copy",
            "request_id": request_id,
            "context_fingerprint": str(context.get("context_fingerprint", "")),
            "status": status,
            "current_slot_id": current_slot_id,
            "completed_count": len(completed),
            "generated_count": len(ordered) - len(skipped),
            "skipped_count": len(skipped),
            "total_count": len(slots),
            "copy_drafts": ordered,
            "skipped_slots": skipped,
            "reason_code": reason_code,
            "current_retry_count": current_retry_count,
            "max_retries": COPY_SLOT_MAX_RETRIES,
            "repair_total_count": repair_total_count,
            "repair_processed_count": repair_processed_count,
            "current_repair_pass": current_repair_pass,
            "max_repair_passes": COPY_FAILED_ONLY_MAX_PASSES,
            "updated_at": _now(),
        }
        store._write_json_atomic(progress_path, document)
        return document

    def stopped_response() -> dict[str, Any] | None:
        """Stop cleanly when an edited plan or back action revokes this request."""

        latest = read_agent_request(store, session_id, request_id)
        status = str(latest.get("status", ""))
        if status not in {"superseded", "cancelled"}:
            return None
        reason_code = str(
            latest.get("reason_code")
            or (
                "AGENT_REQUEST_SUPERSEDED"
                if status == "superseded"
                else "AGENT_REQUEST_CANCELLED"
            )
        )
        write_progress(
            status,
            current_slot_id=current_slot_id,
            reason_code=reason_code,
        )
        return {
            "status": status,
            "request_id": request_id,
            "kind": "copy_draft",
            "result": {"copy_drafts": ordered_completed()},
        }

    factory = page_factory or open_cdp_page
    browser_context = (
        nullcontext(page)
        if page is not None
        else factory(runtime.cdp_url, runtime.material_center_url)
    )
    current_slot_id = ""
    try:
        write_progress("processing")
        with browser_context as browser_page:
            def process_slot(
                product_session: Any,
                slot: Mapping[str, Any],
                *,
                repair_pass: int,
            ) -> tuple[bool, dict[str, Any] | None]:
                """Process one slot; return success and optional stop response."""

                nonlocal current_slot_id, repair_processed_count
                current_slot_id = str(slot.get("slot_id", ""))
                previous = dict(completed.get(current_slot_id, {}))
                previous_attempt_count = int(
                    previous.get("attempt_count", 0) or 0
                )
                initial_skip_reason = str(
                    previous.get("initial_skip_reason_code")
                    or previous.get("skip_reason_code")
                    or ""
                )
                stopped = stopped_response()
                if stopped is not None:
                    return False, stopped
                write_progress(
                    "processing",
                    current_slot_id=current_slot_id,
                )
                attempts_in_pass = 0
                for attempt_index in range(COPY_SLOT_MAX_RETRIES + 1):
                    attempts_in_pass = attempt_index + 1
                    try:
                        draft = product_session.generate_slot(slot)
                        if (
                            not isinstance(draft, Mapping)
                            or str(draft.get("slot_id", ""))
                            != current_slot_id
                        ):
                            raise QianniuCopyError(
                                "QIANNIU_COPY_RESPONSE_INVALID",
                                f"坑位 {current_slot_id} 未返回唯一文案",
                            )
                        stopped = stopped_response()
                        if stopped is not None:
                            return False, stopped
                        generated = dict(draft)
                        generated["generation_status"] = "generated"
                        if repair_pass:
                            generated.update(
                                {
                                    "repair_pass_count": repair_pass,
                                    "recovered_after_batch": True,
                                    "initial_skip_reason_code": (
                                        initial_skip_reason
                                    ),
                                    "attempt_count": (
                                        previous_attempt_count
                                        + attempts_in_pass
                                    ),
                                }
                            )
                            repair_processed_count += 1
                        completed[current_slot_id] = generated
                        write_progress("processing")
                        return True, None
                    except QianniuCopyError as error:
                        if (
                            error.reason_code
                            not in COPY_SLOT_RETRYABLE_REASON_CODES
                        ):
                            raise
                        stopped = stopped_response()
                        if stopped is not None:
                            return False, stopped
                        immediate_skip = (
                            error.reason_code
                            in COPY_SLOT_IMMEDIATE_SKIP_REASON_CODES
                        )
                        if not immediate_skip:
                            product_session.recover_after_failure()
                        if (
                            not immediate_skip
                            and attempt_index < COPY_SLOT_MAX_RETRIES
                        ):
                            write_progress(
                                "processing",
                                current_slot_id=current_slot_id,
                                reason_code=error.reason_code,
                                current_retry_count=attempt_index + 1,
                            )
                            continue
                        skipped = _skipped_copy_draft(
                            slot,
                            error,
                            attempt_count=(
                                previous_attempt_count + attempts_in_pass
                            ),
                        )
                        if repair_pass:
                            skipped.update(
                                {
                                    "repair_pass_count": repair_pass,
                                    "final_retry_exhausted": True,
                                    "initial_skip_reason_code": (
                                        initial_skip_reason
                                    ),
                                    # Retry count describes this pass instead
                                    # of exposing a confusing accumulated 7.
                                    "retry_count": (
                                        0
                                        if immediate_skip
                                        else COPY_SLOT_MAX_RETRIES
                                    ),
                                }
                            )
                            repair_processed_count += 1
                        completed[current_slot_id] = skipped
                        write_progress(
                            "processing",
                            current_slot_id=current_slot_id,
                            reason_code=error.reason_code,
                            current_retry_count=(
                                0
                                if immediate_skip
                                else COPY_SLOT_MAX_RETRIES
                            ),
                        )
                        return False, None
                raise AssertionError("copy retry loop exited unexpectedly")

            def process_product_groups(
                product_groups: list[list[Mapping[str, Any]]],
                *,
                repair_pass: int,
            ) -> dict[str, Any] | None:
                """Keep one product context, rebuilding after two final misses."""

                for product_slots in product_groups:
                    stopped = stopped_response()
                    if stopped is not None:
                        return stopped
                    with open_qianniu_product_copy_session(
                        browser_page,
                        product_slots,
                        material_center_url=runtime.material_center_url,
                    ) as product_session:
                        consecutive_failures = 0
                        for slot_index, slot in enumerate(product_slots):
                            succeeded, stopped = process_slot(
                                product_session,
                                slot,
                                repair_pass=repair_pass,
                            )
                            if stopped is not None:
                                return stopped
                            consecutive_failures = (
                                0 if succeeded else consecutive_failures + 1
                            )
                            if (
                                consecutive_failures >= 2
                                and slot_index + 1 < len(product_slots)
                            ):
                                rebuild = getattr(
                                    product_session,
                                    "rebuild_context",
                                    None,
                                )
                                if callable(rebuild):
                                    try:
                                        rebuild()
                                    except QianniuCopyError:
                                        # The following slot still performs its
                                        # own bounded open/recovery path.
                                        product_session.recover_after_failure()
                                consecutive_failures = 0
                return None

            initial_groups = _unfinished_slots_by_product(slots, completed)
            stopped = process_product_groups(initial_groups, repair_pass=0)
            if stopped is not None:
                return stopped

            failed_only_slots = [
                slot
                for slot in slots
                if (
                    completed.get(str(slot.get("slot_id", "")), {}).get(
                        "generation_status"
                    )
                    == "skipped"
                    and int(
                        completed.get(
                            str(slot.get("slot_id", "")), {}
                        ).get("repair_pass_count", 0)
                        or 0
                    )
                    < COPY_FAILED_ONLY_MAX_PASSES
                )
            ]
            if failed_only_slots:
                repair_total_count = len(failed_only_slots)
                repair_processed_count = 0
                current_repair_pass = 1
                write_progress("processing")
                repair_groups = _unfinished_slots_by_product(
                    failed_only_slots,
                    {
                        slot_id: item
                        for slot_id, item in completed.items()
                        if item.get("generation_status") != "skipped"
                    },
                )
                stopped = process_product_groups(
                    repair_groups,
                    repair_pass=current_repair_pass,
                )
                if stopped is not None:
                    return stopped
        response = complete_agent_request(
            store,
            session_id,
            request_id,
            {
                "request_id": request_id,
                "kind": "copy_draft",
                "provider_id": "qianniu-builtin-ai",
                "response_model": "qianniu-builtin-copy",
                "result": {"copy_drafts": ordered_completed()},
            },
            actor=actor,
            # The frontend checkpoints each newly generated draft into the
            # editable stage input.  Those autosaves legitimately advance the
            # stage revision while the immutable processed image outputs stay
            # unchanged.  Copy requests are already bound to the final-output
            # fingerprint below, so generic revision equality would turn
            # normal progress persistence into AGENT_REQUEST_STALE.
            allow_context_revision_drift=True,
        )
        write_progress("completed")
        state = store.load_session(session_id)
        resolve_agent_diagnostic(
            store._session_path(session_id),
            stage_id="slots_copy",
            revision=int(state["stages"]["slots_copy"]["revision"]),
        )
        return response
    except Exception as error:
        candidate_reason = str(error).split(":", 1)[0].strip()
        if (
            not candidate_reason
            or candidate_reason.upper() != candidate_reason
            or not candidate_reason.replace("_", "").isalnum()
        ):
            candidate_reason = "QIANNIU_COPY_REQUEST_FAILED"
        reason_code = (
            error.reason_code
            if isinstance(error, QianniuCopyError)
            else (
                "CDP_UNAVAILABLE"
                if isinstance(error, CdpUnavailable)
                else candidate_reason
            )
        )
        latest_request = read_agent_request(store, session_id, request_id)
        request_superseded = latest_request.get("status") == "superseded"
        if request_superseded:
            reason_code = str(
                latest_request.get("reason_code")
                or "AGENT_REQUEST_SUPERSEDED"
            )
        write_progress(
            "superseded" if request_superseded else "failed",
            current_slot_id=current_slot_id,
            reason_code=reason_code,
        )
        if not request_superseded:
            fail_agent_request(
                store,
                session_id,
                request_id,
                actor=actor,
                reason_code=reason_code,
            )
        write_exception_diagnostic(
            store,
            session_id,
            "slots_copy",
            processor=PROCESSOR,
            phase="qianniu_copy_generation",
            error=error,
            handoff_kind="copy_draft",
            evidence=[request_root / "request.json", progress_path],
        )
        raise CopyDraftProcessingError(
            reason_code,
            str(error),
            store._session_path(session_id)
            / "agent-diagnostics"
            / "current.json",
        ) from error
