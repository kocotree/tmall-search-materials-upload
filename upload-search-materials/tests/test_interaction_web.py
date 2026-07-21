import html as html_module
import json
import re
import subprocess
from pathlib import Path

import pytest

from upload_search_materials.interaction.web import create_app
from upload_search_materials.interaction.session import SessionStore
from upload_search_materials.interaction.stages import STAGES


@pytest.fixture
def client(tmp_path):
    return create_app(tmp_path).test_client()


@pytest.fixture
def session_id(client):
    return client.post("/api/sessions", json={}).json["session_id"]


def valid_production_confirmation():
    return {
        "store": "测试店铺",
        "product_ids": ["887508274682"],
        "task_ids": ["run-product-image-1"],
        "slot_ids": ["image-1"],
        "max_products": 1,
        "approval_manifest_sha256": "a" * 64,
        "final_confirmation": True,
        "notes": "仅生成交接，不在页面执行发布",
    }


def test_create_session_returns_timestamp_id(client):
    response = client.post("/api/sessions", json={})

    assert response.status_code == 201
    assert re.fullmatch(r"\d{8}_\d{6}(?:_\d{2})?", response.json["session_id"])


def test_root_renders_ten_stage_left_rail(client):
    response = client.get("/")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert response.mimetype == "text/html"
    assert html.count('data-stage-id="') == 10
    assert "完整度巡检" in html
    assert "生产确认" in html


def test_api_is_json_service_description(client):
    response = client.get("/api")

    assert response.status_code == 200
    assert response.is_json
    assert response.json["service"] == "upload-search-materials interaction API"


def test_every_interactive_field_has_named_control(client):
    html = client.get("/").get_data(as_text=True)

    for stage in STAGES:
        for field in stage.fields:
            assert f'name="{field.name}"' in html


def test_page_explains_agent_offline_recovery(client):
    html = client.get("/").get_data(as_text=True)

    assert "Agent 未连接" in html
    assert "复制恢复指令" in html
    assert "当前任务目录" in html
    assert "当前阶段" in html
    assert "最近一次提交时间" in html


def test_page_has_all_reusable_stage_renderers_and_exact_match_labels(client):
    html = client.get("/").get_data(as_text=True)
    components = {
        "InspectionMatrix",
        "ProductScopeTable",
        "AssetMatchGallery",
        "CropDecision",
        "SlotBoard",
        "CopyEditor",
        "DryRunSummary",
        "ApprovalChecklist",
        "ProductionConfirmation",
        "ResultTimeline",
    }

    for component in components:
        assert f'data-component="{component}"' in html
    for label in ("商品 ID 命中", "SKU 命中", "已确认别名", "名称候选 · 待确认"):
        assert label in html


def test_page_uses_explicit_empty_states_without_fabricated_counts(client):
    html = client.get("/").get_data(as_text=True)

    assert html.count('data-empty-state="尚未扫描"') >= 10
    assert "18 张" not in html


def test_asset_matching_prefills_three_editable_labeled_image_roots(client):
    decoded = html_module.unescape(client.get("/").get_data(as_text=True))
    expected_roots = (
        r"Y:\视觉部\1-模特图",
        r"Z:\浙江酷趣\运营中心\营销板块\小红书koc置换&淘宝买家秀\优质买家秀",
        r"Z:\浙江酷趣\运营中心\营销板块\小红书koc置换&买家秀\优质买家秀",
    )

    for root in expected_roots:
        assert f'value="{root}"' in decoded
    source_labels = re.findall(r'data-source-label="([^"]+)"', decoded)
    assert len(source_labels) == 3
    assert len(set(source_labels)) == 3
    assert decoded.count('name="image_roots"') == 3


def test_video_control_is_visible_disabled_and_deferred(client):
    html = client.get("/").get_data(as_text=True)

    assert re.search(r'<input[^>]+name="include_video"[^>]+disabled', html)
    assert "本轮测试延期" in html


def test_page_receives_optional_session_id(client, session_id):
    html = client.get(f"/?session_id={session_id}").get_data(as_text=True)

    assert f'data-session-id="{session_id}"' in html


