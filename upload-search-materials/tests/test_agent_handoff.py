import json
from pathlib import Path

from PIL import Image
import pytest

from upload_search_materials.agent_handoff import (
    AgentRequestError,
    PROVIDER_ID,
    cancel_agent_request,
    claim_agent_request,
    complete_agent_request,
    create_agent_request,
    find_equivalent_agent_request,
    read_agent_request,
    read_analysis_cache,
    wait_for_agent_request,
    write_analysis_cache,
)
from upload_search_materials.interaction.session import (
    InteractionConflict,
    SessionStore,
)
from upload_search_materials.io_tables import sha256_file


def _candidate(tmp_path: Path) -> dict:
    source = tmp_path / "shared-source.jpg"
    source.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1600, 2000), "orange").save(source, quality=95)
    return {
        "asset_id": "asset-1",
        "product_id": "product-1",
        "source_path": str(source),
        "source_sha256": sha256_file(source),
        "source_system": "test",
        "ratio_options": {"3:4": {"native_ratio": False}},
    }


def test_explicit_agent_request_uses_task_local_thumbnail_and_state_machine(tmp_path):
    store = SessionStore(tmp_path / "runs")
    session = store.create_session()
    candidate = _candidate(tmp_path)

    request = create_agent_request(
        store,
        session.session_id,
        kind="slot_plan",
        candidates=[candidate],
        context_revision=0,
    )

    assert request["provider_id"] == PROVIDER_ID
    assert request["status"] == "pending_agent"
    assert candidate["source_path"] not in json.dumps(request, ensure_ascii=False)
    thumbnail = Path(request["candidates"][0]["thumbnail_path"])
    assert thumbnail.is_file()
    assert thumbnail.is_relative_to(session.path)
    assert "继续当前 upload-search-materials 任务" in request["recovery_prompt"]

    claimed = claim_agent_request(store, session.session_id, request["request_id"])
    assert claimed["status"] == "processing"
    response = complete_agent_request(
        store,
        session.session_id,
        request["request_id"],
        {
            "request_id": request["request_id"],
            "kind": "slot_plan",
            "response_model": "codex-test",
            "result": {"proposals": []},
        },
    )
    assert response["response_model"] == "codex-test"
    assert read_agent_request(
        store, session.session_id, request["request_id"]
    )["status"] == "completed"
    assert read_agent_request(
        store, session.session_id, request["request_id"]
    )["status_actor"] == "codex-agent"


def test_agent_request_budget_and_invalid_response_are_stable(tmp_path):
    store = SessionStore(tmp_path / "runs")
    session = store.create_session()
    candidate = _candidate(tmp_path)
    with pytest.raises(ValueError, match="AGENT_IMAGE_BUDGET_EXCEEDED"):
        create_agent_request(
            store,
            session.session_id,
            kind="slot_plan",
            candidates=[candidate, candidate],
            context_revision=0,
            max_images=1,
        )

    request = create_agent_request(
        store,
        session.session_id,
        kind="slot_plan",
        candidates=[candidate],
        context_revision=0,
    )
    claim_agent_request(store, session.session_id, request["request_id"])
    with pytest.raises(ValueError, match="AGENT_RESPONSE_IDENTITY_MISMATCH"):
        complete_agent_request(
            store,
            session.session_id,
            request["request_id"],
            {
                "request_id": "wrong-request",
                "kind": "slot_plan",
                "result": {"proposals": []},
            },
        )
    with pytest.raises(InteractionConflict, match="unavailable or invalid"):
        complete_agent_request(
            store,
            session.session_id,
            "missing-request",
            {},
        )


