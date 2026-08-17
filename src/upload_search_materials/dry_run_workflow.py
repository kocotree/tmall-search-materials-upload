"""Deterministic post-copy dry-run and upload-confirmation preparation."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .interaction.session import InteractionConflict, SessionStore
from .io_tables import sha256_file
from .models import AssetRecord, MaterialItem, MaterialStatus
from .reporting import read_json, write_json
from .state_store import StateStore
from .slot_workflow import final_outputs_sha256


REMOTE_SLOT_RECHECK_REQUIRED = "REMOTE_SLOT_RECHECK_REQUIRED"


def prepare_publish_run_from_authorization(
    store: SessionStore,
    session_id: str,
) -> dict[str, Any]:
    """Materialize the interactive dry-run as an immutable publish run.

    The interaction workflow intentionally stores upload-review tasks separately
    from the legacy CLI run files.  This adapter is the single deterministic
    bridge into the existing approve/publish implementation.
    """

    session_path = store._session_path(session_id)
    approval_path = store._stage_path(session_id, "approval")
    dry_path = store._stage_path(session_id, "dry_run")
    handoff_path = approval_path / "handoff.json"
    approval_input_path = approval_path / "input.json"
    dry_input_path = dry_path / "input.json"
    dry_result_path = dry_path / "result.json"
    dry_tasks_path = dry_path / "dry-run-tasks.json"

    handoff = store._read_json(handoff_path, "approval-handoff")
    approval_input = store._read_json(approval_input_path, "approval-input")
    dry_document = store._read_json(dry_tasks_path, "dry-run-tasks")
    authorization = handoff.get("authorization", {})
    if (
        handoff.get("handoff_kind") != "publish_authorization"
        or authorization.get("action") != "approve_and_publish_exact_tasks"
        or authorization.get("final_confirmation") is not True
    ):
        raise InteractionConflict("PUBLISH_AUTHORIZATION_INVALID")
    if handoff.get("input_sha256") != sha256_file(approval_input_path):
        raise InteractionConflict("APPROVAL_INPUT_HASH_MISMATCH")
    revision = int(handoff.get("revision", 0))
    if revision < 1:
        raise InteractionConflict("PUBLISH_AUTHORIZATION_INVALID")
    publish_path = (
        approval_path
        / "publish-runs"
        / f"r{revision:04d}-{str(handoff['input_sha256'])[:12]}"
    )
    if authorization.get("dry_run_input_sha256") != sha256_file(dry_input_path):
        raise InteractionConflict("DRY_RUN_INPUT_HASH_MISMATCH")
    if authorization.get("dry_run_result_sha256") != sha256_file(dry_result_path):
        raise InteractionConflict("DRY_RUN_RESULT_HASH_MISMATCH")
    if str(authorization.get("store", "")).strip() != str(
        dry_document.get("store", "")
    ).strip():
        raise InteractionConflict("STORE_IDENTITY_MISMATCH")

    requested = [str(value) for value in authorization.get("task_ids", [])]
    if not requested or len(requested) != len(set(requested)):
        raise InteractionConflict("PUBLISH_TASK_SELECTION_INVALID")
    tasks_by_id = {
        str(task.get("task_id", "")): task
        for task in dry_document.get("tasks", [])
        if isinstance(task, dict)
    }
    if set(requested) - set(tasks_by_id):
        raise InteractionConflict("PUBLISH_TASK_NOT_FOUND")

    artifact_paths = [
        publish_path / "run.json",
        publish_path / "product-tasks.json",
        publish_path / "material-items.json",
        publish_path / "run.sqlite3",
    ]
    existing_count = sum(path.exists() for path in artifact_paths)
    if (publish_path / "approval-manifest.json").exists() and not existing_count:
        raise InteractionConflict("PUBLISH_RUN_PARTIAL_STATE")
    if existing_count:
        if existing_count != len(artifact_paths):
            raise InteractionConflict("PUBLISH_RUN_PARTIAL_STATE")
        run = store._read_json(publish_path / "run.json", "publish-run")
        persisted_items = read_json(publish_path / "material-items.json")
        if not isinstance(persisted_items, list) or not all(
            isinstance(item, dict) for item in persisted_items
        ):
            raise InteractionConflict(
                "publish-items.json must contain a list of objects"
            )
        if (
            run.get("run_id") != f"SESSION-{session_id}-APPROVAL-{revision}"
            or str(run.get("store", "")).strip()
            != str(authorization.get("store", "")).strip()
            or {str(item.get("task_id", "")) for item in persisted_items}
            != set(requested)
        ):
            raise InteractionConflict("PUBLISH_RUN_IDENTITY_MISMATCH")
        return {
            "run_dir": str(publish_path),
            "run_id": str(run["run_id"]),
            "store": str(run["store"]),
            "confirmed_by": str(authorization.get("confirmed_by", "")).strip(),
            "task_ids": requested,
            "existing": True,
        }

    items: list[MaterialItem] = []
    for task_id in requested:
        task = tasks_by_id[task_id]
        if task.get("status") != "ready_for_review" or task.get("blocking_reasons"):
            raise InteractionConflict("PUBLISH_TASK_NOT_READY")
        assets = []
        for media in sorted(task.get("media", []), key=lambda value: value["order"]):
            path = Path(str(media.get("output_path", "")))
            expected_sha = str(media.get("output_sha256", ""))
            if not path.is_file() or sha256_file(path) != expected_sha:
                raise InteractionConflict("APPROVED_CONTENT_CHANGED")
            assets.append(
                AssetRecord(
                    product_id=str(task.get("product_id", "")),
                    source_path=str(path),
                    asset_id=f"{task_id}-{int(media['order'])}",
                    asset_type="image",
                    source_system="derived",
                    license_status="confirmed",
                    sha256=expected_sha,
                    width=media.get("width"),
                    height=media.get("height"),
                    size_bytes=media.get("size_bytes"),
                    validation_status="ready_for_review",
                )
            )
        if not assets:
            raise InteractionConflict("PUBLISH_TASK_HAS_NO_MEDIA")
        items.append(
            MaterialItem(
                task_id=task_id,
                product_id=str(task.get("product_id", "")),
                material_type="image_text",
                slot_index=int(task.get("remote_slot_position", 0)),
                status=MaterialStatus.READY_FOR_REVIEW,
                assets=assets,
                title=str(task.get("title", "")),
                description=str(task.get("description", "")),
            )
        )

    source_files = {
        "approval_input": str(approval_input_path.resolve()),
        "dry_run_input": str(dry_input_path.resolve()),
        "dry_run_result": str(dry_result_path.resolve()),
        "dry_run_tasks": str(dry_tasks_path.resolve()),
    }
    source_sha256 = {
        name: sha256_file(Path(path)) for name, path in source_files.items()
    }
    run_id = f"SESSION-{session_id}-APPROVAL-{revision}"
    run = {
        "schema_version": 1,
        "run_id": run_id,
        "store": str(authorization["store"]).strip(),
        "month": 0,
        "mode": "publish",
        "started_at": _now_iso(),
        "source_files": source_files,
        "source_sha256": source_sha256,
    }
    product_tasks = [
        {
            "task_id": f"PRODUCT-{product_id}",
            "run_id": run_id,
            "product_id": product_id,
            "status": "ready_for_review",
        }
        for product_id in sorted({item.product_id for item in items})
    ]
    write_json(publish_path / "run.json", run)
    write_json(publish_path / "product-tasks.json", product_tasks)
    write_json(publish_path / "material-items.json", items)
    state = StateStore(publish_path / "run.sqlite3")
    try:
        state.save_run(
            run_id=run_id,
            store=run["store"],
            month=0,
            mode="publish",
            status="ready_for_approval",
            config_hash=sha256_file(publish_path / "run.json"),
        )
        for item in items:
            state.save_item(item.task_id, "ready_for_review")
    finally:
        state.close()
    return {
        "run_dir": str(publish_path),
        "run_id": run_id,
        "store": run["store"],
        "confirmed_by": str(authorization.get("confirmed_by", "")).strip(),
        "task_ids": requested,
        "existing": False,
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def _set_current_stage(
    store: SessionStore,
    session_id: str,
    stage_id: str,
) -> None:
    with store._session_lock(session_id):
        state = store.load_session(session_id)
        state["current_stage"] = stage_id
        store._write_session_state(session_id, state)


def return_blocked_dry_run_to_slots_copy(
    store: SessionStore,
    session_id: str,
    *,
    blocking_reasons: list[str] | None = None,
    evidence: list[str] | None = None,
) -> dict[str, Any]:
    """Reopen the copy page and surface actionable dry-run problems there."""

    transitioned = False
    with store._session_lock(session_id):
        state = store.load_session(session_id)
        dry_state = state["stages"]["dry_run"]
        copy_state = state["stages"]["slots_copy"]
        already_returned = (
            state.get("current_stage") == "slots_copy"
            and copy_state.get("status") == "needs_user_input"
        )
        if state.get("current_stage") != "dry_run" and not already_returned:
            raise InteractionConflict("DRY_RUN_RETURN_STAGE_CHANGED")
        if not already_returned and dry_state.get("status") not in {
            "blocked",
            "needs_user_input",
        }:
            raise InteractionConflict("DRY_RUN_RETURN_STATUS_INVALID")
        if not already_returned and copy_state.get("status") != "completed":
            raise InteractionConflict("SLOTS_COPY_RETURN_STATUS_INVALID")
        if not already_returned:
            copy_state["status"] = "needs_user_input"
            state["current_stage"] = "slots_copy"
            store._write_session_state(session_id, state)
            transitioned = True

    _archive_completed_slot_result_for_reopen(store, session_id)
    _restore_slots_copy_review_context(
        store,
        session_id,
        blocking_reasons=blocking_reasons,
        evidence=evidence,
    )
    if transitioned:
        store._append_event(
            store._session_path(session_id),
            "dry_run_returned_to_slots_copy",
            session_id=session_id,
            dry_run_revision=int(dry_state["revision"]),
            slots_copy_revision=int(copy_state["revision"]),
        )
    return {
        "status": "needs_user_input",
        "next_stage": "slots_copy",
        "revision": int(copy_state["revision"]),
    }


def _archive_completed_slot_result_for_reopen(
    store: SessionStore,
    session_id: str,
) -> bool:
    """Hide the completed result from hydration while preserving its audit copy."""

    stage_path = store._stage_path(session_id, "slots_copy")
    result_path = stage_path / "result.json"
    if not result_path.is_file():
        return False
    result = store._read_json(result_path, "result")
    revision = int(result.get("revision", 0))
    archive_path = (
        stage_path
        / "reopened-after-dry-run"
        / f"r{revision:04d}-{sha256_file(result_path)[:12]}"
        / "result.json"
    )
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    if archive_path.is_file():
        raise InteractionConflict("SLOTS_COPY_REOPEN_ARCHIVE_CONFLICT")
    result_path.replace(archive_path)
    store._append_event(
        store._session_path(session_id),
        "slots_copy_result_archived_for_reopen",
        session_id=session_id,
        revision=revision,
        archive_path=str(archive_path),
    )
    return True


def _restore_slots_copy_review_context(
    store: SessionStore,
    session_id: str,
    *,
    blocking_reasons: list[str] | None = None,
    evidence: list[str] | None = None,
) -> bool:
    """Rebuild the board payload without changing the confirmed slot plan."""

    from .deterministic_slot_planning import build_deterministic_slot_plan
    from .slot_planning import build_rule_slot_plan

    asset_path = store._stage_path(session_id, "asset_matching")
    preflight_path = asset_path / "selected-asset-preflight.json"
    package_path = asset_path / "final-material-package.json"
    if not preflight_path.is_file() or not package_path.is_file():
        return False
    preflight_document = store._read_json(
        preflight_path, "selected-asset-preflight"
    )
    package = store._read_json(package_path, "final-material-package")
    preflight = preflight_document.get("data")
    if not isinstance(preflight, dict):
        return False
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
    board_data["deterministic_plan"] = build_deterministic_slot_plan(
        board_data,
        missing_slots_by_product=package.get("missing_slots_by_product", {}),
    )
    state = store.load_session(session_id)
    revision = int(state["stages"]["slots_copy"]["revision"])
    displayed_blockers = list(blocking_reasons or [])
    context = {
        "schema_version": 1,
        "session_id": session_id,
        "stage_id": "slots_copy",
        "revision": revision,
        "status": "needs_user_input",
        "summary": (
            f"上传前检查发现 {len(displayed_blockers)} 个需要修改的问题"
            if displayed_blockers
            else "已恢复当前坑位、图片输出和文案草稿，可继续编辑后重新检查。"
        ),
        "blocking_reasons": displayed_blockers,
        "evidence": list(evidence or [str(package_path)]),
        "next_action": (
            "请根据页面顶部提示修改坑位、图片或文案，然后重新确认标题与描述。"
            if displayed_blockers
            else "检查或修改文案后，重新确认并自动执行上传前检查。"
        ),
        "created_at": _now_iso(),
        "data": board_data,
    }
    store.write_review_context(session_id, "slots_copy", context)
    return True


def _copy_remote_positions(
    store: SessionStore,
    session_id: str,
    copies: dict[str, dict[str, Any]],
    *,
    outputs_identity: str,
) -> dict[str, int]:
    stage_path = store._stage_path(session_id, "slots_copy")
    request_ids = {
        str(item.get("request_id", "")).strip()
        for item in copies.values()
        if str(item.get("request_id", "")).strip()
    }
    request_root = stage_path / "agent-requests"
    if request_root.is_dir():
        for path in sorted(request_root.iterdir()):
            if not path.is_dir():
                continue
            try:
                request = store._read_json(path / "request.json", "copy-request")
            except InteractionConflict:
                continue
            context = request.get("request_context", {})
            if (
                request.get("kind") == "copy_draft"
                and request.get("status") == "completed"
                and isinstance(context, dict)
                and context.get("final_outputs_sha256") == outputs_identity
            ):
                request_ids.add(path.name)
    positions: dict[str, int] = {}
    for request_id in sorted(request_ids):
        request_path = request_root / request_id
        try:
            response = store._read_json(
                request_path / "response.json",
                "copy-response",
            )
        except InteractionConflict:
            response = None

        drafts = (
            response.get("result", {}).get("copy_drafts", [])
            if isinstance(response, dict)
            else []
        )
        if not drafts:
            # The copy processor writes every Qianniu result to progress.json
            # before it finalizes response.json.  If a downstream autosave
            # superseded the request after the last slot was generated, the
            # response is absent even though the exact remote positions are
            # already durable.  Recover only a complete, identity-matched
            # progress set; partial progress must remain blocking.
            try:
                request = store._read_json(
                    request_path / "request.json", "copy-request"
                )
                progress = store._read_json(
                    request_path / "progress.json", "copy-progress"
                )
            except InteractionConflict:
                continue
            request_context = request.get("request_context", {})
            expected_slots = {
                str(item.get("slot_id", ""))
                for item in request_context.get("slots", [])
                if isinstance(item, dict) and str(item.get("slot_id", ""))
            }
            progress_drafts = progress.get("copy_drafts", [])
            progress_slots = {
                str(item.get("slot_id", ""))
                for item in progress_drafts
                if isinstance(item, dict) and str(item.get("slot_id", ""))
            }
            total_count = int(progress.get("total_count", 0))
            completed_count = int(progress.get("completed_count", 0))
            if (
                request_context.get("final_outputs_sha256") != outputs_identity
                or not expected_slots
                or progress_slots != expected_slots
                or len(progress_drafts) != len(progress_slots)
                or completed_count != total_count
                or total_count != len(expected_slots)
            ):
                continue
            drafts = progress_drafts

        for draft in drafts:
            if not isinstance(draft, dict):
                continue
            slot_id = str(draft.get("slot_id", ""))
            copy = copies.get(slot_id)
            position = draft.get("remote_slot_position")
            if (
                copy is not None
                and str(draft.get("product_id", ""))
                == str(copy.get("product_id", ""))
                and isinstance(position, int)
                and position > 0
            ):
                positions[slot_id] = position
    return positions


def build_dry_run_document(
    store: SessionStore,
    session_id: str,
    *,
    revision: int,
    input_sha256: str,
) -> dict[str, Any]:
    """Validate the immutable local upload inputs and build exact tasks."""

    slot_path = store._stage_path(session_id, "slots_copy")
    setup_path = store._stage_path(session_id, "setup")
    slot_input = store._read_json(slot_path / "input.json", "input")
    processed = store._read_json(
        slot_path / "processed-outputs.json", "processed-outputs"
    )
    confirmed = store._read_json(
        slot_path / "confirmed-copy-drafts.json",
        "confirmed-copy-drafts",
    )
    setup_input = store.read_optional_stage_document(
        session_id, "setup", "input"
    ) or {}
    actual_input_sha256 = hashlib.sha256(
        (slot_path / "input.json").read_bytes()
    ).hexdigest()
    if actual_input_sha256 != input_sha256:
        raise ValueError("HANDOFF_INPUT_SHA256_MISMATCH")

    assignments = {
        str(item.get("slot_id", "")): item
        for item in slot_input.get("values", {}).get("slot_assignments", [])
        if isinstance(item, dict) and item.get("slot_id")
    }
    copies = {
        str(item.get("slot_id", "")): item
        for item in confirmed.get("copy_drafts", [])
        if isinstance(item, dict) and item.get("slot_id")
    }
    outputs = {
        str(item.get("slot_id", "")): item
        for item in processed.get("slots", [])
        if isinstance(item, dict) and item.get("slot_id")
    }
    positions = _copy_remote_positions(
        store,
        session_id,
        copies,
        outputs_identity=final_outputs_sha256(processed),
    )
    blocking_reasons: list[str] = []
    warnings: list[dict[str, str]] = []
    tasks: list[dict[str, Any]] = []
    seen_targets: set[tuple[str, int]] = set()

    if not assignments or set(assignments) != set(copies) or set(assignments) != set(outputs):
        blocking_reasons.append("DRY_RUN_SLOT_BOUNDARY_MISMATCH")

    for slot_id in sorted(
        assignments,
        key=lambda value: (
            str(assignments[value].get("product_id", "")),
            value,
        ),
    ):
        assignment = assignments[slot_id]
        copy = copies.get(slot_id, {})
        processed_slot = outputs.get(slot_id, {})
        product_id = str(assignment.get("product_id", ""))
        task_blockers: list[str] = []
        task_warnings: list[str] = []
        position = positions.get(slot_id)
        if not product_id or str(copy.get("product_id", "")) != product_id:
            task_blockers.append("DRY_RUN_PRODUCT_ID_MISMATCH")
        if position is None:
            task_blockers.append("DRY_RUN_REMOTE_SLOT_POSITION_MISSING")
        elif (product_id, position) in seen_targets:
            task_blockers.append("DRY_RUN_REMOTE_SLOT_POSITION_DUPLICATE")
        else:
            seen_targets.add((product_id, position))
        if (
            copy.get("confirmed") is not True
            or not str(copy.get("title", "")).strip()
            or not str(copy.get("description", "")).strip()
        ):
            task_blockers.append("DRY_RUN_COPY_NOT_CONFIRMED")

        media: list[dict[str, Any]] = []
        expected_output_sha = [
            str(value) for value in copy.get("output_sha256", [])
        ]
        actual_output_sha: list[str] = []
        for output in processed_slot.get("outputs", []):
            if not isinstance(output, dict):
                continue
            output_path = Path(str(output.get("output_path", "")))
            if not output_path.is_file():
                task_blockers.append("DRY_RUN_OUTPUT_MISSING")
                continue
            digest = hashlib.sha256(output_path.read_bytes()).hexdigest()
            if digest != str(output.get("output_sha256", "")):
                task_blockers.append("DRY_RUN_OUTPUT_SHA256_MISMATCH")
                continue
            actual_output_sha.append(digest)
            media.append(
                {
                    "order": int(output.get("order", len(media) + 1)),
                    "output_path": str(output_path),
                    "output_sha256": digest,
                    "width": int(output.get("output_width", 0)),
                    "height": int(output.get("output_height", 0)),
                    "size_bytes": int(output.get("output_size_bytes", 0)),
                }
            )
        if expected_output_sha and expected_output_sha != actual_output_sha:
            task_blockers.append("DRY_RUN_APPROVED_MEDIA_CHANGED")
        for risk in copy.get("risks", []):
            risk_text = str(risk).strip()
            if risk_text:
                task_warnings.append(risk_text)
                warnings.append(
                    {
                        "code": "COPY_REVIEW_WARNING",
                        "slot_id": slot_id,
                        "message": risk_text,
                    }
                )

        identity = (
            f"{session_id}:{product_id}:{slot_id}:{position}:"
            f"{input_sha256}"
        )
        task_id = "task-" + hashlib.sha256(
            identity.encode("utf-8")
        ).hexdigest()[:16]
        unique_blockers = sorted(set(task_blockers))
        blocking_reasons.extend(
            f"{reason}:{slot_id}" for reason in unique_blockers
        )
        tasks.append(
            {
                "task_id": task_id,
                "status": (
                    "blocked" if unique_blockers else "ready_for_review"
                ),
                "product_id": product_id,
                "slot_id": slot_id,
                "remote_slot_position": position,
                "target_ratio": str(assignment.get("target_ratio", "")),
                "title": str(copy.get("title", "")),
                "description": str(copy.get("description", "")),
                "copy_request_id": str(copy.get("request_id", "")),
                "media": media,
                "blocking_reasons": unique_blockers,
                "warnings": task_warnings,
            }
        )

    warnings.append(
        {
            "code": REMOTE_SLOT_RECHECK_REQUIRED,
            "slot_id": "",
            "message": "正式上传前仍需在千牛实时确认目标坑位为空。",
        }
    )
    return {
        "schema_version": 1,
        "session_id": session_id,
        "stage_id": "dry_run",
        "source_stage": {
            "stage_id": "slots_copy",
            "revision": revision,
            "input_sha256": input_sha256,
        },
        "status": "blocked" if blocking_reasons else "ready_for_review",
        "created_at": _now_iso(),
        "store": str(setup_input.get("values", {}).get("store", "")),
        "product_count": len(
            {task["product_id"] for task in tasks if task["product_id"]}
        ),
        "task_count": len(tasks),
        "media_count": sum(len(task["media"]) for task in tasks),
        "blocking_reasons": sorted(set(blocking_reasons)),
        "warnings": warnings,
        "tasks": tasks,
    }


def _review_context(
    document: dict[str, Any],
    *,
    stage_id: str,
    revision: int,
) -> dict[str, Any]:
    blocked = bool(document["blocking_reasons"])
    if stage_id == "approval":
        summary = (
            f"上传任务确认：{document['product_count']} 个商品、"
            f"{document['task_count']} 个坑位、"
            f"{document['media_count']} 张图片"
        )
        next_action = "选择精确任务并提交；本次提交即授权系统自动上传所选任务。"
    else:
        summary = (
            f"dry-run 发现 {len(document['blocking_reasons'])} 个阻塞项"
            if blocked
            else "dry-run 已通过"
        )
        next_action = "处理阻塞项后重新提交第四阶段。"
    return {
        "schema_version": 1,
        "session_id": document["session_id"],
        "stage_id": stage_id,
        "revision": revision,
        "status": "blocked" if blocked else "needs_user_input",
        "summary": summary,
        "blocking_reasons": document["blocking_reasons"],
        "evidence": [],
        "next_action": next_action,
        "data": document,
    }


def _slots_copy_blocking_messages(document: dict[str, Any]) -> list[str]:
    """Translate internal dry-run gates into actionable workbench guidance."""

    messages = {
        "DRY_RUN_SLOT_BOUNDARY_MISMATCH": (
            "坑位、图片输出与文案的数量不一致，请重新检查坑位编排并处理图片。"
        ),
        "DRY_RUN_PRODUCT_ID_MISMATCH": (
            "文案对应的商品与当前坑位不一致，请重新生成该坑位的标题与描述。"
        ),
        "DRY_RUN_REMOTE_SLOT_POSITION_MISSING": (
            "未取得千牛目标坑位，请重新生成或载入该坑位的标题与描述。"
        ),
        "DRY_RUN_REMOTE_SLOT_POSITION_DUPLICATE": (
            "多个坑位指向同一个千牛目标坑位，请重新生成相关坑位的标题与描述。"
        ),
        "DRY_RUN_COPY_NOT_CONFIRMED": (
            "标题或描述尚未完整确认，请补齐内容后重新勾选整批确认。"
        ),
        "DRY_RUN_OUTPUT_MISSING": (
            "已处理图片不存在，请返回图片处理并重新生成该坑位图片。"
        ),
        "DRY_RUN_OUTPUT_SHA256_MISMATCH": (
            "已处理图片发生变化，请返回图片处理并重新生成该坑位图片。"
        ),
        "DRY_RUN_APPROVED_MEDIA_CHANGED": (
            "当前图片与文案确认时的图片不一致，请重新处理图片并核对文案。"
        ),
    }
    rendered: list[str] = []
    for raw_reason in document.get("blocking_reasons", []):
        reason = str(raw_reason)
        code, separator, slot_id = reason.partition(":")
        message = messages.get(
            code,
            "上传前检查未通过，请检查当前坑位、图片处理和文案后重试。",
        )
        rendered.append(f"坑位 {slot_id}：{message}" if separator else message)
    return rendered


def advance_slots_copy_after_submit(
    store: SessionStore,
    session_id: str,
    *,
    claimant_id: str = "local-dry-run",
) -> dict[str, Any]:
    """Claim the exact slots handoff and advance to review or approval."""

    handoff = store.wait_for_handoff(
        session_id,
        "slots_copy",
        timeout_seconds=0.5,
        claimant_id=claimant_id,
        lease_seconds=180,
    )
    claim = store.processing_claim(session_id, "slots_copy") or {}
    document = build_dry_run_document(
        store,
        session_id,
        revision=int(handoff["revision"]),
        input_sha256=str(handoff["input_sha256"]),
    )
    dry_path = store._stage_path(session_id, "dry_run")
    dry_tasks_path = dry_path / "dry-run-tasks.json"
    store._write_json_atomic(dry_tasks_path, document)
    store.write_result(
        session_id,
        "slots_copy",
        int(handoff["revision"]),
        str(handoff["input_sha256"]),
        status="completed",
        summary=(
            f"第四阶段已确认 {document['task_count']} 个坑位并自动执行 dry-run"
        ),
        blocking_reasons=document["blocking_reasons"],
        evidence=[str(dry_tasks_path)],
        next_action=(
            "处理 dry-run 阻塞项"
            if document["blocking_reasons"]
            else "进入上传任务确认"
        ),
        data={
            "product_count": document["product_count"],
            "task_count": document["task_count"],
            "media_count": document["media_count"],
            "warning_count": len(document["warnings"]),
        },
        claim_id=str(claim.get("claim_id", "")) or None,
        attempt_id=str(claim.get("attempt_id", "")) or None,
    )

    if document["blocking_reasons"]:
        _set_current_stage(store, session_id, "dry_run")
        context = _review_context(document, stage_id="dry_run", revision=0)
        context["evidence"] = [str(dry_tasks_path)]
        store.write_review_context(session_id, "dry_run", context)
        returned = return_blocked_dry_run_to_slots_copy(
            store,
            session_id,
            blocking_reasons=_slots_copy_blocking_messages(document),
            evidence=[str(dry_tasks_path)],
        )
        return {
            **returned,
            "document": document,
        }

    _set_current_stage(store, session_id, "dry_run")
    dry_state = store.load_session(session_id)["stages"]["dry_run"]
    dry_handoff = store.save_input(
        session_id,
        "dry_run",
        {
            "decision": "confirm",
            "warning_notes": "自动 dry-run 无阻塞；警告将在上传任务确认页展示。",
        },
        expected_revision=int(dry_state["revision"]) + 1,
        allowed_current_statuses={"draft", "needs_user_input", "blocked"},
        request_id=f"auto-dry-run-{handoff['revision']}",
    )
    store.wait_for_handoff(
        session_id,
        "dry_run",
        timeout_seconds=0.5,
        claimant_id=claimant_id,
        lease_seconds=180,
    )
    dry_claim = store.processing_claim(session_id, "dry_run") or {}
    store.write_result(
        session_id,
        "dry_run",
        int(dry_handoff["revision"]),
        str(dry_handoff["input_sha256"]),
        status="completed",
        summary=(
            f"dry-run 自动通过：{document['product_count']} 个商品、"
            f"{document['task_count']} 个坑位、"
            f"{document['media_count']} 张图片"
        ),
        evidence=[str(dry_tasks_path)],
        next_action="进入上传任务确认",
        data=document,
        claim_id=str(dry_claim.get("claim_id", "")) or None,
        attempt_id=str(dry_claim.get("attempt_id", "")) or None,
    )
    _set_current_stage(store, session_id, "approval")
    approval_revision = int(
        store.load_session(session_id)["stages"]["approval"]["revision"]
    )
    approval_context = _review_context(
        document,
        stage_id="approval",
        revision=approval_revision,
    )
    approval_context["evidence"] = [str(dry_tasks_path)]
    store.write_review_context(session_id, "approval", approval_context)
    return {
        "status": "needs_user_input",
        "next_stage": "approval",
        "document": document,
    }


def promote_ready_dry_run_to_approval(
    store: SessionStore,
    session_id: str,
    *,
    claimant_id: str = "local-dry-run",
) -> dict[str, Any]:
    """Promote a legacy no-blocker dry-run review into upload confirmation."""

    dry_tasks_path = (
        store._stage_path(session_id, "dry_run") / "dry-run-tasks.json"
    )
    document = store._read_json(dry_tasks_path, "dry-run-tasks")
    document.setdefault("blocking_reasons", [])
    document.setdefault("warnings", [])
    if not any(
        warning.get("code") == REMOTE_SLOT_RECHECK_REQUIRED
        for warning in document["warnings"]
        if isinstance(warning, dict)
    ):
        document["warnings"].append(
            {
                "code": REMOTE_SLOT_RECHECK_REQUIRED,
                "slot_id": "",
                "message": "正式上传前仍需在千牛实时确认目标坑位为空。",
            }
        )
    for task in document.get("tasks", []):
        if isinstance(task, dict):
            task.setdefault("status", "ready_for_review")
            task.setdefault("blocking_reasons", [])
            task.setdefault("warnings", [])
    store._write_json_atomic(dry_tasks_path, document)
    if document.get("blocking_reasons"):
        return {
            "status": "blocked",
            "next_stage": "dry_run",
            "document": document,
        }
    state = store.load_session(session_id)
    if (
        state.get("current_stage") == "approval"
        and state["stages"]["approval"]["status"] == "needs_user_input"
    ):
        return {
            "status": "needs_user_input",
            "next_stage": "approval",
            "document": document,
        }
    _set_current_stage(store, session_id, "dry_run")
    dry_state = store.load_session(session_id)["stages"]["dry_run"]
    if dry_state["status"] != "completed":
        handoff = store.save_input(
            session_id,
            "dry_run",
            {
                "decision": "confirm",
                "warning_notes": "自动 dry-run 无阻塞；警告将在上传任务确认页展示。",
            },
            expected_revision=int(dry_state["revision"]) + 1,
            allowed_current_statuses={"draft", "needs_user_input", "blocked"},
            request_id="promote-existing-dry-run",
        )
        store.wait_for_handoff(
            session_id,
            "dry_run",
            timeout_seconds=0.5,
            claimant_id=claimant_id,
            lease_seconds=180,
        )
        claim = store.processing_claim(session_id, "dry_run") or {}
        store.write_result(
            session_id,
            "dry_run",
            int(handoff["revision"]),
            str(handoff["input_sha256"]),
            status="completed",
            summary=(
                f"dry-run 自动通过：{document['product_count']} 个商品、"
                f"{document['task_count']} 个坑位、"
                f"{document['media_count']} 张图片"
            ),
            evidence=[str(dry_tasks_path)],
            next_action="进入上传任务确认",
            data=document,
            claim_id=str(claim.get("claim_id", "")) or None,
            attempt_id=str(claim.get("attempt_id", "")) or None,
        )
    _set_current_stage(store, session_id, "approval")
    approval_revision = int(
        store.load_session(session_id)["stages"]["approval"]["revision"]
    )
    context = _review_context(
        document,
        stage_id="approval",
        revision=approval_revision,
    )
    context["evidence"] = [str(dry_tasks_path)]
    store.write_review_context(session_id, "approval", context)
    return {
        "status": "needs_user_input",
        "next_stage": "approval",
        "document": document,
    }
