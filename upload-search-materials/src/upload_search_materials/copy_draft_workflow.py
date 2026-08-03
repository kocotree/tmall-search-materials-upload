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
    retry_agent_request,
)
from .browser.qianniu_copy import QianniuCopyError, generate_qianniu_copy_drafts
from .browser.session import CdpUnavailable, open_cdp_page
from .interaction.session import InteractionConflict, SessionStore
from .slot_workflow import (
    final_outputs_sha256,
    read_current_slot_plan,
    save_current_slot_plan,
)


PROCESSOR = "process-copy-request"


class CopyDraftProcessingError(RuntimeError):
    def __init__(self, reason_code: str, message: str, diagnostic_path: Path):
        super().__init__(f"{reason_code}: {message}")
        self.reason_code = reason_code
        self.diagnostic_path = Path(diagnostic_path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    context_fingerprint = hashlib.sha256(
        f"{outputs_identity}|{copy_provider}".encode("utf-8")
    ).hexdigest()
    if not regenerate:
        existing = find_equivalent_agent_request(
            store,
            session_id,
            kind="copy_draft",
            context_revision=int(state["stages"]["slots_copy"]["revision"]),
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
        slots.append(
            {
                "slot_id": str(slot.get("slot_id", "")),
                "product_id": product_id,
                "target_ratio": str(slot.get("target_ratio", "")),
                "theme": str(slot.get("theme", "")),
                "ordered_outputs": [
                    {
                        "asset_id": str(output.get("asset_id", "")),
                        "output_sha256": str(output.get("output_sha256", "")),
                        "output_path": str(output.get("output_path", "")),
                        "order": int(output.get("order", 0)),
                    }
                    for output in slot.get("outputs", [])
                    if isinstance(output, Mapping)
                ],
                "trusted_source_fields": {
                    "商品标题": product_titles.get(product_id, "")
                },
            }
        )
    if not slots:
        raise ValueError("copy request has no slots")

    created = create_agent_request(
        store,
        session_id,
        kind="copy_draft",
        candidates=[],
        context_revision=int(state["stages"]["slots_copy"]["revision"]),
        request_context={
            "slot_plan_revision": int(current["plan_revision"]),
            "final_outputs_sha256": outputs_identity,
            "context_fingerprint": context_fingerprint,
            "copy_provider": copy_provider,
            "processor": PROCESSOR,
            "slots": slots,
            "prohibited_terms": [],
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

    def write_progress(
        status: str, *, current_slot_id: str = "", reason_code: str = ""
    ) -> dict[str, Any]:
        document = {
            "schema_version": 1,
            "session_id": session_id,
            "stage_id": "slots_copy",
            "request_id": request_id,
            "context_fingerprint": str(context.get("context_fingerprint", "")),
            "status": status,
            "current_slot_id": current_slot_id,
            "completed_count": len(completed),
            "total_count": len(slots),
            "copy_drafts": [
                completed[str(slot.get("slot_id", ""))]
                for slot in slots
                if str(slot.get("slot_id", "")) in completed
            ],
            "reason_code": reason_code,
            "updated_at": _now(),
        }
        store._write_json_atomic(progress_path, document)
        return document

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
            for slot in slots:
                current_slot_id = str(slot.get("slot_id", ""))
                if current_slot_id in completed:
                    continue
                write_progress("processing", current_slot_id=current_slot_id)
                # Reuse the maintained Playwright implementation.  Passing one
                # slot at a time lets us persist completed work without copying
                # or reimplementing any browser selectors or navigation.
                drafts = generate_qianniu_copy_drafts(
                    browser_page,
                    [slot],
                    material_center_url=runtime.material_center_url,
                )
                if len(drafts) != 1:
                    raise QianniuCopyError(
                        "QIANNIU_COPY_RESPONSE_INVALID",
                        f"坑位 {current_slot_id} 未返回唯一文案",
                    )
                completed[current_slot_id] = dict(drafts[0])
                write_progress("processing")
        response = complete_agent_request(
            store,
            session_id,
            request_id,
            {
                "request_id": request_id,
                "kind": "copy_draft",
                "provider_id": "qianniu-builtin-ai",
                "response_model": "qianniu-builtin-copy",
                "result": {"copy_drafts": list(completed.values())},
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
        write_progress(
            "failed",
            current_slot_id=current_slot_id,
            reason_code=reason_code,
        )
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
