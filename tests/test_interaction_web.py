import html as html_module
import hashlib
import json
import re
import subprocess
import threading
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import pytest
import yaml
from PIL import Image

from upload_search_materials.browser.session import CdpStatus
from upload_search_materials.interaction.web import create_app
import upload_search_materials.interaction.web as web_module
from upload_search_materials.interaction.session import SessionStore
from upload_search_materials.interaction.stages import STAGES
from upload_search_materials.runtime_config import DiscoveredPath, RuntimeConfig


class AuthorizedLarkAuthCoordinator:
    def status(self, *, refresh=True):
        return {
            "status": "authorized",
            "message": "当前飞书账号已授权。",
            "verification_url": "",
            "user_name": "测试用户",
        }

    def start(self):
        return self.status()


@pytest.fixture
def client(tmp_path):
    workspace = Path(__file__).parents[1]
    docs = workspace / "src" / "upload_search_materials" / "docs"
    runtime = RuntimeConfig(
        workspace_root=workspace,
        products=DiscoveredPath(
            docs / "天猫商品信息表_产品数据表_数据总表.csv",
            "discovered",
        ),
        rules=DiscoveredPath(
            docs / "天猫商品信息表_每月推品规则（合并）_Grid View.csv",
            "discovered",
        ),
        image_sources=(
            {"label": "视觉部 · 模特图", "path": r"Y:\视觉部\1-模特图"},
            {
                "label": "小红书 KOC 置换 · 淘宝买家秀",
                "path": r"Z:\浙江酷趣\运营中心\营销板块\小红书koc置换&淘宝买家秀\优质买家秀",
            },
            {
                "label": "小红书 KOC 置换 · 买家秀",
                "path": r"Z:\浙江酷趣\运营中心\营销板块\小红书koc置换&买家秀\优质买家秀",
            },
        ),
        runs_root=tmp_path,
        config_path=tmp_path / "config" / "runtime.json",
        user_data_root=tmp_path / "user-data",
    )
    return create_app(
        tmp_path,
        runtime_config=runtime,
        enforce_stage_order=False,
        lark_auth_coordinator=AuthorizedLarkAuthCoordinator(),
    ).test_client()


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


def valid_setup_values(tmp_path):
    team_index = tmp_path / "team-index"
    image_root = tmp_path / "image-root"
    team_index.mkdir(exist_ok=True)
    image_root.mkdir(exist_ok=True)
    return {
        "store": "测试店铺",
        "products_csv": str(tmp_path / "products.csv"),
        "rules_csv": str(tmp_path / "rules.csv"),
        "team_folder_index_root": str(team_index),
        "image_source_labels": ["视觉部"],
        "image_roots": [str(image_root)],
    }


def sample_completeness_matrix():
    return {
        "contract_version": 1,
        "source_filter": "search_recommend_high_value",
        "products": [
            {
                "product_id": "898439684957",
                "sku": "KQ25051",
                "product_title": "椰椰小岛两栖泳衣",
                "owner": "洋葱",
                "status": "needs_supplement",
                "selectable": True,
                "promotion": {
                    "target_slots": 9,
                    "current_count": 4,
                    "missing_count": 5,
                    "exact_slot_status": "collected",
                    "empty_slot_indexes": [5, 6, 7, 8, 9],
                    "evidence": "product=898439684957;target=9;current=4",
                },
            }
        ],
        "summary": {
            "product_count": 1,
            "selectable_count": 1,
            "excluded_count": 0,
            "status_counts": {"needs_supplement": 1},
        },
    }


def test_create_session_returns_timestamp_id(client):
    response = client.post("/api/sessions", json={})

    assert response.status_code == 201
    assert re.fullmatch(r"\d{8}_\d{6}(?:_\d{2})?", response.json["session_id"])


def test_noncurrent_asset_requests_cannot_restore_stale_removed_products(
    tmp_path,
):
    runtime = RuntimeConfig(
        workspace_root=Path(__file__).parents[1],
        products=DiscoveredPath(None, "missing"),
        rules=DiscoveredPath(None, "missing"),
        image_sources=(),
        runs_root=tmp_path,
        config_path=tmp_path / "config" / "runtime.json",
        user_data_root=tmp_path / "user-data",
    )
    strict_client = create_app(
        tmp_path, runtime_config=runtime
    ).test_client()
    strict_session_id = strict_client.post(
        "/api/sessions", json={}
    ).json["session_id"]
    stale_values = {
        "image_roots": [str(tmp_path / "images")],
        "source_types": ["image"],
        "removed_product_ids": ["P1"],
        "folder_decisions": [],
    }

    responses = [
        strict_client.post(
            f"/api/sessions/{strict_session_id}/stages/asset_matching/draft",
            json={"revision": 0, "values": stale_values},
        ),
        strict_client.post(
            f"/api/sessions/{strict_session_id}/stages/asset_matching/submit",
            json={"values": stale_values},
        ),
        strict_client.post(
            f"/api/sessions/{strict_session_id}/stages/asset_matching/prepare-gallery",
            json={"revision": 0, "values": stale_values},
        ),
    ]

    assert [response.status_code for response in responses] == [409, 422, 409]
    assert {
        response.json["reason_code"] for response in (responses[0], responses[2])
    } == {"STAGE_NOT_CURRENT"}
    assert "stage" in responses[1].json["field_errors"]
    assert SessionStore(tmp_path).read_optional_stage_document(
        strict_session_id, "asset_matching", "input"
    ) is None


def test_runtime_lark_base_config_can_be_saved(client):
    response = client.put(
        "/api/runtime/lark-base",
        json={
            "lark_base": {
                "enabled": True,
                "product_base_url": "https://example.feishu.cn/wiki/base",
                "product_table_id": "产品数据表",
                "upload_log_base_url": "https://example.feishu.cn/wiki/base",
                "upload_log_table_id": "搜推素材上传记录",
            }
        },
    )

    assert response.status_code == 200
    assert response.json["saved"] is True
    assert response.json["product_sync_configured"] is True
    assert response.json["upload_log_configured"] is True

    current = client.get("/api/runtime/lark-base")
    assert current.json["enabled"] is True
    assert current.json["product_table_id"] == "产品数据表"


def test_runtime_lark_base_check_is_soft_readiness(
    client, monkeypatch
):
    observed = {}

    def fake_inspect(config):
        observed["config"] = config
        return {
            "status": "available",
            "message": "ok",
            "product_sync": {"status": "available"},
            "upload_log": {"status": "available"},
        }

    monkeypatch.setattr(web_module, "inspect_lark_base_config", fake_inspect)

    response = client.post(
        "/api/runtime/lark-base/check",
        json={
            "lark_base": {
                "enabled": True,
                "product_base_token": "base-token",
                "product_table_id": "产品数据表",
            }
        },
    )

    assert response.status_code == 200
    assert response.json["status"] == "available"
    assert observed["config"].product_base_token == "base-token"


def test_runtime_lark_authorization_refreshes_owner_snapshot(
    client, monkeypatch
):
    observed = {}

    class SnapshotResult:
        def public_status(self):
            return {
                "status": "completed",
                "metadata_count": 12,
                "owner_count": 8,
                "updated_at": "2026-08-26T12:00:00+08:00",
            }

    def fake_refresh(config, path):
        observed["config"] = config
        observed["path"] = path
        return SnapshotResult()

    monkeypatch.setattr(
        web_module, "refresh_product_metadata_snapshot", fake_refresh
    )

    response = client.post(
        "/api/runtime/lark-base/product-owner-snapshot/refresh", json={}
    )

    assert response.status_code == 200
    assert response.json["status"] == "completed"
    assert response.json["metadata_count"] == 12
    assert observed["path"].name == "product-owner-snapshot.json"


def test_runtime_lark_refresh_updates_owner_and_upload_history_snapshots(
    client, monkeypatch
):
    class SnapshotResult:
        def __init__(self, payload):
            self.payload = payload

        def public_status(self):
            return self.payload

    monkeypatch.setattr(
        web_module,
        "refresh_product_metadata_snapshot",
        lambda _config, _path: SnapshotResult(
            {"status": "completed", "metadata_count": 12}
        ),
    )
    monkeypatch.setattr(
        web_module,
        "refresh_upload_history_snapshot",
        lambda _config, _path: SnapshotResult(
            {"status": "completed", "fingerprint_count": 34}
        ),
    )

    response = client.post(
        "/api/runtime/lark-base/snapshots/refresh", json={}
    )

    assert response.status_code == 200
    assert response.json["status"] == "completed"
    assert response.json["product_owner_snapshot"]["metadata_count"] == 12
    assert response.json["upload_history_snapshot"]["fingerprint_count"] == 34


def test_authorized_lark_can_refresh_current_completeness_owners(
    client, session_id, monkeypatch
):
    observed = {}

    def fake_refresh(**kwargs):
        observed.update(kwargs)
        return {
            "status": "refreshed",
            "reason_code": "",
            "message": "已刷新负责人。",
            "lark_product_sync": {
                "fetched_count": 20,
                "matched_count": 1,
                "updated_owner_count": 1,
            },
        }

    monkeypatch.setattr(
        web_module, "refresh_completeness_product_metadata", fake_refresh
    )

    response = client.post(
        f"/api/sessions/{session_id}/stages/completeness/refresh-lark-owners",
        json={},
    )

    assert response.status_code == 200
    assert response.json["status"] == "refreshed"
    assert observed["session_id"] == session_id
    assert observed["runs_root"] == Path(observed["runs_root"])


def test_runtime_lark_auth_is_owned_by_the_workbench(tmp_path):
    workspace = Path(__file__).parents[1]

    class FakeAuthCoordinator:
        def status(self):
            return {
                "status": "authorization_required",
                "message": "请授权飞书账号。",
                "verification_url": "",
                "user_name": "",
            }

        def start(self):
            return {
                "status": "awaiting_user",
                "message": "请在飞书页面完成授权。",
                "verification_url": "https://accounts.feishu.cn/device",
                "user_name": "",
            }

    runtime = RuntimeConfig(
        workspace_root=workspace,
        products=DiscoveredPath(None, "missing"),
        rules=DiscoveredPath(None, "missing"),
        image_sources=(),
        runs_root=tmp_path,
        config_path=tmp_path / "config" / "runtime.json",
        user_data_root=tmp_path / "user-data",
    )
    local_client = create_app(
        tmp_path,
        runtime_config=runtime,
        enforce_stage_order=False,
        lark_auth_coordinator=FakeAuthCoordinator(),
    ).test_client()

    current = local_client.get("/api/runtime/lark-base/auth")
    started = local_client.post("/api/runtime/lark-base/auth/start", json={})

    assert current.status_code == 200
    assert current.json["status"] == "authorization_required"
    assert started.status_code == 202
    assert started.json["status"] == "awaiting_user"
    assert started.json["verification_url"] == "https://accounts.feishu.cn/device"
    assert "device_code" not in started.json


def test_setup_page_uses_one_click_lark_authorization(client):
    response = client.get("/")
    text = response.get_data(as_text=True)
    script = client.get("/static/app.js").get_data(as_text=True)

    assert response.status_code == 200
    assert "data-authorize-lark-base" in text
    assert "data-refresh-lark-owner-snapshot" in text
    assert "商品信息表和上传记录表已由团队预设" not in text
    assert "素材加载时自动排除已经成功上传的原图" not in text
    assert "data-lark-enabled" not in text
    assert "data-save-lark-base" not in text
    assert "商品信息表缺少飞书 Wiki 读取权限" in script
    assert "/stages/completeness/refresh-lark-owners" in script
    assert "/api/runtime/lark-base/snapshots/refresh" in script
    assert "正在把负责人和成功上传记录更新到本机" in script
    assert "刷新飞书数据" in text
    assert "当前任务负责人稍后会自动刷新" not in script
    assert "没有读取到任何商品记录" in script
    assert "已授权 · 需补充权限" in script
    assert "飞书账号已授权，但默认数据表暂时不可用" not in script
    assert 'name="confirmed_by"' not in text


def test_bounded_agent_action_processes_product_selection_inside_workbench(
    client, session_id, tmp_path, monkeypatch
):
    store = SessionStore(tmp_path)
    handoff = store.save_input(
        session_id,
        "completeness",
        {"selected_product_ids": ["886506466908"]},
    )
    calls = []

    def fake_process(store_arg, session_arg, **kwargs):
        calls.append((store_arg.runs_root, session_arg, kwargs))
        return {"status": "completed", "candidate_rows": 3}

    monkeypatch.setattr(
        web_module,
        "process_product_selection_handoff",
        fake_process,
    )

    response = client.post(
        f"/api/sessions/{session_id}/agent-actions/process-product-selection",
        json={
            "revision": handoff["revision"],
            "input_sha256": handoff["input_sha256"],
            "claimant_id": "codex-agent",
        },
    )

    assert response.status_code == 200
    assert response.json["result"] == {
        "status": "completed",
        "candidate_rows": 3,
    }
    assert calls[0][1] == session_id
    assert calls[0][2]["claimant_id"] == "codex-agent"


def test_bounded_agent_action_rejects_stale_identity(
    client, session_id, tmp_path, monkeypatch
):
    store = SessionStore(tmp_path)
    handoff = store.save_input(
        session_id,
        "completeness",
        {"selected_product_ids": ["886506466908"]},
    )
    called = False

    def fake_process(*_args, **_kwargs):
        nonlocal called
        called = True
        return {"status": "completed"}

    monkeypatch.setattr(
        web_module,
        "process_product_selection_handoff",
        fake_process,
    )

    response = client.post(
        f"/api/sessions/{session_id}/agent-actions/process-product-selection",
        json={
            "revision": handoff["revision"],
            "input_sha256": "0" * 64,
        },
    )

    assert response.status_code == 409
    assert called is False


def test_agent_wait_api_projects_live_and_expired_recovery_status(
    client, session_id, tmp_path
):
    created = client.post(
        f"/api/sessions/{session_id}/stages/setup/agent-wait",
        json={
            "action": "create",
            "expected_revision": 1,
            "claimant_id": "codex-agent",
        },
    )

    assert created.status_code == 200
    assert created.json["handoff_status"]["status"] == "waiting"
    assert created.json["handoff_status"]["agent_wait"]["remaining_seconds"] > 0
    renewed = client.post(
        f"/api/sessions/{session_id}/stages/setup/agent-wait",
        json={
            "action": "renew",
            "wait_id": created.json["agent_wait"]["wait_id"],
        },
    )
    assert renewed.status_code == 200
    assert (
        renewed.json["agent_wait"]["wait_id"]
        == created.json["agent_wait"]["wait_id"]
    )

    store = SessionStore(tmp_path)
    state = store.load_session(session_id)
    state["agent_wait"]["expires_at"] = "2000-01-01T00:00:00+00:00"
    store._write_json_atomic(
        store._session_path(session_id) / "session.json", state
    )
    status = client.get(
        f"/api/sessions/{session_id}/stages/setup/status"
    ).json
    assert status["handoff_status"]["status"] == "waiting_expired"


def test_stage_status_returns_verified_normal_action_without_artifact_reads(
    client, session_id, tmp_path
):
    store = SessionStore(tmp_path)
    handoff = store.save_input(
        session_id,
        "completeness",
        {"selected_product_ids": ["886506466908"]},
    )

    status = client.get(
        f"/api/sessions/{session_id}/stages/completeness/status"
    )

    assert status.status_code == 200
    identity = status.json["handoff_status"]["handoff_identity"]
    assert identity == {
        "session_id": session_id,
        "stage_id": "completeness",
        "revision": handoff["revision"],
        "input_sha256": handoff["input_sha256"],
        "handoff_kind": "",
        "allowed_action": "process-product-selection",
        "transport": "workbench_api",
        "endpoint": (
            f"/api/sessions/{session_id}/agent-actions/process-product-selection"
        ),
        "claimant_id": "codex-agent",
    }


def test_agent_wait_can_return_ready_handoff_identity_in_same_response(
    client, session_id, tmp_path
):
    store = SessionStore(tmp_path)
    submitted = {}

    def submit_setup():
        submitted["handoff"] = store.save_input(
            session_id,
            "setup",
            {"store": "测试店铺", "image_roots": [str(tmp_path)]},
        )

    delayed_submit = threading.Timer(0.05, submit_setup)
    delayed_submit.start()

    response = client.post(
        f"/api/sessions/{session_id}/stages/setup/agent-wait",
        json={
            "action": "create",
            "expected_revision": 1,
            "claimant_id": "codex-agent",
            "wait_seconds": 1,
        },
    )
    delayed_submit.join(timeout=1)

    assert response.status_code == 200
    handoff = submitted["handoff"]
    identity = response.json["handoff_status"]["handoff_identity"]
    assert identity["revision"] == handoff["revision"]
    assert identity["input_sha256"] == handoff["input_sha256"]
    assert identity["allowed_action"] == "process-setup"


def test_listen_action_returns_handoff_submitted_before_listener_without_revision(
    client, session_id, tmp_path
):
    store = SessionStore(tmp_path)
    handoff = store.save_input(
        session_id,
        "setup",
        {"store": "测试店铺", "image_roots": [str(tmp_path)]},
    )

    response = client.post(
        f"/api/sessions/{session_id}/stages/setup/agent-wait",
        json={
            "action": "listen",
            "claimant_id": "codex-agent",
            "wait_seconds": 0,
        },
    )

    assert response.status_code == 200
    assert response.json["status"] == "handoff_ready"
    assert response.json["segment_seconds"] == 0
    assert response.json["lease_seconds"] == 30
    identity = response.json["handoff_status"]["handoff_identity"]
    assert identity["revision"] == handoff["revision"]
    assert identity["input_sha256"] == handoff["input_sha256"]
    assert store.processing_claim(session_id, "setup") is None


