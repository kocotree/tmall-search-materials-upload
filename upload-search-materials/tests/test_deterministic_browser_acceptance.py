from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import threading

from PIL import Image
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

from upload_search_materials.interaction.session import SessionStore
from upload_search_materials.interaction.web import create_app
from upload_search_materials.runtime_config import DiscoveredPath, RuntimeConfig


@contextmanager
def _live_server(app):
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _prepared_browser_session(tmp_path):
    runs = tmp_path / "runs"
    sources = tmp_path / "sources"
    sources.mkdir()
    runtime = RuntimeConfig(
        workspace_root=tmp_path,
        products=DiscoveredPath(None, "missing"),
        rules=DiscoveredPath(None, "missing"),
        image_sources=(str(sources),),
        runs_root=runs,
    )
    app = create_app(
        runs,
        runtime_config=runtime,
        enforce_stage_order=False,
    )
    store = SessionStore(runs)
    session = store.create_session()
    candidates = []
    for index in range(35):
        source = sources / f"asset-{index:02d}.jpg"
        Image.effect_noise((720, 960), 90 + index).convert("RGB").save(
            source,
            quality=92,
        )
        source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
        size_bytes = source.stat().st_size
        candidates.append(
            {
                "asset_id": f"asset-{index:02d}",
                "product_id": "P1",
                "product_title": "浏览器验收商品",
                "source_path": str(source),
                "source_system": f"folder-{index % 3}",
                "sha256": source_sha256,
                "source_sha256": source_sha256,
                "match_type": "exact_product_name",
                "match_status": "confirmed",
                "validation_status": "valid",
                "remote_duplicate": False,
                "width": 720,
                "height": 960,
                "size_display": f"{size_bytes / 1024:.0f}KB",
                "source_inspection": {
                    "size_bytes": size_bytes,
                    "size_display": f"{size_bytes / 1024:.0f}KB",
                    "width": 720,
                    "height": 960,
                    "ratio_display": "3:4",
                    "format": "JPEG",
                },
                "preflight_status": "direct",
                "preflight": {
                    "selectable": True,
                    "status": "direct",
                    "size_display": f"{size_bytes / 1024:.0f}KB",
                    "original_ratio": "3:4",
                    "matching_ratios": ["3:4", "1:1"],
                    "resolution_checks": {
                        "3:4": {
                            "max_crop_width": 720,
                            "max_crop_height": 960,
                            "minimum_status": "meets_minimum",
                            "status": "below_recommended",
                        },
                        "1:1": {
                            "max_crop_width": 720,
                            "max_crop_height": 720,
                            "minimum_status": "meets_minimum",
                            "status": "below_recommended",
                        },
                    },
                    "reason_messages": [],
                },
            }
        )
    handoff = store.save_input(
        session.session_id,
        "asset_matching",
        {
            "image_roots": [str(sources)],
            "source_types": ["image"],
        },
    )
    store.write_result(
        session.session_id,
        "asset_matching",
        handoff["revision"],
        handoff["input_sha256"],
        status="needs_user_input",
        summary="请选择素材",
        data={
            "requirements": [
                {
                    "product_id": "P1",
                    "product_title": "浏览器验收商品",
                    "missing_materials": 3,
                }
            ],
            "asset_candidates": candidates,
            "page_size": 30,
            "remote_dedupe_status": "checked",
            "scan_summary": {
                "per_product": [
                    {"product_id": "P1", "discovered_images": 35}
                ]
            },
        },
    )
    return app, store, session.session_id


def test_deterministic_two_page_browser_acceptance(tmp_path):
    app, store, session_id = _prepared_browser_session(tmp_path)
    evidence_root = Path(
        os.getenv("UPLOAD_SEARCH_MATERIALS_BROWSER_EVIDENCE", tmp_path / "evidence")
    )
    evidence_root.mkdir(parents=True, exist_ok=True)
    evidence = {}

    with _live_server(app) as base_url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.goto(
            f"{base_url}/?session_id={session_id}",
            wait_until="networkidle",
        )
        page.locator(".asset-card").first.wait_for()
        assert page.locator(".asset-card").count() == 30
        next_batch = page.get_by_role("button", name="换一批")
        assert next_batch.is_enabled()
        next_batch.click()
        assert "本批 5 张" in page.locator(".asset-page-label").inner_text()
        page.get_by_role("button", name="上一批").click()

        checkboxes = page.locator(".asset-card input[type=checkbox]")
        checkboxes.nth(0).check()
        summary = page.locator(".asset-selection-summary")
        assert "还差 2 张" in summary.inner_text()
        for index in range(1, 9):
            checkboxes.nth(index).check()
        assert "预计创建 3 个完整坑位（3+3+3）" in summary.inner_text()
        assert (
            page.evaluate(
                "document.documentElement.scrollWidth <= window.innerWidth"
            )
            is True
        )

        with page.expect_response(
            lambda response: response.url.endswith(
                f"/api/sessions/{session_id}/stages/asset_matching/submit"
            )
        ) as submit_response:
            page.locator("[data-submit-stage]").click()
        response = submit_response.value
        assert response.status == 202, response.text()
        page.get_by_role("button", name="图片裁剪与压缩").wait_for()
        assert page.locator(".slot-workflow-step").count() == 2
        assert page.locator(".slot-card").count() == 3
        assert page.locator(".slot-count").all_inner_texts() == [
            "已选 3 张",
            "已选 3 张",
            "已选 3 张",
        ]

        page.get_by_role("button", name="从坑位移除").first.click()
        assert "数量不合规" in page.locator(".slot-count").first.inner_text()
        page.locator(".slot-candidate-section").first.locator("summary").click()
        page.get_by_role("button", name="加入当前坑位").first.click()
        assert page.locator(".slot-count").first.inner_text() == "已选 3 张"

        page.get_by_role(
            "button", name="确认坑位并进入图片裁剪"
        ).click()
        page.locator(".slot-process-card").first.wait_for()
        assert page.locator(".slot-process-card").count() == 3
        page.screenshot(
            path=str(evidence_root / "two-page-narrow-workflow.png"),
            full_page=True,
        )

        evidence = {
            "session_id": session_id,
            "viewport": {"width": 390, "height": 844},
            "candidate_first_batch": 30,
            "candidate_second_batch": 5,
            "slot_counts": [3, 3, 3],
            "two_page_navigation": True,
            "manual_remove_and_restore": True,
            "crop_controls_visible_after_confirmation": True,
            "real_upload_executed": False,
        }
        browser.close()

    (evidence_root / "browser-acceptance.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    assert (
        store._stage_path(session_id, "slots_copy")
        / "current-slot-plan.json"
    ).is_file()
