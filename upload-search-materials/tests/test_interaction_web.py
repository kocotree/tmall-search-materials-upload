import html as html_module
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


@pytest.fixture
def client(tmp_path):
    workspace = Path(__file__).parents[2]
    runtime = RuntimeConfig(
        workspace_root=workspace,
        products=DiscoveredPath(
            workspace / "docs" / "天猫商品信息表_产品数据表_数据总表.csv",
            "discovered",
        ),
        rules=DiscoveredPath(
            workspace / "docs" / "天猫商品信息表_每月推品规则（合并）_Grid View.csv",
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
    )
    return create_app(
        tmp_path, runtime_config=runtime, enforce_stage_order=False
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


def test_create_session_returns_timestamp_id(client):
    response = client.post("/api/sessions", json={})

    assert response.status_code == 201
    assert re.fullmatch(r"\d{8}_\d{6}(?:_\d{2})?", response.json["session_id"])


def test_root_renders_nine_stage_left_rail(client):
    response = client.get("/")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert response.mimetype == "text/html"
    assert html.count('data-stage-id="') == 9


def test_new_session_hides_legacy_image_review_stage(client, session_id):
    html = client.get(f"/?session_id={session_id}").get_data(as_text=True)

    assert html.count('data-stage-id="') == 8
    assert 'data-stage-id="image_review"' not in html
    assert 'data-stage-id="slots_copy"' in html
    assert "完整度巡检" in html
    assert "生产确认" in html
    assert re.search(r"/static/app\.js\?v=[0-9a-f]{12}", html)


def test_setup_page_separates_user_choices_automatic_inputs_and_advanced_imports(client):
    html = html_module.unescape(client.get("/").get_data(as_text=True))

    assert "业务配置" in html
    assert "自动准备项" in html
    assert "高级设置 · 导入已有文件" in html
    assert "搜推素材" in html and "全量自动采集" in html
    assert "搜推高价值" in html
    assert 'name="promotion_max_pages"' not in html
    assert 'name="product_scope"' not in html
    assert "在文件夹归属审查中逐步积累" not in html
    assert "视频" in html and "本轮延期" in html
    assert f'value="{date.today():%Y-%m}"' in html
    assert 'name="products_csv"' in html and 'type="hidden"' in html
    assert 'name="rules_csv"' in html
    assert html.count('name="image_roots"') >= 3
    for removed in ("basic_xlsx", "search_xlsx", "asset_root", "runs_root"):
        assert f'name="{removed}"' not in html
    assert 'data-component="CollectionRuntimeConfig"' in html
    assert 'data-save-selector-profile' in html
    assert "CDP Chrome" in html


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
        (
            tmp_path
            / "upload-search-materials"
            / "config"
            / "local-paths.json"
        ).read_text(encoding="utf-8")
    )
    assert config["selectors_file"] == str(selectors)


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
    project = workspace / "upload-search-materials"
    scripts = project / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    (workspace / "docs").mkdir()
    (project / "config").mkdir()
    (scripts / "python.exe").write_bytes(b"prepared")
    (scripts / "tmall-materials.exe").write_bytes(b"prepared")
    (project / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    runtime = RuntimeConfig(
        workspace_root=workspace,
        products=DiscoveredPath(None, "missing"),
        rules=DiscoveredPath(None, "missing"),
        image_sources=(),
        runs_root=workspace / "runs",
    )

    class Locator:
        def count(self):
            return 1

        def inner_text(self):
            return "测试店铺"

        def is_visible(self):
            return False

    class Page:
        url = (
            "https://myseller.taobao.com/home.htm/"
            "material-center/material-management"
        )

        def locator(self, _selector):
            return Locator()

    @contextmanager
    def open_page(*_args, **_kwargs):
        yield Page()

    connected = CdpStatus(
        connected=True,
        endpoint="http://127.0.0.1:9222",
    )
    monkeypatch.setattr(web_module, "open_cdp_page", open_page)
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


def test_production_setup_submission_is_blocked_by_readiness(tmp_path):
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
                "asset_manifest": "",
                "historical_basic_xlsx": "",
                "historical_promotion_csv": "",
                "user_notes": "",
            }
        },
    )

    assert response.status_code == 422
    assert "collection_readiness" in response.json["field_errors"]
    assert "ENVIRONMENT_NOT_PREPARED" in response.json["field_errors"][
        "collection_readiness"
    ]


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
    assert "添加图片源" in html
    assert "检测路径" in html
    assert "保存为本机配置" in html
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
    assert "0 个图片源" in html
    assert "添加图片源" in html
    assert html.count('name="image_roots"') >= 1


def test_runtime_image_source_api_saves_checks_and_reloads_multiple_roots(tmp_path):
    workspace = tmp_path / "workspace"
    (workspace / "upload-search-materials" / "config").mkdir(parents=True)
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
    config = workspace / "upload-search-materials" / "config" / "local-paths.json"
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