def test_default_agent_wait_action_is_bounded_listener(client, session_id):
    first = client.post(
        f"/api/sessions/{session_id}/stages/setup/agent-wait",
        json={"wait_seconds": 0},
    )
    second = client.post(
        f"/api/sessions/{session_id}/stages/setup/agent-wait",
        json={"wait_seconds": 0},
    )

    assert first.status_code == second.status_code == 200
    assert first.json["status"] == second.json["status"] == "waiting"
    assert (
        first.json["agent_wait"]["wait_id"]
        == second.json["agent_wait"]["wait_id"]
    )
    assert (
        first.json["agent_wait"]["started_at"]
        == second.json["agent_wait"]["started_at"]
    )


def test_agent_wait_rejects_unbounded_long_poll(client, session_id):
    response = client.post(
        f"/api/sessions/{session_id}/stages/setup/agent-wait",
        json={
            "action": "create",
            "expected_revision": 1,
            "wait_seconds": 31,
        },
    )

    assert response.status_code == 422
    assert "wait_seconds" in response.json["field_errors"]

    listen = client.post(
        f"/api/sessions/{session_id}/stages/setup/agent-wait",
        json={
            "action": "listen",
            "wait_seconds": 15.1,
        },
    )
    assert listen.status_code == 422
    assert "wait_seconds" in listen.json["field_errors"]


def test_submitted_handoff_without_live_wait_shows_workbench_queue_prompt(
    client, session_id
):
    response = client.post(
        f"/api/sessions/{session_id}/stages/production_confirmation/submit",
        json={
            "values": valid_production_confirmation(),
        },
    )

    assert response.status_code == 202
    stage = client.get(
        f"/api/sessions/{session_id}/stages/production_confirmation"
    ).json
    assert stage["handoff_status"]["base_status"] == "ready"
    assert "工作台后台已接收提交" in stage["handoff_status"]["resume_prompt"]


def test_root_renders_nine_stage_left_rail(client):
    response = client.get("/")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert response.mimetype == "text/html"
    assert html.count('data-stage-id="') == 9


def test_root_uses_non_repeating_workbench_brand_name(client):
    html = client.get("/").get_data(as_text=True)

    assert "<title>天猫搜推素材上新工作台</title>" in html
    assert '<p class="eyebrow">天猫搜推素材</p>' in html
    assert '<p class="brand-name">上新工作台</p>' in html
    assert '<p class="brand-name">素材上新工作台</p>' not in html


def test_root_displays_current_plugin_version_at_header_center(client):
    manifest = json.loads(
        (Path(__file__).parents[1] / ".codex-plugin" / "plugin.json").read_text(
            encoding="utf-8"
        )
    )
    html = client.get("/").get_data(as_text=True)

    assert 'class="plugin-version"' in html
    assert "data-plugin-version" in html
    assert f"v{manifest['version']}" in html


def test_root_shows_plain_language_lark_identity_and_compact_empty_states(client):
    html = client.get("/").get_data(as_text=True)

    assert "data-header-lark-user" in html
    assert "data-header-lark-user-name" in html
    assert "飞书账号" in html
    assert "PROMOTION MATERIAL MATRIX" not in html
    assert "ASSET MATCH" not in html
    assert "UPLOAD CONFIRMATION" not in html
    assert "工作台后台返回真实巡检结果" not in html
    assert "查看排查信息" in html
    assert "复制异常诊断说明" in html


def test_new_session_hides_internal_and_legacy_stages(client, session_id):
    html = client.get(f"/?session_id={session_id}").get_data(as_text=True)

    assert html.count('data-stage-id="') == 6
    assert 'data-stage-id="image_review"' not in html
    assert 'data-stage-id="dry_run"' not in html
    assert 'data-stage-id="slots_copy"' in html
    assert "完整度巡检" in html
    assert "生产确认" not in html
    assert re.search(r"/static/app\.js\?v=[0-9a-f]{12}", html)


def test_setup_page_separates_user_choices_automatic_inputs_and_advanced_imports(client):
    html = html_module.unescape(client.get("/").get_data(as_text=True))

    assert "确认本次任务" in html
    assert "确认索引" in html
    assert "确认店铺" not in html
    assert "核对系统识别的店铺名称" not in html
    assert "请核对这里显示的是本次要操作的店铺" not in html
    assert re.search(r'<input id="setup-store" name="store" type="hidden"', html)
    assert "任务所需内容" in html
    assert "按下面 3 步完成" in html
    assert "高级设置 · 导入已有文件" in html
    assert "规则表</span>" not in html
    assert "全量自动采集" not in html
    assert "提交后由系统自动采集" not in html
    assert 'name="promotion_max_pages"' not in html
    assert 'name="product_scope"' not in html
    assert "在文件夹归属审查中逐步积累" not in html
    assert "本轮延期" not in html
    assert 'name="month"' not in html
    assert "目标月份" not in html
    assert 'data-setup-login-gate' in html
    assert "本机配置文件：" not in html
    assert 'name="products_csv"' in html and 'type="hidden"' in html
    assert 'name="rules_csv"' in html
    assert html.count('name="image_roots"') >= 3
    assert 'name="store_confirmed"' not in html
    assert "我已确认当前页面店铺与目标店铺一致" not in html
    assert "本阶段输入" not in html
    assert 'data-approver-options' not in html
    for removed in ("basic_xlsx", "search_xlsx", "asset_root", "runs_root"):
        assert f'name="{removed}"' not in html
    assert re.search(
        r'data-component="CollectionRuntimeConfig"[^>]+hidden', html
    )
    assert re.search(r'data-check-image-sources[^>]+hidden', html)


def test_setup_login_gate_uses_automatic_safe_status(client, monkeypatch):
    monkeypatch.setattr(
        web_module,
        "inspect_login_browser",
        lambda _runtime: {
            "ready": True,
            "login_state": "authenticated",
            "reason_code": "READY",
            "observed_store": "测试店铺",
        },
    )

    response = client.get("/api/runtime/login-status")

    assert response.status_code == 200
    assert response.json == {
        "ready": True,
        "status": "ready",
        "message": "已识别店铺，可以配置",
        "login_state": "authenticated",
        "reason_code": "READY",
        "observed_store": "测试店铺",
    }


@pytest.mark.parametrize(
    ("login_state", "status", "message"),
    (
        ("browser_unavailable", "chrome_unavailable", "专用 Chrome 未启动"),
        (
            "opening_material_center",
            "opening_material_center",
            "已连接 Chrome，正在打开千牛",
        ),
        ("interaction_required", "waiting_for_login", "千牛页面已打开，等待用户登录"),
        ("human_check", "waiting_for_login", "千牛页面已打开，等待用户登录"),
        ("store_unrecognized", "store_unrecognized", "已登录，但暂未识别店铺"),
    ),
)
def test_setup_login_gate_exposes_distinct_safe_waiting_states(
    client, monkeypatch, login_state, status, message
):
    monkeypatch.setattr(
        web_module,
        "inspect_login_browser",
        lambda _runtime: {
            "ready": False,
            "login_state": login_state,
            "reason_code": "WAITING",
        },
    )

    response = client.get("/api/runtime/login-status")

    assert response.status_code == 200
    assert response.json["ready"] is False
    assert response.json["status"] == status
    assert response.json["message"] == message
    assert response.json["observed_store"] == ""


def test_setup_login_browser_route_reuses_or_opens_visible_browser(
    client, monkeypatch
):
    calls = []
    focused = []

    def ensure(**kwargs):
        calls.append(kwargs)
        return {
            "status": "connected",
            "reused": False,
            "endpoint": kwargs["cdp_url"],
            "pages": [{"url": kwargs["material_center_url"]}],
        }

    monkeypatch.setattr(web_module, "ensure_cdp_browser", ensure)

    class Page:
        def bring_to_front(self):
            focused.append(True)

    @contextmanager
    def open_page(*_args, **_kwargs):
        yield Page()

    monkeypatch.setattr(web_module, "open_cdp_page", open_page)

    response = client.post(
        "/api/runtime/collection/login-browser",
        json={},
    )

    assert response.status_code == 200
    assert response.json["connected"] is True
    assert response.json["page_count"] == 1
    assert calls[0]["material_center_url"].startswith("https://")
    assert focused == [True]


def test_collection_runtime_panel_validates_and_saves_local_profile(
    tmp_path, monkeypatch
):
    selectors = tmp_path / "selectors.local.yaml"
    selectors.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "profile_name": "local-test",
                "profile_version": "2026.07.29",
                "production": True,
                "supported_purposes": ["high_value_collection"],
                "store_name": "[data-store-name]",
                "human_check": "[data-human-check]",
                "promotion_tab": '[role="tab"]:has-text("搜推素材")',
                "high_value_filter": '[role="checkbox"]:has-text("搜推高价值")',
                "promotion_rows": "tbody tr",
                "promotion_current_page": "[aria-current=page]",
                "promotion_first_page": "[data-page='1']",
                "promotion_terminal_page": "[data-page-last=true]",
                "promotion_next_page": 'button:has-text("下一页")',
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    runtime = RuntimeConfig(
        workspace_root=tmp_path,
        products=DiscoveredPath(None, "missing"),
        rules=DiscoveredPath(None, "missing"),
        image_sources=(),
        runs_root=tmp_path / "runs",
    )
    monkeypatch.setattr(
        web_module,
        "inspect_cdp_endpoint",
        lambda *_args, **_kwargs: CdpStatus(
            connected=True,
            endpoint="http://127.0.0.1:9222",
            pages=(
                {
                    "id": "page-1",
                    "title": "素材中心",
                    "url": "https://myseller.taobao.com/material-center",
                },
            ),
        ),
    )
    local_client = create_app(
        runtime.runs_root, runtime_config=runtime
    ).test_client()

    saved = local_client.put(
        "/api/runtime/selector-profile",
        json={"selectors_file": str(selectors)},
    )
    status = local_client.get("/api/runtime/collection")

    assert saved.status_code == 200
    assert saved.json["profile_name"] == "local-test"
    assert status.json["selector_profile"]["configured"] is True
    assert status.json["cdp"]["connected"] is True
    config = json.loads(
        (tmp_path / "config" / "local-paths.json").read_text(
            encoding="utf-8"
        )
    )
    installed = tmp_path / "config" / "selectors.local.yaml"
    assert config["selectors_file"] == str(installed.resolve())
    assert saved.json["selectors_file"] == str(installed.resolve())
    assert installed.read_bytes() == selectors.read_bytes()


def test_collection_runtime_reports_session_login_and_store_evidence(
    client, session_id, monkeypatch
):
    monkeypatch.setattr(
        web_module,
        "inspect_cdp_endpoint",
        lambda *_args, **_kwargs: CdpStatus(
            connected=True,
            endpoint="http://127.0.0.1:9222",
        ),
    )
    # The fixture runs root is available through the route's closure, so use
    # the current session path returned by its durable state location.
    runs_root = Path(
        client.get(f"/?session_id={session_id}")
        .get_data(as_text=True)
        .split('data-runs-root="', 1)[1]
        .split('"', 1)[0]
    )
    evidence = (
        runs_root
        / session_id
        / "collected"
        / "promotion"
        / "store-page-evidence.json"
    )
    evidence.parent.mkdir(parents=True)
    evidence.write_text(
        json.dumps(
            {
                "observed_store": "测试店铺",
                "page_url": (
                    "https://myseller.taobao.com/home.htm/"
                    "material-center/material-management"
                ),
                "page_identity": "material_center",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    response = client.get(
        f"/api/runtime/collection?session_id={session_id}"
    )

    assert response.status_code == 200
    assert response.json["session"]["login_state"] == "authenticated"
    assert response.json["session"]["observed_store"] == "测试店铺"


def test_guided_selector_bootstrap_validates_and_promotes_current_dom(
    tmp_path, monkeypatch
):
    workspace = tmp_path / "workspace"
    project = workspace
    user_data = workspace / "user-data"
    scripts = user_data / "runtime" / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    (workspace / "src" / "upload_search_materials" / "docs").mkdir(
        parents=True
    )
    (project / "config").mkdir()
    (workspace / "SKILL.md").write_text(
        "---\nname: test\ndescription: test\n---\n",
        encoding="utf-8",
    )
    (workspace / "pyproject.toml").write_text(
        "[project]\nname='test'\n",
        encoding="utf-8",
    )
    (scripts / "python.exe").write_bytes(b"prepared")
    plugin_launcher = project / "scripts" / "run-plugin.py"
    plugin_launcher.parent.mkdir()
    plugin_launcher.touch()
    lock = project / "uv.lock"
    lock.write_text("version = 1\n", encoding="utf-8")
    (user_data / "runtime" / "environment-fingerprint.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest(),
                "python": str((scripts / "python.exe").resolve()),
                "dependency_mode": "no-install-project",
                "launch_mode": "current-plugin-source",
            }
        ),
        encoding="utf-8",
    )
    runtime = RuntimeConfig(
        workspace_root=workspace,
        products=DiscoveredPath(None, "missing"),
        rules=DiscoveredPath(None, "missing"),
        image_sources=(),
        runs_root=workspace / "runs",
        user_data_root=user_data,
    )

    class Locator:
        def __init__(self, selector):
            self.selector = selector

        def count(self):
            return 1

        def inner_text(self):
            if any(
                marker in self.selector
                for marker in (
                    "aria-current",
                    "first-of-type",
                    "last-of-type",
                )
            ):
                return "1"
            return "测试店铺"

        def all_inner_texts(self):
            if self.selector == "tbody tr":
                return ["商品 商品ID 100"]
            return []

        def get_attribute(self, _name):
            return None

        def is_enabled(self):
            return "下一页" not in self.selector

        def is_visible(self):
            return False

    class Page:
        url = (
            "https://myseller.taobao.com/home.htm/"
            "material-center/material-management"
        )

        def locator(self, selector):
            return Locator(selector)

    @contextmanager
    def open_page(*_args, **_kwargs):
        yield Page()

    connected = CdpStatus(
        connected=True,
        endpoint="http://127.0.0.1:9222",
    )
    monkeypatch.setattr(web_module, "open_cdp_page", open_page)
    monkeypatch.setattr(
        web_module,
        "prepare_high_value_validation_page",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        web_module, "inspect_cdp_endpoint", lambda *_a, **_k: connected
    )
    monkeypatch.setattr(
        "upload_search_materials.collection_readiness."
        "inspect_cdp_endpoint",
        lambda *_a, **_k: connected,
    )
    local_client = create_app(
        runtime.runs_root, runtime_config=runtime
    ).test_client()
    session_id = local_client.post("/api/sessions", json={}).json[
        "session_id"
    ]
    candidate = project / "config" / "selectors.local.yaml"

    created = local_client.post(
        "/api/runtime/selector-profile/bootstrap",
        json={"selectors_file": str(candidate)},
    )
    validated = local_client.post(
        "/api/runtime/collection/validate",
        json={
            "session_id": session_id,
            "expected_store": "测试店铺",
            "selectors_file": str(candidate),
        },
    )

    assert created.status_code == 201
    assert created.json["production"] is False
    assert validated.status_code == 200
    assert validated.json["validation"]["ready"] is True
    assert validated.json["promoted"]["production"] is True
    assert validated.json["collection_readiness"]["ready"] is True


def test_setup_submission_hands_technical_readiness_to_agent(tmp_path):
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "upload-search-materials").mkdir()
    products = workspace / "products.csv"
    rules = workspace / "rules.csv"
    products.write_text("商品ID\n1\n", encoding="utf-8")
    rules.write_text("month,rule\n7,include\n", encoding="utf-8")
    runtime = RuntimeConfig(
        workspace_root=workspace,
        products=DiscoveredPath(products, "configured"),
        rules=DiscoveredPath(rules, "configured"),
        image_sources=({"label": "本地", "path": str(workspace)},),
        runs_root=workspace / "runs",
    )
    local_client = create_app(
        runtime.runs_root,
        runtime_config=runtime,
        enforce_stage_order=True,
    ).test_client()
    session_id = local_client.post("/api/sessions", json={}).json[
        "session_id"
    ]

    response = local_client.post(
        f"/api/sessions/{session_id}/stages/setup/submit",
        json={
            "values": {
                "store": "测试店铺",
                "store_confirmed": True,
                "month": "2026-07",
                "products_csv": str(products),
                "rules_csv": str(rules),
                "image_source_labels": ["本地"],
                "image_roots": [str(workspace)],
                "folder_index_root": str(workspace / ".index"),
                "team_folder_index_root": str(workspace),
                "asset_manifest": "",
                "historical_basic_xlsx": "",
                "historical_promotion_csv": "",
                "user_notes": "",
            }
        },
    )

    assert response.status_code == 202
    assert response.json["status"] == "ready_for_agent"
    handoff_path = (
        SessionStore(runtime.runs_root)._stage_path(session_id, "setup")
        / "handoff.json"
    )
    handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
    assert handoff["stage_id"] == "setup"


def test_setup_page_shows_discovered_inputs_and_configurable_image_sources(client):
    html = html_module.unescape(client.get("/").get_data(as_text=True))

    assert "天猫商品信息表_产品数据表_数据总表.csv" in html
    assert "天猫商品信息表_每月推品规则（合并）_Grid View.csv" in html
    for root in (
        r"Y:\视觉部\1-模特图",
        r"Z:\浙江酷趣\运营中心\营销板块\小红书koc置换&淘宝买家秀\优质买家秀",
        r"Z:\浙江酷趣\运营中心\营销板块\小红书koc置换&买家秀\优质买家秀",
    ):
        assert root in html
    assert 'data-component="ImageSourceConfig"' in html
    assert "已预填 3 个常用来源" in html
    assert "确认本次图片源" in html
    assert "未自动找到时，可粘贴路径或选择文件夹" in html
    assert "添加图片源" in html
    assert "检测路径" in html
    assert "保存为常用图片源" in html
    assert 'class="source-section source-section-selected"' in html
    assert 'id="selected-source-heading">本次图片源</h4>' in html
    assert "公司共享盘" not in html
    assert 'class="image-source-row-actions"' in html
    assert "data-image-source-candidate-select" in html
    assert html.count('name="image_source_labels"') >= 3


