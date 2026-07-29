from pathlib import Path

from PIL import Image

from upload_search_materials.image_compliance import default_image_policy
from upload_search_materials.image_review import build_image_review_data
from upload_search_materials.interaction.session import SessionStore
from upload_search_materials.interaction.web import create_app


def _prepared_client(tmp_path):
    runs = tmp_path / "runs"
    store = SessionStore(runs)
    session = store.create_session()
    root = tmp_path / "images"
    root.mkdir()
    source = root / "source.jpg"
    Image.effect_noise((1200, 1600), 100).convert("RGB").save(
        source,
        format="JPEG",
        quality=95,
    )
    matching = store.save_input(
        session.session_id,
        "asset_matching",
        {"image_roots": [str(root)]},
    )
    data = build_image_review_data(
        [{
            "asset_id": "asset-a",
            "product_id": "P1",
            "product_title": "测试商品",
            "source_system": "test",
            "source_path": str(source),
        }],
        [{
            "asset_id": "asset-a",
            "product_id": "P1",
            "decision": "selected",
            "selection_order": 1,
        }],
        policy=default_image_policy(),
        policy_sha256="policy",
        asset_matching_revision=matching["revision"],
    )
    store.write_review_context(
        session.session_id,
        "image_review",
        {
            "schema_version": 1,
            "session_id": session.session_id,
            "stage_id": "image_review",
            "revision": 0,
            "status": "needs_user_input",
            "summary": "ready",
            "blocking_reasons": [],
            "evidence": [],
            "next_action": "review",
            "data": data,
        },
    )
    return (
        create_app(runs, enforce_stage_order=False).test_client(),
        store,
        session.session_id,
        data,
    )


def test_image_review_context_and_preview_are_served(tmp_path):
    client, _, session_id, _ = _prepared_client(tmp_path)
    stage = client.get(f"/api/sessions/{session_id}/stages/image_review")
    preview = client.get(
        f"/api/sessions/{session_id}/stages/image_review/assets/asset-a"
    )
    assert stage.status_code == 200
    assert stage.json["result"]["data"]["capabilities"]["manual_crop"] is True
    assert preview.status_code == 200
    assert preview.mimetype == "image/jpeg"


def test_image_review_cached_preview_is_served_when_source_is_offline(tmp_path):
    client, store, session_id, data = _prepared_client(tmp_path)
    preview_url = (
        f"/api/sessions/{session_id}/stages/image_review/assets/asset-a"
    )

    initial = client.get(preview_url)
    assert initial.status_code == 200
    preview_path = (
        store._stage_path(session_id, "image_review")
        / "preview-cache"
        / "asset-a.jpg"
    )
    assert preview_path.is_file()

    Path(data["assets"][0]["source_path"]).unlink()
    cached = client.get(preview_url)

    assert cached.status_code == 200
    assert cached.mimetype == "image/jpeg"
    assert cached.headers["Cache-Control"] == "private, max-age=300"


def test_slot_board_reuses_review_preview_when_source_is_offline(tmp_path):
    client, store, session_id, data = _prepared_client(tmp_path)
    review_preview = client.get(
        f"/api/sessions/{session_id}/stages/image_review/assets/asset-a"
    )
    assert review_preview.status_code == 200

    store.write_review_context(
        session_id,
        "slots_copy",
        {
            "schema_version": 1,
            "session_id": session_id,
            "stage_id": "slots_copy",
            "revision": 0,
            "status": "needs_user_input",
            "summary": "ready",
            "blocking_reasons": [],
            "evidence": [],
            "next_action": "review",
            "data": {
                "products": [{
                    "product_id": "P1",
                    "outputs": [{
                        "asset_id": "asset-a",
                        "source_path": data["assets"][0]["source_path"],
                    }],
                }],
            },
        },
    )
    Path(data["assets"][0]["source_path"]).unlink()

    cached = client.get(
        f"/api/sessions/{session_id}/stages/slots_copy/assets/asset-a"
    )

    assert cached.status_code == 200
    assert cached.mimetype == "image/jpeg"
    assert cached.headers["Cache-Control"] == "private, max-age=300"