def test_image_source_frontend_uses_diagnostic_copy_and_unc_action():
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
    assert "已采用 UNC，等待检测" in source
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
    for label in ("商品 ID 命中", "SKU 命中", "完整商品名称命中 · 待确认"):
        assert label in html
    assert "已确认别名" not in html


def test_completeness_stage_exposes_review_controls_without_raw_json_as_primary_ui(client):
    html = client.get("/").get_data(as_text=True)

    assert "搜推素材完整度" in html
    assert "目标 / 已有 / 缺失 / 证据" in html
    assert 'data-field="selected_product_ids"' in html
    assert html.count("interaction-data-field") >= 1

    script = client.get("/static/app.js").get_data(as_text=True)
    for text in (
        "搜索商品 ID、货号或名称",
        "选择当前筛选结果",
        "取消当前筛选结果",
        "选择进入素材匹配",
        "已自动排除",
        "命中自动排除规则",
        "查看后台证据",
    ):
        assert text in script
    assert 'element("small", "", "基础素材")' not in script
    assert 'if (!event.target?.getAttribute?.("name")) return;' in script


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
        "完成第五阶段并进入 dry-run",
    ):
        assert text in javascript
    assert '? ["process", "copy"]' in javascript
    assert "请求 ID：" not in javascript
    assert 'detail: { source: "explicit-user-edit" }' in javascript
    assert (
        'event.detail?.source !== "explicit-user-edit"' in javascript
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


def test_javascript_supports_dynamic_image_source_configuration(client):
    javascript = client.get("/static/app.js").get_data(as_text=True)

    for expected in (
        "appendImageSource",
        "hydrateImageSources",
        "configuredImageSources",
        "/api/runtime/image-sources/check",
        "/api/runtime/folder-picker",
        'method: "PUT"',
        "每个图片源都必须填写来源名称和根路径",
        "scheduleAutoSave",
        "setFormLocked",
        "/withdraw",
    ):
        assert expected in javascript


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


def test_results_recovery_rechecks_status_inside_save_lock(
    tmp_path, monkeypatch
):
    store = SessionStore(tmp_path)
    session = store.create_session()
    initial_handoff = store.save_input(
        session.session_id,
        "results",
        {"recovery_action": "retry"},
    )
    store.write_result(
        session.session_id,
        "results",
        initial_handoff["revision"],
        initial_handoff["input_sha256"],
        status="blocked",
        summary="awaiting recovery decision",
    )

    route_reached_save = threading.Event()
    allow_recovery_save = threading.Event()
    original_save_input = SessionStore.save_input

    def pause_before_save_lock(self, session_id, stage_id, *args, **kwargs):
        if stage_id == "results":
            route_reached_save.set()
            assert allow_recovery_save.wait(2)
        return original_save_input(self, session_id, stage_id, *args, **kwargs)

    monkeypatch.setattr(SessionStore, "save_input", pause_before_save_lock)
    responses = []

    def submit_recovery():
        with create_app(tmp_path, enforce_stage_order=False).test_client() as client:
            responses.append(
                client.post(
                    f"/api/sessions/{session.session_id}/stages/results/submit",
                    json={
                        "values": {
                            "recovery_action": "retry",
                            "manual_notes": "retry only if still blocked",
                            "allow_retry_after_remote_absence": True,
                        }
                    },
                )
            )

    request_thread = threading.Thread(target=submit_recovery)
    request_thread.start()
    assert route_reached_save.wait(2)

    store.write_result(
        session.session_id,
        "results",
        initial_handoff["revision"],
        initial_handoff["input_sha256"],
        status="completed",
        summary="agent completed while recovery request was waiting",
    )
    results_path = session.path / "09-results"
    completed_artifacts = {
        name: (results_path / name).read_bytes()
        for name in ("input.json", "handoff.json", "result.json")
    }
    completed_state = (session.path / "session.json").read_bytes()

    allow_recovery_save.set()
    request_thread.join(2)

    assert not request_thread.is_alive()
    assert responses[0].status_code == 409
    assert responses[0].json["error"] == "stage status does not allow input submission"
    assert (session.path / "session.json").read_bytes() == completed_state
    for name, expected_bytes in completed_artifacts.items():
        assert (results_path / name).read_bytes() == expected_bytes


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
        json={"values": {"store": "测试店铺"}, "revision": 0},
    )

    assert response.status_code == 200
    assert response.json["status"] == "draft"
    assert response.json["revision"] == 1
    stage_path = tmp_path / session_id / "01-setup"
    assert not (stage_path / "handoff.json").exists()
    assert (stage_path / "revisions" / "0001" / "input.json").is_file()
    assert not (stage_path / "revisions" / "0001" / "handoff.json").exists()


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
    assert client.get(f"/api/sessions/{session_id}/stages/production_confirmation/status").json == {
        "revision": 1,
        "status": "ready_for_agent",
    }
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
    assert client.get(f"/api/sessions/{session_id}/stages/production_confirmation/status").json == {
        "revision": 0,
        "status": "draft",
    }


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
    assert {field["name"] for field in stage.json["stage"]["fields"]} >= {"store", "month"}
    assert status.json["revision"] == 0
    assert status.json["status"] == "draft"
    assert status.json["collection_status"]["status"] == "draft"
    assert status.json["collection_status"]["history"] == []


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
            "source_system": "",
        },
        {
            "decision": "confirmed",
            "folder_id": "folder-b",
            "folder_path": "",
            "note": "",
            "product_id": "1",
            "source_system": "",
        },
    ]
    stage_path = tmp_path / session_id / "03-asset-matching"
    assert not (stage_path / "result.json").exists()
    assert (stage_path / "review-context.json").is_file()


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
    client, session_id, tmp_path
):
    image_path = tmp_path / "candidate.png"
    Image.new("RGB", (20, 30), "red").save(image_path)
    submitted = client.post(
        f"/api/sessions/{session_id}/stages/asset_matching/submit",
        json={
            "values": {
                "image_roots": [str(tmp_path)],
                "source_types": ["image"],
                "license_decisions": [{"asset_id": "A", "status": "confirmed"}],
                "asset_decisions": [{"asset_id": "A", "decision": "selected"}],
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
                    "asset_id": "A",
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
        f"/api/sessions/{session_id}/stages/asset_matching/assets/A"
    )
    missing = client.get(
        f"/api/sessions/{session_id}/stages/asset_matching/assets/UNKNOWN"
    )

    assert stage.json["result"]["data"]["asset_candidates"][0]["asset_id"] == "A"
    assert preview.status_code == 200
    assert preview.mimetype == "image/jpeg"
    assert preview.headers["Cache-Control"] == "private, max-age=300"
    assert (
        tmp_path
        / session_id
        / "03-asset-matching"
        / "preview-cache"
        / "A.jpg"
    ).is_file()
    assert missing.status_code == 404
    image_path.unlink()
    cached_preview = client.get(
        f"/api/sessions/{session_id}/stages/asset_matching/assets/A"
    )
    assert cached_preview.status_code == 200
    assert cached_preview.mimetype == "image/jpeg"
    assert cached_preview.headers["Cache-Control"] == "private, max-age=300"

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
                        "asset_id": "A",
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
        {"asset_id": "A", "status": "confirmed"}
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

    for expected in (
        "远端去重未完成",
        "asset_decisions",
        "license_decisions",
        "folder_decisions",
        "排除该文件夹",
        "候选文件夹默认采用",
        "folder-decision-changed",
        "pruneSelectedCandidates",
        "历史候选未关联文件夹",
        "preservesReviewContext",
    ):
        assert expected in source
    assert '["pending", "待确认"]' not in source
    assert "采用即确认该图片可用于本次发布" in source
    assert 'document.createTextNode("授权已确认")' not in source
    assert "按每坑 3–9 张自动生成坑位草稿" in source
    assert "预计创建 ${guidance.completeSlots} 个完整坑位" in source
    assert "重复素材不计入可用数量" in source
    assert '"换一批"' in source
    assert "已选素材" in source
    assert "发现 ${duplicateCount} 张完全重复图片" in source
    assert "data.page_size || 30" in source
    assert "已选满" not in source
    assert "确认归属并记录别名" not in source
    assert 'candidate?.match_type !== "confirmed_alias"' in source


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
            "source_types": ["模特图", "买家秀"],
            "include_video": False,
        },
    }