def test_setup_page_still_opens_without_machine_local_image_configuration(tmp_path):
    runtime = RuntimeConfig(
        workspace_root=tmp_path,
        products=DiscoveredPath(None, "missing"),
        rules=DiscoveredPath(None, "missing"),
        image_sources=(),
        runs_root=tmp_path / "runs",
    )

    response = create_app(tmp_path / "runs", runtime_config=runtime).test_client().get("/")
    html = html_module.unescape(response.get_data(as_text=True))

    assert response.status_code == 200
    assert "本次尚未选择" in html
    assert "添加图片源" in html
    assert html.count('name="image_roots"') >= 1


def test_setup_page_exposes_team_index_picker_and_keeps_local_cache_internal(
    tmp_path,
):
    index_root = tmp_path / "folder-index"
    index_root.mkdir()
    (index_root / "folder-index.sqlite3").write_bytes(b"partial")
    (index_root / "folder-index-progress.json").write_text(
        json.dumps(
            {
                "status": "running",
                "folders_discovered": 321,
                "current_relative_path": "campaign/current",
            }
        ),
        encoding="utf-8",
    )
    runtime = RuntimeConfig(
        workspace_root=tmp_path,
        products=DiscoveredPath(None, "missing"),
        rules=DiscoveredPath(None, "missing"),
        image_sources=(),
        runs_root=tmp_path / "runs",
        folder_index_root=index_root,
    )

    response = create_app(
        tmp_path / "runs", runtime_config=runtime
    ).test_client().get("/")
    html = html_module.unescape(response.get_data(as_text=True))

    assert response.status_code == 200
    assert "团队索引文件夹" in html
    assert "data-pick-team-index" in html
    assert "data-save-team-index" in html
    assert 'name="folder_index_root" type="hidden"' in html
    assert (
        r"\\192.168.110.20\浙江酷趣\天猫部\搜推素材索引-虾米"
        not in html
    )
    assert "正在查找本机映射盘中的团队索引文件夹" in html
    assert "campaign/current" not in html


def test_setup_page_server_render_does_not_probe_team_index_contents(
    tmp_path, monkeypatch
):
    runtime = RuntimeConfig(
        workspace_root=tmp_path,
        products=DiscoveredPath(None, "missing"),
        rules=DiscoveredPath(None, "missing"),
        image_sources=(),
        runs_root=tmp_path / "runs",
        team_folder_index_root=Path(r"\\missing\team-index"),
    )
    monkeypatch.setattr(
        web_module,
        "inspect_team_folder_index_root",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("server render must not enumerate team index contents")
        ),
    )

    response = create_app(
        tmp_path / "runs", runtime_config=runtime
    ).test_client().get("/")

    assert response.status_code == 200
    assert "data-pick-team-index" in response.get_data(as_text=True)


def test_team_index_discovery_api_returns_read_only_auto_fill_candidate(
    tmp_path, monkeypatch
):
    candidate = tmp_path / "Z" / "浙江酷趣" / "天猫部" / "搜推素材索引-虾米"
    runtime = RuntimeConfig(
        workspace_root=tmp_path,
        products=DiscoveredPath(None, "missing"),
        rules=DiscoveredPath(None, "missing"),
        image_sources=(),
        runs_root=tmp_path / "runs",
    )
    monkeypatch.setattr(
        web_module,
        "discover_team_folder_index_root",
        lambda _runtime: {
            "status": "discovered",
            "path": str(candidate),
            "candidates": [str(candidate)],
            "auto_fill": True,
            "message": "已自动找到团队索引文件夹，提交任务时会保存。",
        },
    )
    client = create_app(tmp_path / "runs", runtime_config=runtime).test_client()

    response = client.get("/api/runtime/team-folder-index/discover")

    assert response.status_code == 200
    assert response.json["auto_fill"] is True
    assert response.json["path"] == str(candidate)


def test_team_index_frontend_discovers_after_stage_hydration_without_user_edit():
    source = (
        Path(__file__).parents[1]
        / "src"
        / "upload_search_materials"
        / "interaction"
        / "static"
        / "app.js"
    ).read_text(encoding="utf-8")

    assert 'fetchJson("/api/runtime/team-folder-index/discover")' in source
    assert "input.value = payload.path" in source
    discovery = source.split("async function discoverTeamIndex()", 1)[1].split(
        "function initializeTeamIndexConfig()", 1
    )[0]
    assert "dispatchEvent" not in discovery
    assert "teamIndexDiscoveryPromise" in discovery
    assert "input.value.trim() === initialPath" in discovery
    assert "暂未自动找到团队索引文件夹，可以手动选择" in discovery
    initializer = source.split("function initializeTeamIndexConfig()", 1)[1].split(
        "function larkBasePayload()", 1
    )[0]
    assert "discoverTeamIndex()" not in initializer
    load_stage = source.split("async function loadStage()", 1)[1].split(
        "async function prepareLocalGallery", 1
    )[0]
    assert load_stage.index("hydrateForm(form, payload.input.values)") < (
        load_stage.index("const setupDiscoveryTasks = []")
    )
    assert "setupDiscoveryTasks.push(discoverTeamIndex())" in load_stage


def test_image_source_discovery_api_returns_default_candidates(tmp_path, monkeypatch):
    runtime = RuntimeConfig(
        workspace_root=tmp_path,
        products=DiscoveredPath(None, "missing"),
        rules=DiscoveredPath(None, "missing"),
        image_sources=(),
        runs_root=tmp_path / "runs",
    )
    monkeypatch.setattr(
        web_module,
        "discover_image_sources",
        lambda _runtime: {
            "status": "ambiguous",
            "source": "defaults",
            "auto_fill": True,
            "image_sources": [
                {
                    "source_id": "source-test",
                    "label": "视觉部 · 模特图",
                    "path": "",
                    "candidates": [
                        {"path": r"Y:\视觉部\1-模特图"},
                        {"path": r"Z:\视觉部\1-模特图"},
                    ],
                }
            ],
            "message": "请选择文件夹位置。",
        },
    )
    client = create_app(tmp_path / "runs", runtime_config=runtime).test_client()

    response = client.get("/api/runtime/image-sources/discover")

    assert response.status_code == 200
    assert response.json["auto_fill"] is True
    assert len(response.json["image_sources"][0]["candidates"]) == 2


def test_image_source_frontend_autofills_and_uses_location_select_without_unc_copy():
    source = (
        Path(__file__).parents[1]
        / "src"
        / "upload_search_materials"
        / "interaction"
        / "static"
        / "app.js"
    ).read_text(encoding="utf-8")

    assert 'fetchJson("/api/runtime/image-sources/discover")' in source
    assert "payload.auto_fill" in source
    assert "data-image-source-candidate-select" in source
    assert 'new Option("请选择", "")' in source
    discovery = source.split("async function discoverImageSources()", 1)[1].split(
        "function configuredImageSources()", 1
    )[0]
    assert "dispatchEvent" not in discovery


def test_runtime_image_source_api_saves_checks_and_reloads_multiple_roots(tmp_path):
    workspace = tmp_path / "workspace"
    (workspace / "config").mkdir(parents=True)
    available = workspace / "available"
    available.mkdir()
    runtime = RuntimeConfig(
        workspace_root=workspace,
        products=DiscoveredPath(None, "missing"),
        rules=DiscoveredPath(None, "missing"),
        image_sources=(),
        runs_root=workspace / "runs",
    )
    client = create_app(workspace / "runs", runtime_config=runtime).test_client()
    sources = [
        {"label": "可访问", "path": str(available)},
        {"label": "待挂载", "path": str(workspace / "missing")},
    ]

    checked = client.post("/api/runtime/image-sources/check", json={"image_sources": sources})
    saved = client.put("/api/runtime/image-sources", json={"image_sources": sources})
    loaded = client.get("/api/runtime/image-sources")

    assert checked.status_code == 200
    assert [item["status"] for item in checked.json["image_sources"]] == [
        "available", "unavailable"
    ]
    assert saved.status_code == 200 and saved.json["saved"] is True
    assert len(loaded.json["image_sources"]) == 2
    config = workspace / "config" / "local-paths.json"
    assert config.is_file()


def test_runtime_image_source_api_rejects_zero_or_duplicate_roots(tmp_path):
    runtime = RuntimeConfig(
        workspace_root=tmp_path,
        products=DiscoveredPath(None, "missing"),
        rules=DiscoveredPath(None, "missing"),
        image_sources=(),
        runs_root=tmp_path / "runs",
    )
    client = create_app(tmp_path / "runs", runtime_config=runtime).test_client()

    empty = client.put("/api/runtime/image-sources", json={"image_sources": []})
    duplicate = client.put(
        "/api/runtime/image-sources",
        json={"image_sources": [
            {"label": "A", "path": str(tmp_path)},
            {"label": "B", "path": str(tmp_path)},
        ]},
    )

    assert empty.status_code == 422
    assert duplicate.status_code == 422


def test_runtime_team_index_api_checks_and_saves_machine_path(tmp_path):
    workspace = tmp_path / "workspace"
    index_source = workspace / "team-index" / "sources" / "source-a"
    index_source.mkdir(parents=True)
    (index_source / "current.json").write_text("{}", encoding="utf-8")
    runtime = RuntimeConfig(
        workspace_root=workspace,
        products=DiscoveredPath(None, "missing"),
        rules=DiscoveredPath(None, "missing"),
        image_sources=(),
        runs_root=workspace / "runs",
    )
    client = create_app(workspace / "runs", runtime_config=runtime).test_client()
    index_root = index_source.parents[1]

    checked = client.post(
        "/api/runtime/team-folder-index/check", json={"path": str(index_root)}
    )
    saved = client.put(
        "/api/runtime/team-folder-index", json={"path": str(index_root)}
    )

    assert checked.status_code == 200
    assert checked.json["snapshot_source_count"] == 1
    assert saved.status_code == 200
    assert saved.json["saved"] is True
    assert saved.json["retry_enqueued"] is False
    config = workspace / "config" / "local-paths.json"
    assert json.loads(config.read_text(encoding="utf-8"))[
        "team_folder_index_root"
    ] == str(index_root.resolve())


def test_folder_picker_api_returns_only_user_selected_directory(
    client, monkeypatch, tmp_path
):
    monkeypatch.setattr(web_module, "choose_directory", lambda initial: str(tmp_path))

    response = client.post(
        "/api/runtime/folder-picker", json={"initial_path": str(tmp_path)}
    )

    assert response.status_code == 200
    assert response.json == {
        "cancelled": False,
        "path": str(tmp_path),
        "reason_code": "FOLDER_PICKER_SELECTED",
    }


def test_folder_picker_api_returns_reason_coded_gui_failure(
    client, monkeypatch
):
    monkeypatch.setattr(
        web_module,
        "choose_directory",
        lambda _initial: (_ for _ in ()).throw(
            web_module.FolderPickerError(
                "FOLDER_PICKER_GUI_UNAVAILABLE"
            )
        ),
    )

    response = client.post(
        "/api/runtime/folder-picker",
        json={"initial_path": r"Z:\未映射"},
    )

    assert response.status_code == 503
    assert response.json["reason_code"] == (
        "FOLDER_PICKER_GUI_UNAVAILABLE"
    )
    assert "手工" in response.json["message"]


def test_folder_picker_api_preserves_cancellation(client, monkeypatch):
    monkeypatch.setattr(
        web_module, "choose_directory", lambda _initial: None
    )

    response = client.post(
        "/api/runtime/folder-picker",
        json={"initial_path": r"C:\existing"},
    )

    assert response.status_code == 200
    assert response.json == {
        "cancelled": True,
        "path": "",
        "reason_code": "FOLDER_PICKER_CANCELLED",
    }


def test_image_source_frontend_uses_diagnostic_copy_and_portable_path_action():
    source = (
        Path(__file__).parents[1]
        / "src"
        / "upload_search_materials"
        / "interaction"
        / "static"
        / "app.js"
    ).read_text(encoding="utf-8")

    assert "diagnostic.reason_code" in source
    assert "diagnostic.message" in source
    assert "portable_path_suggestion" in source
    assert "error.userMessage" in source
    assert "已使用推荐路径，等待检测" in source
    assert "if (!payload.cancelled)" in source


def test_stage_submission_requires_previous_stage_completion(tmp_path):
    app = create_app(tmp_path, enforce_stage_order=True)
    client = app.test_client()
    session_id = client.post("/api/sessions", json={}).json["session_id"]

    response = client.post(
        f"/api/sessions/{session_id}/stages/completeness/submit",
        json={"values": {"selected_product_ids": ["1"]}},
    )

    assert response.status_code == 422
    assert "setup" in response.json["field_errors"]["stage"]


def test_setup_submit_checks_the_sources_currently_shown_on_the_page(
    client, session_id, tmp_path
):
    missing = tmp_path / "new-source-that-does-not-exist"
    response = client.post(
        f"/api/sessions/{session_id}/stages/setup/submit",
        json={
            "values": {
                "store": "test",
                "store_confirmed": True,
                "products_csv": str(tmp_path),
                "rules_csv": str(tmp_path),
                "image_source_labels": ["本次新增图片源"],
                "image_roots": [str(missing)],
                "folder_index_root": str(tmp_path / ".index"),
                "team_folder_index_root": str(tmp_path),
                "asset_manifest": "",
                "historical_basic_xlsx": "",
                "historical_promotion_csv": "",
                "user_notes": "",
            }
        },
    )

    assert response.status_code == 422
    assert "image_roots" in response.json["field_errors"]
    assert "本次新增图片源" in response.json["field_errors"]["image_roots"]


def test_setup_submit_persists_image_sources_for_team_index_binding(tmp_path):
    workspace = Path(__file__).parents[1]
    config_path = tmp_path / "config" / "runtime.json"
    image_root = tmp_path / "images"
    team_index_root = tmp_path / "team-index"
    products_csv = tmp_path / "products.csv"
    rules_csv = tmp_path / "rules.csv"
    image_root.mkdir()
    team_index_root.mkdir()
    products_csv.write_text("product_id\n1\n", encoding="utf-8")
    rules_csv.write_text("product_id\n1\n", encoding="utf-8")
    runtime = RuntimeConfig(
        workspace_root=workspace,
        products=DiscoveredPath(None, "missing"),
        rules=DiscoveredPath(None, "missing"),
        image_sources=(),
        runs_root=tmp_path / "runs",
        config_path=config_path,
        user_data_root=tmp_path,
    )
    app = create_app(
        runtime.runs_root,
        runtime_config=runtime,
        enforce_stage_order=False,
    )
    client = app.test_client()
    session_id = client.post("/api/sessions", json={}).json["session_id"]

    response = client.post(
        f"/api/sessions/{session_id}/stages/setup/submit",
        json={
            "values": {
                "store": "测试店铺",
                "store_confirmed": True,
                "products_csv": str(products_csv),
                "rules_csv": str(rules_csv),
                "team_folder_index_root": str(team_index_root),
                "image_source_labels": ["测试图片源"],
                "image_roots": [str(image_root)],
                "asset_manifest": "",
                "historical_basic_xlsx": "",
                "historical_promotion_csv": "",
                "user_notes": "",
            }
        },
    )

    assert response.status_code == 202
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["team_folder_index_root"] == str(team_index_root.resolve())
    assert saved["image_sources"] == [
        {
            "source_id": saved["image_sources"][0]["source_id"],
            "label": "测试图片源",
            "path": str(image_root.resolve()),
        }
    ]
    assert saved["image_sources"][0]["source_id"].startswith("source-")
    configured = client.get("/api/runtime/image-sources")
    assert configured.status_code == 200
    assert configured.json["image_sources"] == saved["image_sources"]


def test_unclaimed_submission_can_be_withdrawn_but_processing_cannot(
    client, session_id, tmp_path
):
    readable = tmp_path / "table.csv"
    readable.write_text("ok", encoding="utf-8")
    values = {
        "store": "test",
        "store_confirmed": True,
        "month": "2026-07",
        "products_csv": str(readable),
        "rules_csv": str(readable),
        "team_folder_index_root": str(tmp_path),
        "image_source_labels": ["source"],
        "image_roots": [str(tmp_path)],
        "asset_manifest": "",
        "historical_basic_xlsx": "",
        "historical_promotion_csv": "",
        "user_notes": "",
    }
    submitted = client.post(
        f"/api/sessions/{session_id}/stages/setup/submit",
        json={"values": values},
    )
    withdrawn = client.post(
        f"/api/sessions/{session_id}/stages/setup/withdraw",
        json={"revision": submitted.json["revision"]},
    )

    assert withdrawn.status_code == 200
    assert withdrawn.json["status"] == "draft"

    submitted_again = client.post(
        f"/api/sessions/{session_id}/stages/setup/submit",
        json={"values": {**values, "store": "test again"}},
    )
    SessionStore(tmp_path).wait_for_handoff(session_id, "setup", timeout_seconds=0.1)
    rejected = client.post(
        f"/api/sessions/{session_id}/stages/setup/withdraw",
        json={"revision": submitted_again.json["revision"]},
    )

    assert rejected.status_code == 409


