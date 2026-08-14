import hashlib
from pathlib import Path

from PIL import Image
import pytest

from upload_search_materials.agent_handoff import (
    claim_agent_request,
    complete_agent_request,
    read_agent_request,
)
from upload_search_materials.copy_draft_workflow import (
    _with_remote_slot_occurrences,
    process_copy_draft_request,
)
from upload_search_materials.image_compliance import default_image_policy
from upload_search_materials.interaction.session import (
    InteractionConflict,
    LEGACY_AI_COMPATIBILITY_PROFILE,
    SessionStore,
)
from upload_search_materials.interaction.web import create_app


def _prepared_slot_client(
    tmp_path, *, include_previews=True, asset_count=3
):
    runs = tmp_path / "runs"
    store = SessionStore(runs)
    session = store.create_session()
    # This fixture exercises the historical AI slot-planning compatibility
    # routes. New deterministic sessions deliberately reject these requests.
    legacy_state = store.load_session(session.session_id)
    legacy_state["workflow_profile"] = LEGACY_AI_COMPATIBILITY_PROFILE
    store._write_session_state(session.session_id, legacy_state)
    stage_path = store._stage_path(session.session_id, "slots_copy")
    outputs = []
    for index in range(asset_count):
        asset_id = f"asset-{index}"
        source_path = tmp_path / "offline" / f"{asset_id}.jpg"
        if include_previews:
            source_path.parent.mkdir(parents=True, exist_ok=True)
            Image.new(
                "RGB", (1440, 1920), (80 + index * 30, 120, 160)
            ).save(source_path, quality=92)
            source_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()
            source_size = source_path.stat().st_size
        else:
            source_sha256 = f"{index + 1:064x}"
            source_size = 0
        outputs.extend(
            {
                "asset_id": asset_id,
                "product_id": "P1",
                "product_title": "测试商品",
                "source_path": str(source_path),
                "source_sha256": source_sha256,
                "source_system": "test",
                "target_ratio": ratio,
                "crop_box": {"normalized": [0.0, 0.0, 1.0, 1.0]},
                "native_ratio": ratio == "3:4",
                "requires_compression": False,
                "width": 1440,
                "height": 1920,
                "size_bytes": source_size,
            }
            for ratio in ("1:1", "3:4")
        )
        if include_previews:
            preview = (
                store._stage_path(session.session_id, "image_review")
                / "preview-cache"
                / f"{asset_id}.jpg"
            )
            preview.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (480, 640), (80 + index * 30, 120, 160)).save(
                preview,
                quality=85,
            )
    policy = default_image_policy()
    policy["min_size_kb"] = 1
    store.write_review_context(
        session.session_id,
        "slots_copy",
        {
            "schema_version": 1,
            "session_id": session.session_id,
            "stage_id": "slots_copy",
            "revision": 0,
            "status": "needs_user_input",
            "summary": "ready",
            "blocking_reasons": [],
            "evidence": [],
            "next_action": "review",
            "data": {
                "slot_image_min": 3,
                "slot_image_max": 9,
                "image_review_revision": 1,
                "policy_sha256": "policy",
                "policy": policy,
                "products": [{
                    "product_id": "P1",
                    "product_title": "测试商品",
                    "outputs": outputs,
                }],
            },
        },
    )
    return (
        create_app(runs, enforce_stage_order=False).test_client(),
        store,
        session.session_id,
    )


def _valid_response(request_id):
    return {
        "request_id": request_id,
        "kind": "slot_plan",
        "response_model": "codex-test",
        "result": {
            "proposals": [{
                "product_id": "P1",
                "slot_id": "P1-ai-slot-1",
                "target_ratio": "3:4",
                "ordered_asset_ids": ["asset-0", "asset-1", "asset-2"],
                "reason": "三张图片主体完整，顺序覆盖整体与细节",
                "ai_confidence": 0.82,
                "estimated_crop_count": 0,
                "estimated_compression_count": 0,
                "crop_risk": "低",
                "diversity_summary": "整体、侧面和细节互补",
                "duplicate_summary": "未发现重复",
            }]
        },
    }


