"""Versioned fifth-stage draft and sub-state management."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from .agent_handoff import WORKFLOW_MIGRATION_ID
from .interaction.session import InteractionConflict, SessionStore
from .slot_planning import validate_slot_assignments


CURRENT_SLOT_PLAN_SCHEMA_VERSION = 2
WORKFLOW_STATES = (
    "analysing",
    "plan_review",
    "plan_confirmed",
    "processing",
    "outputs_ready",
    "copy_generating",
    "copy_review",
    "completed",
)

THREE_STEP_PAGES = ("compose", "process", "copy")
TWO_STEP_PAGES = ("process", "copy")


def workflow_page(workflow_state: str | None) -> str:
    """Map durable legacy sub-states to one of the three user pages."""

    if workflow_state in {"plan_confirmed", "processing"}:
        return "process"
    if workflow_state in {
        "outputs_ready",
        "copy_generating",
        "copy_review",
        "completed",
    }:
        return "copy"
    return "compose"


def two_step_workflow_page(workflow_state: str | None) -> str:
    """Map both historical and current durable states to the two-page UI."""

    if workflow_state in {
        "outputs_ready",
        "copy_generating",
        "copy_review",
        "completed",
    }:
        return "copy"
    return "process"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _path(store: SessionStore, session_id: str) -> Path:
    return store._stage_path(session_id, "slots_copy") / "current-slot-plan.json"


def rule_fallback_assignments(
    board_data: Mapping[str, Any],
    *,
    product_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Choose one deterministic rule draft per requested product."""

    chosen: dict[str, Mapping[str, Any]] = {}
    for draft in board_data.get("rule_drafts", []):
        if not isinstance(draft, Mapping):
            continue
        product_id = str(draft.get("product_id", ""))
        if product_ids is not None and product_id not in product_ids:
            continue
        chosen.setdefault(product_id, draft)
    return [
        {
            "slot_id": str(item.get("slot_id", f"{product_id}-slot-1")),
            "product_id": product_id,
            "target_ratio": str(item.get("target_ratio", "")),
            "asset_ids": [
                str(value) for value in item.get("ordered_asset_ids", [])
            ],
            "plan_source": "rules",
            "theme": "规则兜底",
            "quantity_reason": str(item.get("reason", "")),
            "image_roles": [],
            "estimated_processing_count": int(
                item.get("estimated_processing_count", 0)
            ),
        }
        for product_id, item in chosen.items()
    ]


def ai_assignments(
    slot_plan: Iterable[Mapping[str, Any]], *, request_id: str
) -> list[dict[str, Any]]:
    return [
        {
            "slot_id": str(
                item.get("slot_id")
                or f"{item.get('product_id')}-slot-{index}"
            ),
            "product_id": str(item.get("product_id", "")),
            "target_ratio": str(item.get("target_ratio", "")),
            "asset_ids": [
                str(value) for value in item.get("ordered_asset_ids", [])
            ],
            "plan_source": "agent_assisted",
            "theme": str(item.get("theme", "")),
            "quantity_reason": str(item.get("quantity_reason", "")),
            "image_roles": list(item.get("image_roles", [])),
            "unused_reasons": list(item.get("unused_reasons", [])),
            "estimated_processing_count": int(
                item.get("estimated_processing_count", 0)
            ),
            "agent_request_id": request_id,
        }
        for index, item in enumerate(slot_plan, start=1)
    ]


def read_current_slot_plan(
    store: SessionStore, session_id: str
) -> dict[str, Any] | None:
    path = _path(store, session_id)
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise InteractionConflict("current slot plan is invalid") from error
    if (
        value.get("schema_version") != CURRENT_SLOT_PLAN_SCHEMA_VERSION
        or value.get("session_id") != session_id
        or value.get("workflow_state") not in WORKFLOW_STATES
    ):
        raise InteractionConflict("current slot plan is invalid")
    return value