@pytest.mark.parametrize(
    ("current_stage", "target_stage", "target_title"),
    (
        ("completeness", "setup", "任务配置"),
        ("asset_matching", "completeness", "完整度巡检"),
        ("slots_copy", "asset_matching", "素材匹配"),
        ("approval", "slots_copy", "坑位编排、图片处理与文案"),
    ),
)
def test_current_stage_can_return_to_previous_editable_stage(
    client,
    session_id,
    tmp_path,
    current_stage,
    target_stage,
    target_title,
):
    store = SessionStore(tmp_path)
    state = store.load_session(session_id)
    state["current_stage"] = current_stage
    state["stages"][current_stage] = {
        "revision": 3,
        "status": "needs_user_input",
    }
    store._write_session_state(session_id, state)

    stage = client.get(
        f"/api/sessions/{session_id}/stages/{current_stage}"
    )

    assert stage.status_code == 200
    assert stage.json["back_navigation"]["visible"] is True
    assert stage.json["back_navigation"]["enabled"] is True
    assert stage.json["back_navigation"]["from_stage_id"] == current_stage
    assert stage.json["back_navigation"]["target_stage_id"] == target_stage
    assert stage.json["back_navigation"]["target_stage_title"] == target_title
    assert stage.json["back_navigation"]["label"] == f"上一步：{target_title}"
    assert stage.json["back_navigation"]["message"]
    assert stage.json["back_navigation"]["reason_code"] == ""
    reopened = client.post(
        f"/api/sessions/{session_id}/stages/{current_stage}/back",
        json={"revision": 3},
    )

    assert reopened.status_code == 200
    assert reopened.json["target_stage_id"] == target_stage
    assert store.load_session(session_id)["current_stage"] == target_stage


def test_gallery_loading_temporarily_disables_previous_stage(
    client, session_id, tmp_path
):
    store = SessionStore(tmp_path)
    state = store.load_session(session_id)
    state["current_stage"] = "asset_matching"
    state["stages"]["asset_matching"] = {
        "revision": 1,
        "status": "needs_user_input",
    }
    store._write_session_state(session_id, state)
    store._write_json_atomic(
        store._stage_path(session_id, "asset_matching") / "gallery-job.json",
        {"schema_version": 1, "status": "running"},
    )

    status = client.get(
        f"/api/sessions/{session_id}/stages/asset_matching/status"
    )

    assert status.status_code == 200
    assert status.json["back_navigation"]["visible"] is True
    assert status.json["back_navigation"]["enabled"] is False
    assert (
        status.json["back_navigation"]["reason_code"]
        == "STAGE_BACK_GALLERY_ACTIVE"
    )


def test_returning_to_product_selection_archives_task_work_but_keeps_previews(
    client, session_id, tmp_path
):
    store = SessionStore(tmp_path)
    state = store.load_session(session_id)
    state["current_stage"] = "asset_matching"
    state["stages"]["asset_matching"] = {
        "revision": 1,
        "status": "needs_user_input",
    }
    store._write_session_state(session_id, state)
    asset_path = store._stage_path(session_id, "asset_matching")
    slots_path = store._stage_path(session_id, "slots_copy")
    store._write_json_atomic(
        asset_path / "gallery-job.json",
        {"schema_version": 1, "status": "completed"},
    )
    store._write_json_atomic(
        asset_path / "confirmed-gallery.json",
        {"schema_version": 1},
    )
    preview = asset_path / "preview-cache" / "asset.jpg"
    preview.parent.mkdir()
    preview.write_bytes(b"task-local-preview")
    store._write_json_atomic(
        slots_path / "current-slot-plan.json",
        {"schema_version": 1},
    )
    requests = slots_path / "agent-requests" / "completed-request"
    requests.mkdir(parents=True)
    store._write_json_atomic(
        requests / "request.json",
        {"schema_version": 1, "status": "completed"},
    )

    reopened = client.post(
        f"/api/sessions/{session_id}/stages/asset_matching/back",
        json={"revision": 1},
    )

    assert reopened.status_code == 200
    assert not (asset_path / "gallery-job.json").exists()
    assert not (asset_path / "confirmed-gallery.json").exists()
    assert preview.is_file()
    assert not (slots_path / "current-slot-plan.json").exists()
    assert not (slots_path / "agent-requests").exists()
    archived_asset = list(
        (asset_path / "invalidated").glob("back-*/gallery-job.json")
    )
    archived_slots = list(
        (slots_path / "invalidated").glob("back-*/current-slot-plan.json")
    )
    assert len(archived_asset) == 1
    assert len(archived_slots) == 1


def test_returning_to_product_selection_restores_inspection_matrix_fallback(
    client,
    session_id,
    tmp_path,
):
    store = SessionStore(tmp_path)
    setup = store.save_input(session_id, "setup", valid_setup_values(tmp_path))
    store.write_result(
        session_id,
        "setup",
        setup["revision"],
        setup["input_sha256"],
        status="completed",
        summary="setup completed",
    )
    completeness_matrix = sample_completeness_matrix()
    completeness_path = store._stage_path(session_id, "completeness")
    store._write_json_atomic(
        completeness_path / "completeness-matrix.json",
        completeness_matrix,
    )
    completeness = store.save_input(
        session_id,
        "completeness",
        {"selected_product_ids": ["898439684957"]},
    )
    store.write_result(
        session_id,
        "completeness",
        completeness["revision"],
        completeness["input_sha256"],
        status="completed",
        summary="product selection completed",
    )
    asset_matching = store.save_input(
        session_id,
        "asset_matching",
        {"image_roots": valid_setup_values(tmp_path)["image_roots"]},
    )
    state = store.load_session(session_id)
    state["current_stage"] = "asset_matching"
    state["stages"]["asset_matching"]["status"] = "needs_user_input"
    store._write_session_state(session_id, state)

    reopened = client.post(
        f"/api/sessions/{session_id}/stages/asset_matching/back",
        json={"revision": asset_matching["revision"]},
    )

    assert reopened.status_code == 200
    assert not (completeness_path / "result.json").exists()
    assert not (completeness_path / "review-context.json").exists()

    stage = client.get(f"/api/sessions/{session_id}/stages/completeness")

    assert stage.status_code == 200
    assert stage.json["result"]["fallback_source"] == "completeness-matrix.json"
    product = stage.json["result"]["data"]["products"][0]
    assert product["product_id"] == "898439684957"
    assert product["promotion"]["missing_count"] == 5

    drafted = client.post(
        f"/api/sessions/{session_id}/stages/completeness/draft",
        json={
            "revision": reopened.json["target_revision"],
            "values": {"selected_product_ids": ["898439684957"]},
        },
    )
    after_autosave = client.get(
        f"/api/sessions/{session_id}/stages/completeness"
    )

    assert drafted.status_code == 200
    assert drafted.json["status"] == "draft"
    assert after_autosave.status_code == 200
    assert after_autosave.json["state"]["status"] == "draft"
    assert (
        after_autosave.json["result"]["fallback_source"]
        == "completeness-matrix.json"
    )
    assert (
        after_autosave.json["result"]["data"]["products"][0]["product_id"]
        == "898439684957"
    )


def test_returning_from_slots_restores_confirmed_asset_gallery(
    client,
    session_id,
    tmp_path,
):
    store = SessionStore(tmp_path)
    folder_decision = {
        "product_id": "898439684957",
        "folder_id": "folder-1",
        "source_system": "视觉部",
        "source_id": "source-1",
        "relative_path": "泳衣/椰椰小岛",
        "folder_path": "泳衣/椰椰小岛",
        "decision": "confirmed",
    }
    asset = store.save_draft(
        session_id,
        "asset_matching",
        {
            "folder_decisions": [folder_decision],
            "asset_decisions": [],
        },
        expected_revision=0,
    )
    asset_path = store._stage_path(session_id, "asset_matching")
    store._write_json_atomic(
        asset_path / "confirmed-gallery.json",
        {
            "schema_version": 1,
            "workflow_step": "image_selection",
            "gallery_complete": True,
            "requirements": [
                {
                    "product_id": "898439684957",
                    "product_title": "椰椰小岛两栖泳衣",
                }
            ],
            "folder_candidates": [folder_decision],
            "asset_candidates": [
                {
                    "product_id": "898439684957",
                    "asset_id": "asset-1",
                    "folder_id": "folder-1",
                }
            ],
            "gallery_identity": {
                "session_id": session_id,
                "stage_id": "asset_matching",
                "prepared_from_revision": asset["revision"],
                "prepared_from_input_sha256": hashlib.sha256(
                    (asset_path / "input.json").read_bytes()
                ).hexdigest(),
                "prepared_folder_keys": [
                    {
                        "product_id": "898439684957",
                        "folder_id": "folder-1",
                    }
                ],
            },
        },
    )
    state = store.load_session(session_id)
    state["current_stage"] = "slots_copy"
    state["stages"]["asset_matching"]["status"] = "completed"
    state["stages"]["slots_copy"] = {
        "revision": 0,
        "status": "needs_user_input",
    }
    store._write_session_state(session_id, state)

    reopened = client.post(
        f"/api/sessions/{session_id}/stages/slots_copy/back",
        json={"revision": 0},
    )
    stage = client.get(
        f"/api/sessions/{session_id}/stages/asset_matching"
    )

    assert reopened.status_code == 200
    assert stage.status_code == 200
    assert stage.json["result"]["fallback_source"] == "confirmed-gallery.json"
    assert stage.json["result"]["data"]["workflow_step"] == "image_selection"
    assert stage.json["result"]["data"]["asset_candidates"][0]["asset_id"] == "asset-1"


def test_completeness_submit_rejects_products_with_full_material_slots(
    client,
    session_id,
    tmp_path,
):
    store = SessionStore(tmp_path)
    state = store.load_session(session_id)
    state["current_stage"] = "completeness"
    state["stages"]["completeness"] = {
        "revision": 0,
        "status": "needs_user_input",
    }
    store._write_session_state(session_id, state)
    store.write_review_context(
        session_id,
        "completeness",
        {
            "schema_version": 1,
            "session_id": session_id,
            "stage_id": "completeness",
            "revision": 0,
            "status": "needs_user_input",
            "summary": "完整度巡检",
            "data": {
                "products": [
                    {
                        "product_id": "full-product",
                        "status": "complete",
                        "selectable": True,
                        "promotion": {
                            "target_slots": 3,
                            "current_count": 3,
                            "missing_count": 0,
                        },
                    },
                    {
                        "product_id": "open-product",
                        "status": "needs_supplement",
                        "selectable": True,
                        "promotion": {
                            "target_slots": 3,
                            "current_count": 2,
                            "missing_count": 1,
                        },
                    },
                ]
            },
        },
    )

    response = client.post(
        f"/api/sessions/{session_id}/stages/completeness/submit",
        json={"values": {"selected_product_ids": ["full-product"]}},
    )

    assert response.status_code == 422
    assert "坑位已经填满" in response.json["field_errors"]["selected_product_ids"]


def test_completeness_reinspect_resubmits_setup_and_archives_downstream_work(
    client,
    session_id,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        web_module,
        "inspect_team_folder_index_root",
        lambda runtime, path: {"status": "available", "message": "可用"},
    )
    monkeypatch.setattr(
        web_module,
        "inspect_image_sources",
        lambda runtime, sources: [
            {**source, "status": "available", "message": "可用"}
            for source in sources
        ],
    )
    store = SessionStore(tmp_path)
    setup_values = valid_setup_values(tmp_path)
    setup = store.save_input(session_id, "setup", setup_values)
    store.write_result(
        session_id,
        "setup",
        setup["revision"],
        setup["input_sha256"],
        status="completed",
        summary="setup completed",
    )
    completeness_matrix = sample_completeness_matrix()
    completeness_path = store._stage_path(session_id, "completeness")
    store._write_json_atomic(
        completeness_path / "completeness-matrix.json",
        completeness_matrix,
    )
    store.write_review_context(
        session_id,
        "completeness",
        {
            "schema_version": 1,
            "session_id": session_id,
            "stage_id": "completeness",
            "revision": 0,
            "status": "needs_user_input",
            "summary": "请选择商品",
            "blocking_reasons": [],
            "evidence": [],
            "next_action": "选择商品并提交给工作台",
            "data": completeness_matrix,
        },
    )
    state = store.load_session(session_id)
    state["current_stage"] = "completeness"
    store._write_session_state(session_id, state)
    asset_path = store._stage_path(session_id, "asset_matching")
    slots_path = store._stage_path(session_id, "slots_copy")
    store._write_json_atomic(
        asset_path / "gallery-job.json",
        {"schema_version": 1, "status": "completed"},
    )
    store._write_json_atomic(
        slots_path / "current-slot-plan.json",
        {"schema_version": 1},
    )

    response = client.post(
        f"/api/sessions/{session_id}/stages/completeness/reinspect",
        json={"request_id": "reinspect-test-0001"},
    )

    assert response.status_code == 202
    assert response.json["target_stage_id"] == "setup"
    state = store.load_session(session_id)
    assert state["current_stage"] == "setup"
    assert state["stages"]["setup"]["status"] == "ready_for_agent"
    assert state["stages"]["setup"]["revision"] == setup["revision"] + 1
    assert state["stages"]["completeness"]["status"] == "draft"
    handoff = store.read_optional_stage_document(session_id, "setup", "handoff")
    assert handoff["reinspection"]["requested_from_stage"] == "completeness"
    assert not (asset_path / "gallery-job.json").exists()
    assert not (slots_path / "current-slot-plan.json").exists()
    assert list((asset_path / "invalidated").glob("reinspect-*/gallery-job.json"))
    assert list((slots_path / "invalidated").glob("reinspect-*/current-slot-plan.json"))


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


def test_page_explains_workbench_offline_recovery_without_exposing_task_path(client):
    html = client.get("/").get_data(as_text=True)

    assert 'data-task-awareness' in html
    assert "整个任务" in html
    assert "这里会持续显示当前进度和下一步操作" in html
    assert "当前任务暂时无法连接" in html
    assert "查看排查信息" in html
    assert "复制异常诊断说明" in html
    assert "当前任务目录" in html
    assert 'class="technical-only" aria-hidden="true"><dt>当前任务目录' in html
    assert "当前阶段" in html
    assert "最近一次提交时间" in html
    assert 'data-end-current-task' in html
    assert "结束当前任务" in html


def test_end_current_task_uses_managed_shutdown_api_and_preserves_recovery(client):
    javascript = client.get("/static/app.js").get_data(as_text=True)

    assert 'apiPath("/end")' in javascript
    assert "任务记录会保留，之后仍可恢复" in javascript
    assert 'endCurrentTaskButton?.addEventListener("click", endCurrentTask)' in javascript


def test_destructive_actions_use_workbench_confirmation_modal(client):
    html = client.get("/").get_data(as_text=True)
    javascript = client.get("/static/app.js").get_data(as_text=True)
    stylesheet = client.get("/static/app.css").get_data(as_text=True)

    assert 'data-confirmation-modal' in html
    assert 'role="dialog"' in html
    assert 'aria-modal="true"' in html
    assert "function confirmAction" in javascript
    assert "window.confirm(" not in javascript
    for label in (
        "删除图片源",
        "去掉当前商品",
        "去掉当前坑位",
        "返回上一步",
        "结束当前任务",
    ):
        assert label in javascript
    assert ".confirmation-modal" in stylesheet
    assert "body.confirmation-modal-open" in stylesheet


def test_page_has_all_reusable_stage_renderers_and_exact_match_labels(client):
    html = client.get("/").get_data(as_text=True)
    components = {
        "InspectionMatrix",
        "AssetMatchGallery",
        "CropDecision",
        "SlotBoard",
        "CopyEditor",
        "DryRunSummary",
        "ApprovalChecklist",
        "ProductionConfirmation",
        "UploadResults",
    }

    for component in components:
        assert f'data-component="{component}"' in html
    for label in ("商品 ID 命中", "SKU 命中", "完整商品名称命中 · 待确认"):
        assert label in html
    assert "已确认别名" not in html


def test_completeness_stage_exposes_review_controls_without_raw_json_as_primary_ui(client):
    html = client.get("/").get_data(as_text=True)

    assert "搜推素材完整度" in html
    assert "目标 / 已有 / 缺失 / 证据" not in html
    assert 'data-field="selected_product_ids"' in html
    assert html.count("interaction-data-field") >= 1

    script = client.get("/static/app.js").get_data(as_text=True)
    for text in (
        "搜索商品 ID、货号或名称",
        "全部负责人",
        "未分配负责人",
        "筛选负责人",
        "选择当前筛选结果",
        "取消当前筛选结果",
        "重新巡检",
        "/stages/completeness/reinspect",
        "已自动排除",
        "命中自动排除规则",
        "产品等级",
        "查看后台证据",
    ):
        assert text in script
    inspection_script = script.split("function renderInspectionMatrix", 1)[1].split(
        "function readJsonListControl", 1
    )[0]
    assert "选择进入素材匹配" not in inspection_script
    assert 'card.setAttribute("role", "checkbox")' in inspection_script
    assert 'card.addEventListener("click"' in inspection_script
    assert 'card.addEventListener("keydown"' in inspection_script
    assert 'stateBadge.textContent = selectable' in inspection_script
    assert '["needs_manual_review", "需人工确认"],' not in script
    assert '["需人工确认", statusCounts.needs_manual_review || 0],' not in script
    assert 'element("small", "", "基础素材")' not in script
    assert 'if (!event.target?.getAttribute?.("name")) return;' in script


def test_collection_progress_uses_concise_business_summary(client):
    script = client.get("/static/app.js").get_data(as_text=True)

    assert "采集 Worker 正在执行" not in script
    assert "恢复建议：" not in script
    assert "`${page}；${pagination}`" in script
    assert "worker.pagination_reason_code" not in script


def test_page_uses_explicit_empty_states_without_fabricated_counts(client):
    html = client.get("/").get_data(as_text=True)

    assert html.count('data-empty-state="尚未扫描"') >= 9
    assert "18 张" not in html


def test_unscanned_slot_board_has_no_fabricated_slot_count(client):
    html = client.get("/").get_data(as_text=True)

    assert "3 / 9 坑位" not in html
    assert 'data-component="SlotBoard"' in html
    assert 'data-empty-state="尚未扫描"' in html