def test_recovery_returns_session_store_instruction(client, session_id):
    response = client.get(f"/api/sessions/{session_id}/stages/setup/recovery")

    assert response.status_code == 200
    assert "handoff.json" in response.json["instruction"]


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
    assert "处理租约已于" in source


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


def test_setup_validation_requires_store_confirmation(
    client, session_id, tmp_path
):
    readable = tmp_path / "readable.txt"
    readable.write_text("ok", encoding="utf-8")
    values = {
        "store": "测试店铺",
        "store_confirmed": False,
        "month": "2026-07",
        "products_csv": str(readable),
        "rules_csv": str(readable),
        "image_roots": [str(tmp_path)],
        "asset_manifest": "",
        "historical_basic_xlsx": "",
        "historical_promotion_csv": "",
        "user_notes": "",
    }

    response = client.post(f"/api/sessions/{session_id}/stages/setup/submit", json={"values": values})

    assert response.status_code == 422
    assert response.json["field_errors"]["store_confirmed"] == "must be confirmed"


def test_source_code_never_imports_execution_modules():
    source = Path(__file__).parents[1] / "src" / "upload_search_materials" / "interaction" / "web.py"
    text = source.read_text(encoding="utf-8")

    assert "subprocess" not in text
    assert "playwright" not in text.lower()
    assert "BrowserUploader" not in text
    assert "_publish" not in text


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
