"""Deterministic post-copy dry-run and upload-confirmation preparation."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .interaction.session import InteractionConflict, SessionStore
from .io_tables import sha256_file
from .models import AssetRecord, MaterialItem, MaterialStatus
from .reporting import write_json
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
        persisted_items = store._read_json(
            publish_path / "material-items.json",
            "publish-items",
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
        try:
            response = store._read_json(
                stage_path
                / "agent-requests"
                / request_id
                / "response.json",
                "copy-response",
            )
        except InteractionConflict:
            continue
        for draft in response.get("result", {}).get("copy_drafts", []):
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
        return {
            "status": "blocked",
            "next_stage": "dry_run",
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