def _combined_response(request_id):
    analyses = [
        {
            "asset_id": f"asset-{index}",
            "scene": "户外",
            "subject": "商品",
            "shot_type": "全身",
            "angle": angle,
            "pose": "佩戴",
            "product_visibility": "清晰",
            "visual_style": "自然",
            "quality": "高",
            "ratio_risk": "低",
            "duplicate_group": f"group-{index}",
        }
        for index, angle in enumerate(("正面", "侧面", "动态"))
    ]
    return {
        "request_id": request_id,
        "kind": "slot_plan_with_analysis",
        "response_model": "codex-test",
        "result": {
            "asset_analysis": analyses,
            "clusters": [{
                "product_id": "P1",
                "target_ratio": "3:4",
                "theme": "户外佩戴",
                "reason": "同一主题下角度互补",
                "asset_ids": ["asset-0", "asset-1", "asset-2"],
            }],
            "slot_plan": [{
                "product_id": "P1",
                "slot_id": "P1-ai-slot-1",
                "target_ratio": "3:4",
                "theme": "户外佩戴",
                "quantity_reason": "三张已经覆盖正面、侧面和动态",
                "unused_reasons": [],
                "estimated_crop_count": 0,
                "estimated_compression_count": 0,
                "ordered_asset_ids": ["asset-0", "asset-1", "asset-2"],
                "image_roles": [
                    {
                        "asset_id": f"asset-{index}",
                        "role": role,
                        "reason": "提供互补视角",
                        "information_gain": role,
                    }
                    for index, role in enumerate(("封面", "侧面", "动态"))
                ],
            }],
        },
    }


def test_agent_web_request_response_and_adoption_only_update_draft(tmp_path):
    client, store, session_id = _prepared_slot_client(tmp_path)

    created = client.post(
        f"/api/sessions/{session_id}/stages/slots_copy/agent-requests",
        json={"max_images": 3},
    )
    assert created.status_code == 201, created.json
    request_id = created.json["request_id"]
    assert all(
        Path(item["thumbnail_path"]).is_file()
        for item in created.json["candidates"]
    )
    assert store.read_decision_mode(
        session_id, "slots_copy", "slot_plan"
    )["mode"] == "agent_assisted"

    claim_agent_request(store, session_id, request_id)
    complete_agent_request(
        store,
        session_id,
        request_id,
        _valid_response(request_id),
    )
    detail = client.get(
        f"/api/sessions/{session_id}/stages/slots_copy/"
        f"agent-requests/{request_id}"
    )
    assert detail.status_code == 200
    assert detail.json["response"]["result"]["proposals"][0][
        "ai_confidence"
    ] == 0.82
    assert detail.json["response"]["result"]["proposals"][0][
        "product_title"
    ] == "测试商品"

    adopted = client.post(
        f"/api/sessions/{session_id}/stages/slots_copy/"
        f"agent-requests/{request_id}/adopt",
        json={"proposal_index": 0},
    )
    assert adopted.status_code == 200, adopted.json
    draft = store.read_optional_stage_document(
        session_id, "slots_copy", "input"
    )
    assignment = draft["values"]["slot_assignments"][0]
    assert assignment["plan_source"] == "agent_assisted"
    assert assignment["agent_request_id"] == request_id
    assert not (store._stage_path(
        session_id, "slots_copy"
    ) / "processed-outputs.json").exists()
    assert not (store._stage_path(
        session_id, "slots_copy"
    ) / "derived").exists()


def test_combined_request_is_idempotent_and_auto_loads_only_current_draft(
    tmp_path,
):
    client, store, session_id = _prepared_slot_client(tmp_path)
    endpoint = (
        f"/api/sessions/{session_id}/stages/slots_copy/agent-requests"
    )
    first = client.post(
        endpoint,
        json={"kind": "slot_plan_with_analysis", "max_images": 3},
    )
    second = client.post(
        endpoint,
        json={"kind": "slot_plan_with_analysis", "max_images": 3},
    )
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json["request_id"] == second.json["request_id"]

    request_id = first.json["request_id"]
    claim_agent_request(store, session_id, request_id)
    complete_agent_request(
        store,
        session_id,
        request_id,
        _combined_response(request_id),
    )
    detail = client.get(
        f"{endpoint}/{request_id}"
    )
    assert detail.status_code == 200
    current = client.get(
        f"/api/sessions/{session_id}/stages/slots_copy/current-slot-plan"
    ).json["current_slot_plan"]
    assert current["decision_source"] == "agent_assisted"
    assert current["confirmed"] is False
    assert current["slot_assignments"][0]["theme"] == "户外佩戴"


def test_new_workflow_has_no_rule_endpoint_and_ai_failure_preserves_empty_plan(
    tmp_path,
):
    client, store, session_id = _prepared_slot_client(tmp_path)
    assert client.post(
        f"/api/sessions/{session_id}/stages/slots_copy/use-rules",
        json={},
    ).status_code == 404
    endpoint = (
        f"/api/sessions/{session_id}/stages/slots_copy/agent-requests"
    )
    created = client.post(
        endpoint,
        json={"kind": "slot_plan_with_analysis", "max_images": 3},
    )
    request_id = created.json["request_id"]
    request_root = (
        store._stage_path(session_id, "slots_copy")
        / "agent-requests"
        / request_id
    )
    request = read_agent_request(store, session_id, request_id)
    request["status"] = "failed"
    request["reason_code"] = "AGENT_RESPONSE_INVALID"
    store._write_json_atomic(request_root / "request.json", request)
    detail = client.get(f"{endpoint}/{request_id}")
    assert detail.status_code == 200
    assert client.get(
        f"/api/sessions/{session_id}/stages/slots_copy/current-slot-plan"
    ).json["current_slot_plan"] is None


