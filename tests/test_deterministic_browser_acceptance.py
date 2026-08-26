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
import upload_search_materials.interaction.web as web_module
from upload_search_materials.final_material_handoff import (
    process_final_material_handoff,
)
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
        page.wait_for_function(
            "document.querySelector('.asset-selection-summary')?.textContent.includes('还差 2 张')"
        )
        assert "还差 2 张" in summary.inner_text()
        for index in range(1, 9):
            checkboxes.nth(index).check()
            expected = index + 1
            page.wait_for_function(
                "([count]) => document.querySelector('.asset-selection-summary')"
                "?.textContent.includes(`已选 ${count} 张`)",
                arg=[expected],
            )
        page.wait_for_function(
            "document.querySelector('.asset-selection-summary')?.textContent.includes('3+3+3')"
        )
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
        assert (
            store.load_session(session_id)["stages"]["asset_matching"][
                "status"
            ]
            == "ready_for_agent"
        )
        process_final_material_handoff(store, session_id)
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

        wide = browser.new_page(viewport={"width": 1440, "height": 900})
        wide.goto(
            f"{base_url}/?session_id={session_id}",
            wait_until="networkidle",
        )
        wide.get_by_role("button", name="图片裁剪与压缩").wait_for()
        assert wide.locator("[data-stage-id]").count() == 6
        assert wide.get_by_role("button", name="阻塞项处理").count() == 0
        assert (
            wide.evaluate(
                "document.documentElement.scrollWidth <= window.innerWidth"
            )
            is True
        )
        wide.screenshot(
            path=str(evidence_root / "two-page-wide-workflow.png"),
            full_page=True,
        )
        wide.close()

        evidence = {
            "session_id": session_id,
            "viewport": {"width": 390, "height": 844},
            "wide_viewport": {"width": 1440, "height": 900},
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


def test_approval_missing_identity_scrolls_to_field_without_submitting(
    tmp_path,
):
    runs = tmp_path / "runs"
    runtime = RuntimeConfig(
        workspace_root=tmp_path,
        products=DiscoveredPath(None, "missing"),
        rules=DiscoveredPath(None, "missing"),
        image_sources=(),
        runs_root=runs,
    )
    app = create_app(
        runs, runtime_config=runtime, enforce_stage_order=False
    )
    store = SessionStore(runs)
    session = store.create_session()
    state = store.load_session(session.session_id)
    state["current_stage"] = "approval"
    state["stages"]["dry_run"]["status"] = "completed"
    store._write_session_state(session.session_id, state)
    tasks = [
        {
            "task_id": f"task-{index}",
            "status": "ready_for_review",
            "product_id": f"product-{index}",
            "remote_slot_position": index,
            "target_ratio": "3:4",
            "title": f"任务 {index}",
            "description": "用于验证长任务列表后的必填字段定位。",
            "media": [{"order": 1}],
            "blocking_reasons": [],
            "warnings": [],
        }
        for index in range(1, 9)
    ]
    store.write_review_context(
        session.session_id,
        "approval",
        {
            "schema_version": 1,
            "session_id": session.session_id,
            "stage_id": "approval",
            "revision": 0,
            "status": "needs_user_input",
            "summary": "请选择上传任务",
            "blocking_reasons": [],
            "evidence": [],
            "next_action": "确认上传任务",
            "data": {
                "product_count": len(tasks),
                "media_count": len(tasks),
                "tasks": tasks,
                "warnings": [],
            },
        },
    )

    with _live_server(app) as base_url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 700})
        submit_requests = []
        page.on(
            "request",
            lambda request: submit_requests.append(request.url)
            if request.url.endswith("/stages/approval/submit")
            else None,
        )
        page.goto(
            f"{base_url}/?session_id={session.session_id}",
            wait_until="networkidle",
        )
        page.locator(".upload-task-card").first.wait_for()
        page.wait_for_function(
            "document.querySelector('[name=task_ids]')?.value.trim()"
            " && !document.querySelector('[data-submit-stage]')?.disabled"
        )
        confirmed_by = page.locator('[name="confirmed_by"]')
        initial_box = confirmed_by.bounding_box()
        assert initial_box is not None and initial_box["y"] > 700

        page.locator("[data-submit-stage]").click()
        page.wait_for_function(
            "document.activeElement?.name === 'confirmed_by'"
        )
        page.wait_for_function(
            "() => {"
            " const rect = document.querySelector('[name=confirmed_by]')"
            ".getBoundingClientRect();"
            " return rect.top >= 0 && rect.bottom < window.innerHeight;"
            "}",
            timeout=5_000,
        )

        box = confirmed_by.bounding_box()
        assert box is not None and 0 <= box["y"] < 700
        assert confirmed_by.get_attribute("aria-invalid") == "true"
        assert "请填写授权人" in page.locator(
            "[data-action-message]"
        ).inner_text()
        assert submit_requests == []
        assert store.read_optional_stage_document(
            session.session_id, "approval", "handoff"
        ) is None
        browser.close()


