import json
from pathlib import Path
import argparse
import csv

from PIL import Image

from upload_search_materials.asset_matching_workflow import (
    FOLDER_REVIEW,
    IMAGE_SELECTION,
    build_gallery_identity,
    folder_decisions_sha256,
    gallery_covers_folder_decisions,
    classify_asset_handoff,
    FINAL_MATERIAL_HANDOFF,
    LEGACY_FOLDER_HANDOFF,
    infer_workflow_step,
)


def test_historical_asset_handoffs_are_classified_from_bound_input():
    legacy = classify_asset_handoff(
        {"stage_id": "asset_matching"},
        {
            "values": {
                "folder_decisions": [
                    {
                        "product_id": "P1",
                        "folder_id": "F1",
                        "decision": "confirmed",
                    }
                ],
                "asset_decisions": [],
            }
        },
    )
    final = classify_asset_handoff(
        {"stage_id": "asset_matching"},
        {
            "values": {
                "folder_decisions": [],
                "asset_decisions": [
                    {"asset_id": "A1", "decision": "selected"}
                ],
            }
        },
    )
    explicit_final = classify_asset_handoff(
        {"handoff_kind": "final_material_selection"},
        {"values": {}},
    )

    assert legacy == LEGACY_FOLDER_HANDOFF
    assert final == FINAL_MATERIAL_HANDOFF
    assert explicit_final == FINAL_MATERIAL_HANDOFF
from upload_search_materials.cli import _process_confirmed_gallery
from upload_search_materials.interaction.session import SessionStore
from upload_search_materials.io_tables import PRODUCT_REQUIRED_COLUMNS


def _decision(folder_id: str, decision: str = "confirmed"):
    return {
        "product_id": "P1",
        "folder_id": folder_id,
        "folder_path": rf"Z:\assets\{folder_id}",
        "source_system": "nas",
        "decision": decision,
    }


def test_historical_step_inference_is_safe():
    assert infer_workflow_step({"folder_candidates": [{}]}) == FOLDER_REVIEW
    assert infer_workflow_step({"requirements": [{"product_id": "P1"}]}) == IMAGE_SELECTION
    assert infer_workflow_step({"asset_candidates": [{"asset_id": "A1"}]}) == IMAGE_SELECTION


def test_folder_decision_identity_is_order_independent():
    left = [_decision("F2"), _decision("F1", "rejected")]
    right = list(reversed(left))
    assert folder_decisions_sha256(left) == folder_decisions_sha256(right)


def test_gallery_allows_exclusion_but_rejects_newly_adopted_folder():
    prepared = [_decision("F1"), _decision("F2")]
    identity = build_gallery_identity(
        session_id="S1",
        revision=3,
        input_sha256="abc",
        folder_decisions=prepared,
    )
    assert gallery_covers_folder_decisions(
        identity,
        [_decision("F1"), _decision("F2", "rejected")],
        session_id="S1",
    )
    assert not gallery_covers_folder_decisions(
        identity,
        [_decision("F1"), _decision("F3")],
        session_id="S1",
    )
    assert not gallery_covers_folder_decisions(
        identity,
        [_decision("F1")],
        session_id="another-session",
    )


def test_sanitized_folder_only_regression_fixture_records_read_only_expectation():
    fixture = json.loads(
        (
            Path(__file__).parent
            / "fixtures"
            / "session_20260730_055346_folder_only_sanitized.json"
        ).read_text(encoding="utf-8")
    )
    assert fixture["stage_before_page_open"]["folder_candidate_count"] == 14
    assert fixture["stage_before_page_open"]["asset_candidate_count"] == 0
    assert fixture["observed_after_read_only_page_open"]["revision"] == 1
    assert fixture["expected_after_fix"]["revision"] == 0


def test_maintained_processor_publishes_revision_bound_gallery(tmp_path):
    store = SessionStore(tmp_path / "runs")
    session_id = store.create_session().session_id
    session_path = tmp_path / "runs" / session_id
    products_path = session_path / "inputs" / "products.csv"
    products_path.parent.mkdir(parents=True, exist_ok=True)
    with products_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=sorted(PRODUCT_REQUIRED_COLUMNS)
        )
        writer.writeheader()
        writer.writerow(
            {
                "商品ID": "123",
                "商品名称（查找引用）": "测试商品",
                "货号（查找引用）": "SKU-123",
                "产品等级": "A",
                "链接": "https://example.invalid/123",
                "运营": "测试",
                "组别": "测试组",
                "品类-公司维度划分": "泳具",
            }
        )
    status_path = (
        session_path
        / "collected"
        / "promotion"
        / "current"
        / "promotion-material-status.csv"
    )
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(
        "商品ID,商品名称,缺失数量\n123,测试商品,1\n",
        encoding="utf-8-sig",
    )
    folder = tmp_path / "nas-folder"
    folder.mkdir()
    for index in range(3):
        Image.new("RGB", (1440, 1920), (index * 30, 10, 20)).save(
            folder / f"{index}.jpg"
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
            "summary": "folder review",
            "blocking_reasons": [],
            "evidence": [],
            "next_action": "confirm",
            "data": {
                "workflow_step": "folder_review",
                "folder_candidates": [
                    {
                        "product_id": "123",
                        "folder_id": "F1",
                        "folder_path": str(folder),
                    }
                ],
            },
        },
    )
    handoff = store.save_input(
        session_id,
        "asset_matching",
        {
            "image_roots": [str(folder)],
            "source_types": ["image"],
            "folder_decisions": [_decision("F1") | {"product_id": "123", "folder_path": str(folder)}],
        },
        allowed_current_statuses={"needs_user_input"},
    )

    exit_code = _process_confirmed_gallery(
        argparse.Namespace(
            runs_root=str(tmp_path / "runs"),
            session=session_id,
            claimant="test-agent",
            candidate_limit=100,
            page_size=30,
        )
    )

    assert exit_code == 0
    result = store.read_optional_stage_document(
        session_id, "asset_matching", "result"
    )
    assert result["revision"] == handoff["revision"]
    assert result["data"]["workflow_step"] == "image_selection"
    assert result["data"]["gallery_identity"]["selected_product_ids"] == ["123"]
    assert len(result["data"]["asset_candidates"]) == 3
    assert (
        store._stage_path(session_id, "asset_matching")
        / "gallery-progress.json"
    ).is_file()