def test_manual_incomplete_plan_can_save_but_cannot_confirm(tmp_path):
    client, _store, session_id = _prepared_slot_client(tmp_path)
    endpoint = (
        f"/api/sessions/{session_id}/stages/slots_copy/current-slot-plan"
    )
    saved = client.post(
        endpoint,
        json={
            "plan_revision": 0,
            "slot_assignments": [{
                "slot_id": "P1-manual-1",
                "product_id": "P1",
                "target_ratio": "3:4",
                "asset_ids": ["asset-0"],
                "plan_source": "manual",
            }],
        },
    )
    assert saved.status_code == 200, saved.json
    assert saved.json["current_slot_plan"]["decision_source"] == "manual"
    confirmed = client.post(
        f"{endpoint}/confirm",
        json={
            "plan_revision": saved.json["current_slot_plan"]["plan_revision"]
        },
    )
    assert confirmed.status_code == 422
    assert "IMAGE_COUNT_INVALID" in str(confirmed.json)


def test_manual_editor_persists_order_delete_and_rejects_cross_slot_duplicate(
    tmp_path,
):
    client, _store, session_id = _prepared_slot_client(
        tmp_path, asset_count=4
    )
    endpoint = (
        f"/api/sessions/{session_id}/stages/slots_copy/current-slot-plan"
    )
    first = client.post(
        endpoint,
        json={
            "plan_revision": 0,
            "slot_assignments": [
                {
                    "slot_id": "slot-a",
                    "product_id": "P1",
                    "target_ratio": "3:4",
                    "asset_ids": ["asset-1", "asset-0"],
                },
                {
                    "slot_id": "slot-b",
                    "product_id": "P1",
                    "target_ratio": "3:4",
                    "asset_ids": ["asset-2"],
                },
            ],
        },
    )
    assert first.status_code == 200, first.json
    plan = first.json["current_slot_plan"]
    assert plan["slot_assignments"][0]["asset_ids"] == [
        "asset-1",
        "asset-0",
    ]

    duplicate = client.post(
        endpoint,
        json={
            "plan_revision": plan["plan_revision"],
            "slot_assignments": [
                {
                    "slot_id": "slot-a",
                    "product_id": "P1",
                    "target_ratio": "3:4",
                    "asset_ids": ["asset-0"],
                },
                {
                    "slot_id": "slot-b",
                    "product_id": "P1",
                    "target_ratio": "3:4",
                    "asset_ids": ["asset-0"],
                },
            ],
        },
    )
    assert duplicate.status_code == 422
    assert "ASSET_ASSIGNED_TO_MULTIPLE_SLOTS" in str(duplicate.json)

    deleted = client.post(
        endpoint,
        json={
            "plan_revision": plan["plan_revision"],
            "slot_assignments": [{
                "slot_id": "slot-a",
                "product_id": "P1",
                "target_ratio": "3:4",
                "asset_ids": ["asset-0", "asset-1"],
            }],
        },
    )
    assert deleted.status_code == 200, deleted.json
    assert deleted.json["current_slot_plan"]["slot_assignments"] == [{
        **deleted.json["current_slot_plan"]["slot_assignments"][0],
        "asset_ids": ["asset-0", "asset-1"],
        "ordered_asset_ids": ["asset-0", "asset-1"],
    }]

    stale = client.post(
        endpoint,
        json={
            "plan_revision": plan["plan_revision"],
            "slot_assignments": [],
        },
    )
    assert stale.status_code == 409