def test_analysis_cache_is_bound_to_source_provider_model_and_schema(tmp_path):
    store = SessionStore(tmp_path / "runs")
    session = store.create_session()
    source_sha256 = "a" * 64
    written = write_analysis_cache(
        store,
        session.session_id,
        source_sha256=source_sha256,
        response_model="codex-test",
        analysis_schema_version=2,
        analysis={"quality": 0.8},
    )
    assert read_analysis_cache(
        store,
        session.session_id,
        source_sha256=source_sha256,
        response_model="codex-test",
        analysis_schema_version=2,
    ) == written
    assert read_analysis_cache(
        store,
        session.session_id,
        source_sha256=source_sha256,
        response_model="codex-other",
        analysis_schema_version=2,
    ) is None


def test_agent_request_reuses_task_preview_when_shared_source_is_offline(
    tmp_path,
):
    store = SessionStore(tmp_path / "runs")
    session = store.create_session()
    candidate = _candidate(tmp_path)
    preview = (
        store._stage_path(session.session_id, "image_review")
        / "preview-cache"
        / "asset-1.jpg"
    )
    preview.parent.mkdir()
    Image.new("RGB", (480, 640), "orange").save(preview, quality=85)
    Path(candidate["source_path"]).unlink()

    request = create_agent_request(
        store,
        session.session_id,
        kind="slot_plan",
        candidates=[candidate],
        context_revision=0,
    )

    item = request["candidates"][0]
    assert item["thumbnail_origin"] == "image_review_cache"
    assert item["source_identity_sha256"] == candidate["source_sha256"]
    assert Path(item["thumbnail_path"]).is_file()


def test_unavailable_agent_thumbnail_is_stable_and_leaves_no_request(
    tmp_path,
):
    store = SessionStore(tmp_path / "runs")
    session = store.create_session()
    candidate = _candidate(tmp_path)
    Path(candidate["source_path"]).unlink()

    with pytest.raises(
        AgentRequestError, match="AGENT_THUMBNAIL_UNAVAILABLE"
    ) as raised:
        create_agent_request(
            store,
            session.session_id,
            kind="slot_plan",
            candidates=[candidate],
            context_revision=0,
        )

    assert raised.value.allowed_actions == (
        "retry_cache",
        "retry_ai",
        "use_manual",
    )
    root = store._stage_path(
        session.session_id, "slots_copy"
    ) / "agent-requests"
    assert not root.exists() or not list(root.iterdir())


def test_invalid_or_outside_task_preview_falls_back_to_shared_source(
    tmp_path,
):
    store = SessionStore(tmp_path / "runs")
    session = store.create_session()
    candidate = _candidate(tmp_path)
    corrupt = (
        store._stage_path(session.session_id, "image_review")
        / "preview-cache"
        / "asset-1.jpg"
    )
    corrupt.parent.mkdir(parents=True)
    corrupt.write_bytes(b"not-an-image")
    outside = tmp_path / "outside-preview.jpg"
    Image.new("RGB", (100, 100), "blue").save(outside)
    candidate["controlled_preview_path"] = str(outside)

    request = create_agent_request(
        store,
        session.session_id,
        kind="slot_plan",
        candidates=[candidate],
        context_revision=0,
    )

    assert request["candidates"][0]["thumbnail_origin"] == "shared_source"


def test_cancelled_request_rejects_late_completion(tmp_path):
    store = SessionStore(tmp_path / "runs")
    session = store.create_session()
    request = create_agent_request(
        store,
        session.session_id,
        kind="slot_plan",
        candidates=[_candidate(tmp_path)],
        context_revision=0,
    )
    claim_agent_request(store, session.session_id, request["request_id"])

    cancelled = cancel_agent_request(
        store, session.session_id, request["request_id"]
    )

    assert cancelled["status"] == "cancelled"
    assert cancelled["reason_code"] == "AGENT_REQUEST_CANCELLED"
    with pytest.raises(InteractionConflict, match="claimed"):
        complete_agent_request(
            store,
            session.session_id,
            request["request_id"],
            {
                "request_id": request["request_id"],
                "kind": "slot_plan",
                "result": {"proposals": []},
            },
        )


