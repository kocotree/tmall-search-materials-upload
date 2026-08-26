from __future__ import annotations

from threading import Event

from upload_search_materials.interaction.lifecycle import (
    DEFAULT_COMPLETION_GRACE_SECONDS,
    WorkflowCompletionMonitor,
)
from upload_search_materials.interaction.session import SessionStore
from upload_search_materials.interaction.web import create_app
from upload_search_materials.runtime_config import DiscoveredPath, RuntimeConfig


def _runtime(tmp_path, runs_root):
    return RuntimeConfig(
        workspace_root=tmp_path,
        products=DiscoveredPath(None, "missing"),
        rules=DiscoveredPath(None, "missing"),
        image_sources=(),
        runs_root=runs_root,
    )


def _write_terminal_upload(store: SessionStore, session_id: str) -> None:
    state = store.load_session(session_id)
    state["current_stage"] = "approval"
    state["stages"]["approval"].update(
        {"revision": 1, "status": "completed"}
    )
    store._write_json_atomic(
        store._session_path(session_id) / "session.json",
        state,
    )
    store._write_json_atomic(
        store._stage_path(session_id, "approval") / "result.json",
        {
            "schema_version": 1,
            "session_id": session_id,
            "stage_id": "approval",
            "revision": 1,
            "status": "completed",
            "summary": "上传任务已结束",
            "blocking_reasons": [],
            "created_at": "2026-08-15T12:00:00+08:00",
            "completed_at": "2026-08-15T12:01:00+08:00",
            "data": {
                "store": "测试店铺",
                "tasks": [
                    {
                        "task_id": "task-1",
                        "status": "submitted",
                        "remote_material_id": "remote-1",
                        "evidence": "product=886506466908;uploaded",
                    }
                ],
            },
        },
    )


def test_completion_monitor_uses_ten_minute_default_and_requests_shutdown():
    terminal = Event()
    shutdown = Event()
    monitor = WorkflowCompletionMonitor(
        lambda: {"summary": "done"} if terminal.is_set() else None,
        shutdown.set,
        grace_seconds=0.05,
        poll_seconds=0.01,
    )

    assert DEFAULT_COMPLETION_GRACE_SECONDS == 600
    monitor.start()
    terminal.set()
    try:
        assert shutdown.wait(1)
        status = monitor.public_status()
        assert status["status"] == "closing"
        assert status["terminal_summary"] == "done"
    finally:
        monitor.stop()


def test_manual_shutdown_is_delayed_long_enough_to_return_http_response():
    shutdown = Event()
    monitor = WorkflowCompletionMonitor(
        lambda: None,
        shutdown.set,
        poll_seconds=0.01,
    )

    monitor.start()
    try:
        monitor.request_manual_shutdown(delay_seconds=0.05)
        status = monitor.public_status()
        assert status["status"] == "closing"
        assert status["shutdown_reason"] == "user_requested"
        assert status["terminal_summary"] == "用户主动结束当前任务"
        assert shutdown.wait(1)
    finally:
        monitor.stop()


def test_only_terminal_upload_schedules_managed_workbench_shutdown(tmp_path):
    runs_root = tmp_path / "runs"
    store = SessionStore(runs_root)
    session = store.create_session()
    shutdown = Event()
    app = create_app(
        runs_root,
        runtime_config=_runtime(tmp_path, runs_root),
        managed_session_id=session.session_id,
        shutdown_event=shutdown,
        completion_grace_seconds=0.05,
        lifecycle_poll_seconds=0.01,
    )
    try:
        assert shutdown.wait(0.15) is False
        state = store.load_session(session.session_id)
        state["current_stage"] = "completeness"
        state["stages"]["setup"].update(
            {"revision": 1, "status": "completed"}
        )
        state["stages"]["completeness"]["status"] = "needs_user_input"
        store._write_json_atomic(
            store._session_path(session.session_id) / "session.json",
            state,
        )
        assert shutdown.wait(0.15) is False
        payload = app.test_client().get(
            f"/api/sessions/{session.session_id}"
        ).get_json()
        assert payload["task_status"]["terminal"] is False
        assert payload["task_status"]["current_stage_id"] == "completeness"
        assert payload["task_status"]["auto_shutdown"]["status"] == "watching"
    finally:
        app.extensions["tmall_workflow_lifecycle"].stop()


def test_terminal_upload_is_reported_and_closes_after_review_window(tmp_path):
    runs_root = tmp_path / "runs"
    store = SessionStore(runs_root)
    session = store.create_session()
    _write_terminal_upload(store, session.session_id)
    shutdown = Event()
    app = create_app(
        runs_root,
        runtime_config=_runtime(tmp_path, runs_root),
        managed_session_id=session.session_id,
        shutdown_event=shutdown,
        completion_grace_seconds=0.1,
        lifecycle_poll_seconds=0.01,
    )
    try:
        client = app.test_client()
        payload = client.get(
            f"/api/sessions/{session.session_id}"
        ).get_json()
        assert payload["task_status"]["terminal"] is True
        assert payload["task_status"]["phase"] == "completed"
        assert payload["task_status"]["current_stage_id"] == "results"
        assert payload["task_status"]["auto_shutdown"]["status"] in {
            "review_window",
            "closing",
        }
        assert shutdown.wait(1)
    finally:
        app.extensions["tmall_workflow_lifecycle"].stop()


def test_managed_workbench_can_end_current_task_without_deleting_data(tmp_path):
    runs_root = tmp_path / "runs"
    store = SessionStore(runs_root)
    session = store.create_session()
    shutdown = Event()
    app = create_app(
        runs_root,
        runtime_config=_runtime(tmp_path, runs_root),
        managed_session_id=session.session_id,
        shutdown_event=shutdown,
        lifecycle_poll_seconds=0.01,
    )
    try:
        response = app.test_client().post(
            f"/api/sessions/{session.session_id}/end",
            json={},
        )

        assert response.status_code == 202
        assert response.json["status"] == "closing"
        assert response.json["task_data_preserved"] is True
        assert response.json["resumable"] is True
        assert shutdown.wait(1)
        state = store.load_session(session.session_id)
        assert state["workbench_shutdown"]["reason"] == "user_requested"
        assert state["workbench_shutdown"]["resumable"] is True
        assert (store._session_path(session.session_id) / "session.json").is_file()
    finally:
        app.extensions["tmall_workflow_lifecycle"].stop()


def test_managed_workbench_refuses_to_end_while_gallery_job_is_running(tmp_path):
    runs_root = tmp_path / "runs"
    store = SessionStore(runs_root)
    session = store.create_session()
    store._write_json_atomic(
        store._stage_path(session.session_id, "asset_matching")
        / "gallery-job.json",
        {
            "schema_version": 1,
            "session_id": session.session_id,
            "status": "running",
        },
    )
    shutdown = Event()
    app = create_app(
        runs_root,
        runtime_config=_runtime(tmp_path, runs_root),
        managed_session_id=session.session_id,
        shutdown_event=shutdown,
        lifecycle_poll_seconds=0.01,
    )
    try:
        response = app.test_client().post(
            f"/api/sessions/{session.session_id}/end",
            json={},
        )

        assert response.status_code == 409
        assert response.json["reason_code"] == "TASK_END_BUSY"
        assert "等待本轮处理结束" in response.json["message"]
        assert shutdown.wait(0.15) is False
        assert "workbench_shutdown" not in store.load_session(session.session_id)
    finally:
        app.extensions["tmall_workflow_lifecycle"].stop()