def test_fifth_stage_supports_two_page_deterministic_ui_without_raw_json_controls(
    client,
):
    html = client.get("/").get_data(as_text=True)
    javascript = client.get("/static/app.js").get_data(as_text=True)

    assert re.search(
        r'<input[^>]+name="slot_assignments"[^>]+type="hidden"', html
    )
    assert not re.search(
        r'<textarea[^>]+name="(?:slot_assignments|copy_edits)"', html
    )
    for text in (
        "候选素材与坑位编排",
        "图片裁剪与压缩",
        "AI 标题与描述",
        "确认坑位并进入图片裁剪",
        "完成图片处理并进入文案生成",
        "进入上传任务确认",
        "重新生成新版本",
        "重新生成会创建独立新版本，不覆盖历史版本，也不会发布。",
        "全部标题和描述已填写，可以进入上传任务确认",
    ):
        assert text in javascript
    assert "已确认标题、描述，可进入上传任务确认" not in javascript
    assert "返回图片裁剪" not in javascript
    assert '? ["process", "copy"]' in javascript
    assert "请求 ID：" not in javascript
    assert 'detail: { source: "explicit-user-edit" }' in javascript
    assert (
        'event.detail?.source !== "explicit-user-edit"' in javascript
    )


def test_copy_versions_restore_the_selected_response_and_persist_loaded_drafts(client):
    javascript = client.get("/static/app.js").get_data(as_text=True)

    assert 'const renderCopyEditor = (processed, selectedRequestId = "")' in javascript
    assert "UiState.copyRequestVersionView" in javascript
    assert "UiState.copyDraftsForVersion" in javascript
    assert 'request_id: String(existing.request_id || "")' in javascript
    assert "当前草稿 · 未绑定 AI 版本" in javascript
    assert "hasMeaningfulCopyDrafts(savedCopyItems)" in javascript
    assert "applyCopyDrafts([], copyRequest.request_id)" in javascript
    assert "版本 ${item.version_number} · ${item.status_label}" in javascript
    assert '"裁剪预校验"' in javascript
    assert 'apiPath("/stages/slots_copy/crop-preflight")' in javascript
    assert 'copyButton.textContent = "重新生成新版本"' in javascript
    assert "copy-skipped-notice" in javascript
    assert "已重试 3 次仍未完成，系统已跳过" in javascript
    assert "当前坑位正在重试 ${retryCount}/3" in javascript
    assert '"生成标题与描述"' not in javascript
    assert re.search(
        r'new CustomEvent\(\s*"input",\s*'
        r'\{ bubbles: true, detail: \{ source: "explicit-user-edit" \} \},',
        javascript,
    )


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
    asset_group = re.search(
        r'<fieldset[^>]+data-field-group="image_roots"[^>]*>(.*?)</fieldset>',
        decoded,
        re.DOTALL,
    )
    assert asset_group is not None
    assert asset_group.group(1).count('name="image_roots"') == 3


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

    assert "initialCompletenessReview" in javascript

    assert "编辑中" in javascript
    assert "已提交，等待工作台处理" in javascript
    assert "工作台处理中" in javascript
    assert "补充后重新提交" in javascript
    assert "当前任务已连接" in javascript
    assert "当前任务正在处理" in javascript
    assert "当前任务暂时无法连接" in javascript
    assert "technicalDiagnosticView" in javascript
    assert "需要重新提交" in javascript
    assert "重新提交到工作台" in javascript
    assert "等待系统恢复" not in javascript
    assert "|| technicalDiagnostic.active" not in javascript
    assert "maxConcurrent: 6" in javascript
    assert "maxWeight: 8" in javascript
    assert "排队中，可再次点击取消" in javascript
    assert "已取消选择；后台结果仅用于缓存" in javascript
    assert "await hydrateSelectionPreflights();" in javascript
    assert "正在恢复已选图片的预裁剪检查状态" in javascript
    assert "已选图片的预裁剪结果无法确认，请取消后重新选择" not in javascript
    hydration_wait = javascript.index("await hydrateSelectionPreflights();")
    assert hydration_wait < javascript.index(
        "const form = activeForm();", hydration_wait
    )
    assert "backNavigationChanged" in javascript
    assert "仍有图片检测在后台进行；返回上一步会放弃当前素材选择。" in javascript
    back_enabled_expression = re.search(
        r"const backEnabled = Boolean\((.*?)\);\s*backButton\.hidden",
        javascript,
        re.DOTALL,
    )
    assert back_enabled_expression is not None
    assert "!localBackLockActive" in back_enabled_expression.group(1)
    assert "!selectionCheckActive" not in back_enabled_expression.group(1)
    assert 'currentStageId === "slots_copy" && localCopyRequestInFlight' in javascript
    assert "setLocalCopyRequestInFlight(true)" in javascript
    assert (
        '["completeness", "asset_matching", "slots_copy"].includes(currentStageId)'
        in javascript
    )
    assert "pendingBackNavigation" in javascript
    assert "selectionPreflightConcurrency = 3" not in javascript
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


def test_javascript_supports_dynamic_image_source_configuration(client):
    javascript = client.get("/static/app.js").get_data(as_text=True)

    for expected in (
        "appendImageSource",
        "hydrateImageSources",
        "configuredImageSources",
        "disambiguateImageSourceLabels",
        "已根据业务目录自动区分",
        "/api/runtime/image-sources/check",
        "/api/runtime/team-folder-index/check",
        "/api/runtime/folder-picker",
        'method: "PUT"',
        "每个图片源都必须填写来源名称并选择图片文件夹",
        "scheduleAutoSave",
        "setFormLocked",
        "/withdraw",
        "focusFirstIncompleteImageSource",
        'row.scrollIntoView({ behavior: "smooth", block: "center" })',
        "还有 ${count} 个图片源未填写完整",
        "需要选择团队索引文件夹",
        "data-machine-runtime-control",
    ):
        assert expected in javascript

    html = client.get("/").get_data(as_text=True)
    assert "data-setup-image-source-incomplete" in html


def test_javascript_exposes_distinct_login_gate_copy(client):
    javascript = client.get("/static/app.js").get_data(as_text=True)
    backend = (
        Path(__file__).parents[1]
        / "src"
        / "upload_search_materials"
        / "interaction"
        / "web.py"
    ).read_text(encoding="utf-8")

    for expected in (
        "工作台服务未运行",
        "专用 Chrome 未启动",
        "已连接 Chrome，正在打开千牛",
        "千牛页面已打开，等待用户登录",
        "已登录，但暂未识别店铺",
        "已识别店铺，可以配置",
    ):
        assert expected in javascript or expected in backend


def test_javascript_selects_result_renderers_by_schema_component(client):
    javascript = client.get("/static/app.js").get_data(as_text=True)

    assert "resultRenderers" in javascript
    for component in {stage.component for stage in STAGES if stage.id != "setup"}:
        assert f'"{component}"' in javascript


def test_asset_matching_hides_local_evidence_paths_from_user_result(client):
    javascript = client.get("/static/app.js").get_data(as_text=True)

    assert 'componentName !== "AssetMatchGallery"' in javascript


def test_results_form_is_hidden_and_has_no_recovery_controls(client):
    html = client.get("/").get_data(as_text=True)

    assert re.search(r'<form[^>]+data-results-recovery[^>]+hidden', html)
    for field_name in ("recovery_action", "manual_notes", "allow_retry_after_remote_absence"):
        assert f'name="{field_name}"' not in html


def test_result_modules_keep_persistent_content_containers(client):
    html = client.get("/").get_data(as_text=True)

    assert html.count("data-result-content") == 9


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
    assert "--control-height: 44px" in stylesheet
    assert ".image-source-row-actions" in stylesheet
    assert ".image-source-config { grid-column: 1 / -1;" in stylesheet
    assert ".source-section-shared" not in stylesheet
    assert ".task-awareness" in stylesheet
    assert ".source-section-selected" in stylesheet
    assert "background: #eef4fa;" in stylesheet
    assert ".source-section-heading" in stylesheet
    assert "grid-template-columns: minmax(0, .65fr) minmax(0, 1.45fr) minmax(0, .9fr);" in stylesheet
    assert "container: image-source-list / inline-size;" in stylesheet
    assert "@container image-source-list (max-width: 760px)" in stylesheet
    assert "@container image-source-list (max-width: 520px)" in stylesheet
    assert '.image-source-row[data-has-candidates="true"]' in stylesheet
    assert 'grid-template-areas: "name" "path" "choice" "actions" "state"' in stylesheet
    assert ".nas-source-actions" not in stylesheet
    assert 'grid-template-areas: "name" "path" "actions" "state"' in stylesheet
    assert ".handoff-actions button { width: 100%; min-width: 0; }" in stylesheet


def test_results_stage_rejects_ordinary_submission_without_creating_handoff(
    client, session_id, tmp_path
):
    response = client.post(
        f"/api/sessions/{session_id}/stages/results/submit",
        json={"values": {}},
    )

    assert response.status_code == 409
    results_directory = tmp_path / session_id / "09-results"
    assert not (results_directory / "input.json").exists()
    assert not (results_directory / "handoff.json").exists()