def test_valid_slot_proposal_requires_explainable_ai_fields(tmp_path):
    store = SessionStore(tmp_path / "runs")
    session = store.create_session()
    candidates = []
    for index in range(3):
        candidate = _candidate(tmp_path / f"candidate-{index}")
        candidate["asset_id"] = f"asset-{index}"
        candidates.append(candidate)
    request = create_agent_request(
        store,
        session.session_id,
        kind="slot_plan",
        candidates=candidates,
        context_revision=0,
    )
    claim_agent_request(store, session.session_id, request["request_id"])

    response = complete_agent_request(
        store,
        session.session_id,
        request["request_id"],
        {
            "request_id": request["request_id"],
            "kind": "slot_plan",
            "result": {
                "proposals": [{
                    "product_id": "product-1",
                    "slot_id": "product-1-slot-1",
                    "target_ratio": "3:4",
                    "ordered_asset_ids": [
                        "asset-0",
                        "asset-1",
                        "asset-2",
                    ],
                    "reason": "主体完整且内容互补",
                    "ai_confidence": 0.8,
                    "estimated_crop_count": 3,
                    "estimated_compression_count": 0,
                    "crop_risk": "低",
                    "diversity_summary": "包含整体和细节",
                    "duplicate_summary": "未发现重复",
                }]
            },
        },
    )

    assert response["result"]["proposals"][0]["ai_confidence"] == 0.8


def test_request_discovery_timeout_and_duplicate_claim_are_safe(tmp_path):
    store = SessionStore(tmp_path / "runs")
    session = store.create_session()
    with pytest.raises(TimeoutError, match="timed out"):
        wait_for_agent_request(
            store,
            session.session_id,
            timeout_seconds=0.01,
        )

    request = create_agent_request(
        store,
        session.session_id,
        kind="slot_plan",
        candidates=[_candidate(tmp_path)],
        context_revision=0,
    )
    first = claim_agent_request(
        store, session.session_id, request["request_id"]
    )
    second = claim_agent_request(
        store, session.session_id, request["request_id"]
    )

    assert first["status"] == second["status"] == "processing"
    assert first["claimed_at"] == second["claimed_at"]


def test_invalid_ai_response_preserves_existing_slot_draft(tmp_path):
    store = SessionStore(tmp_path / "runs")
    session = store.create_session()
    store.save_draft(
        session.session_id,
        "slots_copy",
        {
            "slot_assignments": [{
                "slot_id": "manual-slot",
                "product_id": "product-1",
                "target_ratio": "3:4",
                "asset_ids": ["manual-a", "manual-b", "manual-c"],
            }],
            "user_notes": "keep this",
        },
        "",
        expected_revision=0,
    )
    request = create_agent_request(
        store,
        session.session_id,
        kind="slot_plan",
        candidates=[_candidate(tmp_path)],
        context_revision=1,
    )
    claim_agent_request(store, session.session_id, request["request_id"])

    with pytest.raises(ValueError, match="AGENT_SLOT_PLAN_HARD_RULE_REJECTED"):
        complete_agent_request(
            store,
            session.session_id,
            request["request_id"],
            {
                "request_id": request["request_id"],
                "kind": "slot_plan",
                "result": {
                    "proposals": [{
                        "product_id": "product-1",
                        "target_ratio": "3:4",
                        "ordered_asset_ids": ["asset-1"],
                    }]
                },
            },
        )

    draft = store.read_optional_stage_document(
        session.session_id, "slots_copy", "input"
    )
    assert draft["values"]["user_notes"] == "keep this"
    assert draft["values"]["slot_assignments"][0]["slot_id"] == "manual-slot"
    assert read_agent_request(
        store, session.session_id, request["request_id"]
    )["status"] == "failed"


