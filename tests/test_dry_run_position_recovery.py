import pytest

from upload_search_materials.dry_run_workflow import (
    _slots_copy_blocking_messages,
    _copy_remote_positions,
    advance_slots_copy_after_submit,
    return_blocked_dry_run_to_slots_copy,
)
from upload_search_materials.interaction.session import InteractionConflict
from upload_search_materials.interaction.session import SessionStore


def _write_copy_request(store, session_id, *, completed_count):
    request_id = "copy-request-complete-progress"
    request_path = (
        store._stage_path(session_id, "slots_copy")
        / "agent-requests"
        / request_id
    )
    store._write_json_atomic(
        request_path / "request.json",
        {
            "schema_version": 1,
            "kind": "copy_draft",
            "status": "superseded",
            "request_context": {
                "final_outputs_sha256": "outputs-sha",
                "slots": [
                    {"slot_id": "p1-slot-1", "product_id": "p1"},
                    {"slot_id": "p1-slot-2", "product_id": "p1"},
                ],
            },
        },
    )
    store._write_json_atomic(
        request_path / "progress.json",
        {
            "schema_version": 1,
            "status": "failed",
            "completed_count": completed_count,
            "total_count": 2,
            "copy_drafts": [
                {
                    "slot_id": "p1-slot-1",
                    "product_id": "p1",
                    "remote_slot_position": 2,
                },
                {
                    "slot_id": "p1-slot-2",
                    "product_id": "p1",
                    "remote_slot_position": 4,
                },
            ],
        },
    )
    return request_id