def test_image_review_submit_saves_suitability_without_materializing(tmp_path):
    client, store, session_id, data = _prepared_client(tmp_path)
    response = client.post(
        f"/api/sessions/{session_id}/stages/image_review/submit",
        json={
            "revision": 1,
            "values": {
                "decisions": [{
                    "asset_id": "asset-a",
                    "action": "crop",
                    "target_ratio": "1:1",
                    "crop_box": data["assets"][0]["crop_options"]["1:1"][
                        "normalized"
                    ],
                }],
                "user_notes": "",
            },
        },
    )
    assert response.status_code == 202
    document = store.read_optional_stage_document(
        session_id, "image_review", "input"
    )
    decision = document["values"]["decisions"][0]
    assert decision["decision"] == "candidate"
    assert {"1:1", "3:4"} <= set(decision["candidate_ratios"])
    assert "output" not in decision
    assert not (
        store._stage_path(session_id, "image_review") / "derived"
    ).exists()


def test_image_review_rejects_processing_before_slot_plan(tmp_path):
    client, _, session_id, data = _prepared_client(tmp_path)
    response = client.post(
        f"/api/sessions/{session_id}/stages/image_review/assets/asset-a/process",
        json={
            "action": "crop",
            "target_ratio": "1:1",
            "crop_box": data["assets"][0]["crop_options"]["1:1"]["normalized"],
        },
    )
    assert response.status_code == 409
    assert "SLOT_PLAN_REQUIRED" in response.json["error"]


def test_image_review_rejects_missing_or_stale_decisions(tmp_path):
    client, _, session_id, _ = _prepared_client(tmp_path)
    response = client.post(
        f"/api/sessions/{session_id}/stages/image_review/submit",
        json={"revision": 1, "values": {"decisions": [], "user_notes": ""}},
    )
    assert response.status_code == 422
    assert response.json["field_errors"]["decisions"] == "must not be empty"


def test_frontend_exposes_suitability_then_slot_first_processing():
    static_root = (
        Path(__file__).parents[1]
        / "src"
        / "upload_search_materials"
        / "interaction"
        / "static"
    )
    javascript = (static_root / "app.js").read_text(encoding="utf-8")
    assert "人工裁剪" in javascript
    assert "crop-overlay" in javascript
    assert '"image_review",' in javascript
    assert '"slots_copy",' in javascript
    assert "本阶段只保存适用性和两种比例的候选裁剪框" in javascript
    assert "第五阶段先编排坑位并确认唯一比例" in javascript
    assert "AI 编排坑位" in javascript
    assert "网页不会自动唤醒 Codex" in javascript
    assert 'if (value == null || value === "") return "大小未知"' in javascript
    assert "assetMetaText" in javascript
    assert "asset.size_bytes ?? asset.output_size_bytes" in javascript
    assert "assignment.asset_ids = [];" not in javascript
    assert "AI 裁剪按钮" not in javascript


def test_frontend_restores_current_stage_without_hydration_writes():
    static_root = (
        Path(__file__).parents[1]
        / "src"
        / "upload_search_materials"
        / "interaction"
        / "static"
    )
    javascript = (static_root / "app.js").read_text(encoding="utf-8")
    ui_state = (static_root / "ui-state.js").read_text(encoding="utf-8")

    assert "selectInitialStage" in javascript
    assert "applySessionSnapshot" in javascript
    assert "进入当前阶段" in javascript
    assert "writeJsonListControl(name, values, { notify = false } = {})" in javascript
    assert "草稿保存中；提交已排队" in javascript
    assert "mergePersistenceIntent" in javascript
    assert "function selectInitialStage" in ui_state
    assert "function stageSnapshot" in ui_state