@pytest.mark.parametrize("status", ["draft", "needs_user_input", "blocked"])
def test_results_stage_never_accepts_submissions(
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

    assert response.status_code == 409
    assert response.json["error"] == "read-only stage does not accept submissions"


def test_results_stage_projects_approval_uploads_by_product(
    client, session_id, tmp_path
):
    store = SessionStore(tmp_path)
    handoff = store.save_input(
        session_id,
        "approval",
        {},
    )

    store.write_result(
        session_id,
        "approval",
        handoff["revision"],
        handoff["input_sha256"],
        status="completed",
        summary="upload finished",
        data={
            "store": "测试店铺",
            "tasks": [
                {
                    "task_id": "task-1",
                    "status": "submitted",
                    "remote_material_id": "remote-1",
                },
                {
                    "task_id": "task-2",
                    "status": "failed",
                    "remote_material_id": None,
                },
            ],
        },
    )
    store._write_json_atomic(
        store._session_path(session_id) / "approval-manifest.json",
        {
            "entries": [
                {"task_id": "task-1", "product_id": "1001", "slot_index": 3},
                {"task_id": "task-2", "product_id": "1002", "slot_index": 4},
            ]
        },
    )
    inputs = store._session_path(session_id) / "inputs"
    inputs.mkdir(exist_ok=True)
    (inputs / "products.csv").write_text(
        "商品ID,货号（查找引用）,商品名称（查找引用）,产品等级,链接,运营,组别,品类-公司维度划分\n"
        "1001,SKU-1,商品一,A级,,,,\n"
        "1002,SKU-2,商品二,A级,,,,\n",
        encoding="utf-8",
    )

    result_response = client.get(
        f"/api/sessions/{session_id}/stages/results"
    )
    assert result_response.status_code == 200
    result = result_response.json["result"]
    assert result["summary"] == "上传结束：1 个商品成功，1 个商品未全部成功"
    assert result["data"]["products"] == [
        {
            "product_id": "1001",
            "product_name": "商品一",
            "status": "success",
            "status_label": "上传成功",
            "task_count": 1,
            "success_count": 1,
            "failed_count": 0,
            "materials": [
                {
                    "slot_index": 3,
                    "remote_material_id": "remote-1",
                    "status": "success",
                    "status_label": "上传成功",
                }
            ],
        },
        {
            "product_id": "1002",
            "product_name": "商品二",
            "status": "failed",
            "status_label": "上传失败",
            "task_count": 1,
            "success_count": 0,
            "failed_count": 1,
            "materials": [
                {
                    "slot_index": 4,
                    "remote_material_id": "",
                    "status": "failed",
                    "status_label": "上传失败",
                }
            ],
        },
    ]
    session_response = client.get(f"/api/sessions/{session_id}")
    assert session_response.json["session"]["current_stage"] == "results"


def test_approval_review_tasks_do_not_project_as_upload_failures(
    client, session_id, tmp_path
):
    store = SessionStore(tmp_path)
    with store._session_lock(session_id):
        state = store.load_session(session_id)
        state["current_stage"] = "approval"
        state["stages"]["approval"]["status"] = "needs_user_input"
        store._write_session_state(session_id, state)
    store.write_review_context(
        session_id,
        "approval",
        {
            "schema_version": 1,
            "session_id": session_id,
            "stage_id": "approval",
            "revision": 0,
            "status": "needs_user_input",
            "summary": "review upload tasks",
            "blocking_reasons": [],
            "evidence": [],
            "next_action": "select exact tasks",
            "data": {
                "tasks": [
                    {"task_id": "task-1", "status": "ready_for_review"}
                ]
            },
        },
    )

    session_response = client.get(f"/api/sessions/{session_id}")
    result_response = client.get(
        f"/api/sessions/{session_id}/stages/results"
    )

    assert session_response.json["session"]["current_stage"] == "approval"
    assert result_response.json["result"] is None


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


def test_removed_setup_month_from_stale_page_is_ignored(
    client, session_id, tmp_path
):
    response = client.post(
        f"/api/sessions/{session_id}/stages/setup/draft",
        json={
            "values": {
                "store": "测试店铺",
                "month": "2026-07",
            },
            "revision": 0,
        },
    )

    assert response.status_code == 200
    document = json.loads(
        (tmp_path / session_id / "01-setup" / "input.json").read_text(
            encoding="utf-8"
        )
    )
    assert "month" not in document["values"]


def test_draft_is_saved_without_handoff(client, session_id, tmp_path):
    response = client.post(
        f"/api/sessions/{session_id}/stages/setup/draft",
        json={"values": {"store": "测试店铺"}, "revision": 0},
    )

    assert response.status_code == 200
    assert response.json["status"] == "draft"
    assert response.json["revision"] == 1
    stage_path = tmp_path / session_id / "01-setup"
    assert not (stage_path / "handoff.json").exists()
    assert (stage_path / "revisions" / "0001" / "input.json").is_file()
    assert not (stage_path / "revisions" / "0001" / "handoff.json").exists()


def test_draft_request_id_is_idempotent_and_content_bound(
    client, session_id, tmp_path
):
    request = {
        "values": {"store": "测试店铺"},
        "revision": 0,
        "request_id": "web-draft-idempotent-0001",
    }

    first = client.post(
        f"/api/sessions/{session_id}/stages/setup/draft", json=request
    )
    duplicate = client.post(
        f"/api/sessions/{session_id}/stages/setup/draft", json=request
    )
    conflict = client.post(
        f"/api/sessions/{session_id}/stages/setup/draft",
        json={
            **request,
            "revision": 1,
            "values": {"store": "不同内容"},
        },
    )

    assert first.status_code == 200
    assert duplicate.status_code == 200
    assert duplicate.json == first.json
    assert conflict.status_code == 409
    assert conflict.json["reason_code"] == "REVISION_CONTENT_CONFLICT"
    events = [
        json.loads(line)
        for line in (tmp_path / session_id / "events.ndjson")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert sum(
        event.get("persistence_request_id") == request["request_id"]
        for event in events
    ) == 1


def test_frontend_keeps_request_id_across_retry_and_reloads_conflict(client):
    script = client.get("/static/app.js").get_data(as_text=True)

    assert "persistenceRequestIds" in script
    assert "body.request_id = persistenceIdentity.value" in script
    assert "await loadStage();" in script
    assert "waitStillLive" in script
    assert "等待处理期间仍可继续编辑" in script


def test_frontend_back_navigation_awaits_stage_hydration_without_empty_render(client):
    script = client.get("/static/app.js").get_data(as_text=True)
    activate = script.split("async function activateStage(stageId)", 1)[1].split(
        'railButtons.forEach((button) => {\n    button.addEventListener', 1
    )[0]
    reopen = script.split("async function reopenPreviousStage", 1)[1].split(
        "async function endCurrentTask", 1
    )[0]
    poll = script.split("async function pollStage()", 1)[1].split(
        "async function activateStage", 1
    )[0]

    assert "renderStageResult(stage.component)" not in activate
    assert "await loadStage();" in activate
    assert "await activateStage(payload.target_stage_id);" in reopen
    assert "UiState.stageNeedsResultHydration" in poll


def test_frontend_discards_stale_stage_loads_and_bypasses_dynamic_cache(client):
    script = client.get("/static/app.js").get_data(as_text=True)
    load_stage = script.split("async function loadStage()", 1)[1].split(
        "async function prepareLocalGallery", 1
    )[0]

    assert 'cache: "no-store"' in script
    assert "requestedLoadSequence = ++stageLoadSequence" in load_stage
    assert "requestedGeneration !== stageGeneration" in load_stage
    assert "requestedLoadSequence !== stageLoadSequence" in load_stage
    assert "resetFormForAuthoritativeHydration(form);" in load_stage
    reset_helper = script.split(
        "function resetFormForAuthoritativeHydration(form)", 1
    )[1].split("function selectedAssetDecisions", 1)[0]
    assert "form.reset();" in reset_helper
    assert "data-value-kind=\"json-list\"" in reset_helper
    assert 'control.value = "[]";' in reset_helper


def test_completeness_status_filter_includes_selected_products(client):
    script = client.get("/static/app.js").get_data(as_text=True)

    assert '["selected", "已选"]' in script
    assert "selectedProductIds: selected" in script


def test_draft_rejects_unknown_values_without_persisting_sensitive_input(client, session_id, tmp_path):
    response = client.post(
        f"/api/sessions/{session_id}/stages/setup/draft",
        json={"values": {"store": "测试店铺", "password": "secret"}, "revision": 0},
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


def test_draft_after_submit_is_rejected_until_handoff_is_withdrawn(client, session_id, tmp_path):
    submitted = client.post(
        f"/api/sessions/{session_id}/stages/production_confirmation/submit",
        json={"values": valid_production_confirmation()},
    )
    drafted = client.post(
        f"/api/sessions/{session_id}/stages/production_confirmation/draft",
        json={"values": {"store": "updated store"}, "revision": 1},
    )

    assert submitted.status_code == 202
    assert drafted.status_code == 409
    status = client.get(
        f"/api/sessions/{session_id}/stages/production_confirmation/status"
    ).json
    assert status["revision"] == 1
    assert status["status"] == "ready_for_agent"
    stage_path = tmp_path / session_id / "08-production-confirmation"
    assert (stage_path / "handoff.json").is_file()
    assert (stage_path / "revisions" / "0001" / "handoff.json").is_file()
    input_path = next(tmp_path.glob(f"{session_id}/**/input.json"))
    assert '"password"' not in input_path.read_text(encoding="utf-8")


@pytest.mark.parametrize("revision", [None, True, "0"])
def test_draft_requires_integer_current_revision(client, session_id, revision):
    payload = {"values": {"store": "draft"}}
    if revision is not None:
        payload["revision"] = revision

    response = client.post(
        f"/api/sessions/{session_id}/stages/setup/draft",
        json=payload,
    )

    assert response.status_code == 422
    assert response.json["field_errors"]["revision"] == "must be an integer"


def test_stale_draft_returns_conflict_without_changing_durable_files(client, session_id, tmp_path):
    current = client.post(
        f"/api/sessions/{session_id}/stages/production_confirmation/submit",
        json={"values": valid_production_confirmation()},
    )
    session_path = tmp_path / session_id
    tracked = [
        session_path / "session.json",
        session_path / "08-production-confirmation" / "input.json",
        session_path / "08-production-confirmation" / "handoff.json",
    ]
    before = {path: path.read_bytes() for path in tracked}

    stale = client.post(
        f"/api/sessions/{session_id}/stages/production_confirmation/draft",
        json={"values": {"store": "stale"}, "revision": 0},
    )

    assert current.status_code == 202
    assert stale.status_code == 409
    assert {path: path.read_bytes() for path in tracked} == before


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
    status = client.get(
        f"/api/sessions/{session_id}/stages/production_confirmation/status"
    ).json
    assert status["revision"] == 0
    assert status["status"] == "draft"


def test_validation_requires_selected_products_and_enum_values(client, session_id):
    selection = client.post(
        f"/api/sessions/{session_id}/stages/completeness/submit",
        json={"values": {"selected_product_ids": []}},
    )
    decision = client.post(
        f"/api/sessions/{session_id}/stages/dry_run/submit",
        json={"values": {"decision": ["confirm"]}},
    )

    assert selection.status_code == 422
    assert "selected_product_ids" in selection.json["field_errors"]
    assert decision.status_code == 422
    assert "decision" in decision.json["field_errors"]


def test_stage_read_and_status_expose_schema_and_state(client, session_id):
    stage = client.get(f"/api/sessions/{session_id}/stages/setup")
    status = client.get(f"/api/sessions/{session_id}/stages/setup/status")

    assert stage.status_code == 200
    assert stage.json["stage"]["id"] == "setup"
    setup_fields = {field["name"] for field in stage.json["stage"]["fields"]}
    assert "store" in setup_fields
    assert "month" not in setup_fields
    assert status.json["revision"] == 0
    assert status.json["status"] == "draft"
    assert status.json["collection_status"]["status"] == "draft"
    assert status.json["collection_status"]["history"] == []


def test_dry_run_stage_exposes_agent_prepared_review_context(
    client, session_id, tmp_path
):
    store = SessionStore(tmp_path)
    store.write_review_context(
        session_id,
        "dry_run",
        {
            "schema_version": 1,
            "session_id": session_id,
            "stage_id": "dry_run",
            "revision": 0,
            "status": "needs_user_input",
            "summary": "dry-run ready",
            "blocking_reasons": [],
            "evidence": ["dry-run-tasks.json"],
            "next_action": "review",
            "data": {"task_count": 10},
        },
    )

    response = client.get(
        f"/api/sessions/{session_id}/stages/dry_run"
    )

    assert response.status_code == 200
    assert response.json["result"]["summary"] == "dry-run ready"
    assert response.json["result"]["data"]["task_count"] == 10


def test_approval_stage_exposes_upload_task_review_context(
    client, session_id, tmp_path
):
    store = SessionStore(tmp_path)
    store.write_review_context(
        session_id,
        "completeness",
        {
            "schema_version": 1,
            "session_id": session_id,
            "stage_id": "completeness",
            "revision": 0,
            "status": "completed",
            "summary": "products ready",
            "blocking_reasons": [],
            "evidence": [],
            "next_action": "review",
            "data": {
                "products": [
                    {"product_id": "1001", "owner": "小雨"},
                    {"product_id": "1002", "owner": "阿杰"},
                    {"product_id": "1003", "owner": "小雨"},
                    {"product_id": "1004", "owner": ""},
                ]
            },
        },
    )
    store.write_review_context(
        session_id,
        "approval",
        {
            "schema_version": 1,
            "session_id": session_id,
            "stage_id": "approval",
            "revision": 0,
            "status": "needs_user_input",
            "summary": "upload tasks ready",
            "blocking_reasons": [],
            "evidence": ["dry-run-tasks.json"],
            "next_action": "select exact tasks",
            "data": {
                "task_count": 1,
                "tasks": [
                    {
                        "task_id": "task-1",
                        "status": "ready_for_review",
                        "product_id": "1001",
                    }
                ],
            },
        },
    )

    response = client.get(
        f"/api/sessions/{session_id}/stages/approval"
    )

    assert response.status_code == 200
    assert response.json["result"]["summary"] == "upload tasks ready"
    assert response.json["result"]["data"]["tasks"][0]["task_id"] == "task-1"
    assert response.json["upload_identity"] == {
        "status": "authorized",
        "reason_code": "",
        "message": "上传负责人：测试用户（飞书账号）",
        "user_name": "测试用户",
    }


def test_approval_autosave_preserves_review_context_in_frontend(client):
    javascript = client.get("/static/app.js").get_data(as_text=True)

    preservation_block = javascript.split(
        "const preservesReviewContext = [", 1
    )[1].split("]", 1)[0]
    assert '"approval"' in preservation_block


def test_current_workflow_hides_legacy_production_confirmation(client, session_id):
    page = client.get(f"/?session_id={session_id}").get_data(as_text=True)
    javascript = client.get("/static/app.js").get_data(as_text=True)

    assert 'data-stage-panel="production_confirmation"' not in page
    assert "提交并自动上传所选任务" in javascript


def test_stage_read_exposes_only_current_handoff_submission_time(
    client, session_id, tmp_path
):
    submitted = client.post(
        f"/api/sessions/{session_id}/stages/production_confirmation/submit",
        json={"values": valid_production_confirmation()},
    )
    handoff_path = tmp_path / session_id / "08-production-confirmation" / "handoff.json"
    handoff = json.loads(handoff_path.read_text(encoding="utf-8"))

    current = client.get(
        f"/api/sessions/{session_id}/stages/production_confirmation"
    )
    assert submitted.status_code == 202
    assert current.json["submission"] == {"created_at": handoff["created_at"]}

    handoff["input_sha256"] = "0" * 64
    handoff_path.write_text(json.dumps(handoff), encoding="utf-8")
    invalidated = client.get(
        f"/api/sessions/{session_id}/stages/production_confirmation"
    )
    assert invalidated.json["submission"] is None

    drafted = client.post(
        f"/api/sessions/{session_id}/stages/production_confirmation/draft",
        json={"values": {"store": "updated store"}, "revision": 1},
    )
    draft = client.get(f"/api/sessions/{session_id}/stages/production_confirmation")
    assert drafted.status_code == 409
    assert draft.json["submission"] is None


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

    result_path = (
        tmp_path
        / session_id
        / "08-production-confirmation"
        / "result.json"
    )
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["input_sha256"] = "0" * 64
    result_path.write_text(json.dumps(result), encoding="utf-8")

    stale = client.get(
        f"/api/sessions/{session_id}/stages/production_confirmation"
    )

    assert stale.status_code == 200
    assert stale.json["result"] is None


def test_completeness_review_context_survives_incremental_decision_drafts(
    client, session_id, tmp_path
):
    submitted = client.post(
        f"/api/sessions/{session_id}/stages/completeness/submit",
        json={"values": {"selected_product_ids": ["1"]}},
    )
    SessionStore(tmp_path).write_result(
        session_id,
        "completeness",
        submitted.json["revision"],
        submitted.json["input_sha256"],
        status="needs_user_input",
        summary="搜推素材完整度待确认",
        data={"contract_version": 1, "products": [{"product_id": "1"}]},
    )

    drafted = client.post(
        f"/api/sessions/{session_id}/stages/completeness/draft",
        json={
            "revision": submitted.json["revision"],
            "values": {"selected_product_ids": ["1", "2"]},
        },
    )
    current = client.get(f"/api/sessions/{session_id}/stages/completeness")

    assert drafted.status_code == 200
    assert current.json["state"]["status"] == "draft"
    assert current.json["result"]["summary"] == "搜推素材完整度待确认"
    stage_path = tmp_path / session_id / "02-completeness"
    assert not (stage_path / "result.json").exists()
    assert (stage_path / "review-context.json").is_file()


def test_asset_matching_review_context_survives_rejected_folder_draft(
    client, session_id, tmp_path
):
    submitted = client.post(
        f"/api/sessions/{session_id}/stages/asset_matching/submit",
        json={
            "values": {
                "image_roots": [str(tmp_path)],
                "source_types": ["image"],
                "folder_decisions": [],
            }
        },
    )
    SessionStore(tmp_path).write_result(
        session_id,
        "asset_matching",
        submitted.json["revision"],
        submitted.json["input_sha256"],
        status="needs_user_input",
        summary="请确认候选文件夹",
        data={
            "folder_candidates": [
                {
                    "folder_id": "folder-a",
                    "product_id": "1",
                    "folder_name": "商品 A",
                },
                {
                    "folder_id": "folder-b",
                    "product_id": "1",
                    "folder_name": "商品 A 买家秀",
                },
            ]
        },
    )

    drafted = client.post(
        f"/api/sessions/{session_id}/stages/asset_matching/draft",
        json={
            "revision": submitted.json["revision"],
            "values": {
                "image_roots": [str(tmp_path)],
                "source_types": ["image"],
                "folder_decisions": [
                    {
                        "folder_id": "folder-a",
                        "product_id": "1",
                        "decision": "rejected",
                    }
                ],
            },
        },
    )
    current = client.get(f"/api/sessions/{session_id}/stages/asset_matching")

    assert drafted.status_code == 200
    assert current.json["state"]["status"] == "draft"
    assert len(current.json["result"]["data"]["folder_candidates"]) == 2
    assert current.json["input"]["values"]["folder_decisions"] == [
            {
                "decision": "rejected",
                "folder_id": "folder-a",
                "folder_path": "",
                "note": "",
                "product_id": "1",
                "relative_path": "",
                "source_id": "",
                "source_system": "",
            },
        {
            "decision": "confirmed",
            "folder_id": "folder-b",
                "folder_path": "",
                "note": "",
                "product_id": "1",
                "relative_path": "",
                "source_id": "",
                "source_system": "",
            },
    ]
    stage_path = tmp_path / session_id / "03-asset-matching"
    assert not (stage_path / "result.json").exists()
    assert (stage_path / "review-context.json").is_file()


def test_asset_matching_folder_review_is_a_distinct_first_submit(
    client, session_id, tmp_path, monkeypatch
):
    store = SessionStore(tmp_path)
    store.write_review_context(
        session_id,
        "asset_matching",
        {
            "schema_version": 1,
            "session_id": session_id,
            "stage_id": "asset_matching",
            "revision": 0,
            "status": "needs_user_input",
            "summary": "请确认候选文件夹",
            "blocking_reasons": [],
            "evidence": [],
            "next_action": "确认文件夹并加载图片",
            "data": {
                "workflow_step": "folder_review",
                "folder_candidates": [
                    {
                        "folder_id": "exact",
                        "folder_path": str(tmp_path / "exact"),
                        "product_id": "P1",
                        "source_system": "nas",
                        "match_type": "exact_sku",
                    },
                    {
                        "folder_id": "fuzzy",
                        "folder_path": str(tmp_path / "fuzzy"),
                        "product_id": "P1",
                        "source_system": "nas",
                        "match_type": "fuzzy_name_candidate",
                    },
                    {
                        "folder_id": "split-long",
                        "folder_path": str(tmp_path / "split-long"),
                        "product_id": "P1",
                        "source_system": "nas",
                        "match_type": "split_name_candidate",
                    },
                    {
                        "folder_id": "split-short",
                        "folder_path": str(tmp_path / "split-short"),
                        "product_id": "P1",
                        "source_system": "nas",
                        "match_type": "short_split_name_candidate",
                    },
                ],
            },
        },
    )

    submitted = client.post(
        f"/api/sessions/{session_id}/stages/asset_matching/prepare-gallery",
        json={
            "revision": 0,
            "values": {
                "image_roots": [str(tmp_path)],
                "folder_decisions": [],
            }
        },
    )

    assert submitted.status_code == 202
    assert submitted.json["status"] == "local_processing"
    persisted = store.read_optional_stage_document(
        session_id, "asset_matching", "input"
    )
    assert persisted["values"]["source_types"] == ["image"]
    assert [
        item["decision"] for item in persisted["values"]["folder_decisions"]
    ] == ["confirmed", "confirmed", "confirmed", "rejected"]
    assert persisted["values"].get("asset_decisions", []) == []
    stage_path = tmp_path / session_id / "03-asset-matching"
    assert not (stage_path / "handoff.json").exists()
    assert (stage_path / "gallery-job.json").is_file()
    assert submitted.json["gallery_job"]["status"] == "queued"
    state = store.load_session(session_id)
    assert state["stages"]["asset_matching"]["status"] == "draft"


def test_prepare_gallery_excludes_removed_product_and_keeps_remaining_product(
    client, session_id, tmp_path
):
    store = SessionStore(tmp_path)
    store.write_review_context(
        session_id,
        "asset_matching",
        {
            "schema_version": 1,
            "session_id": session_id,
            "stage_id": "asset_matching",
            "revision": 0,
            "status": "needs_user_input",
            "summary": "请确认候选文件夹",
            "blocking_reasons": [],
            "evidence": [],
            "next_action": "确认文件夹并加载图片",
            "data": {
                "workflow_step": "folder_review",
                "folder_candidates": [
                    {
                        "folder_id": "F1",
                        "folder_path": str(tmp_path / "p1"),
                        "product_id": "P1",
                        "match_type": "exact_product_id",
                    },
                    {
                        "folder_id": "F2",
                        "folder_path": str(tmp_path / "p2"),
                        "product_id": "P2",
                        "match_type": "exact_product_id",
                    },
                ],
            },
        },
    )

    response = client.post(
        f"/api/sessions/{session_id}/stages/asset_matching/prepare-gallery",
        json={
            "revision": 0,
            "values": {
                "image_roots": [str(tmp_path)],
                "folder_decisions": [],
                "removed_product_ids": ["P2"],
            },
        },
    )

    assert response.status_code == 202
    persisted = store.read_optional_stage_document(
        session_id, "asset_matching", "input"
    )
    assert persisted["values"]["removed_product_ids"] == ["P2"]
    decisions = {
        item["product_id"]: item["decision"]
        for item in persisted["values"]["folder_decisions"]
    }
    assert decisions == {"P1": "confirmed", "P2": "rejected"}


def test_prepare_gallery_rejects_removing_every_product(
    client, session_id, tmp_path
):
    SessionStore(tmp_path).write_review_context(
        session_id,
        "asset_matching",
        {
            "schema_version": 1,
            "session_id": session_id,
            "stage_id": "asset_matching",
            "revision": 0,
            "status": "needs_user_input",
            "summary": "请确认候选文件夹",
            "blocking_reasons": [],
            "evidence": [],
            "next_action": "确认文件夹并加载图片",
            "data": {
                "workflow_step": "folder_review",
                "folder_candidates": [
                    {
                        "folder_id": "F1",
                        "product_id": "P1",
                        "match_type": "exact_product_id",
                    }
                ],
            },
        },
    )

    response = client.post(
        f"/api/sessions/{session_id}/stages/asset_matching/prepare-gallery",
        json={
            "revision": 0,
            "values": {
                "image_roots": [str(tmp_path)],
                "folder_decisions": [],
                "removed_product_ids": ["P1"],
            },
        },
    )

    assert response.status_code == 422
    assert response.json["field_errors"]["removed_product_ids"] == (
        "本次任务至少需要保留一个商品"
    )


def test_prepare_gallery_inherits_setup_image_roots_when_stage_payload_is_empty(
    client, session_id, tmp_path
):
    store = SessionStore(tmp_path)
    configured_roots = [str(tmp_path / "source-a"), str(tmp_path / "source-b")]
    store.save_local_input(
        session_id,
        "setup",
        {"image_roots": configured_roots},
        expected_revision=0,
    )
    store.write_review_context(
        session_id,
        "asset_matching",
        {
            "schema_version": 1,
            "session_id": session_id,
            "stage_id": "asset_matching",
            "revision": 0,
            "status": "needs_user_input",
            "summary": "请确认候选文件夹",
            "blocking_reasons": [],
            "evidence": [],
            "next_action": "确认文件夹并加载图片",
            "data": {
                "workflow_step": "folder_review",
                "folder_candidates": [
                    {
                        "folder_id": "folder-a",
                        "folder_path": configured_roots[0],
                        "product_id": "P1",
                        "source_system": "nas",
                        "match_type": "exact_sku",
                    }
                ],
            },
        },
    )

    response = client.post(
        f"/api/sessions/{session_id}/stages/asset_matching/prepare-gallery",
        json={
            "revision": 0,
            "values": {"image_roots": [], "folder_decisions": []},
        },
    )

    assert response.status_code == 202
    persisted = store.read_optional_stage_document(
        session_id, "asset_matching", "input"
    )
    assert persisted["values"]["image_roots"] == configured_roots


def test_asset_matching_folder_counts_wait_for_material_executor(
    client, session_id, tmp_path
):
    folder = tmp_path / "folder-count"
    folder.mkdir()
    (folder / "one.jpg").write_bytes(b"not-an-image")
    (folder / "ignore.txt").write_text("ignore", encoding="utf-8")
    SessionStore(tmp_path).write_review_context(
        session_id,
        "asset_matching",
        {
            "schema_version": 1,
            "session_id": session_id,
            "stage_id": "asset_matching",
            "revision": 0,
            "status": "needs_user_input",
            "summary": "确认候选文件夹",
            "blocking_reasons": [],
            "evidence": [],
            "next_action": "确认文件夹并加载图片",
            "data": {
                "workflow_step": "folder_review",
                "folder_candidates": [
                    {
                        "folder_id": "F1",
                        "folder_path": str(folder),
                        "product_id": "P1",
                    }
                ],
            },
        },
    )

    response = client.get(
        f"/api/sessions/{session_id}/stages/asset_matching/folder-image-counts"
    )

    assert response.status_code == 200
    assert response.json["folder_counts"] == [
        {
            "folder_id": "F1",
            "image_count_status": "unknown",
            "raw_recursive_image_count": None,
            "image_count_reason_code": "MATERIAL_EXECUTOR_REQUIRED",
        }
    ]


def test_asset_matching_cannot_submit_while_gallery_job_is_queued(
    client, session_id, tmp_path
):
    folder = tmp_path / "queued-folder"
    folder.mkdir()
    store = SessionStore(tmp_path)
    store.write_review_context(
        session_id,
        "asset_matching",
        {
            "schema_version": 1,
            "session_id": session_id,
            "stage_id": "asset_matching",
            "revision": 0,
            "status": "needs_user_input",
            "summary": "请确认候选文件夹",
            "blocking_reasons": [],
            "evidence": [],
            "next_action": "确认文件夹并加载图片",
            "data": {
                "workflow_step": "folder_review",
                "folder_candidates": [{
                    "folder_id": "F1",
                    "folder_path": str(folder),
                    "product_id": "P1",
                    "source_system": "nas",
                    "match_type": "exact_product_id",
                }],
            },
        },
    )
    prepared = client.post(
        f"/api/sessions/{session_id}/stages/asset_matching/prepare-gallery",
        json={
            "revision": 0,
            "values": {
                "image_roots": [str(tmp_path)],
                "folder_decisions": [{
                    "folder_id": "F1",
                    "folder_path": str(folder),
                    "product_id": "P1",
                    "source_system": "nas",
                    "decision": "confirmed",
                }],
            },
        },
    )
    assert prepared.status_code == 202

    submitted = client.post(
        f"/api/sessions/{session_id}/stages/asset_matching/submit",
        json={
            "revision": prepared.json["revision"],
            "values": {
                "image_roots": [str(tmp_path)],
                "folder_decisions": [{
                    "folder_id": "F1",
                    "folder_path": str(folder),
                    "product_id": "P1",
                    "source_system": "nas",
                    "decision": "confirmed",
                }],
                "asset_decisions": [],
                "license_decisions": [],
            },
        },
    )

    assert submitted.status_code in {409, 422}, submitted.json
    if submitted.status_code == 422:
        assert "仍在准备" in submitted.json["field_errors"]["asset_decisions"]


def test_asset_matching_rejects_all_folders_before_handoff(
    client, session_id, tmp_path
):
    SessionStore(tmp_path).write_review_context(
        session_id,
        "asset_matching",
        {
            "schema_version": 1,
            "session_id": session_id,
            "stage_id": "asset_matching",
            "revision": 0,
            "status": "needs_user_input",
            "summary": "请确认候选文件夹",
            "blocking_reasons": [],
            "evidence": [],
            "next_action": "确认文件夹并加载图片",
            "data": {
                "workflow_step": "folder_review",
                "folder_candidates": [
                    {
                        "folder_id": "F1",
                        "product_id": "P1",
                        "match_type": "fuzzy_name_candidate",
                    }
                ],
            },
        },
    )

    response = client.post(
        f"/api/sessions/{session_id}/stages/asset_matching/prepare-gallery",
        json={
            "revision": 0,
            "values": {
                "image_roots": [str(tmp_path)],
                "folder_decisions": [
                    {
                        "folder_id": "F1",
                        "product_id": "P1",
                        "decision": "rejected",
                    }
                ],
            }
        },
    )

    assert response.status_code == 422
    assert "至少采用一个" in response.json["field_errors"]["folder_decisions"]


def test_completeness_submit_rejects_excluded_or_stale_product_ids(
    client, session_id, tmp_path
):
    submitted = client.post(
        f"/api/sessions/{session_id}/stages/completeness/submit",
        json={"values": {"selected_product_ids": ["1"]}},
    )
    SessionStore(tmp_path).write_result(
        session_id,
        "completeness",
        submitted.json["revision"],
        submitted.json["input_sha256"],
        status="needs_user_input",
        summary="待用户选择",
        data={
            "products": [
                {"product_id": "1", "status": "needs_supplement", "selectable": True},
                {"product_id": "2", "status": "excluded", "selectable": False},
            ]
        },
    )

    response = client.post(
        f"/api/sessions/{session_id}/stages/completeness/submit",
        json={"values": {"selected_product_ids": ["1", "2", "999"]}},
    )

    assert response.status_code == 422
    assert "2" in response.json["field_errors"]["selected_product_ids"]
    assert "999" in response.json["field_errors"]["selected_product_ids"]


def test_asset_gallery_serves_only_current_result_candidate_images(
    client, session_id, tmp_path, monkeypatch
):
    asset_id = "a" * 16
    image_path = tmp_path / "candidate.png"
    Image.new("RGB", (20, 30), "red").save(image_path)
    submitted = client.post(
        f"/api/sessions/{session_id}/stages/asset_matching/submit",
        json={
            "values": {
                "image_roots": [str(tmp_path)],
                "source_types": ["image"],
                "license_decisions": [
                    {"asset_id": asset_id, "status": "confirmed"}
                ],
                "asset_decisions": [
                    {"asset_id": asset_id, "decision": "selected"}
                ],
            }
        },
    )
    SessionStore(tmp_path).write_result(
        session_id,
        "asset_matching",
        submitted.json["revision"],
        submitted.json["input_sha256"],
        status="needs_user_input",
        summary="请选择素材",
        data={
            "requirements": [
                {
                    "product_id": "123",
                    "product_title": "测试商品",
                    "missing_materials": 1,
                    "images_per_material": 3,
                }
            ],
            "asset_candidates": [
                {
                    "asset_id": asset_id,
                    "product_id": "123",
                    "source_path": str(image_path),
                    "sha256": "a" * 64,
                    "source_system": "model_nas",
                    "match_type": "exact_product_id",
                    "match_status": "matched_unlicensed",
                        "license_status": "confirmed",
                        "validation_status": "valid",
                        "preflight": {"selectable": True, "status": "direct"},
                        "source_inspection": {
                            "size_bytes": image_path.stat().st_size,
                            "width": 20,
                            "height": 30,
                        },
                }
            ],
            "remote_dedupe_status": "not_available",
        },
    )

    stage = client.get(f"/api/sessions/{session_id}/stages/asset_matching")
    preview = client.get(
        f"/api/sessions/{session_id}/stages/asset_matching/assets/{asset_id}"
    )
    missing = client.get(
        f"/api/sessions/{session_id}/stages/asset_matching/assets/UNKNOWN"
    )

    assert (
        stage.json["result"]["data"]["asset_candidates"][0]["asset_id"]
        == asset_id
    )
    assert preview.status_code == 200
    assert preview.mimetype == "image/jpeg"
    assert preview.headers["Cache-Control"] == "private, max-age=300"
    assert (
        tmp_path
        / session_id
        / "03-asset-matching"
        / "preview-cache"
        / f"{asset_id}.jpg"
    ).is_file()
    assert missing.status_code == 404
    image_path.unlink()
    primed_preview = client.get(
        f"/api/sessions/{session_id}/stages/asset_matching/assets/{asset_id}"
    )
    assert primed_preview.status_code == 200
    current_result = web_module._current_result
    monkeypatch.setattr(
        web_module,
        "_current_result",
        lambda *_args, **_kwargs: pytest.fail(
            "cached content-addressed previews must not read gallery context"
        ),
    )
    cached_preview = client.get(
        f"/api/sessions/{session_id}/stages/asset_matching/assets/{asset_id}"
    )
    assert cached_preview.status_code == 200
    assert cached_preview.mimetype == "image/jpeg"
    assert cached_preview.headers["Cache-Control"] == "private, max-age=300"
    monkeypatch.setattr(web_module, "_current_result", current_result)

    draft = client.post(
        f"/api/sessions/{session_id}/stages/asset_matching/draft",
        json={
            "revision": submitted.json["revision"],
            "values": {
                "image_roots": [str(tmp_path)],
                "source_types": ["image"],
                "license_decisions": [],
                "asset_decisions": [
                    {
                        "asset_id": asset_id,
                        "product_id": "123",
                        "sha256": "a" * 64,
                        "source_system": "model_nas",
                        "source_path": str(image_path),
                        "decision": "selected",
                    }
                ],
            },
        },
    )
    assert draft.status_code == 200
    current_input = SessionStore(tmp_path).read_optional_stage_document(
        session_id, "asset_matching", "input"
    )
    assert current_input["values"]["license_decisions"] == [
        {"asset_id": asset_id, "status": "confirmed"}
    ]


def test_asset_gallery_allows_candidate_under_confirmed_folder_alias(
    client, session_id, tmp_path
):
    configured_root = tmp_path / "mapped-drive-root"
    confirmed_folder = tmp_path / "unc-alias" / "confirmed-product"
    configured_root.mkdir()
    confirmed_folder.mkdir(parents=True)
    image_path = confirmed_folder / "candidate.png"
    Image.new("RGB", (20, 30), "red").save(image_path)
    submitted = client.post(
        f"/api/sessions/{session_id}/stages/asset_matching/submit",
        json={
            "values": {
                "image_roots": [str(configured_root)],
                "source_types": ["image"],
                "folder_decisions": [
                    {
                        "decision": "confirmed",
                        "folder_id": "F1",
                        "folder_path": str(confirmed_folder),
                        "product_id": "123",
                        "source_system": "model_nas",
                    }
                ],
            }
        },
    )
    SessionStore(tmp_path).write_result(
        session_id,
        "asset_matching",
        submitted.json["revision"],
        submitted.json["input_sha256"],
        status="needs_user_input",
        summary="review candidate",
        data={
            "asset_candidates": [
                {
                    "asset_id": "A",
                    "product_id": "123",
                    "source_path": str(image_path),
                }
            ]
        },
    )

    preview = client.get(
        f"/api/sessions/{session_id}/stages/asset_matching/assets/A"
    )

    assert preview.status_code == 200
    assert preview.mimetype == "image/jpeg"


def test_asset_matching_normalization_rejects_selected_assets_from_excluded_folders(
    client, session_id, tmp_path
):
    root = tmp_path / "素材"
    parent = root / "商品"
    nested = parent / "精选"
    nested.mkdir(parents=True)
    image_a = parent / "a.jpg"
    image_b = nested / "b.jpg"
    Image.new("RGB", (20, 30), "red").save(image_a)
    Image.new("RGB", (20, 30), "blue").save(image_b)
    submitted = client.post(
        f"/api/sessions/{session_id}/stages/asset_matching/submit",
        json={
            "values": {
                "image_roots": [str(root)],
                "source_types": ["image"],
                "folder_decisions": [],
            }
        },
    )
    SessionStore(tmp_path).write_result(
        session_id,
        "asset_matching",
        submitted.json["revision"],
        submitted.json["input_sha256"],
        status="needs_user_input",
        summary="请选择素材",
        data={
            "folder_candidates": [
                {
                    "folder_id": "PARENT",
                    "folder_path": str(parent),
                    "product_id": "123",
                    "source_system": "model",
                },
                {
                    "folder_id": "NESTED",
                    "folder_path": str(nested),
                    "product_id": "123",
                    "source_system": "model",
                },
            ],
            "asset_candidates": [
                {
                    "asset_id": "A",
                    "folder_id": "PARENT",
                    "folder_path": str(parent),
                    "product_id": "123",
                    "source_path": str(image_a),
                    "sha256": "a" * 64,
                        "source_system": "model",
                        "match_type": "name_candidate",
                        "validation_status": "valid",
                        "preflight": {"selectable": True, "status": "direct"},
                        "source_inspection": {
                            "size_bytes": image_a.stat().st_size,
                            "width": 20,
                            "height": 30,
                        },
                },
                {
                    "asset_id": "B",
                    "product_id": "123",
                    "source_path": str(image_b),
                    "sha256": "b" * 64,
                        "source_system": "model",
                        "match_type": "name_candidate",
                        "validation_status": "valid",
                        "preflight": {"selectable": True, "status": "direct"},
                        "source_inspection": {
                            "size_bytes": image_b.stat().st_size,
                            "width": 20,
                            "height": 30,
                        },
                },
            ],
        },
    )

    drafted = client.post(
        f"/api/sessions/{session_id}/stages/asset_matching/draft",
        json={
            "revision": submitted.json["revision"],
            "values": {
                "image_roots": [str(root)],
                "source_types": ["image"],
                "folder_decisions": [
                    {
                        "folder_id": "PARENT",
                        "product_id": "123",
                        "decision": "rejected",
                    },
                    {
                        "folder_id": "NESTED",
                        "product_id": "123",
                        "decision": "pending",
                    },
                ],
                "asset_decisions": [
                    {"asset_id": "A", "decision": "selected"},
                    {"asset_id": "B", "decision": "selected"},
                ],
                "license_decisions": [
                    {"asset_id": "A", "status": "confirmed"},
                    {"asset_id": "B", "status": "confirmed"},
                ],
            },
        },
    )

    assert drafted.status_code == 200
    current = client.get(
        f"/api/sessions/{session_id}/stages/asset_matching"
    )
    values = current.json["input"]["values"]
    assert [
        item["decision"] for item in values["folder_decisions"]
    ] == ["rejected", "confirmed"]
    assert [item["asset_id"] for item in values["asset_decisions"]] == ["B"]
    assert values["asset_decisions"][0]["folder_id"] == "NESTED"
    assert values["license_decisions"] == [
        {"asset_id": "B", "status": "confirmed"}
    ]


def test_asset_gallery_javascript_exposes_review_controls_and_safety_status():
    source = (
        Path(__file__).parents[1]
        / "src"
        / "upload_search_materials"
        / "interaction"
        / "static"
        / "app.js"
    ).read_text(encoding="utf-8")
    folder_review_source = source[
        source.index("function renderFolderOwnershipReview") :
        source.index("function inferAssetMatchingStep")
    ]

    for expected in (
        "远端去重未完成",
        "asset_decisions",
        "license_decisions",
        "folder_decisions",
        "完整名称片段命中",
        "短名称片段候选",
        "folder-decision-changed",
        "pruneSelectedCandidates",
        "历史候选未关联文件夹",
        "preservesReviewContext",
        "候选仍在加载，完成后可选择",
        "已渐进展示",
    ):
        assert expected in source

    assert "let isHydrating = false" in source
    assert "if (isHydrating) return;" in source
    assert "确认文件夹并加载图片" in source
    assert "确认文件夹并重新加载图片" in source
    assert "文件夹选择已变更，请重新加载图片后继续选图。" in source
    assert "UiState.galleryNeedsReload" in folder_review_source
    assert (
        'card.setAttribute("aria-disabled", String(galleryActive))'
        in folder_review_source
    )
    assert "if (galleryJobIsActive()) return;" in folder_review_source
    assert "button.disabled = galleryJobIsActive() || activeCount <= 1" in source
    assert "图片正在加载，完成后可调整候选文件夹或去掉商品。" not in source
    assert "确认选图并提交给工作台" in source
    assert "第 1 步：筛选文件夹" in source
    assert "第 2 步：选择图片" in source
    assert '["pending", "待确认"]' not in source
    assert "点击图片即可选择" in source
    assert "galleryAutoFocusedFor" in source
    assert "scrollIntoView" in source
    assert 'card.setAttribute("role", "checkbox")' in source
    assert (
        'card.setAttribute("aria-checked", String(selected))'
        in folder_review_source
    )
    assert 'card.addEventListener("click", toggleDecision)' in folder_review_source
    assert 'card.addEventListener("keydown", (event)' in folder_review_source
    assert (
        'stateBadge.textContent = selected ? "已采用" : "已排除"'
        in folder_review_source
    )
    assert 'document.createElement("select")' not in folder_review_source
    assert 'document.createElement("input")' not in folder_review_source
    assert "备注（可选）" not in folder_review_source
    assert "排除该文件夹" not in folder_review_source
    assert '"is-unselectable"' in source
    assert "hydrateApproverOptions" not in source
    assert "尚未取得飞书授权账号" in source
    assert "（飞书账号）" in source
    assert "请先筛选候选文件夹" not in source
    assert "素材数统计中" not in source
    assert "文件夹内共发现" not in source
    assert "上传前检查提醒" not in source
    assert 'document.createTextNode("采用")' not in source
    assert 'document.createTextNode("授权已确认")' not in source
    assert "按每坑 3–9 张自动生成坑位草稿" in source
    assert "预计创建 ${guidance.completeSlots} 个完整坑位" in source
    assert "重复素材不计入可用数量" in source
    assert '"换一批"' in source
    assert "已选素材" in source
    assert "发现 ${duplicateCount} 张完全重复图片" in source
    assert "data.page_size || 30" in source
    assert "每商品候选上限 ${candidateLimit} 张" in source
    assert "每批显示 ${pageSize} 张" in source
    assert "无法覆盖全部非空文件夹" in source
    assert "历史候选未记录文件夹覆盖审计" in source
    assert "FOLDER_COVERAGE_LIMIT_EXCEEDED" in source
    assert "已选满" not in source
    assert "确认归属并记录别名" not in source
    assert 'candidate?.match_type !== "confirmed_alias"' in source
    assert "loadFolderImageCounts" in source
    assert "published_batch_count" in source
    assert "const galleryComplete" in source
    assert "renderProductNavigator" in source
    assert "坑位编排商品导航" in source
    assert "图片裁剪商品导航" in source
    assert "正在加载候选文件夹…" in source
    assert "加载完成后将同时显示候选数量和文件夹列表。" in source
    assert "商品名称未获取" in source
    assert "UiState.folderReviewPresentation" in source
    assert "UiState.resolveProductTitle" in source
    assert '"(prefers-reduced-motion: reduce)"' in source
    assert 'behavior: reduceMotion ? "auto" : "smooth"' in source


def test_prepare_local_gallery_has_no_out_of_scope_stage_reference():
    source = (
        Path(__file__).parents[1]
        / "src"
        / "upload_search_materials"
        / "interaction"
        / "static"
        / "app.js"
    ).read_text(encoding="utf-8")
    function_body = source.split(
        "async function prepareLocalGallery(button)", 1
    )[1].split("async function persistStage", 1)[0]

    assert "requestedStageId" not in function_body
    assert "/stages/asset_matching/folder-image-counts" in source
    assert "确认文件夹后加载候选图片" in source
    assert "不会创建阶段交接" not in source
    assert "本机正在加载图片" in source
    assert "历史进度口径" in source


def test_prepare_local_gallery_materializes_visible_folder_defaults():
    source = (
        Path(__file__).parents[1]
        / "src"
        / "upload_search_materials"
        / "interaction"
        / "static"
        / "app.js"
    ).read_text(encoding="utf-8")
    function_body = source.split(
        "async function prepareLocalGallery(button)", 1
    )[1].split("async function persistStage", 1)[0]

    assert "function materializeFolderDecisions(candidates)" in source
    assert 'writeJsonListControl("folder_decisions", materialized);' in source
    assert "materializeFolderDecisions(folderCandidates)" in function_body
    assert "Number.isInteger(requestRevision)" in function_body
    assert "revision: requestRevision" in function_body
    assert "fieldErrors?.folder_decisions" in function_body
    assert "UiState.galleryNeedsReload" in function_body
    assert "文件夹选择没有变化，无需重新加载图片。" in function_body


def test_fuzzy_folder_default_and_warning_are_visible_risk_controls():
    static_root = (
        Path(__file__).parents[1]
        / "src"
        / "upload_search_materials"
        / "interaction"
        / "static"
    )
    source = (static_root / "app.js").read_text(encoding="utf-8")
    stylesheet = (static_root / "app.css").read_text(encoding="utf-8")

    assert 'candidate.match_type === "fuzzy_name_candidate"' in source
    assert '"asset-warning folder-match-warning-danger"' in source
    assert "粗略名称命中；默认采用，请重点核对，不属于本商品请排除" in source
    assert (
        ".folder-match-warning-danger { color: var(--danger); font-weight: 700; }"
        in stylesheet
    )


def test_asset_matching_internal_decisions_are_hidden_structured_controls(client):
    html = client.get("/").get_data(as_text=True)

    for name in (
        "source_types",
        "folder_decisions",
        "license_decisions",
        "asset_decisions",
    ):
        assert re.search(
            rf'<div class="field" data-field="{name}" hidden>\s*'
            rf'<input id="asset_matching-{name}" name="{name}" '
            rf'type="hidden" value="\[\]" data-value-kind="json-list"',
            html,
        )
        assert f'<label for="asset_matching-{name}">' not in html


def test_generic_result_renderer_includes_optional_agent_actions_as_safe_text():
    source = (
        Path(__file__).parents[1]
        / "src"
        / "upload_search_materials"
        / "interaction"
        / "static"
        / "app.js"
    ).read_text(encoding="utf-8")

    assert 'label.textContent = "阻塞原因"' in source
    assert 'label.textContent = "下一步"' in source
    assert "row.textContent = reason" in source
    assert "nextAction.textContent = result.next_action" in source


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
            },
            "revision": 0,
        },
    )
    input_path = tmp_path / session_id / "03-asset-matching" / "input.json"
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
            "source_types": ["image"],
            "include_video": False,
            "removed_product_ids": [],
        },
    }