def test_combined_request_reuses_analysis_cache_and_is_discoverable(tmp_path):
    store = SessionStore(tmp_path / "runs")
    session = store.create_session()
    candidate = _candidate(tmp_path)
    write_analysis_cache(
        store,
        session.session_id,
        source_sha256=candidate["source_sha256"],
        response_model="codex-current-task",
        analysis_schema_version=2,
        analysis={"asset_id": "asset-1", "scene": "户外"},
    )

    request = create_agent_request(
        store,
        session.session_id,
        kind="slot_plan_with_analysis",
        candidates=[candidate],
        context_revision=0,
        request_context={"remaining_slots": {"product-1": 1}},
    )

    assert request["analysis_cache"] == {
        "hit_count": 1,
        "pending_count": 0,
        "candidate_count": 1,
    }
    assert request["candidates"][0]["cached_analysis"]["scene"] == "户外"
    assert find_equivalent_agent_request(
        store,
        session.session_id,
        kind="slot_plan_with_analysis",
        context_revision=0,
    )["request_id"] == request["request_id"]


def test_combined_response_isolates_invalid_product_and_caches_analysis(tmp_path):
    store = SessionStore(tmp_path / "runs")
    session = store.create_session()
    candidates = []
    for index in range(4):
        candidate = _candidate(tmp_path / f"candidate-{index}")
        candidate["asset_id"] = f"asset-{index}"
        candidates.append(candidate)
    request = create_agent_request(
        store,
        session.session_id,
        kind="slot_plan_with_analysis",
        candidates=candidates,
        context_revision=0,
        request_context={"remaining_slots": {"product-1": 1}},
    )
    claim_agent_request(store, session.session_id, request["request_id"])
    analyses = [
        {
            "asset_id": f"asset-{index}",
            "scene": "户外",
            "subject": "太阳镜",
            "shot_type": "全身",
            "angle": "正面",
            "pose": "站立",
            "product_visibility": "清晰",
            "visual_style": "自然",
            "quality": "高",
            "ratio_risk": "低",
            "duplicate_group": f"group-{index}",
        }
        for index in range(4)
    ]
    response = complete_agent_request(
        store,
        session.session_id,
        request["request_id"],
        {
            "request_id": request["request_id"],
            "kind": "slot_plan_with_analysis",
            "result": {
                "asset_analysis": analyses,
                "clusters": [{
                    "product_id": "product-1",
                    "target_ratio": "3:4",
                    "theme": "户外佩戴",
                    "reason": "场景一致且角度互补",
                    "asset_ids": ["asset-0", "asset-1", "asset-2"],
                }],
                "slot_plan": [
                    {
                        "product_id": "product-1",
                        "slot_id": "valid-slot",
                        "target_ratio": "3:4",
                        "theme": "户外佩戴",
                        "quantity_reason": "三张已完整覆盖主题",
                        "unused_reasons": ["第四张与封面信息重复"],
                        "estimated_crop_count": 3,
                        "estimated_compression_count": 0,
                        "ordered_asset_ids": [
                            "asset-0",
                            "asset-1",
                            "asset-2",
                        ],
                        "image_roles": [
                            {
                                "asset_id": f"asset-{index}",
                                "role": role,
                                "reason": "提供互补信息",
                                "information_gain": role,
                            }
                            for index, role in enumerate(
                                ("封面", "侧面", "动态")
                            )
                        ],
                    },
                    {
                        "product_id": "product-1",
                        "slot_id": "invalid-extra-slot",
                        "target_ratio": "3:4",
                        "theme": "重复坑位",
                        "quantity_reason": "超过剩余坑位",
                        "unused_reasons": [],
                        "estimated_crop_count": 3,
                        "estimated_compression_count": 0,
                        "ordered_asset_ids": [
                            "asset-1",
                            "asset-2",
                            "asset-3",
                        ],
                        "image_roles": [
                            {
                                "asset_id": asset_id,
                                "role": "补充",
                                "reason": "测试",
                                "information_gain": "重复验证",
                            }
                            for asset_id in ("asset-1", "asset-2", "asset-3")
                        ],
                    },
                ],
            },
        },
    )

    assert [slot["slot_id"] for slot in response["result"]["slot_plan"]] == [
        "valid-slot"
    ]
    assert response["result"]["fallback_products"][0]["product_id"] == "product-1"
    assert read_analysis_cache(
        store,
        session.session_id,
        source_sha256=candidates[0]["source_sha256"],
        response_model="codex-current-task",
        analysis_schema_version=2,
    )["analysis"]["scene"] == "户外"


