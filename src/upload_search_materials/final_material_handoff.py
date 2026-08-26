"""Consume the one Codex handoff created by final material selection."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from .deterministic_slot_planning import build_deterministic_slot_plan
from .interaction.session import InteractionConflict, SessionStore
from .slot_planning import build_rule_slot_plan
from .slot_workflow import save_current_slot_plan


def process_final_material_handoff(
    store: SessionStore,
    session_id: str,
    *,
    claimant_id: str = "codex-agent",
) -> dict[str, Any]:
    stage_path = store._stage_path(session_id, "asset_matching")
    handoff = store.wait_for_handoff(
        session_id,
        "asset_matching",
        timeout_seconds=0.5,
        claimant_id=claimant_id,
        reclaim_expired=True,
    )
    if handoff.get("handoff_kind") != "final_material_selection":
        raise InteractionConflict("FINAL_MATERIAL_HANDOFF_REQUIRED")
    input_path = stage_path / "input.json"
    if hashlib.sha256(input_path.read_bytes()).hexdigest() != handoff.get(
        "input_sha256"
    ):
        raise InteractionConflict("FINAL_MATERIAL_INPUT_STALE")
    package_path = stage_path / "final-material-package.json"
    package = store._read_json(package_path, "final-material-package")
    if (
        int(package.get("revision", -1)) != int(handoff["revision"])
        or package.get("input_sha256") != handoff["input_sha256"]
    ):
        raise InteractionConflict("FINAL_MATERIAL_PACKAGE_STALE")
    material_identity = {
        "folder_decisions_sha256": package.get(
            "folder_decisions_sha256"
        ),
        "folder_decisions": package.get("folder_decisions", []),
        "missing_slots_by_product": package.get(
            "missing_slots_by_product", {}
        ),
        "policy_sha256": package.get("policy_sha256"),
        "assets": package.get("assets", []),
    }
    if "removed_product_ids" in package:
        material_identity["removed_product_ids"] = package.get(
            "removed_product_ids", []
        )
    material_identity_sha256 = hashlib.sha256(
        json.dumps(
            material_identity,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if (
        material_identity_sha256
        != package.get("material_identity_sha256")
        or material_identity_sha256
        != handoff.get("final_material_package", {}).get(
            "material_identity_sha256"
        )
    ):
        raise InteractionConflict("FINAL_MATERIAL_PACKAGE_STALE")
    preflight_document = store._read_json(
        stage_path / "selected-asset-preflight.json",
        "selected-asset-preflight",
    )
    preflight = preflight_document.get("data")
    if not isinstance(preflight, dict):
        raise InteractionConflict("FINAL_MATERIAL_PREFLIGHT_MISSING")

    planning_decisions = [
        {
            "asset_id": str(item.get("asset_id", "")),
            "decision": (
                "excluded"
                if item.get("status") == "blocked"
                or item.get("duplicate") is True
                else "selected"
            ),
            "candidate_ratios": list(item.get("crop_options", {}).keys()),
        }
        for item in preflight.get("assets", [])
        if isinstance(item, dict)
    ]
    board_data = build_rule_slot_plan(
        preflight,
        planning_decisions,
        image_review_revision=0,
    )
    deterministic = build_deterministic_slot_plan(
        board_data,
        missing_slots_by_product=package.get(
            "missing_slots_by_product", {}
        ),
    )
    board_data["deterministic_plan"] = deterministic

    completed_data = {
        "workflow_step": "final_selection_processed",
        "selected_asset_preflight": {
            "revision": handoff["revision"],
            "selected_count": preflight.get("selected_count", 0),
            "reviewable_count": preflight.get("reviewable_count", 0),
            "blocked_count": preflight.get("blocked_count", 0),
            "duplicate_count": preflight.get("duplicate_count", 0),
            "policy_sha256": preflight.get("policy_sha256"),
        },
        "final_material_package": package,
    }
    store.write_result(
        session_id,
        "asset_matching",
        int(handoff["revision"]),
        str(handoff["input_sha256"]),
        status="completed",
        summary=(
            f"已校验 {preflight.get('reviewable_count', 0)} 张最终素材，"
            f"生成 {len(deterministic['assignments'])} 个坑位草稿"
        ),
        evidence=[str(package_path)],
        next_action="检查坑位草稿，确认后进行图片裁剪和压缩",
        data=completed_data,
    )
    state = store.load_session(session_id)
    context = {
        "schema_version": 1,
        "session_id": session_id,
        "stage_id": "slots_copy",
        "revision": int(state["stages"]["slots_copy"]["revision"]),
        "status": "needs_user_input",
        "summary": (
            f"已自动创建 {len(deterministic['assignments'])} 个完整坑位"
        ),
        "blocking_reasons": [],
        "evidence": [str(package_path)],
        "next_action": "检查并可人工调整坑位，然后确认进入裁剪",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "data": board_data,
    }
    store.write_review_context(session_id, "slots_copy", context)
    current_plan = save_current_slot_plan(
        store,
        session_id,
        board_data,
        deterministic["assignments"],
        decision_source="deterministic",
        context_revision=int(context["revision"]),
        workflow_state="plan_review",
        confirmed=False,
        actor="workbench",
    )
    state = store.load_session(session_id)
    state["current_stage"] = "slots_copy"
    store._write_session_state(session_id, state)
    return {
        "status": "completed",
        "session_id": session_id,
        "asset_matching_revision": int(handoff["revision"]),
        "slot_count": len(deterministic["assignments"]),
        "current_slot_plan": current_plan,
    }