def test_recovery_returns_session_store_instruction(client, session_id):
    response = client.get(f"/api/sessions/{session_id}/stages/setup/recovery")

    assert response.status_code == 200
    assert "workflow_dispatch is online" in response.json["instruction"]
    assert "workbench dispatcher" in response.json["instruction"]
    assert "Do not inspect project source" in response.json["instruction"]


def test_stage_status_exposes_authoritative_claim_and_expired_recovery(
    client, session_id, tmp_path
):
    submitted = client.post(
        f"/api/sessions/{session_id}/stages/setup/submit",
        json={
            "values": {
                "store": "测试店铺",
                "store_confirmed": True,
                "month": "2026-07",
                "products_csv": str(tmp_path),
                "rules_csv": str(tmp_path),
                "team_folder_index_root": str(tmp_path),
                "image_source_labels": ["测试素材源"],
                "image_roots": [str(tmp_path)],
                "asset_manifest": "",
                "historical_basic_xlsx": "",
                "historical_promotion_csv": "",
                "user_notes": "",
            }
        },
    )
    store = SessionStore(tmp_path)
    store.wait_for_handoff(
        session_id,
        "setup",
        timeout_seconds=0.1,
        claimant_id="first-agent",
        lease_seconds=60,
    )

    active = client.get(
        f"/api/sessions/{session_id}/stages/setup/status"
    )

    assert submitted.status_code == 202
    assert active.json["processing_claim"]["claimant_id"] == "first-agent"
    assert active.json["processing_claim"]["expired"] is False
    state = store.load_session(session_id)
    state["processing_claim"]["lease_expires_at"] = (
        "2000-01-01T00:00:00+00:00"
    )
    store._write_session_state(session_id, state)

    recovered = client.post(
        f"/api/sessions/{session_id}/stages/setup/recover-processing",
        json={"claimant_id": "replacement-agent"},
    )

    assert recovered.status_code == 200
    assert recovered.json["processing_claim"]["claimant_id"] == (
        "replacement-agent"
    )
    assert recovered.json["processing_claim"]["expired"] is False