def test_javascript_uses_task_three_api_and_precise_status_copy(client):
    javascript = "\n".join(
        client.get(path).get_data(as_text=True)
        for path in ("/static/ui-state.js", "/static/app.js")
    )

    assert "编辑中" in javascript
    assert "已提交，等待 Agent" in javascript
    assert "Agent 处理中" in javascript
    assert "补充后重新提交" in javascript
    assert "Agent 未连接" in javascript
    assert "2000" in javascript
    assert "/api/sessions" in javascript
    assert "/draft" in javascript
    assert "/submit" in javascript
    assert "/status" in javascript
    assert "/recovery" in javascript
    for endpoint in re.findall(r"fetch\(([^,)]+)", javascript):
        assert "publish" not in endpoint.lower()
        assert "upload" not in endpoint.lower()
    assert "subprocess" not in javascript.lower()
    assert "playwright" not in javascript.lower()


def test_javascript_selects_result_renderers_by_schema_component(client):
    javascript = client.get("/static/app.js").get_data(as_text=True)

    assert "resultRenderers" in javascript
    for component in {stage.component for stage in STAGES if stage.id != "setup"}:
        assert f'"{component}"' in javascript


def test_results_recovery_form_is_hidden_and_disabled_by_default(client):
    html = client.get("/").get_data(as_text=True)

    assert re.search(r'<form[^>]+data-results-recovery[^>]+hidden', html)
    for field_name in ("recovery_action", "manual_notes", "allow_retry_after_remote_absence"):
        assert re.search(rf'<(?:input|select|textarea)[^>]+name="{field_name}"[^>]+disabled', html)


def test_result_modules_keep_persistent_content_containers(client):
    html = client.get("/").get_data(as_text=True)

    assert html.count("data-result-content") == 10


def test_image_roots_are_grouped_and_controls_describe_stable_field_errors(client):
    html = client.get("/").get_data(as_text=True)

    assert re.search(
        r'<fieldset[^>]+data-field-group="image_roots"[^>]*>.*?<legend>\s*图片源路径',
        html,
        re.DOTALL,
    )
    for stage in STAGES:
        for field in stage.fields:
            error_id = f"{stage.id}-{field.name}-error"
            assert f'id="{error_id}"' in html
            assert f'aria-describedby="{error_id}"' in html


def test_compact_styles_keep_result_tables_scrollable_above_fixed_handoff(client):
    stylesheet = client.get("/static/app.css").get_data(as_text=True)

    assert "@media (max-width: 1120px)" in stylesheet
    assert ".table-scroll" in stylesheet
    assert "overflow-x: auto" in stylesheet
    assert ".handoff-spacer" in stylesheet


def test_results_stage_rejects_ordinary_submission_without_creating_handoff(
    client, session_id, tmp_path
):
    response = client.post(
        f"/api/sessions/{session_id}/stages/results/submit",
        json={"values": {}},
    )

    assert response.status_code == 409
    results_directory = tmp_path / session_id / "10-results"
    assert not (results_directory / "input.json").exists()
    assert not (results_directory / "handoff.json").exists()


@pytest.mark.parametrize("status", ["needs_user_input", "blocked"])
def test_results_stage_accepts_recovery_only_when_session_status_requires_user_action(
    client, session_id, tmp_path, status
):
    state_path = tmp_path / session_id / "session.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["stages"]["results"]["status"] = status
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    response = client.post(
        f"/api/sessions/{session_id}/stages/results/submit",
        json={
            "values": {
                "recovery_action": "retry",
                "manual_notes": "已核对远端状态",
                "allow_retry_after_remote_absence": True,
            }
        },
    )

    assert response.status_code == 202


@pytest.mark.parametrize(
    ("data", "content_type", "status"),
    [(b"{}", None, 415), (b"{", "application/json", 400)],
)
def test_json_body_errors_are_json_with_field_errors(client, data, content_type, status):
    response = client.post("/api/sessions", data=data, content_type=content_type)

    assert response.status_code == status
    assert response.is_json
    assert response.json["field_errors"] == {}
    assert response.json["error"]


def test_submit_rejects_missing_required_store(client, session_id):
    response = client.post(
        f"/api/sessions/{session_id}/stages/setup/submit",
        json={"values": {"month": "2026-07"}},
    )

    assert response.status_code == 422
    assert "store" in response.json["field_errors"]


def test_draft_is_saved_without_handoff(client, session_id, tmp_path):
    response = client.post(
        f"/api/sessions/{session_id}/stages/setup/draft",
        json={"values": {"store": "测试店铺"}},
    )

    assert response.status_code == 200
    assert response.json["status"] == "draft"
    assert not list(tmp_path.glob(f"{session_id}/**/handoff.json"))


