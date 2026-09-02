import hashlib
from datetime import datetime, timezone

import pytest

from upload_search_materials.interaction.session import SessionStore
from upload_search_materials.interaction.workflow_dispatcher import (
    WorkflowDispatcher,
)
from upload_search_materials.publish_retry import (
    PublishRetryError,
    inspect_publish_retry,
    retry_failed_publish_tasks,
)
from upload_search_materials.reporting import write_json
from upload_search_materials.state_store import StateStore


def _build_blocked_publish_session(tmp_path, task_records):
    store = SessionStore(tmp_path)
    session = store.create_session(
        datetime(2026, 9, 2, 8, 0, tzinfo=timezone.utc)
    )
    session_id = session.session_id
    approval_path = store._stage_path(session_id, "approval")
    input_document = {
        "schema_version": 1,
        "session_id": session_id,
        "stage_id": "approval",
        "revision": 1,
        "created_at": "2026-09-02T16:00:00+08:00",
        "submitted_at": "2026-09-02T16:00:00+08:00",
        "values": {"task_ids": list(task_records)},
        "user_notes": "",
        "interaction_history": [],
        "persistence_request_id": "initial-approval",
    }
    store._write_json_atomic(approval_path / "input.json", input_document)
    input_sha256 = hashlib.sha256(
        (approval_path / "input.json").read_bytes()
    ).hexdigest()
    handoff = {
        "schema_version": 1,
        "status": "ready_for_agent",
        "session_id": session_id,
        "stage_id": "approval",
        "revision": 1,
        "created_at": "2026-09-02T16:00:00+08:00",
        "input_sha256": input_sha256,
        "handoff_kind": "publish_authorization",
        "authorization": {
            "action": "approve_and_publish_exact_tasks",
            "final_confirmation": True,
            "task_ids": list(task_records),
            "confirmed_by": "测试用户",
        },
    }
    store._write_json_atomic(approval_path / "handoff.json", handoff)
    store._write_json_atomic(
        approval_path / "result.json",
        {
            "schema_version": 1,
            "session_id": session_id,
            "stage_id": "approval",
            "revision": 1,
            "input_sha256": input_sha256,
            "status": "blocked",
            "summary": "上传未全部完成",
            "blocking_reasons": ["PUBLISH_BATCH_INCOMPLETE"],
            "evidence": [],
            "next_action": "继续上传未完成坑位",
            "created_at": "2026-09-02T16:05:00+08:00",
            "completed_at": "2026-09-02T16:05:00+08:00",
            "data": {"tasks": list(task_records.values())},
        },
    )
    run_path = (
        approval_path
        / "publish-runs"
        / f"r0001-{input_sha256[:12]}"
    )
    store._write_json_atomic(
        run_path / "run.json",
        {"schema_version": 1, "run_id": "RUN-1", "store": "测试店铺"},
    )
    write_json(
        run_path / "product-tasks.json",
        [{"task_id": "PRODUCT-P1", "status": "ready_for_review"}],
    )
    write_json(
        run_path / "material-items.json",
        [
            {
                "task_id": task_id,
                "product_id": record.get("product_id", "P1"),
                "slot_index": record.get("slot_index", 1),
            }
            for task_id, record in task_records.items()
        ],
    )
    store._write_json_atomic(
        run_path / "approval-manifest.json",
        {
            "schema_version": 1,
            "manifest_sha256": "a" * 64,
            "entries": [
                {"task_id": task_id}
                for task_id in task_records
            ],
        },
    )
    state_store = StateStore(run_path / "run.sqlite3")
    try:
        for task_id, record in task_records.items():
            state_store.save_item(
                task_id,
                record["status"],
                remote_material_id=record.get("remote_material_id"),
                evidence=record.get("evidence", ""),
                attempt_count=record.get("attempt_count", 0),
            )
    finally:
        state_store.close()
    state = store.load_session(session_id)
    state["current_stage"] = "approval"
    state["stages"]["approval"] = {
        "revision": 1,
        "status": "blocked",
    }
    store._write_session_state(session_id, state)
    return store, session_id, run_path


