from __future__ import annotations

import json

from upload_search_materials.interaction.session import SessionStore
from upload_search_materials.interaction.web import create_app


def _client(tmp_path):
    app = create_app(tmp_path, enforce_stage_order=False)
    app.config.update(TESTING=True)
    store = SessionStore(tmp_path)
    session_id = store.create_session().session_id
    return app.test_client(), store, session_id


def _prepare_approval_context(store, session_id):
    store.save_draft(
        session_id,
        "setup",
        {"store": "测试店铺"},
        expected_revision=0,
    )
    dry_run_path = store._stage_path(session_id, "dry_run")
    store._write_json_atomic(
        dry_run_path / "result.json",
        {
            "schema_version": 1,
            "session_id": session_id,
            "stage_id": "dry_run",
            "revision": 1,
            "input_sha256": "d" * 64,
            "status": "completed",
        },
    )
    state = store.load_session(session_id)
    state["stages"]["dry_run"]["status"] = "completed"
    store._write_session_state(session_id, state)


def test_stage_api_exposes_component_policy_and_allowed_reasons(tmp_path):
    client, _, session_id = _client(tmp_path)
    response = client.get(f"/api/sessions/{session_id}/stages/setup")
    store_field = next(
        field for field in response.json["stage"]["fields"]
        if field["name"] == "store"
    )
    assert response.status_code == 200
    assert response.json["stage"]["component"] == "setup_form"
    assert response.json["stage"]["interaction_policy"] == "frontend_preferred"
    assert store_field["interaction_policy"] == "frontend_preferred"
    assert "UI_START_FAILED" in store_field["fallback_reason_codes"]


def test_valid_fallback_writes_authoritative_draft_and_hydrates_source(tmp_path):
    client, store, session_id = _client(tmp_path)
    response = client.post(
        f"/api/sessions/{session_id}/stages/setup/chat-fallback",
        json={
            "revision": 0,
            "mode": "draft",
            "reason_code": "UI_START_FAILED",
            "reason_detail": "bounded startup attempts exhausted",
            "actor": "codex-agent",
            "values": {"store": "测试店铺"},
        },
    )
    assert response.status_code == 200
    input_document = store.read_optional_stage_document(
        session_id, "setup", "input"
    )
    assert input_document["values"]["store"] == "测试店铺"
    assert input_document["interaction_history"][-1][
        "interaction_channel"
    ] == "chat_fallback"
    hydrated = client.get(
        f"/api/sessions/{session_id}/stages/setup"
    ).json["input"]
    assert hydrated["interaction_history"][-1][
        "fallback_reason_code"
    ] == "UI_START_FAILED"


def test_fallback_without_reason_or_for_frontend_required_field_is_rejected(tmp_path):
    client, _, session_id = _client(tmp_path)
    missing_reason = client.post(
        f"/api/sessions/{session_id}/stages/setup/chat-fallback",
        json={
            "revision": 0,
            "reason_code": "",
            "reason_detail": "none",
            "values": {"store": "测试店铺"},
        },
    )
    required_frontend = client.post(
        f"/api/sessions/{session_id}/stages/completeness/chat-fallback",
        json={
            "revision": 0,
            "reason_code": "UI_START_FAILED",
            "reason_detail": "failed",
            "values": {"selected_product_ids": ["1"]},
        },
    )
    assert missing_reason.status_code == 422
    assert required_frontend.status_code == 422
    assert "selected_product_ids" in required_frontend.json["field_errors"]


def test_fallback_rejects_stale_revision_and_field_validation(tmp_path):
    client, _, session_id = _client(tmp_path)
    good = {
        "revision": 0,
        "reason_code": "UI_UNREACHABLE",
        "reason_detail": "health endpoint failed",
        "values": {"store": "测试店铺"},
    }
    assert client.post(
        f"/api/sessions/{session_id}/stages/setup/chat-fallback", json=good
    ).status_code == 200
    stale = client.post(
        f"/api/sessions/{session_id}/stages/setup/chat-fallback", json=good
    )
    invalid = client.post(
        f"/api/sessions/{session_id}/stages/setup/chat-fallback",
        json={
            "revision": 1,
            "reason_code": "UI_UNREACHABLE",
            "reason_detail": "health endpoint failed",
            "values": {"store": 42},
        },
    )
    assert stale.status_code == 409
    assert invalid.status_code == 422