def test_draft_rejects_unknown_values_without_persisting_sensitive_input(client, session_id, tmp_path):
    response = client.post(
        f"/api/sessions/{session_id}/stages/setup/draft",
        json={"values": {"store": "测试店铺", "password": "secret"}},
    )

    assert response.status_code == 422
    assert "password" in response.json["field_errors"]
    assert not list(tmp_path.glob(f"{session_id}/**/input.json"))


def test_submit_rejects_unknown_values_without_persisting_sensitive_input(client, session_id, tmp_path):
    response = client.post(
        f"/api/sessions/{session_id}/stages/production_confirmation/submit",
        json={"values": valid_production_confirmation() | {"cookie": "secret"}},
    )

    assert response.status_code == 422
    assert "cookie" in response.json["field_errors"]
    assert not list(tmp_path.glob(f"{session_id}/**/input.json"))


def test_draft_after_submit_invalidates_its_handoff_and_marks_stage_draft(client, session_id, tmp_path):
    submitted = client.post(
        f"/api/sessions/{session_id}/stages/production_confirmation/submit",
        json={"values": valid_production_confirmation()},
    )
    drafted = client.post(
        f"/api/sessions/{session_id}/stages/production_confirmation/draft",
        json={"values": {"store": "updated store"}},
    )

    assert submitted.status_code == 202
    assert drafted.status_code == 200
    assert client.get(f"/api/sessions/{session_id}/stages/production_confirmation/status").json == {
        "revision": 2,
        "status": "draft",
    }
    assert not list(tmp_path.glob(f"{session_id}/**/handoff.json"))
    input_path = next(tmp_path.glob(f"{session_id}/**/input.json"))
    assert '"password"' not in input_path.read_text(encoding="utf-8")


def test_submit_returns_store_handoff_identity(client, session_id):
    response = client.post(
        f"/api/sessions/{session_id}/stages/production_confirmation/submit",
        json={"values": valid_production_confirmation()},
    )

    assert response.status_code == 202
    assert response.json["revision"] == 1
    assert re.fullmatch(r"[0-9a-f]{64}", response.json["input_sha256"])


def test_production_confirmation_route_only_writes_handoff(client, session_id, monkeypatch):
    called = False

    def forbidden(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(subprocess, "run", forbidden)
    response = client.post(
        f"/api/sessions/{session_id}/stages/production_confirmation/submit",
        json={"values": valid_production_confirmation()},
    )

    assert response.status_code == 202
    assert called is False


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("get", "/api/sessions/missing"),
        ("get", "/api/sessions/missing/stages/setup"),
        ("get", "/api/sessions/missing/stages/setup/status"),
        ("get", "/api/sessions/missing/stages/setup/recovery"),
        ("post", "/api/sessions/missing/stages/setup/draft"),
        ("post", "/api/sessions/missing/stages/setup/submit"),
        ("get", "/api/sessions/../escape"),
    ],
)
def test_unknown_sessions_are_not_found(client, method, url):
    response = getattr(client, method)(url, json={})

    assert response.status_code == 404
    assert response.json["error"] == "not found"


@pytest.mark.parametrize("stage", ["missing", "..", "../setup"])
def test_unknown_or_traversal_stage_is_rejected(client, session_id, stage):
    response = client.get(f"/api/sessions/{session_id}/stages/{stage}")

    assert response.status_code in {400, 404}
    assert response.is_json


def test_submit_rejects_stale_revision(client, session_id):
    first = client.post(
        f"/api/sessions/{session_id}/stages/production_confirmation/submit",
        json={"values": valid_production_confirmation(), "revision": 0},
    )

    assert first.status_code == 409
    assert client.get(f"/api/sessions/{session_id}/stages/production_confirmation/status").json == {
        "revision": 0,
        "status": "draft",
    }


def test_validation_requires_override_reasons_and_enum_values(client, session_id):
    overrides = client.post(
        f"/api/sessions/{session_id}/stages/completeness/submit",
        json={
            "values": {
                "confirmed_product_ids": ["887508274682"],
                "overrides": [{"product_id": "887508274682", "reason": ""}],
            }
        },
    )
    decision = client.post(
        f"/api/sessions/{session_id}/stages/dry_run/submit",
        json={"values": {"decision": ["confirm"]}},
    )

    assert overrides.status_code == 422
    assert "overrides" in overrides.json["field_errors"]
    assert decision.status_code == 422
    assert "decision" in decision.json["field_errors"]