def test_explicit_ai_replan_requires_exact_manual_revision_and_replaces_once(
    tmp_path,
):
    client, store, session_id = _prepared_slot_client(tmp_path)
    plan_endpoint = (
        f"/api/sessions/{session_id}/stages/slots_copy/current-slot-plan"
    )
    manual = client.post(
        plan_endpoint,
        json={
            "plan_revision": 0,
            "slot_assignments": [{
                "slot_id": "manual-slot",
                "product_id": "P1",
                "target_ratio": "3:4",
                "asset_ids": ["asset-2", "asset-1", "asset-0"],
            }],
        },
    ).json["current_slot_plan"]
    request_endpoint = (
        f"/api/sessions/{session_id}/stages/slots_copy/agent-requests"
    )
    stale = client.post(
        request_endpoint,
        json={
            "kind": "slot_plan_with_analysis",
            "force_replan": True,
            "plan_revision": manual["plan_revision"] - 1,
            "max_images": 3,
        },
    )
    assert stale.status_code == 409

    created = client.post(
        request_endpoint,
        json={
            "kind": "slot_plan_with_analysis",
            "force_replan": True,
            "plan_revision": manual["plan_revision"],
            "max_images": 3,
        },
    )
    assert created.status_code == 201, created.json
    request_id = created.json["request_id"]
    claim_agent_request(store, session_id, request_id)
    complete_agent_request(
        store,
        session_id,
        request_id,
        _combined_response(request_id),
    )
    client.get(f"{request_endpoint}/{request_id}")
    replaced = client.get(plan_endpoint).json["current_slot_plan"]
    assert replaced["decision_source"] == "agent_assisted"
    assert replaced["agent_request_id"] == request_id
    assert replaced["slot_assignments"][0]["slot_id"] == "P1-ai-slot-1"


def test_three_step_confirm_and_processing_are_separate_and_idempotent(
    tmp_path,
):
    client, store, session_id = _prepared_slot_client(tmp_path)
    requests_endpoint = (
        f"/api/sessions/{session_id}/stages/slots_copy/agent-requests"
    )
    created = client.post(
        requests_endpoint,
        json={"kind": "slot_plan_with_analysis", "max_images": 3},
    )
    request_id = created.json["request_id"]
    claim_agent_request(store, session_id, request_id)
    complete_agent_request(
        store,
        session_id,
        request_id,
        _combined_response(request_id),
    )
    client.get(f"{requests_endpoint}/{request_id}")
    current_endpoint = (
        f"/api/sessions/{session_id}/stages/slots_copy/current-slot-plan"
    )
    before = client.get(current_endpoint).json
    assert before["three_step_ui"] is True
    assert before["workflow_page"] == "compose"

    confirm_endpoint = f"{current_endpoint}/confirm"
    first_confirm = client.post(
        confirm_endpoint,
        json={
            "plan_revision": before["current_slot_plan"]["plan_revision"]
        },
    )
    assert first_confirm.status_code == 200, first_confirm.json
    confirmed_revision = first_confirm.json["current_slot_plan"][
        "plan_revision"
    ]
    assert first_confirm.json["current_slot_plan"]["workflow_state"] == (
        "plan_confirmed"
    )
    assert not (
        store._stage_path(session_id, "slots_copy")
        / "processed-outputs.json"
    ).exists()

    second_confirm = client.post(
        confirm_endpoint,
        json={"plan_revision": confirmed_revision},
    )
    assert second_confirm.status_code == 200
    assert second_confirm.json["current_slot_plan"]["plan_revision"] == (
        confirmed_revision
    )

    process_endpoint = (
        f"/api/sessions/{session_id}/stages/slots_copy/process-plan"
    )
    preflight_endpoint = (
        f"/api/sessions/{session_id}/stages/slots_copy/crop-preflight"
    )
    required_preflight = client.post(
        process_endpoint,
        json={"crop_parameters": {}},
    )
    assert required_preflight.status_code == 422
    assert "裁剪预校验" in str(required_preflight.json)
    preflight = client.post(
        preflight_endpoint,
        json={"crop_parameters": {}},
    )
    assert preflight.status_code == 200, preflight.json
    assert preflight.json["workflow_state"] == "crop_preflight_passed"
    first_process = client.post(
        process_endpoint,
        json={"crop_parameters": {}},
    )
    assert first_process.status_code == 200, first_process.json
    after_first = client.get(current_endpoint).json["current_slot_plan"]
    first_processed_mtime = (
        store._stage_path(session_id, "slots_copy")
        / "processed-outputs.json"
    ).stat().st_mtime_ns

    second_process = client.post(
        process_endpoint,
        json={"crop_parameters": {}},
    )
    assert second_process.status_code == 200, second_process.json
    assert second_process.json["processing_sha256"] == (
        first_process.json["processing_sha256"]
    )
    assert (
        store._stage_path(session_id, "slots_copy")
        / "processed-outputs.json"
    ).stat().st_mtime_ns == first_processed_mtime
    assert client.get(current_endpoint).json["current_slot_plan"][
        "plan_revision"
    ] == after_first["plan_revision"]


