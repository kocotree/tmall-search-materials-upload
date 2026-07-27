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


def test_image_review_submit_materializes_manual_crop(tmp_path):
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
    output = document["values"]["decisions"][0]["output"]
    assert output["kind"] == "crop"
    assert Path(output["output_path"]).is_file()
    assert Path(output["output_path"]).is_relative_to(
        store._stage_path(session_id, "image_review") / "derived"
    )


def test_image_review_can_generate_and_serve_processed_preview(tmp_path):
    client, _, session_id, data = _prepared_client(tmp_path)
    response = client.post(
        f"/api/sessions/{session_id}/stages/image_review/assets/asset-a/process",
        json={
            "action": "crop",
            "target_ratio": "1:1",
            "crop_box": data["assets"][0]["crop_options"]["1:1"]["normalized"],
        },
    )
    assert response.status_code == 200
    output = response.json["output"]
    preview = client.get(
        f"/api/sessions/{session_id}/stages/image_review/outputs/asset-a"
        f"?sha256={output['output_sha256']}"
    )
    assert preview.status_code == 200
    assert preview.mimetype == "image/jpeg"


def test_image_review_rejects_missing_or_stale_decisions(tmp_path):
    client, _, session_id, _ = _prepared_client(tmp_path)
    response = client.post(
        f"/api/sessions/{session_id}/stages/image_review/submit",
        json={"revision": 1, "values": {"decisions": [], "user_notes": ""}},
    )
    assert response.status_code == 422
    assert response.json["field_errors"]["decisions"] == "must not be empty"


def test_frontend_exposes_manual_crop_and_real_compression_controls():
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
    assert "人工裁剪与本地图片压缩已启用" in javascript
    assert "生成处理预览" in javascript
    assert "裁剪并压缩" in javascript
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