def test_folder_prepare_refresh_and_retry_never_wake_codex(
    tmp_path, monkeypatch
):
    runs = tmp_path / "runs"
    folder = tmp_path / "adopted"
    folder.mkdir()
    runtime = RuntimeConfig(
        workspace_root=tmp_path,
        products=DiscoveredPath(None, "missing"),
        rules=DiscoveredPath(None, "missing"),
        image_sources=(str(tmp_path),),
        runs_root=runs,
    )
    app = create_app(
        runs, runtime_config=runtime, enforce_stage_order=False
    )
    store = SessionStore(runs)
    session = store.create_session()
    state = store.load_session(session.session_id)
    state["current_stage"] = "asset_matching"
    store._write_session_state(session.session_id, state)
    store.save_draft(
        session.session_id,
        "asset_matching",
        {
            "image_roots": [str(tmp_path)],
            "source_types": ["image"],
            "folder_decisions": [],
            "asset_decisions": [],
            "license_decisions": [],
        },
        expected_revision=0,
    )
    store.write_review_context(
        session.session_id,
        "asset_matching",
        {
            "schema_version": 1,
            "session_id": session.session_id,
            "stage_id": "asset_matching",
            "revision": 1,
            "status": "needs_user_input",
            "summary": "确认文件夹",
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
                        "source_system": "nas",
                        "match_type": "exact_product_id",
                    }
                ],
            },
        },
    )
    with _live_server(app) as base_url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 900, "height": 800})
        page.goto(
            f"{base_url}/?session_id={session.session_id}",
            wait_until="networkidle",
        )
        with page.expect_response(
            lambda response: response.url.endswith("/prepare-gallery")
        ):
            page.get_by_role(
                "button", name="确认文件夹并加载图片"
            ).click()
        stage_path = store._stage_path(
            session.session_id, "asset_matching"
        )
        assert not (stage_path / "handoff.json").exists()
        assert store.processing_claim(
            session.session_id, "asset_matching"
        ) is None
        assert store.agent_wait(
            session.session_id, "asset_matching"
        ) is None
        revision = store.load_session(session.session_id)["stages"][
            "asset_matching"
        ]["revision"]

        page.reload(wait_until="networkidle")
        assert store.load_session(session.session_id)["stages"][
            "asset_matching"
        ]["revision"] == revision
        job = json.loads(
            (stage_path / "gallery-job.json").read_text(encoding="utf-8")
        )
        first_attempt = job["attempt_id"]
        job.update(
            {
                "status": "failed",
                "reason_code": "GALLERY_JOB_FAILED",
                "message": "test interruption",
                "lease_expires_at": None,
            }
        )
        store._write_json_atomic(stage_path / "gallery-job.json", job)
        page.reload(wait_until="networkidle")
        with page.expect_response(
            lambda response: response.url.endswith("/gallery-job/retry")
        ):
            page.get_by_role("button", name="重试加载图片").click()
        retried = json.loads(
            (stage_path / "gallery-job.json").read_text(encoding="utf-8")
        )
        assert retried["attempt_id"] != first_attempt
        assert retried["status"] == "queued"
        assert not (stage_path / "handoff.json").exists()
        browser.close()