def test_remote_positions_recover_from_complete_copy_progress(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create_session()
    request_id = _write_copy_request(
        store, session.session_id, completed_count=2
    )
    copies = {
        "p1-slot-1": {"product_id": "p1", "request_id": request_id},
        "p1-slot-2": {"product_id": "p1", "request_id": request_id},
    }

    assert _copy_remote_positions(
        store,
        session.session_id,
        copies,
        outputs_identity="outputs-sha",
    ) == {"p1-slot-1": 2, "p1-slot-2": 4}


def test_remote_positions_ignore_partial_copy_progress(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create_session()
    request_id = _write_copy_request(
        store, session.session_id, completed_count=1
    )
    copies = {
        "p1-slot-1": {"product_id": "p1", "request_id": request_id},
        "p1-slot-2": {"product_id": "p1", "request_id": request_id},
    }

    assert _copy_remote_positions(
        store,
        session.session_id,
        copies,
        outputs_identity="outputs-sha",
    ) == {}


def test_remote_positions_recover_across_copy_resume_history(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create_session()
    request_id = _write_copy_request(
        store, session.session_id, completed_count=2
    )
    request_path = (
        store._stage_path(session.session_id, "slots_copy")
        / "agent-requests"
        / request_id
    )
    store._write_json_atomic(
        request_path / "response.json",
        {
            "schema_version": 1,
            "result": {
                "copy_drafts": [
                    {
                        "slot_id": "p1-slot-1",
                        "product_id": "p1",
                    },
                    {
                        "slot_id": "p1-slot-2",
                        "product_id": "p1",
                        "remote_slot_position": 4,
                    },
                ]
            }
        },
    )
    store._write_json_atomic(
        request_path
        / "resume-history"
        / "round-001"
        / "previous-response.json",
        {
            "schema_version": 1,
            "result": {
                "copy_drafts": [
                    {
                        "slot_id": "p1-slot-1",
                        "product_id": "p1",
                        "remote_slot_position": 2,
                    }
                ]
            }
        },
    )
    copies = {
        "p1-slot-1": {"product_id": "p1", "request_id": request_id},
        "p1-slot-2": {"product_id": "p1", "request_id": request_id},
    }

    assert _copy_remote_positions(
        store,
        session.session_id,
        copies,
        outputs_identity="outputs-sha",
    ) == {"p1-slot-1": 2, "p1-slot-2": 4}


def test_remote_position_history_conflict_remains_blocked(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create_session()
    request_id = _write_copy_request(
        store, session.session_id, completed_count=2
    )
    request_path = (
        store._stage_path(session.session_id, "slots_copy")
        / "agent-requests"
        / request_id
    )
    store._write_json_atomic(
        request_path / "response.json",
        {
            "schema_version": 1,
            "result": {
                "copy_drafts": [
                    {
                        "slot_id": "p1-slot-1",
                        "product_id": "p1",
                        "remote_slot_position": 2,
                    }
                ]
            }
        },
    )
    store._write_json_atomic(
        request_path
        / "resume-history"
        / "round-001"
        / "previous-response.json",
        {
            "schema_version": 1,
            "result": {
                "copy_drafts": [
                    {
                        "slot_id": "p1-slot-1",
                        "product_id": "p1",
                        "remote_slot_position": 3,
                    }
                ]
            }
        },
    )

    assert _copy_remote_positions(
        store,
        session.session_id,
        {
            "p1-slot-1": {
                "product_id": "p1",
                "request_id": request_id,
            }
        },
        outputs_identity="outputs-sha",
    ) == {}


def test_blocked_dry_run_can_reopen_completed_copy_stage(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create_session()
    with store._session_lock(session.session_id):
        state = store.load_session(session.session_id)
        state["current_stage"] = "dry_run"
        state["stages"]["slots_copy"].update(
            {"revision": 7, "status": "completed"}
        )
        state["stages"]["dry_run"].update(
            {"revision": 0, "status": "needs_user_input"}
        )
        store._write_session_state(session.session_id, state)

    result = return_blocked_dry_run_to_slots_copy(
        store, session.session_id
    )

    assert result == {
        "status": "needs_user_input",
        "next_stage": "slots_copy",
        "revision": 7,
    }
    state = store.load_session(session.session_id)
    assert state["current_stage"] == "slots_copy"
    assert state["stages"]["slots_copy"]["status"] == "needs_user_input"


def test_dry_run_return_refuses_unrelated_current_stage(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create_session()

    with pytest.raises(InteractionConflict, match="DRY_RUN_RETURN_STAGE_CHANGED"):
        return_blocked_dry_run_to_slots_copy(store, session.session_id)


def test_blocked_automatic_dry_run_returns_to_copy_page_with_business_guidance(
    tmp_path, monkeypatch
):
    store = SessionStore(tmp_path)
    session = store.create_session()
    with store._session_lock(session.session_id):
        state = store.load_session(session.session_id)
        state["current_stage"] = "slots_copy"
        store._write_session_state(session.session_id, state)
    store.save_input(
        session.session_id,
        "slots_copy",
        {"slot_assignments": [], "copy_edits": [], "user_notes": ""},
    )
    document = {
        "schema_version": 1,
        "session_id": session.session_id,
        "stage_id": "dry_run",
        "source_stage": {},
        "status": "blocked",
        "product_count": 1,
        "task_count": 1,
        "media_count": 3,
        "blocking_reasons": [
            "DRY_RUN_REMOTE_SLOT_POSITION_MISSING:p1-slot-1"
        ],
        "warnings": [],
        "tasks": [],
    }
    captured = {}

    monkeypatch.setattr(
        "upload_search_materials.dry_run_workflow.build_dry_run_document",
        lambda *args, **kwargs: document,
    )

    def capture_restore(*args, **kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(
        "upload_search_materials.dry_run_workflow._restore_slots_copy_review_context",
        capture_restore,
    )

    result = advance_slots_copy_after_submit(store, session.session_id)

    assert result["next_stage"] == "slots_copy"
    assert result["status"] == "needs_user_input"
    state = store.load_session(session.session_id)
    assert state["current_stage"] == "slots_copy"
    assert state["stages"]["slots_copy"]["status"] == "needs_user_input"
    assert state["stages"]["dry_run"]["status"] == "needs_user_input"
    assert captured["blocking_reasons"] == [
        "坑位 p1-slot-1：未取得千牛目标坑位绑定，请重新获取该坑位信息；已有标题和描述会保留。"
    ]
    assert _slots_copy_blocking_messages(document) == captured["blocking_reasons"]