def test_single_crop_change_invalidates_only_its_slot_copy(tmp_path):
    client, store, session_id = _prepared_slot_client(
        tmp_path, asset_count=6
    )
    plan_endpoint = (
        f"/api/sessions/{session_id}/stages/slots_copy/current-slot-plan"
    )
    saved = client.post(
        plan_endpoint,
        json={
            "plan_revision": 0,
            "slot_assignments": [
                {
                    "slot_id": "slot-a",
                    "product_id": "P1",
                    "target_ratio": "3:4",
                    "asset_ids": ["asset-0", "asset-1", "asset-2"],
                },
                {
                    "slot_id": "slot-b",
                    "product_id": "P1",
                    "target_ratio": "3:4",
                    "asset_ids": ["asset-3", "asset-4", "asset-5"],
                },
            ],
        },
    ).json["current_slot_plan"]
    confirmed = client.post(
        f"{plan_endpoint}/confirm",
        json={"plan_revision": saved["plan_revision"]},
    )
    assert confirmed.status_code == 200, confirmed.json
    process_endpoint = (
        f"/api/sessions/{session_id}/stages/slots_copy/process-plan"
    )
    preflight_endpoint = (
        f"/api/sessions/{session_id}/stages/slots_copy/crop-preflight"
    )
    first_preflight = client.post(
        preflight_endpoint, json={"crop_parameters": {}}
    )
    assert first_preflight.status_code == 200, first_preflight.json
    first = client.post(process_endpoint, json={"crop_parameters": {}})
    assert first.status_code == 200, first.json

    stage_path = store._stage_path(session_id, "slots_copy")
    store._write_json_atomic(
        stage_path / "confirmed-copy-drafts.json",
        {
            "schema_version": 1,
            "copy_drafts": [
                {"slot_id": "slot-a", "title": "A", "description": "A"},
                {"slot_id": "slot-b", "title": "B", "description": "B"},
            ],
        },
    )
    changed_payload = {
        "crop_parameters": {
            "slot-a:asset-0": {
                "normalized_box": [0.05, 0.05, 0.95, 0.95],
                "confirm_compression": True,
            }
        }
    }
    changed_preflight = client.post(
        preflight_endpoint,
        json=changed_payload,
    )
    assert changed_preflight.status_code == 200, changed_preflight.json
    changed = client.post(process_endpoint, json=changed_payload)
    assert changed.status_code == 200, changed.json
    copy_state = SessionStore._read_json(
        stage_path / "confirmed-copy-drafts.json",
        "confirmed-copy-drafts",
    )
    assert copy_state["invalidated_slot_ids"] == ["slot-a"]
    assert [item["slot_id"] for item in copy_state["copy_drafts"]] == [
        "slot-b"
    ]


def test_copy_request_is_blocked_until_final_outputs_are_ready(tmp_path):
    client, _store, session_id = _prepared_slot_client(tmp_path)

    response = client.post(
        f"/api/sessions/{session_id}/stages/slots_copy/copy-request",
        json={},
    )

    assert response.status_code == 422
    assert "final image outputs are not ready" in str(response.json)


def test_copy_progress_autosave_does_not_supersede_open_request(tmp_path):
    client, store, session_id = _prepared_slot_client(tmp_path)
    current_endpoint = (
        f"/api/sessions/{session_id}/stages/slots_copy/current-slot-plan"
    )
    current = client.post(
        current_endpoint,
        json={
            "plan_revision": 0,
            "slot_assignments": [{
                "slot_id": "slot-a",
                "product_id": "P1",
                "target_ratio": "3:4",
                "asset_ids": ["asset-0", "asset-1", "asset-2"],
            }],
        },
    ).json["current_slot_plan"]
    assert client.post(
        f"{current_endpoint}/confirm",
        json={"plan_revision": current["plan_revision"]},
    ).status_code == 200
    initial_values = {
        # Model the real UI sequence: the stage input can still contain an
        # older pre-confirmation draft even though current-slot-plan already
        # holds the authoritative processed assignment.
        "slot_assignments": [{
            **current["slot_assignments"][0],
            "asset_ids": ["asset-2", "asset-1", "asset-0"],
            "ordered_asset_ids": ["asset-2", "asset-1", "asset-0"],
        }],
        "copy_edits": [],
    }
    initial_draft = client.post(
        f"/api/sessions/{session_id}/stages/slots_copy/draft",
        json={"revision": 0, "values": initial_values},
    )
    assert initial_draft.status_code == 200, initial_draft.json
    assert client.post(
        f"/api/sessions/{session_id}/stages/slots_copy/crop-preflight",
        json={"crop_parameters": {}},
    ).status_code == 200
    completed = client.post(
        f"/api/sessions/{session_id}/stages/slots_copy/process-plan",
        json={"crop_parameters": {}},
    )
    request_id = completed.json["copy_request_id"]
    stage_input = store.read_optional_stage_document(
        session_id, "slots_copy", "input"
    )
    values = dict(stage_input.get("values", {}))
    values["slot_assignments"] = current["slot_assignments"]
    values["copy_edits"] = [{
        "slot_id": "slot-a",
        "product_id": "P1",
        "title": "partial",
        "description": "partial draft",
        "confirmed": False,
    }]

    saved = client.post(
        f"/api/sessions/{session_id}/stages/slots_copy/draft",
        json={
            "revision": stage_input["revision"],
            "values": values,
        },
    )

    assert saved.status_code == 200, saved.json
    assert read_agent_request(
        store, session_id, request_id
    )["status"] == "pending_agent"


