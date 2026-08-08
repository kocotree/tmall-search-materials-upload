"""Controlled chat-fallback writes into the authoritative stage store."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
from typing import Any

from .session import InteractionConflict, SessionStore
from .stages import FALLBACK_REASON_CODES, get_stage


def safety_context(
    store: SessionStore,
    session_id: str,
    stage_id: str,
    values: dict[str, Any],
) -> dict[str, Any]:
    if stage_id == "approval":
        setup = store.read_optional_stage_document(
            session_id, "setup", "input"
        )
        dry_run = store.read_optional_stage_document(
            session_id, "dry_run", "result"
        )
        store_name = (
            str(setup.get("values", {}).get("store", "")).strip()
            if setup
            else ""
        )
        if not store_name or not dry_run:
            raise ValueError("APPROVAL_CHECKLIST_CONTEXT_MISSING")
        dry_run_path = store._stage_path(
            session_id, "dry_run"
        ) / "result.json"
        return {
            "action": "approve_and_publish_exact_tasks",
            "store": store_name,
            "task_ids": list(values.get("task_ids", [])),
            "dry_run_input_sha256": dry_run.get("input_sha256"),
            "dry_run_result_sha256": hashlib.sha256(
                dry_run_path.read_bytes()
            ).hexdigest(),
        }
    if stage_id == "production_confirmation":
        return {
            "action": "publish_exact_tasks",
            "store": values.get("store"),
            "product_ids": list(values.get("product_ids", [])),
            "task_ids": list(values.get("task_ids", [])),
            "slot_ids": list(values.get("slot_ids", [])),
            "approval_manifest_sha256": values.get(
                "approval_manifest_sha256"
            ),
        }
    return {}


def write_chat_fallback(
    store: SessionStore,
    session_id: str,
    stage_id: str,
    *,
    values: dict[str, Any],
    expected_revision: int,
    reason_code: str,
    reason_detail: str,
    actor: str = "codex-agent",
    mode: str = "draft",
    user_notes: str = "",
) -> dict[str, Any]:
    """Validate and persist one reason-coded fallback write."""

    # Import validators lazily to keep the stage schema as the single source.
    from .web import _field_error, _value_errors

    if reason_code not in FALLBACK_REASON_CODES:
        raise ValueError("FALLBACK_REASON_REQUIRED")
    if not reason_detail.strip() or not actor.strip():
        raise ValueError("FALLBACK_AUDIT_INCOMPLETE")
    if mode not in {"draft", "submit"}:
        raise ValueError("FALLBACK_MODE_INVALID")
    state = store.load_session(session_id)
    if state["stages"][stage_id]["status"] not in {
        "draft",
        "needs_user_input",
        "blocked",
    }:
        raise InteractionConflict(
            "stage status does not allow chat fallback"
        )
    current_revision = int(state["stages"][stage_id]["revision"])
    if expected_revision != current_revision:
        raise InteractionConflict("expected revision is stale")
    stage = get_stage(stage_id)
    if (
        mode == "submit"
        and stage.previous_stage
        and state["stages"][stage.previous_stage]["status"] != "completed"
    ):
        raise InteractionConflict(
            f"complete previous stage '{stage.previous_stage}' first"
        )
    field_map = {field.name: field for field in stage.fields}
    unknown = sorted(set(values) - set(field_map))
    if unknown:
        if reason_code == "SCHEMA_GAP":
            store._write_json_atomic(
                store._stage_path(session_id, stage_id) / "frontend-gap.json",
                {
                    "schema_version": 1,
                    "session_id": session_id,
                    "stage_id": stage_id,
                    "revision": expected_revision,
                    "reason_code": reason_code,
                    "missing_fields": unknown,
                    "detail": reason_detail,
                    "created_at": datetime.now().astimezone().isoformat(),
                    "status": "frontend_schema_task_required",
                },
            )
        raise ValueError(f"SCHEMA_GAP:{','.join(unknown)}")
    for name in values:
        field = field_map[name]
        if (
            field.interaction_policy == "frontend_required"
            or reason_code not in field.fallback_reason_codes
        ):
            raise ValueError(f"FRONTEND_REQUIRED:{name}")

    current = store.read_optional_stage_document(session_id, stage_id, "input")
    merged = dict(current.get("values", {})) if current else {}
    merged.update(values)
    errors: dict[str, str] = {}
    for name in values:
        error = _field_error(field_map[name], merged.get(name), True)
        if error:
            errors[name] = error
    if mode == "submit":
        errors |= _value_errors(stage, merged)
    if errors:
        raise ValueError(
            "FALLBACK_VALIDATION_FAILED:"
            + json.dumps(errors, ensure_ascii=False, sort_keys=True)
        )
    value_sha = hashlib.sha256(
        json.dumps(
            merged,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    audit = {
        "interaction_channel": "chat_fallback",
        "fallback_reason_code": reason_code,
        "fallback_detail": reason_detail,
        "recorded_at": datetime.now().astimezone().isoformat(),
        "recorded_by": actor,
        "session_id": session_id,
        "stage_id": stage_id,
        "base_revision": expected_revision,
        "input_sha256": value_sha,
    }
    if stage_id in {"approval", "production_confirmation"}:
        checklist = safety_context(
            store, session_id, stage_id, merged
        )
        audit["safety_checklist"] = checklist
        audit["safety_checklist_sha256"] = hashlib.sha256(
            json.dumps(
                checklist,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        audit["safety_checklist_fields"] = sorted(checklist)
    if mode == "draft":
        document = store.save_draft(
            session_id,
            stage_id,
            merged,
            user_notes,
            expected_revision=expected_revision,
            interaction_audit=audit,
        )
        return {
            "status": "draft",
            "revision": document["revision"],
            "interaction_audit": audit,
        }
    handoff = store.save_input(
        session_id,
        stage_id,
        merged,
        user_notes,
        expected_revision=expected_revision + 1,
        allowed_current_statuses={"draft", "needs_user_input", "blocked"},
        interaction_audit=audit,
    )
    return {
        "status": "ready_for_agent",
        "revision": handoff["revision"],
        "input_sha256": handoff["input_sha256"],
        "interaction_audit": audit,
    }