def test_stage_read_and_status_expose_schema_and_state(client, session_id):
    stage = client.get(f"/api/sessions/{session_id}/stages/setup")
    status = client.get(f"/api/sessions/{session_id}/stages/setup/status")

    assert stage.status_code == 200
    assert stage.json["stage"]["id"] == "setup"
    assert {field["name"] for field in stage.json["stage"]["fields"]} >= {"store", "month"}
    assert status.json == {"revision": 0, "status": "draft"}


def test_stage_read_exposes_current_agent_result_for_schema_renderer(
    client, session_id, tmp_path
):
    submitted = client.post(
        f"/api/sessions/{session_id}/stages/production_confirmation/submit",
        json={"values": valid_production_confirmation()},
    )
    SessionStore(tmp_path).write_result(
        session_id,
        "production_confirmation",
        submitted.json["revision"],
        submitted.json["input_sha256"],
        status="completed",
        summary="真实结果：1 个商品已完成",
        evidence=[{"product_id": "887508274682", "count": 1}],
    )

    response = client.get(
        f"/api/sessions/{session_id}/stages/production_confirmation"
    )

    assert response.status_code == 200
    assert response.json["result"]["summary"] == "真实结果：1 个商品已完成"
    assert response.json["result"]["revision"] == submitted.json["revision"]


def test_stage_read_returns_only_allowlisted_current_input_values(
    client, session_id, tmp_path
):
    roots = [r"Y:\视觉部\1-模特图", r"Z:\图片\买家秀"]
    saved = client.post(
        f"/api/sessions/{session_id}/stages/asset_matching/draft",
        json={
            "values": {
                "image_roots": roots,
                "source_types": ["模特图", "买家秀"],
                "include_video": False,
            }
        },
    )
    input_path = tmp_path / session_id / "04-asset-matching" / "input.json"
    document = json.loads(input_path.read_text(encoding="utf-8"))
    document["values"]["password"] = "must-not-leak"
    input_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

    response = client.get(f"/api/sessions/{session_id}/stages/asset_matching")

    assert saved.status_code == 200
    assert response.status_code == 200
    assert response.json["input"] == {
        "revision": 1,
        "values": {
            "image_roots": roots,
            "source_types": ["模特图", "买家秀"],
            "include_video": False,
        },
    }


def test_recovery_returns_session_store_instruction(client, session_id):
    response = client.get(f"/api/sessions/{session_id}/stages/setup/recovery")

    assert response.status_code == 200
    assert "handoff.json" in response.json["instruction"]


def test_validation_enforces_paths_lists_dates_and_boolean_confirmation(client, session_id, tmp_path):
    path = tmp_path / "readable.txt"
    path.write_text("ok", encoding="utf-8")
    response = client.post(
        f"/api/sessions/{session_id}/stages/approval/submit",
        json={
            "values": {
                "task_ids": "not a list",
                "confirmed_by": "reviewer",
                "confirmed_at": "not-a-date",
                "valid_until": "2026-07-21T12:00:00",
                "acknowledgement": False,
            }
        },
    )

    assert response.status_code == 422
    assert {"task_ids", "confirmed_at", "acknowledgement"} <= response.json["field_errors"].keys()


def test_setup_validation_requires_readable_paths_and_exactly_one_asset_source(client, session_id, tmp_path):
    readable = tmp_path / "readable.txt"
    readable.write_text("ok", encoding="utf-8")
    values = {
        "store": "测试店铺",
        "month": "2026-07",
        "product_scope": "single",
        "products_csv": str(readable),
        "rules_csv": str(readable),
        "basic_xlsx": str(readable),
        "search_xlsx": str(readable),
        "runs_root": str(tmp_path),
        "asset_root": str(tmp_path),
        "asset_manifest": str(readable),
    }

    response = client.post(f"/api/sessions/{session_id}/stages/setup/submit", json={"values": values})

    assert response.status_code == 422
    assert "asset_root" in response.json["field_errors"]


def test_source_code_never_imports_execution_modules():
    source = Path(__file__).parents[1] / "src" / "upload_search_materials" / "interaction" / "web.py"
    text = source.read_text(encoding="utf-8")

    assert "subprocess" not in text
    assert "playwright" not in text.lower()
    assert "BrowserUploader" not in text
    assert "_publish" not in text