def _queue_copy_request(client, store, session_id):
    current_endpoint = (
        f"/api/sessions/{session_id}/stages/slots_copy/current-slot-plan"
    )
    current = client.post(
        current_endpoint,
        json={
            "plan_revision": 0,
            "slot_assignments": [
                {
                    "slot_id": "slot-a",
                    "product_id": "P1",
                    "target_ratio": "3:4",
                    "asset_ids": ["asset-0", "asset-1", "asset-2"],
                }
            ],
        },
    ).json["current_slot_plan"]
    confirmed = client.post(
        f"{current_endpoint}/confirm",
        json={"plan_revision": current["plan_revision"]},
    )
    assert confirmed.status_code == 200, confirmed.json
    preflight = client.post(
        f"/api/sessions/{session_id}/stages/slots_copy/crop-preflight",
        json={"crop_parameters": {}},
    )
    assert preflight.status_code == 200, preflight.json
    completed = client.post(
        f"/api/sessions/{session_id}/stages/slots_copy/process-plan",
        json={"crop_parameters": {}},
    )
    assert completed.status_code == 200, completed.json
    return completed.json["copy_request_id"]


def test_image_completion_queues_copy_for_codex_and_reuses_existing_playwright(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace
    import upload_search_materials.copy_draft_workflow as workflow

    client, store, session_id = _prepared_slot_client(tmp_path)
    request_id = _queue_copy_request(client, store, session_id)
    request_document = read_agent_request(store, session_id, request_id)
    assert request_document["kind"] == "copy_draft"
    assert request_document["status"] == "pending_agent"
    assert "process-copy-request" in request_document["recovery_prompt"]
    assert (
        "禁止在聊天中再次索取同意、授权或确认"
        in request_document["recovery_prompt"]
    )
    authorization = request_document["request_context"]["authorization"]
    assert authorization["status"] == "granted"
    assert authorization["source"] == "workbench_image_completion"
    assert authorization["requires_chat_confirmation"] is False
    assert authorization["publish_allowed"] is False
    assert authorization["final_outputs_sha256"] == request_document[
        "request_context"
    ]["final_outputs_sha256"]
    first_output = request_document["request_context"]["slots"][0][
        "ordered_outputs"
    ][0]
    assert authorization["seed_images"] == [{
        "slot_id": "slot-a",
        "product_id": "P1",
        "asset_id": "asset-0",
        "output_sha256": first_output["output_sha256"],
        "output_path": first_output["output_path"],
        "order": first_output["order"],
    }]
    assert len(authorization["authorization_sha256"]) == 64

    calls = []

    def fake_generate(page, slots, *, material_center_url):
        calls.append(
            (
                page,
                slots[0]["slot_id"],
                material_center_url,
                slots[0]["remote_slot_occurrence"],
            )
        )
        slot = slots[0]
        return [
            {
                "slot_id": slot["slot_id"],
                "product_id": slot["product_id"],
                "title": "KK树儿童防晒帽",
                "description": "轻盈舒适，适合儿童日常户外防晒使用。",
                "evidence": ["千牛商品坑位内置 AI 生成"],
                "risks": [],
                "source": "qianniu_builtin_ai",
            }
        ]

    monkeypatch.setattr(workflow, "generate_qianniu_copy_drafts", fake_generate)
    page = object()
    response = process_copy_draft_request(
        store,
        session_id,
        request_id,
        runtime=SimpleNamespace(
            cdp_url="http://127.0.0.1:9222",
            material_center_url="https://example.test/materials",
        ),
        page=page,
    )
    assert response["result"]["copy_drafts"]
    assert calls and all(call[0] is page for call in calls)
    assert [call[3] for call in calls] == [0]
    assert read_agent_request(store, session_id, request_id)["status"] == "completed"
    detail = client.get(
        f"/api/sessions/{session_id}/stages/slots_copy/agent-requests/{request_id}"
    ).json
    assert detail["progress"]["status"] == "completed"
    assert detail["progress"]["completed_count"] == detail["progress"]["total_count"]


def test_copy_processor_rejects_tampered_authorization_before_browser(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace
    import upload_search_materials.copy_draft_workflow as workflow

    client, store, session_id = _prepared_slot_client(tmp_path)
    request_id = _queue_copy_request(client, store, session_id)
    request = read_agent_request(store, session_id, request_id)
    request["request_context"]["authorization"]["publish_allowed"] = True
    request_path = (
        store._stage_path(session_id, "slots_copy")
        / "agent-requests"
        / request_id
        / "request.json"
    )
    store._write_json_atomic(request_path, request)

    monkeypatch.setattr(
        workflow,
        "generate_qianniu_copy_drafts",
        lambda *_args, **_kwargs: pytest.fail("browser must not run"),
    )
    with pytest.raises(
        InteractionConflict, match="COPY_DRAFT_AUTHORIZATION_INVALID"
    ):
        process_copy_draft_request(
            store,
            session_id,
            request_id,
            runtime=SimpleNamespace(
                cdp_url="http://127.0.0.1:9222",
                material_center_url="https://example.test/materials",
            ),
            page=object(),
        )
    assert read_agent_request(store, session_id, request_id)["status"] == (
        "pending_agent"
    )


def test_copy_processor_upgrades_legacy_request_without_chat_confirmation(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace
    import upload_search_materials.copy_draft_workflow as workflow

    client, store, session_id = _prepared_slot_client(tmp_path)
    request_id = _queue_copy_request(client, store, session_id)
    request_path = (
        store._stage_path(session_id, "slots_copy")
        / "agent-requests"
        / request_id
        / "request.json"
    )
    request = SessionStore._read_json(request_path, "request")
    request["request_context"].pop("authorization")
    request["recovery_prompt"] = "旧版恢复提示"
    store._write_json_atomic(request_path, request)

    projected = read_agent_request(store, session_id, request_id)
    assert "禁止在聊天中再次索取同意、授权或确认" in projected[
        "recovery_prompt"
    ]

    def fake_generate(_page, slots, *, material_center_url):
        assert material_center_url == "https://example.test/materials"
        return [{
            "slot_id": slots[0]["slot_id"],
            "product_id": slots[0]["product_id"],
            "title": "兼容旧请求",
            "description": "无需重新创建任务。",
            "evidence": ["千牛商品坑位内置 AI 生成"],
            "risks": [],
            "source": "qianniu_builtin_ai",
        }]

    monkeypatch.setattr(workflow, "generate_qianniu_copy_drafts", fake_generate)
    response = process_copy_draft_request(
        store,
        session_id,
        request_id,
        runtime=SimpleNamespace(
            cdp_url="http://127.0.0.1:9222",
            material_center_url="https://example.test/materials",
        ),
        page=object(),
    )
    assert response["result"]["copy_drafts"][0]["title"] == "兼容旧请求"
    upgraded = SessionStore._read_json(request_path, "request")
    assert upgraded["request_context"]["authorization"]["source"] == (
        "legacy_workbench_copy_request"
    )
    assert upgraded["request_context"]["authorization"][
        "requires_chat_confirmation"
    ] is False


def test_copy_slots_keep_distinct_remote_occurrences_across_checkpoint_calls():
    prepared = _with_remote_slot_occurrences(
        [
            {"slot_id": "p1-a", "product_id": "p1"},
            {"slot_id": "p2-a", "product_id": "p2"},
            {"slot_id": "p1-b", "product_id": "p1"},
            {
                "slot_id": "p1-fixed",
                "product_id": "p1",
                "remote_slot_position": 9,
            },
            {"slot_id": "p1-c", "product_id": "p1"},
        ]
    )

    assert [item.get("remote_slot_occurrence") for item in prepared] == [
        0,
        0,
        1,
        None,
        3,
    ]


def test_copy_request_completion_allows_frontend_progress_autosave(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace
    import upload_search_materials.copy_draft_workflow as workflow

    client, store, session_id = _prepared_slot_client(tmp_path)
    current_endpoint = (
        f"/api/sessions/{session_id}/stages/slots_copy/current-slot-plan"
    )
    current = client.post(
        current_endpoint,
        json={
            "plan_revision": 0,
            "slot_assignments": [{
                "slot_id": "slot-a",
                "product_id": "P1",
                "target_ratio": "3:4",
                "asset_ids": ["asset-0", "asset-1", "asset-2"],
            }],
        },
    ).json["current_slot_plan"]
    confirmed = client.post(
        f"{current_endpoint}/confirm",
        json={"plan_revision": current["plan_revision"]},
    )
    assert confirmed.status_code == 200, confirmed.json
    preflight = client.post(
        f"/api/sessions/{session_id}/stages/slots_copy/crop-preflight",
        json={"crop_parameters": {}},
    )
    assert preflight.status_code == 200, preflight.json
    processed = client.post(
        f"/api/sessions/{session_id}/stages/slots_copy/process-plan",
        json={"crop_parameters": {}},
    )
    assert processed.status_code == 200, processed.json
    request_id = processed.json["copy_request_id"]
    request_revision = read_agent_request(
        store, session_id, request_id
    )["context_revision"]
    autosaved = False

    def fake_generate(page, slots, *, material_center_url):
        nonlocal autosaved
        if not autosaved:
            state = store.load_session(session_id)
            store.save_draft(
                session_id,
                "slots_copy",
                {},
                "",
                expected_revision=int(
                    state["stages"]["slots_copy"]["revision"]
                ),
                request_id="copy-progress-autosave",
            )
            autosaved = True
        slot = slots[0]
        return [{
            "slot_id": slot["slot_id"],
            "product_id": slot["product_id"],
            "title": "KK树儿童防晒帽",
            "description": "轻盈舒适，适合儿童日常户外防晒使用。",
            "evidence": ["千牛商品坑位内置 AI 生成"],
            "risks": [],
            "source": "qianniu_builtin_ai",
            "remote_slot_position": 1,
        }]

    monkeypatch.setattr(workflow, "generate_qianniu_copy_drafts", fake_generate)
    response = process_copy_draft_request(
        store,
        session_id,
        request_id,
        runtime=SimpleNamespace(
            cdp_url="http://127.0.0.1:9222",
            material_center_url="https://example.test/materials",
        ),
        page=object(),
    )

    assert response["result"]["copy_drafts"]
    assert (
        store.load_session(session_id)["stages"]["slots_copy"]["revision"]
        > request_revision
    )
    assert read_agent_request(store, session_id, request_id)["status"] == "completed"


def test_agent_web_thumbnail_failure_is_structured_and_atomic(tmp_path):
    client, store, session_id = _prepared_slot_client(
        tmp_path, include_previews=False
    )

    response = client.post(
        f"/api/sessions/{session_id}/stages/slots_copy/agent-requests",
        json={"max_images": 3},
    )

    assert response.status_code == 422
    assert response.json["reason_code"] == "AGENT_THUMBNAIL_UNAVAILABLE"
    assert response.json["allowed_actions"] == [
        "retry_cache",
        "retry_ai",
        "use_manual",
    ]
    request_root = store._stage_path(
        session_id, "slots_copy"
    ) / "agent-requests"
    assert not request_root.exists() or not list(request_root.iterdir())


def test_switching_from_ai_cancels_request_and_preserves_manual_draft(tmp_path):
    client, store, session_id = _prepared_slot_client(tmp_path)
    draft = store.save_draft(
        session_id,
        "slots_copy",
        {
            "slot_assignments": [{
                "slot_id": "manual-slot",
                "product_id": "P1",
                "target_ratio": "3:4",
                "asset_ids": ["asset-0", "asset-1", "asset-2"],
            }],
            "user_notes": "keep me",
        },
        "",
        expected_revision=0,
    )
    created = client.post(
        f"/api/sessions/{session_id}/stages/slots_copy/agent-requests",
        json={"max_images": 3},
    )
    assert created.status_code == 201, created.json

    switched = client.post(
        f"/api/sessions/{session_id}/stages/slots_copy/"
        "decisions/slot_plan/mode",
        json={"mode": "manual", "revision": draft["revision"]},
    )

    assert switched.status_code == 200, switched.json
    assert read_agent_request(
        store, session_id, created.json["request_id"]
    )["status"] == "cancelled"
    preserved = store.read_optional_stage_document(
        session_id, "slots_copy", "input"
    )
    assert preserved["values"]["user_notes"] == "keep me"
    assert preserved["values"]["slot_assignments"][0][
        "slot_id"
    ] == "manual-slot"


def test_editing_slot_revision_supersedes_open_ai_request(tmp_path):
    client, store, session_id = _prepared_slot_client(tmp_path)
    created = client.post(
        f"/api/sessions/{session_id}/stages/slots_copy/agent-requests",
        json={"max_images": 3},
    )
    assert created.status_code == 201, created.json

    saved = client.post(
        f"/api/sessions/{session_id}/stages/slots_copy/draft",
        json={
            "revision": 0,
            "values": {
                "slot_assignments": [{
                    "slot_id": "manual-slot",
                    "product_id": "P1",
                    "target_ratio": "3:4",
                    "asset_ids": ["asset-0", "asset-1", "asset-2"],
                }],
                "user_notes": "changed while AI was pending",
            },
        },
    )

    assert saved.status_code == 200, saved.json
    request = read_agent_request(
        store, session_id, created.json["request_id"]
    )
    assert request["status"] == "superseded"
    assert request["reason_code"] == "AGENT_REQUEST_SUPERSEDED"