def save_current_slot_plan(
    store: SessionStore,
    session_id: str,
    board_data: Mapping[str, Any],
    assignments: Iterable[Mapping[str, Any]],
    *,
    decision_source: str,
    context_revision: int,
    expected_plan_revision: int | None = None,
    workflow_state: str = "plan_review",
    confirmed: bool = False,
    request_id: str | None = None,
    response_sha256: str | None = None,
    fallback_reason: str | None = None,
    actor: str = "system",
) -> dict[str, Any]:
    if workflow_state not in WORKFLOW_STATES:
        raise ValueError("invalid slot workflow state")
    existing = read_current_slot_plan(store, session_id)
    current_revision = int(existing.get("plan_revision", 0)) if existing else 0
    if (
        expected_plan_revision is not None
        and int(expected_plan_revision) != current_revision
    ):
        raise InteractionConflict("CURRENT_SLOT_PLAN_REVISION_STALE")
    assignment_values = [dict(item) for item in assignments]
    normalized = validate_slot_assignments(
        assignment_values,
        board_data,
        allow_incomplete=not confirmed and workflow_state == "plan_review",
    )
    metadata = {
        str(item.get("slot_id", "")): dict(item)
        for item in assignment_values
        if isinstance(item, Mapping)
    }
    enriched = [
        {**item, **{
            key: value
            for key, value in metadata.get(item["slot_id"], {}).items()
            if key not in {"asset_ids", "ordered_asset_ids"}
        }}
        for item in normalized
    ]
    now = _now()
    document = {
        "schema_version": CURRENT_SLOT_PLAN_SCHEMA_VERSION,
        "workflow_migration_id": WORKFLOW_MIGRATION_ID,
        "session_id": session_id,
        "stage_id": "slots_copy",
        "workflow_state": workflow_state,
        "context_revision": int(context_revision),
        "plan_revision": current_revision + 1,
        "decision_source": decision_source,
        "confirmed": bool(confirmed),
        "slot_assignments": enriched,
        "agent_request_id": request_id,
        "agent_response_sha256": response_sha256,
        "fallback_from": (
            "agent_assisted" if fallback_reason else None
        ),
        "fallback_reason": fallback_reason,
        "created_at": existing.get("created_at", now) if existing else now,
        "updated_at": now,
        "updated_by": actor,
        "audit": list(existing.get("audit", [])) if existing else [],
    }
    document["audit"].append(
        {
            "plan_revision": document["plan_revision"],
            "decision_source": decision_source,
            "workflow_state": workflow_state,
            "actor": actor,
            "created_at": now,
            "request_id": request_id,
        }
    )
    store._write_json_atomic(_path(store, session_id), document)
    return document


def mark_manual_override(
    store: SessionStore,
    session_id: str,
    board_data: Mapping[str, Any],
    assignments: Iterable[Mapping[str, Any]],
    *,
    context_revision: int,
    expected_plan_revision: int,
) -> dict[str, Any]:
    existing = read_current_slot_plan(store, session_id)
    existing_source = str(existing.get("decision_source", "")) if existing else ""
    decision_source = (
        "manual_override"
        if existing_source in {"agent_assisted", "manual_override"}
        else "manual"
    )
    return save_current_slot_plan(
        store,
        session_id,
        board_data,
        assignments,
        decision_source=decision_source,
        context_revision=context_revision,
        expected_plan_revision=expected_plan_revision,
        request_id=existing.get("agent_request_id") if existing else None,
        response_sha256=(
            existing.get("agent_response_sha256") if existing else None
        ),
        actor="user",
    )


def response_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def final_outputs_sha256(processed: Mapping[str, Any]) -> str:
    identity = [
        {
            "slot_id": slot.get("slot_id"),
            "target_ratio": slot.get("target_ratio"),
            "outputs": [
                {
                    "asset_id": output.get("asset_id"),
                    "output_sha256": output.get("output_sha256"),
                }
                for output in slot.get("outputs", [])
            ],
        }
        for slot in processed.get("slots", [])
    ]
    return hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