def test_completeness_page_recovery_does_not_replace_specialized_claim(
    client, session_id, tmp_path
):
    store = SessionStore(tmp_path)
    setup = store.save_input(session_id, "setup", {"store": "shop"})
    store.write_result(
        session_id,
        "setup",
        setup["revision"],
        setup["input_sha256"],
        status="completed",
        summary="setup complete",
    )
    store.save_input(
        session_id,
        "completeness",
        {"selected_product_ids": ["1"]},
    )
    store.wait_for_handoff(
        session_id,
        "completeness",
        timeout_seconds=0.1,
        claimant_id="first-agent",
        lease_seconds=60,
    )
    state = store.load_session(session_id)
    original_claim_id = state["processing_claim"]["claim_id"]
    state["processing_claim"]["lease_expires_at"] = (
        "2000-01-01T00:00:00+00:00"
    )
    store._write_session_state(session_id, state)

    response = client.post(
        f"/api/sessions/{session_id}/stages/completeness/recover-processing",
        json={"claimant_id": "replacement-agent"},
    )

    assert response.status_code == 200
    assert response.json["reason_code"] == "SPECIALIZED_PROCESSOR_REQUIRED"
    assert response.json["claim_deferred"] is True
    assert response.json["processor"] == "process-product-selection"
    current = store.load_session(session_id)
    assert current["processing_claim"]["claim_id"] == original_claim_id
    assert current["processing_claim"]["claimant_id"] == "first-agent"


def test_frontend_shows_processing_lease_and_expired_recovery_action():
    source = (
        Path(__file__).parents[1]
        / "src"
        / "upload_search_materials"
        / "interaction"
        / "static"
        / "app.js"
    ).read_text(encoding="utf-8")

    assert "恢复过期处理" in source
    assert "processing_claim" in source
    assert "/recover-processing" in source
    assert "处理等待已于" in source
    assert 'currentStageId === "completeness"' in source
    assert "dispatcherOwnsRecovery" in source
    assert "currentWorkflowDispatch?.online === true" in source


def test_frontend_enables_copy_submit_only_when_all_fields_are_complete():
    source = (
        Path(__file__).parents[1]
        / "src"
        / "upload_search_materials"
        / "interaction"
        / "static"
        / "app.js"
    ).read_text(encoding="utf-8")

    assert "已确认标题、描述，可进入上传任务确认" not in source
    assert "我已核对该标题、描述与左侧素材一致，可进入 dry-run" not in source
    assert "batchConfirmation" not in source
    assert "finishButton.disabled = !hasCompleteDrafts;" in source
    assert "finishButton.hidden = !hasCompleteDrafts;" not in source
    assert "String(draft.title || \"\").trim()" in source
    assert "String(draft.description || \"\").trim()" in source
    assert "上传前检查发现需要修改的内容" in source
    assert 'element("section", "slot-blocking-summary")' in source


def test_approval_tasks_default_to_all_ready_tasks_once():
    source = (
        Path(__file__).parents[1]
        / "src"
        / "upload_search_materials"
        / "interaction"
        / "static"
        / "app.js"
    ).read_text(encoding="utf-8")

    assert 'task.status === "ready_for_review"' in source
    assert "readyTaskIds.forEach((taskId) => selected.add(taskId));" in source
    assert "content.dataset.approvalSelectionInitialized" in source
    assert "!currentStageHasPersistedInput" in source


def test_frontend_scrolls_and_focuses_the_first_invalid_field():
    source = (
        Path(__file__).parents[1]
        / "src"
        / "upload_search_materials"
        / "interaction"
        / "static"
        / "app.js"
    ).read_text(encoding="utf-8")

    assert "function clientFieldErrors(form)" in source
    assert "function focusFirstFieldError(form, fieldErrors)" in source
    assert 'target.scrollIntoView({ behavior: "smooth", block: "center" });' in source
    assert "focusControl.focus({ preventScroll: true });" in source
    assert "页面已定位到需要补充的位置" in source


def test_approval_submit_uses_one_click_authorization(
    client, session_id, tmp_path
):
    store = SessionStore(tmp_path)
    store.save_input(session_id, "setup", {"store": "测试店铺"})
    dry_handoff = store.save_input(
        session_id,
        "dry_run",
        {"decision": "confirm", "warning_notes": ""},
    )
    store.write_result(
        session_id,
        "dry_run",
        dry_handoff["revision"],
        dry_handoff["input_sha256"],
        status="completed",
        summary="dry-run complete",
    )
    response = client.post(
        f"/api/sessions/{session_id}/stages/approval/submit",
        json={
            "values": {
                "task_ids": ["task-1"],
            }
        },
    )

    assert response.status_code == 202
    handoff = store.read_optional_stage_document(session_id, "approval", "handoff")
    assert handoff["handoff_kind"] == "publish_authorization"
    assert handoff["authorization"]["action"] == "approve_and_publish_exact_tasks"
    assert handoff["authorization"]["final_confirmation"] is True
    assert handoff["authorization"]["confirmed_by"] == "测试用户"
    assert handoff["authorization"]["confirmed_by_source"] == (
        "lark_authorized_user"
    )
    approval_input = store.read_optional_stage_document(
        session_id, "approval", "input"
    )
    assert approval_input["values"] == {"task_ids": ["task-1"]}
    assert "confirmed_at" not in handoff["authorization"]
    assert "valid_until" not in handoff["authorization"]


def test_validation_enforces_approval_task_list(client, session_id, tmp_path):
    path = tmp_path / "readable.txt"
    path.write_text("ok", encoding="utf-8")
    response = client.post(
        f"/api/sessions/{session_id}/stages/approval/submit",
        json={
            "values": {
                "task_ids": "not a list",
            }
        },
    )

    assert response.status_code == 422
    assert "task_ids" in response.json["field_errors"]


def test_approval_submit_requires_a_named_authorized_lark_user(tmp_path):
    class UnauthorizedLarkAuthCoordinator:
        def status(self, *, refresh=True):
            return {
                "status": "authorization_required",
                "message": "请授权飞书账号。",
                "verification_url": "",
                "user_name": "",
            }

    workspace = Path(__file__).parents[1]
    runtime = RuntimeConfig(
        workspace_root=workspace,
        products=DiscoveredPath(None, "missing"),
        rules=DiscoveredPath(None, "missing"),
        image_sources=(),
        runs_root=tmp_path,
        config_path=tmp_path / "config" / "runtime.json",
        user_data_root=tmp_path / "user-data",
    )
    local_client = create_app(
        tmp_path,
        runtime_config=runtime,
        enforce_stage_order=False,
        lark_auth_coordinator=UnauthorizedLarkAuthCoordinator(),
    ).test_client()
    store = SessionStore(tmp_path)
    session_id = local_client.post("/api/sessions", json={}).json["session_id"]
    store.save_input(session_id, "setup", {"store": "测试店铺"})
    dry_handoff = store.save_input(
        session_id,
        "dry_run",
        {"decision": "confirm", "warning_notes": ""},
    )
    store.write_result(
        session_id,
        "dry_run",
        dry_handoff["revision"],
        dry_handoff["input_sha256"],
        status="completed",
        summary="dry-run complete",
    )

    response = local_client.post(
        f"/api/sessions/{session_id}/stages/approval/submit",
        json={"values": {"task_ids": ["task-1"]}},
    )

    assert response.status_code == 422
    assert response.json["reason_code"] == "LARK_UPLOAD_IDENTITY_REQUIRED"
    assert "完成飞书授权" in response.json["message"]
    assert store.read_optional_stage_document(
        session_id, "approval", "handoff"
    ) is None




def test_setup_validation_does_not_require_store_confirmation(
    client, session_id, tmp_path
):
    readable = tmp_path / "readable.txt"
    readable.write_text("ok", encoding="utf-8")
    values = {
        "store": "测试店铺",
        "products_csv": str(readable),
        "rules_csv": str(readable),
        "team_folder_index_root": str(tmp_path),
        "image_source_labels": ["测试图片源"],
        "image_roots": [str(tmp_path)],
        "asset_manifest": "",
        "historical_basic_xlsx": "",
        "historical_promotion_csv": "",
        "user_notes": "",
    }

    response = client.post(f"/api/sessions/{session_id}/stages/setup/submit", json={"values": values})

    assert response.status_code == 202
    assert (tmp_path / "user-data" / "config" / "runtime.json").is_file()


def test_source_code_never_imports_execution_modules():
    source = Path(__file__).parents[1] / "src" / "upload_search_materials" / "interaction" / "web.py"
    text = source.read_text(encoding="utf-8")

    assert "subprocess" not in text
    assert "playwright" not in text.lower()
    assert "BrowserUploader" not in text
    assert "def _publish(" not in text
    assert "import _publish" not in text


@pytest.mark.parametrize("artifact_name", ["input.json", "handoff.json", "result.json"])
@pytest.mark.parametrize("schema_version", [None, 999])
def test_stage_read_rejects_missing_or_unsupported_artifact_schema_version(
    client, session_id, tmp_path, artifact_name, schema_version
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
        summary="done",
    )
    artifact_path = (
        tmp_path
        / session_id
        / "08-production-confirmation"
        / artifact_name
    )
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    if schema_version is None:
        artifact.pop("schema_version")
    else:
        artifact["schema_version"] = schema_version
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")

    response = client.get(
        f"/api/sessions/{session_id}/stages/production_confirmation"
    )

    assert response.status_code == 409
    assert "schema_version" in response.json["error"]


def test_fifth_stage_uses_ai_manual_shared_pool_and_visual_crop():
    source = (
        Path(__file__).parents[1]
        / "src"
        / "upload_search_materials"
        / "interaction"
        / "static"
        / "app.js"
    ).read_text(encoding="utf-8")
    assert "人工添加坑位" in source
    assert "添加候选图片 · 可用" in source
    assert "加入当前坑位" in source
    assert "从坑位移除" in source
    assert "恢复建议框" in source
    assert "slot-ratio-buttons" in source
    assert source.count("overlay.tabIndex = 0") >= 2
    assert source.count('"aria-keyshortcuts"') >= 2
    assert 'overlay.addEventListener("keydown"' in source
    assert "window.prompt(" not in source
    assert "/stages/slots_copy/use-rules" not in source
    stylesheet = (
        Path(__file__).parents[1]
        / "src"
        / "upload_search_materials"
        / "interaction"
        / "static"
        / "app.css"
    ).read_text(encoding="utf-8")
    assert "@media (max-width: 760px)" in stylesheet
    assert "@media (max-width: 480px)" in stylesheet
    assert ".crop-overlay:focus-visible" in stylesheet


def test_asset_and_slot_editors_expose_scoped_remove_actions(client):
    html = client.get("/").get_data(as_text=True)
    source = client.get("/static/app.js").get_data(as_text=True)

    assert re.search(
        r'<input[^>]+name="removed_product_ids"[^>]+type="hidden"',
        html,
    )
    assert "去掉当前商品" in source
    assert "去掉当前坑位" in source
    assert "本次任务至少需要保留一个商品" in source
    assert "该商品的候选文件夹、已选图片和后续坑位会一并移除" in source
    assert "该坑位的图片编排会移除" in source


def test_collection_ui_prompts_for_human_check_and_auto_resume():
    source = (
        Path(__file__).parents[1]
        / "src"
        / "upload_search_materials"
        / "interaction"
        / "static"
        / "app.js"
    ).read_text(encoding="utf-8")

    assert 'worker.phase === "waiting_human_check"' in source
    assert "请在千牛窗口中完成验证" in source
    assert "验证通过后会自动继续采集" in source