def test_schema_gap_records_frontend_task_but_not_unknown_value(tmp_path):
    client, store, session_id = _client(tmp_path)
    response = client.post(
        f"/api/sessions/{session_id}/stages/setup/chat-fallback",
        json={
            "revision": 0,
            "reason_code": "SCHEMA_GAP",
            "reason_detail": "new field has no control",
            "values": {"unsupported_future_field": "value"},
        },
    )
    gap_path = store._stage_path(session_id, "setup") / "frontend-gap.json"
    assert response.status_code == 422
    assert gap_path.is_file()
    gap = json.loads(gap_path.read_text(encoding="utf-8"))
    assert gap["missing_fields"] == ["unsupported_future_field"]
    assert store.read_optional_stage_document(session_id, "setup", "input") is None


def test_frontend_edit_preserves_fallback_audit_history(tmp_path):
    client, store, session_id = _client(tmp_path)
    response = client.post(
        f"/api/sessions/{session_id}/stages/setup/chat-fallback",
        json={
            "revision": 0,
            "reason_code": "BROWSER_OPEN_FAILED",
            "reason_detail": "URL was copied manually",
            "values": {"store": "测试店铺"},
        },
    )
    assert response.status_code == 200
    store.save_draft(
        session_id,
        "setup",
        {"store": "修改后的店铺"},
        expected_revision=1,
    )
    current = store.read_optional_stage_document(session_id, "setup", "input")
    assert current["values"]["store"] == "修改后的店铺"
    assert len(current["interaction_history"]) == 1


def test_all_structured_stage_fields_declare_a_valid_frontend_policy(tmp_path):
    client, _, session_id = _client(tmp_path)
    session = client.get(f"/api/sessions/{session_id}").json["session"]
    for stage_id in session["stages"]:
        response = client.get(
            f"/api/sessions/{session_id}/stages/{stage_id}"
        )
        assert response.status_code == 200
        stage = response.json["stage"]
        assert stage["component"]
        assert stage["interaction_policy"] in {
            "frontend_required",
            "frontend_preferred",
            "chat_fallback",
        }
        for field in stage["fields"]:
            assert field["component"]
            assert field["interaction_policy"] in {
                "frontend_required",
                "frontend_preferred",
                "chat_fallback",
            }


def test_approval_fallback_is_exact_and_rejects_broad_acknowledgement(tmp_path):
    client, store, session_id = _client(tmp_path)
    _prepare_approval_context(store, session_id)
    values = {
        "task_ids": ["task-001"],
        "confirmed_by": "tester",
    }
    accepted = client.post(
        f"/api/sessions/{session_id}/stages/approval/chat-fallback",
        json={
            "revision": 0,
            "mode": "submit",
            "reason_code": "UI_UNREACHABLE",
            "reason_detail": "approval page health failed",
            "values": values,
        },
    )
    assert accepted.status_code == 200
    document = store.read_optional_stage_document(
        session_id, "approval", "input"
    )
    audit = document["interaction_history"][-1]
    assert len(audit["safety_checklist_sha256"]) == 64
    assert audit["safety_checklist"]["store"] == "测试店铺"
    assert audit["safety_checklist"]["task_ids"] == ["task-001"]
    assert len(audit["safety_checklist"]["dry_run_result_sha256"]) == 64

    broad_client, broad_store, another_session = _client(tmp_path / "broad")
    _prepare_approval_context(broad_store, another_session)
    broad = broad_client.post(
        f"/api/sessions/{another_session}/stages/approval/chat-fallback",
        json={
            "revision": 0,
            "mode": "submit",
            "reason_code": "UI_UNREACHABLE",
            "reason_detail": "approval page health failed",
            "values": {**values, "acknowledgement": "全部继续"},
        },
    )
    assert broad.status_code == 422


def test_fallback_cannot_overwrite_submitted_or_processing_stage(tmp_path):
    client, _, session_id = _client(tmp_path)
    submitted = client.post(
        f"/api/sessions/{session_id}/stages/setup/chat-fallback",
        json={
            "revision": 0,
            "mode": "draft",
            "reason_code": "UI_START_FAILED",
            "reason_detail": "failed",
            "values": {"store": "测试店铺"},
        },
    )
    assert submitted.status_code == 200
    direct_submit = client.post(
        f"/api/sessions/{session_id}/stages/setup/submit",
        json={
            "revision": 2,
            "values": {
                "store": "测试店铺",
                "store_confirmed": True,
                "products_csv": "products.csv",
                "rules_csv": "rules.csv",
                "image_source_labels": ["来源"],
                "image_roots": [str(tmp_path)],
            },
        },
    )
    assert direct_submit.status_code == 202, direct_submit.json
    rejected = client.post(
        f"/api/sessions/{session_id}/stages/setup/chat-fallback",
        json={
            "revision": 2,
            "reason_code": "UI_UNREACHABLE",
            "reason_detail": "failed again",
            "values": {"store": "覆盖店铺"},
        },
    )
    assert rejected.status_code == 409