def test_retry_requeues_only_safe_pre_publish_failures(tmp_path):
    store, session_id, run_path = _build_blocked_publish_session(
        tmp_path,
        {
            "task-failed": {
                "status": "blocked",
                "evidence": (
                    "QIANNIU_FORM_NOT_READY;pre_publish_attempts=4;"
                    "skipped_after_pre_publish_retries=true"
                ),
                "product_id": "10001",
                "slot_index": 2,
                "attempt_count": 1,
            },
            "task-success": {
                "status": "submitted",
                "remote_material_id": "REMOTE-1",
                "evidence": "remote material verified",
                "product_id": "10001",
                "slot_index": 1,
                "attempt_count": 1,
            },
        },
    )

    summary = inspect_publish_retry(store, session_id)
    assert summary["can_retry"] is True
    assert summary["retryable_count"] == 1
    assert summary["successful_count"] == 1
    assert summary["retryable_tasks"][0]["task_id"] == "task-failed"

    first = retry_failed_publish_tasks(
        store,
        session_id,
        request_id="retry-request-1",
        authorized_user_name="测试用户",
    )
    duplicate = retry_failed_publish_tasks(
        store,
        session_id,
        request_id="retry-request-1",
        authorized_user_name="测试用户",
    )

    assert first["status"] == "ready_for_agent"
    assert duplicate["request_id"] == "retry-request-1"
    session_state = store.load_session(session_id)
    assert session_state["stages"]["approval"]["status"] == "ready_for_agent"
    assert session_state["stages"]["approval"]["retry_generation"] == 1
    queued = WorkflowDispatcher(
        store,
        session_id,
        lambda task: {"status": "completed"},
    )._discover_tasks()
    assert queued[0].generation == "ready-r1-g1"
    state_store = StateStore(run_path / "run.sqlite3")
    try:
        assert state_store.item_status("task-failed") == "ready_for_review"
        assert state_store.item_record("task-success")["remote_material_id"] == "REMOTE-1"
        assert state_store.transitions_for("task-failed")[-1]["reason"] == (
            "USER_RETRY_SAFE_PRE_PUBLISH_FAILURE"
        )
    finally:
        state_store.close()


def test_retry_refuses_publish_uncertain_batch(tmp_path):
    store, session_id, _ = _build_blocked_publish_session(
        tmp_path,
        {
            "task-uncertain": {
                "status": "publish_uncertain",
                "evidence": "publish click result unknown",
                "product_id": "10002",
                "slot_index": 3,
            }
        },
    )

    summary = inspect_publish_retry(store, session_id)
    assert summary["can_retry"] is False
    assert summary["uncertain_count"] == 1

    with pytest.raises(
        PublishRetryError,
        match="UPLOAD_RETRY_REMOTE_STATUS_REQUIRED",
    ):
        retry_failed_publish_tasks(
            store,
            session_id,
            request_id="retry-request-uncertain",
            authorized_user_name="测试用户",
        )


def test_retry_requires_same_authorized_user(tmp_path):
    store, session_id, _ = _build_blocked_publish_session(
        tmp_path,
        {
            "task-failed": {
                "status": "blocked",
                "evidence": (
                    "pre_publish_attempts=4;"
                    "skipped_after_pre_publish_retries=true"
                ),
            }
        },
    )

    with pytest.raises(PublishRetryError, match="UPLOAD_RETRY_USER_CHANGED"):
        retry_failed_publish_tasks(
            store,
            session_id,
            request_id="retry-request-user",
            authorized_user_name="另一位用户",
        )


def test_retry_refuses_non_retryable_blocked_task(tmp_path):
    store, session_id, _ = _build_blocked_publish_session(
        tmp_path,
        {
            "task-blocked": {
                "status": "blocked",
                "evidence": "HUMAN_CHECK_REQUIRED;pre_publish_attempts=1",
            },
            "task-not-started": {
                "status": "approved",
                "evidence": "manifest=approved",
            },
        },
    )

    summary = inspect_publish_retry(store, session_id)

    assert summary["can_retry"] is False
    assert summary["retryable_count"] == 1
    assert summary["manual_review_count"] == 1
    with pytest.raises(
        PublishRetryError,
        match="UPLOAD_RETRY_MANUAL_REVIEW_REQUIRED",
    ):
        retry_failed_publish_tasks(
            store,
            session_id,
            request_id="retry-request-blocked",
            authorized_user_name="测试用户",
        )