def test_copy_request_binds_final_outputs_and_rejects_unsupported_claim(tmp_path):
    store = SessionStore(tmp_path / "runs")
    session = store.create_session()
    request = create_agent_request(
        store,
        session.session_id,
        kind="copy_draft",
        candidates=[],
        context_revision=0,
        request_context={
            "slot_plan_revision": 3,
            "final_outputs_sha256": "a" * 64,
            "slots": [{
                "slot_id": "slot-1",
                "trusted_source_fields": {"商品标题": "分龄成长太阳镜"},
            }],
        },
    )
    claim_agent_request(store, session.session_id, request["request_id"])
    response = complete_agent_request(
        store,
        session.session_id,
        request["request_id"],
        {
            "request_id": request["request_id"],
            "kind": "copy_draft",
            "result": {
                "copy_drafts": [{
                    "slot_id": "slot-1",
                    "title": "分龄成长太阳镜",
                    "description": "多场景佩戴图片展示，具体信息以商品页面为准。",
                    "evidence": ["商品标题", "最终坑位图片"],
                    "risks": [],
                }]
            },
        },
    )

    assert response["result"]["copy_drafts"][0]["status"] == "valid"
    assert response["result"]["copy_drafts"][0]["confirmed"] is False


@pytest.mark.parametrize(
    ("count", "expected_valid"),
    [(2, False), (4, True)],
)
def test_combined_slot_count_stops_below_three_and_accepts_four(
    tmp_path, count, expected_valid
):
    store = SessionStore(tmp_path / "runs")
    session = store.create_session()
    candidates = []
    for index in range(count):
        candidate = _candidate(tmp_path / f"c-{index}")
        candidate["asset_id"] = f"asset-{index}"
        candidates.append(candidate)
    request = create_agent_request(
        store,
        session.session_id,
        kind="slot_plan_with_analysis",
        candidates=candidates,
        context_revision=0,
        request_context={"remaining_slots": {"product-1": 1}},
    )
    claim_agent_request(store, session.session_id, request["request_id"])
    asset_ids = [f"asset-{index}" for index in range(count)]
    response = complete_agent_request(
        store,
        session.session_id,
        request["request_id"],
        {
            "request_id": request["request_id"],
            "kind": "slot_plan_with_analysis",
            "result": {
                "asset_analysis": [
                    {
                        "asset_id": asset_id,
                        "scene": "户外",
                        "subject": "商品",
                        "shot_type": "全身",
                        "angle": f"角度{index}",
                        "pose": "佩戴",
                        "product_visibility": "清晰",
                        "visual_style": "自然",
                        "quality": "高",
                        "ratio_risk": "低",
                        "duplicate_group": f"group-{index}",
                    }
                    for index, asset_id in enumerate(asset_ids)
                ],
                "clusters": [{
                    "product_id": "product-1",
                    "target_ratio": "3:4",
                    "theme": "户外",
                    "reason": "同主题互补",
                    "asset_ids": asset_ids,
                }],
                "slot_plan": [{
                    "product_id": "product-1",
                    "slot_id": "slot-1",
                    "target_ratio": "3:4",
                    "theme": "户外",
                    "quantity_reason": "每张均提供新增角度",
                    "ordered_asset_ids": asset_ids,
                    "image_roles": [
                        {
                            "asset_id": asset_id,
                            "role": f"角色{index}",
                            "reason": "互补",
                            "information_gain": f"角度{index}",
                        }
                        for index, asset_id in enumerate(asset_ids)
                    ],
                    "unused_reasons": [],
                    "estimated_crop_count": count,
                    "estimated_compression_count": 0,
                }],
            },
        },
    )
    assert bool(response["result"]["slot_plan"]) is expected_valid
    assert bool(response["result"]["fallback_products"]) is (not expected_valid)
